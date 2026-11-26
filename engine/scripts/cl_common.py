"""Shared helpers for AI Stream Clipper. Pure stdlib + numpy-free on purpose."""
from __future__ import annotations

import glob
import json
import os
import platform
import re
import shutil
import site
import subprocess
import sys
import time
from functools import lru_cache
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
ASSETS = ROOT / "assets"
FONTS = ASSETS / "fonts"
YUNET = ASSETS / "models" / "face_detection_yunet_2023mar.onnx"
CONFIG_DIR = Path.home() / ".ai-stream-clipper"
CONFIG_FILE = CONFIG_DIR / "config.json"
DEFAULT_OUT = Path.home() / "StreamClipper"
IS_WIN = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"


def utf8_console() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def log(*parts) -> None:
    print(*parts, flush=True)


def read_json(p, default=None):
    p = Path(p)
    if not p.exists():
        return default
    with open(p, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def write_json(p, data) -> None:
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def write_text(p, text: str) -> None:
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def load_config() -> dict:
    return read_json(CONFIG_FILE, {}) or {}


def save_config(cfg: dict) -> None:
    write_json(CONFIG_FILE, cfg)


def strings() -> dict:
    return read_json(SCRIPTS / "strings.json")


def lang_strings(lang: str) -> dict:
    s = strings()
    return s.get(lang) or s["en"]


def fmt_ts(sec: float) -> str:
    sec = max(0, int(round(sec)))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_dur(sec: float) -> str:
    sec = int(round(sec))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h} h {m:02d} min"
    return f"{m} min {s:02d} s"


_TR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
               ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t",
                "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))


def slug(text: str, maxlen: int = 40) -> str:
    text = "".join(_TR.get(c, c) for c in (text or "").lower())
    t = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return t[:maxlen].strip("-") or "clip"


# ---------- external tools ----------

def _extra_bin_dirs():
    dirs = []
    if IS_WIN:
        la = os.environ.get("LOCALAPPDATA", "")
        if la:
            dirs.append(os.path.join(la, "Microsoft", "WinGet", "Links"))
            dirs += glob.glob(os.path.join(la, "Microsoft", "WinGet", "Packages", "Gyan.FFmpeg*", "*", "bin"))
        dirs.append(r"C:\ffmpeg\bin")
    if IS_MAC:
        dirs += ["/opt/homebrew/bin", "/usr/local/bin"]
    return dirs


def which(name: str):
    p = shutil.which(name)
    if p:
        return p
    for d in _extra_bin_dirs():
        for cand in (name + ".exe", name):
            fp = os.path.join(d, cand)
            if os.path.isfile(fp):
                return fp
    return None


def ffmpeg() -> str:
    p = which("ffmpeg")
    if not p:
        raise SystemExit("ffmpeg not found. Run: python -I scripts/clipper.py setup --install")
    return p


def ffprobe() -> str:
    p = which("ffprobe")
    if not p:
        raise SystemExit("ffprobe not found. Run: python -I scripts/clipper.py setup --install")
    return p


def run(cmd, cwd=None, check=True, timeout=None, quiet=True):
    """Run a command given as a list (never through a shell). Returns CompletedProcess."""
    cmd = [str(c) for c in cmd]
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    if check and r.returncode != 0:
        tail = (r.stderr or r.stdout or "")[-2500:]
        raise RuntimeError(f"Command failed ({r.returncode}): {Path(cmd[0]).name} ...\n{tail}")
    return r


def probe(path) -> dict:
    r = run([ffprobe(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)])
    return json.loads(r.stdout)


def media_duration(path) -> float:
    info = probe(path)
    d = info.get("format", {}).get("duration")
    return float(d) if d else 0.0


def video_stream(path) -> dict:
    for s in probe(path).get("streams", []):
        if s.get("codec_type") == "video":
            return s
    return {}


