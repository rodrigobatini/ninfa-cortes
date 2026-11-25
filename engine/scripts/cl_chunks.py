"""Compact transcript chunks for Claude to read, ranked by how likely they hold a clip.

Token budget matters (a $20 plan): lines are long (a sentence or two, up to ~15 s), each line starts
with its start second only, and signals are one summary line per chunk. index.md ranks the chunks by
reaction words, topic words, loudness and speech pace, with the hottest lines of each chunk, so
Claude reads the top chunks first and only opens the rest when their hot lines look promising.
Line-splitting idea (phrases cut on pauses) from browser-use/video-use (MIT)."""
from __future__ import annotations

import math
import re
from pathlib import Path

from cl_common import fmt_ts, lang_strings, read_json, write_text


def lines_from_words(words, max_len=15.0, gap=1.0):
    lines, cur = [], []
    for w in words:
        if cur:
            prev = cur[-1]
            span = w["e"] - cur[0]["s"]
            ends = prev["w"].rstrip().endswith((".", "!", "?", "…"))
            if w["s"] - prev["e"] > gap or span > max_len or (ends and prev["e"] - cur[0]["s"] > 6.0):
                lines.append(cur)
                cur = []
        cur.append(w)
    if cur:
        lines.append(cur)
    return [{"s": l[0]["s"], "e": l[-1]["e"], "text": " ".join(x["w"] for x in l)} for l in lines]


def _count(text: str, words) -> int:
    t = " " + re.sub(r"[^\w\s']", " ", text.lower()) + " "
    return sum(t.count(" " + w) if len(w) >= 5 else t.count(" " + w + " ") for w in words)


def build(job: Path, minutes: int = 10, read_all: bool = False) -> int:
    meta = read_json(job / "job.json")
    lines = lines_from_words(read_json(job / "transcript" / "words.json", []))
    spikes = read_json(job / "signals" / "spikes.json", [])
    rows = read_json(job / "signals" / "per_minute.json", [])
    lang = meta.get("language", "en")
    ls = lang_strings(lang)
    react, topic = ls.get("reaction_words", []), ls.get("topic_words", [])
    dur = meta.get("duration") or (lines[-1]["e"] if lines else 0)
    cdir = job / "chunks"
    cdir.mkdir(parents=True, exist_ok=True)
    for old in cdir.glob("chunk_*.md"):
        old.unlink()
    # lines heard more than once (trailer re-watched, ads, music) are not the streamer: no points for them
    def key(t):
        ws = re.sub(r"[^\w\s]", " ", t.lower()).split()
        return " ".join(ws[:6]) if len(ws) >= 5 else None
    seen = {}
    for l in lines:
        k = key(l["text"])
        if k:
            seen[k] = seen.get(k, 0) + 1
    replay = lambda l: (key(l["text"]) is not None and seen.get(key(l["text"]), 0) >= 2)
    size = minutes * 60
    n = max(1, int(math.ceil(dur / size)))
    chunks = []
    for i in range(n):
        a, b = i * size, min(dur, (i + 1) * size)
        cl = [l for l in lines if a <= l["s"] < b]
        sp = [s for s in spikes if s["speech"] and a <= s["t"] < b]
        rr = rows[int(a // 60):int((b + 59) // 60)]
        out_lines, heat = [], []
        for l in cl:
            marks = [s for s in sp if l["s"] - 0.5 <= s["t"] <= l["e"] + 0.5]
            m = f" [LOUD {max(s['z'] for s in marks):.1f}]" if marks else ""
            out_lines.append(f"{int(l['s'])} {l['text']}{m}")
            if replay(l):
                continue
            h = (2 * _count(l["text"], react) + _count(l["text"], topic) + l["text"].count("!") + l["text"].count("?")
                 + (3 * max(s["z"] for s in marks) if marks else 0))
            heat.append((h, l))
        mins = max(1e-6, (b - a) / 60)
        words = sum(r["words"] for r in rr)
        own = [l for l in cl if not replay(l)]
        kw = sum(_count(l["text"], react) for l in own) / mins
        tp = sum(_count(l["text"], topic) for l in own) / mins
        loud = sum(s["z"] for s in sp if s["z"] >= 2.5) / mins
        sig = [f"words/min {int(words / mins)}"]
        if sp:
            top = max(sp, key=lambda s: s["z"])
            sig.append(f"loud x{len(sp)} (max z {top['z']} at {int(top['t'])})")
        hot = sorted([l for h, l in sorted(heat, key=lambda x: -x[0])[:3] if h > 0], key=lambda l: l["s"])
        chunks.append({"i": i, "a": a, "b": b, "kw": kw, "tp": tp, "loud": loud, "wpm": words / mins,
                       "sig": sig, "lines": out_lines, "hot": hot, "words": words})
    # speech pace: talking faster than usual for this stream (excited, arguing, reacting)
    paces = sorted(c["wpm"] for c in chunks if c["wpm"] > 0)
    med = paces[len(paces) // 2] if paces else 0
    for c in chunks:
        pace = c["wpm"] / med if med else 0
        c["score"] = 1.0 * c["kw"] + 2.0 * c["tp"] + 0.8 * c["loud"] + 3.0 * max(0.0, pace - 1.0) + 0.01 * c["wpm"]
        if pace >= 1.15:
            c["sig"].append(f"pace x{pace:.2f}")
        c["sig"] = "; ".join(c["sig"])
    ranked = sorted(chunks, key=lambda c: -c["score"])
    k_first = n if read_all or n <= 6 else max(6, int(math.ceil(0.33 * n)))
    first = {c["i"] for c in ranked[:k_first]}
    for c in chunks:
        rank = ranked.index(c) + 1
        head = [f"# Chunk {c['i'] + 1}/{n} {fmt_ts(c['a'])}-{fmt_ts(c['b'])} | rank {rank}/{n} | {c['sig']}",
                "Each line: <start second> text. [LOUD z] = yelling (z>=3 real; can be game audio).", ""]
        write_text(cdir / f"chunk_{c['i'] + 1:02d}.md", "\n".join(head + c["lines"]) + "\n")
    idx = [f"# {meta.get('title', '')}",
           f"{meta.get('channel', '')} | {fmt_ts(dur)} | language {lang} | {n} chunks of {minutes} min", "",
           f"Chunks ranked by reaction words, topic words, loud spikes and speech pace, each with its hottest",
           f"lines. Read about {k_first} chunks in full: the READ FIRST ones, but swap in any 'maybe' chunk whose",
           "hot lines look stronger than a READ FIRST one (you judge better than the score). Read every chunk",
           "only if the user asked for a thorough pass.", ""]
    for c in ranked:
        tag = "READ FIRST" if c["i"] in first else "maybe"
        idx.append(f"- chunk_{c['i'] + 1:02d}.md ({fmt_ts(c['a'])}) {tag} | score {c['score']:.1f} | {c['sig']}")
        for l in c["hot"]:
            t = l["text"] if len(l["text"]) <= 120 else l["text"][:117] + "..."
            idx.append(f"    {int(l['s'])} {t}")
    write_text(cdir / "index.md", "\n".join(idx) + "\n")
    return n
