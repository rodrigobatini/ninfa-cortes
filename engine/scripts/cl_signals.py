"""Signals: loudness spikes (streamer yelling) and speech pace. Exports per-minute tables."""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

from cl_common import fmt_ts, log, read_json, write_json, write_text

HOP = 0.25  # seconds per loudness frame


def loudness_db(wav: Path) -> np.ndarray:
    """Short-window (0.5 s) RMS in dBFS every 0.25 s."""
    with wave.open(str(wav), "rb") as w:
        sr = w.getframerate()
        hop = int(sr * HOP)
        out = []
        carry = np.zeros(0, dtype=np.float32)
        while True:
            raw = w.readframes(sr * 60)
            if not raw:
                break
            x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            x = np.concatenate([carry, x])
            n = (len(x) - hop) // hop
            if n <= 0:
                carry = x
                continue
            sq = x[: (n + 1) * hop] ** 2
            frames = sq.reshape(n + 1, hop).mean(axis=1)
            win = (frames[:-1] + frames[1:]) / 2.0  # 0.5 s window
            out.append(win)
            carry = x[n * hop:]
    if not out:
        return np.zeros(0)
    ms = np.concatenate(out)
    return 10 * np.log10(ms + 1e-10)


def rolling_median(x: np.ndarray, win: int) -> np.ndarray:
    """Median over a centered window, computed on a coarse grid then interpolated (fast)."""
    n = len(x)
    if n == 0:
        return x
    step = max(1, win // 8)
    centers = np.arange(0, n, step)
    vals = np.array([np.median(x[max(0, c - win // 2): c + win // 2 + 1]) for c in centers])
    return np.interp(np.arange(n), centers, vals)


def loud_spikes(db: np.ndarray, words_t: np.ndarray):
    """z-score against a rolling 2-minute baseline; returns per-frame z and merged spikes."""
    base = rolling_median(db, int(120 / HOP))
    dev = np.abs(db - base)
    mad = rolling_median(dev, int(300 / HOP))
    z = (db - base) / (1.4826 * mad + 1.5)
    gate = db > np.percentile(db, 60)
    cand = np.where((z >= 2.0) & gate)[0]
    spikes = []
    for i in cand:
        t = i * HOP
        if spikes and t - spikes[-1]["end"] <= 4.0:
            sp = spikes[-1]
            sp["end"] = t
            if z[i] > sp["z"]:
                sp["z"], sp["t"], sp["db"] = float(z[i]), t, float(db[i])
        else:
            spikes.append({"t": t, "start": t, "end": t, "z": float(z[i]), "db": float(db[i])})
    for sp in spikes:
        # did the streamer talk during the spike? (game/trailer audio also gets loud)
        if len(words_t):
            k = np.searchsorted(words_t, sp["start"] - 1.0)
            sp["speech"] = bool(k < len(words_t) and words_t[k] <= sp["end"] + 1.0)
        else:
            sp["speech"] = False
        sp["z"] = round(sp["z"], 2)
        sp["db"] = round(sp["db"], 1)
    return z, spikes


def compute(job: Path) -> dict:
    wav = job / "media" / "audio16k.wav"
    words = read_json(job / "transcript" / "words.json", [])
    words_t = np.array([w["s"] for w in words]) if words else np.zeros(0)
    db = loudness_db(wav)
    z, spikes = loud_spikes(db, words_t)
    dur = len(db) * HOP
    n_min = int(np.ceil(dur / 60.0)) or 1
    per = int(60 / HOP)
    rows = []
    word_counts = np.bincount((words_t // 60).astype(int), minlength=n_min)[:n_min] if len(words_t) else np.zeros(n_min)
    for m in range(n_min):
        seg = db[m * per:(m + 1) * per]
        zz = z[m * per:(m + 1) * per]
        sp = [s for s in spikes if m * 60 <= s["t"] < (m + 1) * 60]
        rows.append({
            "minute": m, "time": fmt_ts(m * 60),
            "loud_db": round(float(seg.mean()), 1) if len(seg) else None,
            "loud_max_z": round(float(zz.max()), 2) if len(zz) else None,
            "spikes": len(sp), "speech_spikes": sum(1 for s in sp if s["speech"]),
            "words": int(word_counts[m]),
        })
    sig = job / "signals"
    write_json(sig / "spikes.json", spikes)
    write_json(sig / "per_minute.json", rows)
    head = "minute,time,loud_db,loud_max_z,spikes,speech_spikes,words_per_min"
    lines = [head] + [
        f"{r['minute']},{r['time']},{r['loud_db']},{r['loud_max_z']},{r['spikes']},{r['speech_spikes']},{r['words']}"
        for r in rows]
    write_text(sig / "per_minute.csv", "\n".join(lines) + "\n")
    np.save(sig / "loudness_db.npy", db.astype(np.float32))
    summary = {"duration": dur, "spikes": len(spikes),
               "speech_spikes": sum(1 for s in spikes if s["speech"])}
    write_json(sig / "summary.json", summary)
    log(f"  signals: {len(spikes)} loud spikes ({summary['speech_spikes']} while talking)")
    return summary
