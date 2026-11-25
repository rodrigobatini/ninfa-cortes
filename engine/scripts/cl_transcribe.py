"""Local transcription with word timestamps (faster-whisper). GPU if available, else CPU."""
from __future__ import annotations

import os
import re
import time
import wave
from contextlib import contextmanager
from pathlib import Path

import numpy as np

from cl_common import (CONFIG_DIR, Timer, add_cuda_dll_dirs, fmt_ts, lang_strings, load_config, log,
                       write_json, write_text)


@contextmanager
def gpu_lock(enabled=True):
    """One transcription at a time on the GPU (if two prepare commands are started)."""
    if not enabled:
        yield
        return
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lock = CONFIG_DIR / "whisper.lock"
    waited = False
    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > 3 * 3600:  # stale lock
                    lock.unlink()
                    continue
            except FileNotFoundError:
                continue
            if not waited:
                log("  whisper: another stream is transcribing, waiting for the GPU...")
                waited = True
            time.sleep(3)
    try:
        yield
    finally:
        try:
            lock.unlink()
        except FileNotFoundError:
            pass


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def pick_device(force_cpu=False):
    cfg = load_config()
    if force_cpu:
        return "cpu", "int8", cfg.get("cpu_model", "small")
    dev = cfg.get("device")
    if dev == "cuda":
        return "cuda", cfg.get("compute_type", "float16"), cfg.get("model", "large-v3-turbo")
    if dev == "cpu":
        return "cpu", "int8", cfg.get("model", "small")
    # setup not run yet: probe
    add_cuda_dll_dirs()
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", "float16", "large-v3-turbo"
    except Exception:
        pass
    return "cpu", "int8", "small"


def load_model(device, compute, name):
    add_cuda_dll_dirs()
    from faster_whisper import WhisperModel
    return WhisperModel(name, device=device, compute_type=compute)


def detect_language(model, audio, sr=16000):
    """Vote over 5 windows from the middle of the stream (streams often start with music)."""
    n = len(audio)
    votes = {}
    for frac in (0.2, 0.35, 0.5, 0.65, 0.8):
        a = int(n * frac)
        seg = audio[a:a + 30 * sr]
        if len(seg) < 5 * sr:
            continue
        try:
            lang, prob, _ = model.detect_language(seg, vad_filter=True)
        except TypeError:
            lang, prob, _ = model.detect_language(seg)
        except Exception:
            continue
        votes[lang] = votes.get(lang, 0) + prob
    if not votes:
        return "en"
    return max(votes, key=votes.get)


def is_hallucination(text: str, lang: str) -> bool:
    t = text.lower().strip()
    if not t:
        return True
    for h in lang_strings(lang).get("hallucinations", []):
        if h in t:
            return True
    return False


def transcribe(job: Path, wav: Path, language=None, force_cpu=False, model_name=None) -> dict:
    device = pick_device(force_cpu)[0]
    with gpu_lock(device == "cuda"):
        return _transcribe(job, wav, language, force_cpu, model_name)


def _transcribe(job: Path, wav: Path, language=None, force_cpu=False, model_name=None) -> dict:
    from faster_whisper import BatchedInferencePipeline
    device, compute, name = pick_device(force_cpu)
    if model_name:
        name = model_name
    t = Timer()
    log(f"  whisper: model {name} on {device} ({compute})")
    try:
        model = load_model(device, compute, name)
    except Exception as e:
        if device == "cuda":
            log(f"  whisper: GPU failed ({str(e)[:120]}), falling back to CPU")
            device, compute, name = "cpu", "int8", load_config().get("cpu_model", "small")
            model = load_model(device, compute, name)
        else:
            raise
    audio = read_wav(wav)
    dur = len(audio) / 16000.0
    lang = language if language and language != "auto" else detect_language(model, audio)
    log(f"  whisper: language = {lang}")
    batched = BatchedInferencePipeline(model=model)
    bs = 16 if device == "cuda" else 4
    segments, info = batched.transcribe(audio, language=lang, word_timestamps=True, batch_size=bs,
                                        vad_filter=True, beam_size=5 if device == "cuda" else 1)
    words, segs = [], []
    last_pct = -10
    prev_text = None
    repeat = 0
    for s in segments:
        text = (s.text or "").strip()
        if is_hallucination(text, lang) and (s.no_speech_prob > 0.3 or len(text) < 60):
            continue
        if text == prev_text:
            repeat += 1
            if repeat >= 2:
                continue
        else:
            repeat = 0
        prev_text = text
        w0 = len(words)
        for w in (s.words or []):
            ww = w.word.strip()
            if not ww:
                continue
            words.append({"s": round(w.start, 3), "e": round(w.end, 3), "w": ww,
                          "p": round(w.probability, 3)})
        if len(words) == w0:
            continue
        segs.append({"s": round(s.start, 2), "e": round(s.end, 2), "text": text, "w0": w0,
                     "w1": len(words)})
        pct = int(100 * s.end / max(1, dur))
        if pct >= last_pct + 10:
            last_pct = pct
            log(f"  whisper: {pct}% ({fmt_ts(s.end)} of {fmt_ts(dur)}, {t():.0f} s)")
    segs.sort(key=lambda x: x["s"])
    tdir = job / "transcript"
    write_json(tdir / "words.json", words)
    write_json(tdir / "segments.json", segs)
    write_text(tdir / "transcript.txt", "\n".join(f"[{fmt_ts(s['s'])}] {s['text']}" for s in segs))
    res = {"language": lang, "device": device, "model": name, "seconds": round(t(), 1),
           "duration": dur, "words": len(words), "segments": len(segs)}
    write_json(tdir / "info.json", res)
    log(f"  whisper: done, {len(words)} words in {t():.0f} s")
    return res


_PUNCT = re.compile(r"^[\W_]+|[\W_]+$", re.UNICODE)


def clean_word(w: str) -> str:
    return _PUNCT.sub("", w)
