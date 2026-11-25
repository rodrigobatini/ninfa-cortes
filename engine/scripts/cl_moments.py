"""Validates moments.json written by Claude and snaps cut points to word boundaries.
The snapped times go to moments.checked.json (moments.json itself is never rewritten)."""
from __future__ import annotations

import re
from pathlib import Path

from cl_common import fmt_ts, lang_strings, read_json, write_json, write_text
from cl_transcribe import clean_word

EMOJI = re.compile("[\U0001F000-\U0001FFFF☀-➿️]")
END_PUNCT = (".", "!", "?", "…")


def words_between(words, a, b):
    return [w for w in words if w["e"] > a and w["s"] < b]


def snap(words, start, end):
    """Start just before the first word that ends after `start`; end just after the last word
    that starts before `end`, never running into the next word. Returns (s, e, i, j)."""
    idx = [i for i, w in enumerate(words) if w["e"] > start + 0.05]
    if not idx:
        return start, end, None, None
    i = idx[0]
    s = words[i]["s"] - 0.12
    if i > 0:
        s = max(s, words[i - 1]["e"] + 0.02)
    jdx = [j for j, w in enumerate(words) if w["s"] < end - 0.05]
    j = jdx[-1] if jdx else i
    e = words[j]["e"] + 0.45
    if j + 1 < len(words):
        e = min(e, words[j + 1]["s"] - 0.02)
    e = max(e, words[j]["e"])
    return round(max(0.0, s), 2), round(e, 2), i, j


def starts_with_filler(ws, fillers) -> str:
    toks = [clean_word(w["w"]).lower() for w in ws[:3]]
    for f in sorted(fillers, key=lambda x: -len(x.split())):
        parts = f.split()
        if toks[:len(parts)] == parts:
            return f
    return ""


def check(job: Path, top_n: int = 3, min_len: float = 15, max_len: float = 60):
    meta = read_json(job / "job.json")
    lang = meta.get("language", "en")
    words = read_json(job / "transcript" / "words.json", [])
    m = read_json(job / "moments.json")
    errors, warns, out = [], [], []
    if not m:
        return ["moments.json not found or empty"], [], None
    top_n = max(1, min(3, int(top_n or 3)))   # this version: up to 3 clips per stream
    cands = m.get("candidates") or []
    picks = m.get("picks") or []
    if not picks:
        errors.append("no picks in moments.json")
    if len(picks) > top_n:
        warns.append(f"{len(picks)} picks, keeping the {top_n} with the highest score")
        picks = sorted(picks, key=lambda p: -float(p.get("score", 0)))[:top_n]
    fillers = set(lang_strings(lang).get("fillers", []))
    dur = meta.get("duration") or 1e12
    report = [f"# Check: {meta.get('title', '')}", "",
              "Each pick: exact first and last words with times (seconds). The first words must hook, "
              "the last words must be the payoff. Words just outside the cut are shown in (brackets).", ""]
    picks = sorted(picks, key=lambda p: -float(p.get("score", 0) or 0))
    for k, p in enumerate(picks, 1):
        for f in ("start", "end", "score", "hook", "caption", "hashtags", "title"):
            if f not in p:
                errors.append(f"pick {k}: missing field '{f}'")
        try:
            a, b = float(p["start"]), float(p["end"])
        except Exception:
            errors.append(f"pick {k}: start/end must be numbers (seconds)")
            continue
        if not (0 <= a < b <= dur + 1):
            errors.append(f"pick {k}: start/end outside the stream")
            continue
        s, e, i, j = snap(words, a, b)
        d = e - s
        if d < min_len - 3 or d > max_len + 15:
            errors.append(f"pick {k}: {d:.1f} s long, must be {min_len:.0f}-{max_len:.0f} s")
        elif d < min_len or d > max_len:
            warns.append(f"pick {k}: {d:.1f} s long (target {min_len:.0f}-{max_len:.0f} s)")
        ws = words_between(words, s, e)
        if not ws or i is None:
            errors.append(f"pick {k}: no speech inside")
            continue
        fw = starts_with_filler(ws, fillers)
        if fw:
            warns.append(f"pick {k}: starts with filler '{fw}' - move start to the next real phrase")
        if i > 0 and words[i]["s"] - words[i - 1]["e"] < 0.25 and not words[i - 1]["w"].rstrip().endswith(END_PUNCT + (",",)):
            warns.append(f"pick {k}: starts mid-sentence (previous word '{words[i - 1]['w']}' runs straight in)")
        if j + 1 < len(words) and words[j + 1]["s"] - words[j]["e"] < 0.3 and not words[j]["w"].rstrip().endswith(END_PUNCT + (",",)):
            warns.append(f"pick {k}: ends mid-sentence on '{words[j]['w']}' (next: '{words[j + 1]['w']}')")
        hook = str(p.get("hook", ""))
        if EMOJI.search(hook):
            warns.append(f"pick {k}: emoji in hook will be removed (captions font has no emoji)")
        if len(hook) > 50 or len(hook.split()) > 8:
            warns.append(f"pick {k}: hook is long ({len(hook.split())} words, {len(hook)} chars); aim for 3-7 words")
        tags = p.get("hashtags") or []
        if not (3 <= len(tags) <= 8):
            warns.append(f"pick {k}: {len(tags)} hashtags, use 4-6")
        q = dict(p)
        q.update(start=s, end=e, duration=round(d, 2), order=k)
        out.append(q)
        before = " ".join(w["w"] for w in words[max(0, i - 4):i])
        after = " ".join(w["w"] for w in words[j + 1:j + 5])
        first = " ".join(f"{w['w']}[{w['s']:.1f}]" for w in ws[:8])
        last = " ".join(f"{w['w']}[{w['e']:.1f}]" for w in ws[-8:])
        report += [f"## Pick {k}: {fmt_ts(s)} - {fmt_ts(e)} ({d:.1f} s), {s:.2f}-{e:.2f}, score {p.get('score')}",
                   f"Hook: {hook}", "",
                   f"Starts: ({before}) | {first}", f"Ends: {last} | ({after})", "",
                   "Full text: " + " ".join(w["w"] for w in ws), ""]
    out.sort(key=lambda q: q["start"])
    for x, y in zip(out, out[1:]):
        if y["start"] < x["end"]:
            errors.append(f"picks overlap around {fmt_ts(y['start'])}")
    res = {"candidates_total": len(cands) if cands else len(picks), "picks": out}
    write_json(job / "moments.checked.json", res)
    report += ["## Errors"] + (errors or ["none"]) + ["", "## Warnings"] + (warns or ["none"])
    write_text(job / "moments.check.md", "\n".join(report) + "\n")
    return errors, warns, res
