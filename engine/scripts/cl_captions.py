"""Word-by-word burned-in captions + hook title as an ASS file (rendered by libass in ffmpeg)."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from cl_common import FONTS, write_text

CAP_FONT_FILE = FONTS / "Montserrat-Black.ttf"
HOOK_FONT_FILE = FONTS / "Montserrat-Black.ttf"
CAP_FONT = "Montserrat Black"
HOOK_FONT = "Montserrat Black"
CAP_SIZE = 84
HOOK_SIZE = 76
HOOK_SIZES = (80, 76, 72, 68)
HOOK_MAX_W = 940
MAX_W = 900            # max caption line width, px (frame is 1080)
HIGHLIGHT = "&H0000E6FF&"   # #FFE600 in ASS BGR
WHITE = "&H00FFFFFF&"
EMOJI = re.compile("[\U0001F000-\U0001FFFF☀-➿️‍]")
STRIP = re.compile(r"^[^\w$%#@&+]+|[^\w%!?$]+$", re.UNICODE)


@lru_cache(maxsize=4)
def _font(path: str):
    from fontTools.ttLib import TTFont
    f = TTFont(path)
    cmap = f.getBestCmap()
    hmtx = f["hmtx"]
    os2 = f["OS/2"]
    upm = f["head"].unitsPerEm
    cell = (os2.usWinAscent + os2.usWinDescent) / upm  # libass: font size = this cell height
    return cmap, hmtx, upm, cell


def text_width(text: str, size: float, path=CAP_FONT_FILE) -> float:
    try:
        cmap, hmtx, upm, cell = _font(str(path))
    except Exception:
        return len(text) * size * 0.62
    em = size / cell
    total = 0
    for ch in text:
        g = cmap.get(ord(ch))
        adv = hmtx[g][0] if g else upm * 0.6
        total += adv
    return total * em / upm


def ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, r = divmod(cs, 360000)
    m, r = divmod(r, 6000)
    s, c = divmod(r, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def esc(t: str) -> str:
    return t.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


# Profanity is masked in captions and hooks by default (--no-censor turns it off).
_VOW = set("AEIOUYАЕЁИОУЫЭЮЯaeiouyаеёиоуыэюя")
_BAD = re.compile(
    r"(?i)\b("
    r"(?:mother)?f+u+c*k+\w*|\w*shit\w*|bitch\w*|cunt\w*|dick(?:head)?s?|puss(?:y|ies)|asshole\w*|"
    r"bastard\w*|cock(?:sucker)?s?|whore\w*|slut\w*|nigg\w*|fag(?:got)?s?|retard\w*|wtf"
    r"|\w*ху[йяеёию]\w*|\w*пизд\w*|бля\w*|\w*[её]ба[нлтш]\w*|(?:за|вы|у|от|отъ|на|по|съ|до|раз|про|под)[её]б\w*"
    r"|[её]б[ауи]\w*|сук(?:а|и|е|у|ой|ам|ами|ин\w*)|суч\w*|муда[кч]\w*|мудил\w*|пид[оа]р\w*|гандон\w*|шлюх\w*|залуп\w*"
    r"|долбо[её]б\w*)\b")
CENSOR = True


_ROOT = re.compile(r"(?i)[её]б|ху[йяеёию]|пизд|бля|сук|суч|муд|пид|гандон|шлюх|залуп|fuck|shit")


def _mask(m) -> str:
    w = m.group(0)
    if w.lower() == "wtf":
        return w[0] + "*" + w[2:]
    r = _ROOT.search(w)  # mask a vowel inside the bad root, not in a prefix ("за*бываешь", not "з*ебываешь")
    if r:
        for i in range(r.start(), len(w)):
            if w[i] in _VOW and i > 0:
                return w[:i] + "*" + w[i + 1:]
    for i in range(1, len(w)):
        if w[i] in _VOW:
            return w[:i] + "*" + w[i + 1:]
    return w[0] + "*" + w[2:]


def censor(text: str) -> str:
    return _BAD.sub(_mask, text) if CENSOR else text


def display_word(w: str) -> str:
    w = STRIP.sub("", w.strip())
    return censor(w.upper())


def group_words(words):
    """Split into short groups of 1-3 words that fit one line."""
    groups, cur = [], []
    for i, w in enumerate(words):
        txt = display_word(w["w"])
        if not txt:
            continue
        item = dict(w, d=txt)
        if cur:
            prev = cur[-1]
            gap = w["s"] - prev["e"]
            joined = " ".join(x["d"] for x in cur + [item])
            end_punct = bool(re.search(r"[.!?…]$", prev["w"].strip()))
            comma = prev["w"].strip().endswith(",") and len(cur) >= 2
            if (len(cur) >= 3 or gap > 0.45 or end_punct or comma or len(joined) > 20
                    or text_width(joined, CAP_SIZE) > MAX_W):
                groups.append(cur)
                cur = []
        cur.append(item)
    if cur:
        groups.append(cur)
    return groups


def rounded_rect(w, h, r):
    r = min(r, h / 2, w / 2)
    c = r * 0.45  # bezier handle
    return (f"m {r} 0 l {w - r} 0 b {w - c} 0 {w} {c} {w} {r} l {w} {h - r} "
            f"b {w} {h - c} {w - c} {h} {w - r} {h} l {r} {h} b {c} {h} 0 {h - c} 0 {h - r} "
            f"l 0 {r} b 0 {c} {c} 0 {r} 0")


def wrap(text: str, size: float, max_w: float, path) -> list:
    """Greedy wrap, then rebalance so the last line is not a lonely short word."""
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if cur and text_width(t, size, path) > max_w:
            lines.append(cur)
            cur = w
        else:
            cur = t
    if cur:
        lines.append(cur)
    if len(lines) == 2:
        best, best_w = lines, max(text_width(x, size, path) for x in lines)
        for k in range(1, len(words)):
            a, b = " ".join(words[:k]), " ".join(words[k:])
            wmax = max(text_width(a, size, path), text_width(b, size, path))
            if wmax <= max_w and wmax < best_w:
                best, best_w = [a, b], wmax
        lines = best
    return lines


def face_top_out(seg):
    """Where the top of the face lands in the 1080x1920 output (None if no face)."""
    if not isinstance(seg, dict) or "face" not in seg:
        return None
    fx, fy, fw, fh = seg["face"]
    if seg["mode"] == "facecam":
        cx, cy, cw, ch = seg["cam"]
        return (fy - cy) / ch * 720.0
    x, y, w, h = seg["full"]
    return (fy - y) / h * 1920.0


def place_hook(hook: str, seg):
    """Big hook at the top of the frame, above the face. Returns (lines, size, plate_top)."""
    face_top = face_top_out(seg)
    limit = (face_top + 4) if face_top is not None else 700   # YuNet's face box starts at the brows
    for size in HOOK_SIZES:
        hl = wrap(hook, size, HOOK_MAX_W - 76, HOOK_FONT_FILE)
        if len(hl) > 2:
            continue
        plate = len(hl) * size + 44
        top = min(70, limit - plate)
        if top >= 24:
            return hl, size, top
    # no room above the face: at the seam between the panels (chest / gameplay top)
    for size in HOOK_SIZES:
        hl = wrap(hook, size, HOOK_MAX_W - 76, HOOK_FONT_FILE)
        if len(hl) <= 2:
            return hl, size, 720 - (len(hl) * size + 44) / 2
    hl = wrap(hook, HOOK_SIZES[-1], HOOK_MAX_W - 76, HOOK_FONT_FILE)[:2]
    return hl, HOOK_SIZES[-1], 720 - (2 * HOOK_SIZES[-1] + 44) / 2


def build_ass(words, clip_start, clip_end, hook, out: Path, segments, hook_secs: float = 2.6, censor_on=True):
    global CENSOR
    CENSOR = censor_on
    mode = segments if isinstance(segments, str) else segments[0]["mode"]
    seg0 = None if isinstance(segments, str) else segments[0]
    dur = clip_end - clip_start
    ws = []
    for w in words:
        s, e = w["s"] - clip_start, w["e"] - clip_start
        if e <= 0.02 or s >= dur - 0.02:
            continue
        ws.append({"s": max(0.0, s), "e": min(dur, e), "w": w["w"]})
    cap_y = 1265           # bottom of the caption line (safe zone: above ~1300 px)
    lines = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920",
        "WrapStyle: 2", "ScaledBorderAndShadow: yes", "YCbCr Matrix: TV.709", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Cap,{CAP_FONT},{CAP_SIZE},&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,0,0,0,0,100,100,1,0,1,7,3,2,60,60,0,1",
        f"Style: Hook,{HOOK_FONT},{HOOK_SIZE},&H00101010,&H00101010,&H00FFFFFF,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,40,40,0,1",
        "Style: Box,Arial,20,&H00FFFFFF,&H00FFFFFF,&H00FFFFFF,&H64000000,0,0,0,0,100,100,0,0,1,0,6,7,0,0,0,1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    # hook title
    hook = censor(EMOJI.sub("", hook or "").strip())
    if hook:
        hl, size, top = place_hook(hook, seg0)
        tw = max(text_width(x, size, HOOK_FONT_FILE) for x in hl)
        pad_x, pad_y = 38, 22
        bw, bh = tw + 2 * pad_x, len(hl) * size + 2 * pad_y
        x0, y0 = 540 - bw / 2, top
        cy = top + bh / 2
        end = min(hook_secs, dur)
        lines.append(f"Dialogue: 2,{ass_time(0)},{ass_time(end)},Box,,0,0,0,,"
                     f"{{\\an7\\pos({x0:.0f},{y0:.0f})\\fad(100,200)\\p1}}{rounded_rect(bw, bh, 28)}{{\\p0}}")
        lines.append(f"Dialogue: 3,{ass_time(0)},{ass_time(end)},Hook,,0,0,0,,"
                     f"{{\\an5\\pos(540,{cy:.0f})\\fs{size}\\fad(100,200)}}" + "\\N".join(esc(x) for x in hl))
    # captions
    groups = group_words(ws)
    for gi, g in enumerate(groups):
        g_end = g[-1]["e"]
        if gi + 1 < len(groups):
            nxt = groups[gi + 1][0]["s"]
            g_end = nxt if nxt - g_end < 0.6 else g_end + 0.25
        else:
            g_end = min(dur, g_end + 0.4)
        texts = [x["d"] for x in g]
        full = " ".join(texts)
        fs = CAP_SIZE
        wpx = text_width(full, fs)
        scale = min(1.0, MAX_W / wpx) if wpx > 0 else 1.0
        for i, x in enumerate(g):
            s = x["s"]
            e = g[i + 1]["s"] if i + 1 < len(g) else g_end
            if e - s < 0.05:
                continue
            parts = []
            for j, t in enumerate(texts):
                if j == i:
                    parts.append(f"{{\\1c{HIGHLIGHT}\\fscx{int(108 * scale)}\\fscy{int(108 * scale)}}}{esc(t)}"
                                 f"{{\\1c{WHITE}\\fscx{int(100 * scale)}\\fscy{int(100 * scale)}}}")
                else:
                    parts.append(esc(t))
            pre = f"{{\\an2\\pos(540,{cap_y})\\fscx{int(100 * scale)}\\fscy{int(100 * scale)}}}"
            lines.append(f"Dialogue: 1,{ass_time(s)},{ass_time(e)},Cap,,0,0,0,,{pre}" + " ".join(parts))
    write_text(out, "\n".join(lines) + "\n")
    return {"groups": len(groups), "words": len(ws)}