@lru_cache(maxsize=64)
def _video_decoder(path: Path, modified: int, size: int, binary: str) -> tuple[str, ...]:
    if video_stream(path).get("codec_name") != "av1":
        return ()
    options = ((), ("-hwaccel", "d3d11va", "-c:v", "av1"), ("-hwaccel", "cuda", "-c:v", "av1"))
    for decoder in options:
        try:
            result = subprocess.run(
                [binary, "-nostdin", "-v", "quiet", "-xerror", *decoder, "-i", str(path),
                 "-map", "0:v:0", "-frames:v", "1", "-vf", "scale=32:32", "-c:v", "png", "-f", "image2pipe", "pipe:1"],
                capture_output=True, timeout=12,
            )
        except subprocess.TimeoutExpired:
            continue
        if result.returncode == 0 and result.stdout.startswith(b"\x89PNG"):
            return decoder
    raise RuntimeError("O FFmpeg não conseguiu decodificar este vídeo AV1. Tente uma versão em H.264.")


def video_input(path) -> list[str]:
    path = Path(path)
    decoder = ()
    if IS_WIN:
        stat = path.stat()
        decoder = _video_decoder(path, stat.st_mtime_ns, stat.st_size, ffmpeg())
    return [*decoder, "-i", str(path)]


def add_cuda_dll_dirs() -> None:
    """faster-whisper (CTranslate2) on Windows needs cuBLAS 12 + cuDNN 9 DLLs.
    They ship in the pip packages nvidia-cublas-cu12 / nvidia-cudnn-cu12."""
    if not IS_WIN:
        return
    roots = []
    try:
        roots += site.getsitepackages()
    except Exception:
        pass
    try:
        roots.append(site.getusersitepackages())
    except Exception:
        pass
    for sp in roots:
        for d in glob.glob(os.path.join(sp, "nvidia", "*", "bin")):
            try:
                os.add_dll_directory(d)
            except Exception:
                pass
            if d not in os.environ.get("PATH", ""):
                os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")


class Timer:
    def __init__(self):
        self.t0 = time.time()

    def __call__(self) -> float:
        return time.time() - self.t0


def ytdlp_base_opts(cookies_from_browser=None, cookies_file=None) -> dict:
    """Options shared by every yt-dlp call. YouTube needs a JS runtime (deno or node)."""
    cfg = load_config()
    opts = {"quiet": True, "no_warnings": True, "noprogress": True, "retries": 10,
            "fragment_retries": 10, "concurrent_fragment_downloads": 8, "socket_timeout": 30}
    rt = {}
    if which("deno"):
        rt["deno"] = {}
    if which("node"):
        rt["node"] = {}
    if rt:
        opts["js_runtimes"] = rt
    ff = which("ffmpeg")
    if ff:
        opts["ffmpeg_location"] = str(Path(ff).parent)
    cb = cookies_from_browser or cfg.get("cookies_from_browser")
    if cb:
        opts["cookiesfrombrowser"] = (cb,)
    if cookies_file:
        opts["cookiefile"] = str(cookies_file)
    return opts


class QuietLogger:
    """yt-dlp logger: keeps errors for our own message instead of spamming the console."""

    def __init__(self):
        self.errors = []

    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        self.errors.append(msg)


BOT_CHECK = ("confirm you", "not a bot", "sign in to confirm", "age-restricted", "inappropriate for some users")


def is_bot_check(err) -> bool:
    t = str(err).lower()
    return any(k in t for k in BOT_CHECK)


def ytdlp_call(action, cookies_from_browser=None, cookies_file=None):
    """Run action(opts) with yt-dlp options. If YouTube asks to sign in (bot check after many downloads,
    or an age-restricted video), stop with a clear message: browser cookies are only used when the user
    passes --cookies-from-browser / --cookies himself."""
    opts = ytdlp_base_opts(cookies_from_browser, cookies_file)
    opts["logger"] = QuietLogger()
    try:
        return action(opts)
    except Exception as e:
        if not is_bot_check(e):
            raise
        hint = ("YouTube asks to sign in ('confirm you are not a bot': too many downloads from this internet "
                "connection, or an age-restricted video). Log into YouTube in Firefox and run the same command "
                "with --cookies-from-browser firefox (or wait an hour and try again).")
        if cookies_from_browser or cookies_file:
            hint = "YouTube still asks to sign in with these cookies: log into YouTube in that browser first."
        raise RuntimeError(hint + " Details: " + str(e)[:160]) from None
