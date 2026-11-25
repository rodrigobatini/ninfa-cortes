"""Checks and installs everything the clipper needs. Prints a ready / not-ready report."""
from __future__ import annotations

import importlib
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

from cl_common import (FONTS, IS_MAC, IS_WIN, YUNET, add_cuda_dll_dirs, load_config, log, run, save_config,
                       which)

PIP = {  # import name -> pip package
    "yt_dlp": "yt-dlp[default]",
    "faster_whisper": "faster-whisper",
    "cv2": "opencv-python",
    "numpy": "numpy",
    "fontTools": "fonttools",
}
OPTIONAL = ()


def pip_install(pkgs):
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "--disable-pip-version-check"] + pkgs
    log("  pip install " + " ".join(pkgs))
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        log("  pip failed:\n" + r.stderr[-1500:])
    return r.returncode == 0


def has_module(name):
    try:
        importlib.import_module(name)
        return True
    except Exception:
        return False


def install_ffmpeg():
    if IS_WIN and which("winget"):
        log("  installing ffmpeg with winget (Gyan.FFmpeg)...")
        subprocess.run(["winget", "install", "-e", "--id", "Gyan.FFmpeg", "--accept-source-agreements",
                        "--accept-package-agreements", "--silent"])
    elif IS_MAC and which("brew"):
        log("  installing ffmpeg with Homebrew...")
        subprocess.run(["brew", "install", "ffmpeg"])
    elif which("apt-get"):
        subprocess.run(["sudo", "apt-get", "install", "-y", "ffmpeg"])


def ffmpeg_features():
    ff = which("ffmpeg")
    if not ff:
        return None, False, []
    filters = run([ff, "-hide_banner", "-filters"], check=False).stdout
    has_ass = " ass " in filters
    encs = []
    tmp = Path(tempfile.gettempdir()) / "stream_clipper_enc_test.mp4"
    for enc in (["h264_nvenc"] if not IS_MAC else ["h264_videotoolbox"]) + ["libx264"]:
        r = run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30", "-t", "1",
                 "-c:v", enc, "-pix_fmt", "yuv420p", str(tmp)], check=False)
        if r.returncode == 0:
            encs.append(enc)
    try:
        tmp.unlink()
    except Exception:
        pass
    return ff, has_ass, encs


def test_gpu_whisper():
    add_cuda_dll_dirs()
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() == 0:
            return False, "no CUDA GPU"
    except Exception as e:
        return False, f"ctranslate2: {e}"
    ff = which("ffmpeg")
    wav = Path(tempfile.gettempdir()) / "stream_clipper_test.wav"
    run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=300:duration=2", "-ar", "16000",
         "-ac", "1", str(wav)])
    code = ("import sys;sys.path.insert(0,sys.argv[1]);from cl_common import add_cuda_dll_dirs;"
            "add_cuda_dll_dirs();from faster_whisper import WhisperModel;"
            "m=WhisperModel('tiny',device='cuda',compute_type='float16');"
            "s,i=m.transcribe(sys.argv[2]);list(s);print('GPU_OK')")
    r = subprocess.run([sys.executable, "-I", "-c", code, str(Path(__file__).parent), str(wav)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if "GPU_OK" in r.stdout:
        return True, "ok"
    return False, (r.stderr or "")[-300:]


def main(install: bool = True, force_cpu: bool = False, cookies_browser=None):
    log("AI Stream Clipper setup")
    log(f"  system: {platform.system()} {platform.release()}, python {sys.version.split()[0]}")
    rows = []
    ok_all = True
    if sys.version_info < (3, 9):
        rows.append(("python >= 3.9", False, sys.version.split()[0]))
        ok_all = False
    # python packages
    missing = [pkg for mod, pkg in PIP.items() if not has_module(mod)]
    if install:
        # yt-dlp breaks when YouTube changes things: always update it
        pip_install(["yt-dlp[default]"] + [p for p in missing if not p.startswith("yt-dlp")])
        importlib.invalidate_caches()
    for mod, pkg in PIP.items():
        good = has_module(mod)
        rows.append((pkg, good, "" if good else "pip install " + pkg))
        if not good and mod not in OPTIONAL:
            ok_all = False
    # ffmpeg
    ff, has_ass, encs = ffmpeg_features()
    if (not ff or not has_ass) and install:
        install_ffmpeg()
        ff, has_ass, encs = ffmpeg_features()
    rows.append(("ffmpeg", bool(ff), ff or "not found"))
    rows.append(("ffmpeg libass (captions)", has_ass, "" if has_ass else "need an ffmpeg build with libass"))
    ok_all &= bool(ff) and has_ass
    encoder = encs[0] if encs else None
    rows.append(("video encoder", bool(encoder), encoder or "none"))
    ok_all &= bool(encoder)
    # JS runtime for YouTube
    js = which("deno") or which("node")
    if not js and install:
        if IS_WIN and which("winget"):
            subprocess.run(["winget", "install", "-e", "--id", "DenoLand.Deno", "--accept-source-agreements",
                            "--accept-package-agreements", "--silent"])
        elif IS_MAC and which("brew"):
            subprocess.run(["brew", "install", "deno"])
        js = which("deno") or which("node")
    rows.append(("JS runtime for YouTube (deno/node)", bool(js), js or "install deno: https://deno.com"))
    # GPU whisper
    device, compute, model = "cpu", "int8", "small"
    if not force_cpu and has_module("faster_whisper"):
        good, why = test_gpu_whisper()
        if not good and install and IS_WIN and ("cublas" in why.lower() or "cudnn" in why.lower()):
            pip_install(["nvidia-cublas-cu12", "nvidia-cudnn-cu12==9.*"])
            good, why = test_gpu_whisper()
        if good:
            device, compute, model = "cuda", "float16", "large-v3-turbo"
        rows.append(("NVIDIA GPU for transcription", good, "large-v3-turbo on GPU" if good else
                     f"CPU mode, model 'small' ({why.strip().splitlines()[-1][:80] if why.strip() else 'no GPU'})"))
    # whisper model download
    if has_module("faster_whisper") and install:
        try:
            from faster_whisper import download_model
            log(f"  downloading whisper model '{model}' (first time only)...")
            download_model(model)
            rows.append((f"whisper model {model}", True, ""))
        except Exception as e:
            rows.append((f"whisper model {model}", False, str(e)[:100]))
            ok_all = False
    rows.append(("face detector (YuNet)", YUNET.exists(), str(YUNET)))
    fonts_ok = (FONTS / "Montserrat-Black.ttf").exists()
    rows.append(("caption fonts (Cyrillic)", fonts_ok, ""))
    ok_all &= YUNET.exists() and fonts_ok
    cfg = load_config()
    cfg.update({"device": device, "compute_type": compute, "model": model, "cpu_model": "small",
                "encoder": encoder or "libx264"})
    if cookies_browser:
        cfg["cookies_from_browser"] = cookies_browser
    save_config(cfg)
    log("")
    for name, good, note in rows:
        log(f"  [{'OK' if good else '--'}] {name}{'  ' + note if note else ''}")
    log("")
    if ok_all:
        log(f"READY. Transcription on {device.upper()} ({model}), encoder {cfg['encoder']}.")
        log("Next: python -I scripts/clipper.py prepare <stream link>")
    else:
        log("NOT READY. Fix the [--] lines above (run setup again with --install).")
    return ok_all
