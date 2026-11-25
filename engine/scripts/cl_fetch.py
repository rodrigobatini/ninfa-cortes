"""Source probing and downloads: audio only and short video sections.
Everything downloaded goes into <job>/media/ and is treated as untrusted data (never executed)."""
from __future__ import annotations

import re
import time
from pathlib import Path

from cl_common import Timer, ffmpeg, log, media_duration, run, slug, ytdlp_call


def is_url(src: str) -> bool:
    return bool(re.match(r"^https?://", src.strip(), re.I))


def platform_of(url: str) -> str:
    u = url.lower()
    if "twitch.tv" in u:
        return "twitch"
    if "kick.com" in u:
        return "kick"
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    return "other"


def probe_source(src: str, cookies=None, cookies_file=None) -> dict:
    """Metadata for a URL (via yt-dlp) or a local file (via ffprobe)."""
    if not is_url(src):
        p = Path(src).expanduser().resolve()
        if not p.exists():
            raise SystemExit(f"File not found: {p}")
        return {"kind": "file", "path": str(p), "id": slug(p.stem, 30), "title": p.stem,
                "channel": "", "url": "", "platform": "file", "duration": media_duration(p)}
    import yt_dlp

    def act(opts):
        opts["skip_download"] = True
        with yt_dlp.YoutubeDL(opts) as y:
            return y.extract_info(src, download=False)
    info = ytdlp_call(act, cookies, cookies_file)
    if info.get("_type") == "playlist" and info.get("entries"):
        info = next(e for e in info["entries"] if e)
    plat = platform_of(info.get("webpage_url") or src)
    if info.get("live_status") == "is_live":
        raise SystemExit("This stream is still live. Wait until it ends and the VOD is available.")
    return {
        "kind": "url", "url": info.get("webpage_url") or src, "platform": plat,
        "id": info.get("id") or slug(src, 30), "title": info.get("title") or "",
        "channel": info.get("channel") or info.get("uploader") or "",
        "duration": float(info.get("duration") or 0), "upload_date": info.get("upload_date"),
        "height": info.get("height"), "fps": info.get("fps"), "age_limit": info.get("age_limit"),
        "was_live": info.get("was_live"), "language": info.get("language"),
    }


def _find_downloaded(folder: Path, stem: str):
    for p in sorted(folder.glob(stem + ".*")):
        if p.suffix not in (".part", ".ytdl", ".json") and p.stat().st_size > 0:
            return p
    return None


def download_audio(job: Path, meta: dict, cookies=None, cookies_file=None) -> Path:
    """Audio only -> media/audio16k.wav (mono 16 kHz, used by whisper, loudness and sync)."""
    media = job / "media"
    media.mkdir(parents=True, exist_ok=True)
    wav = media / "audio16k.wav"
    if wav.exists() and wav.stat().st_size > 1000:
        log("  audio: already downloaded")
        return wav
    if meta["kind"] == "file":
        src = Path(meta["path"])
    else:
        src = _find_downloaded(media, "audio")
        if not src:
            import yt_dlp

            def act(opts):
                opts.update({"format": "bestaudio[ext=m4a]/bestaudio/best[height<=480]/best",
                             "outtmpl": str(media / "audio.%(ext)s"), "http_chunk_size": 10485760})
                with yt_dlp.YoutubeDL(opts) as y:
                    y.download([meta["url"]])
            t = Timer()
            ytdlp_call(act, cookies, cookies_file)
            src = _find_downloaded(media, "audio")
            if not src:
                raise RuntimeError("Audio download produced no file")
            log(f"  audio: downloaded {src.stat().st_size / 1e6:.0f} MB in {t():.0f} s")
    run([ffmpeg(), "-v", "error", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", str(wav)])
    return wav


# ---------- video sections ----------

def download_section(job: Path, meta: dict, start: float, end: float, out_stem: Path,
                     cookies=None, cookies_file=None, max_height: int = 1440) -> Path:
    """Download only [start, end] of the stream video (good quality). Returns file path."""
    existing = _find_downloaded(out_stem.parent, out_stem.name)
    if existing:
        return existing
    import yt_dlp
    from yt_dlp.utils import download_range_func
    extra = {
        "format": f"bv*[height<={max_height}]+ba/b[height<={max_height}]/bv*+ba/b",
        "format_sort": [f"res:{max_height}", "fps"],
        "download_ranges": download_range_func(None, [(max(0, start), end)]),
        "force_keyframes_at_cuts": False,
        "merge_output_format": "mkv",
        "outtmpl": str(out_stem) + ".%(ext)s",
    }

    def act(opts):
        opts.update(extra)
        with yt_dlp.YoutubeDL(opts) as y:
            y.download([meta["url"]])
    last = None
    for attempt in range(3):
        try:
            ytdlp_call(act, cookies, cookies_file)
            break
        except Exception as e:  # network hiccups are common on long VODs
            last = e
            if "asks to sign in" in str(e):
                break
            time.sleep(3)
    p = _find_downloaded(out_stem.parent, out_stem.name)
    if not p:
        raise RuntimeError(f"Section download failed: {last}")
    return p
