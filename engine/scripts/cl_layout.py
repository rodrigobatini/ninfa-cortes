"""Facecam detection and 1080x1920 layout planning.

mode "facecam":  webcam crop on top (1080x720), gameplay crop below (1080x1200)
mode "fullcam":  the camera is the whole picture -> one 9:16 crop around the main face
mode "gameplay": no stable face -> one 9:16 smart crop of the gameplay
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from cl_common import YUNET, ffmpeg, video_stream

OUT_W, OUT_H = 1080, 1920
TOP_H = 720           # 37.5 % of the frame for the facecam
BOT_H = OUT_H - TOP_H
AW = 1280             # analysis width
DEBUG = False
REGION_K = 2.0
LIVE_MIN = 2.2        # min face pixel change between samples (static pictures are ~0-1)
# a face in a painting / poster / photo filmed by a moving camera: the picture shifts, so plain pixel
# change is high, but once the face is aligned by its landmarks almost nothing changes (no blinks,
# no mouth) and the face geometry stays the same (no head turns). Live people: 13+ and 0.2+.
PIC_RES = 8.0         # max aligned-face change (0-255) of a picture
PIC_GEO = 0.06        # max spread of landmark geometry (share of eye distance) of a picture
# two or more people in one camera picture (IRL, interview): a camera shot, not a webcam
PEOPLE_LIVE = 6.0     # min _liveness of a person in a camera shot (people: 10+, game / trailer faces: ~2-4)
_CANON = np.float32([[38.3, 51.7], [73.5, 51.5], [56.0, 71.7], [41.5, 92.4], [70.7, 92.2]]) * (64 / 112)


def grab_frames(video: Path, t0: float, dur: float, fps: float = 2.0):
    """Decode the clip once at `fps` frames per second (analysis size). Returns frames, times, W, H."""
    vs = video_stream(video)
    W, H = int(vs["width"]), int(vs["height"])
    ah = int(round(H * AW / W / 2) * 2)
    cmd = [ffmpeg(), "-v", "error", "-ss", f"{t0:.3f}", "-t", f"{dur:.3f}", "-i", str(video),
           "-vf", f"fps={fps},scale={AW}:{ah}", "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
    r = subprocess.run(cmd, capture_output=True)
    size = AW * ah * 3
    n = len(r.stdout) // size
    frames = [np.frombuffer(r.stdout, np.uint8, size, k * size).reshape(ah, AW, 3) for k in range(n)]
    times = [k / fps for k in range(n)]
    return frames, times, W, H


def detect_faces(frames):
    import cv2
    det = None
    if YUNET.exists():
        model = np.frombuffer(YUNET.read_bytes(), dtype=np.uint8)
        det = cv2.FaceDetectorYN.create("onnx", model, np.empty(0, dtype=np.uint8), (320, 320), 0.6, 0.3, 5000)
    casc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml") if det is None else None
    out = []
    for i, f in enumerate(frames):
        h, w = f.shape[:2]
        if det is not None:
            det.setInputSize((w, h))
            _, faces = det.detect(f)
            if faces is None:
                continue
            for r in faces:
                if r[14] >= 0.7 and r[3] >= 0.035 * h:
                    # (frame, x, y, w, h, score, 5 landmarks: eyes, nose, mouth corners)
                    out.append((i, float(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[14]))
                               + tuple(float(v) for v in r[4:14]))
        else:
            g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
            for (x, y, ww, hh) in casc.detectMultiScale(g, 1.1, 6, minSize=(int(0.04 * h),) * 2):
                out.append((i, float(x), float(y), float(ww), float(hh), 0.8))
    return out


def _picture_like(items, frames) -> bool:
    """A face on a wall painting, poster or photo, filmed by a handheld camera: aligned by its own
    landmarks it does not change between samples, and its geometry (head turn, mouth) never moves."""
    import cv2
    its = sorted((f for f in items if len(f) >= 16), key=lambda f: f[0])
    if len(its) < 4:
        return False
    crops, geo = [], []
    for f in its:
        lm = np.float32(f[6:16]).reshape(5, 2)
        M, _ = cv2.estimateAffinePartial2D(lm, _CANON)
        if M is None:
            continue
        g = cv2.cvtColor(frames[f[0]], cv2.COLOR_BGR2GRAY)
        crops.append((f[0], cv2.warpAffine(g, M, (64, 64)).astype(np.float32)))
        e = float(np.linalg.norm(lm[1] - lm[0])) + 1e-6
        m = (lm[0] + lm[1]) / 2
        geo.append([(lm[2] - m)[0] / e, (lm[2] - m)[1] / e, float(np.linalg.norm(lm[4] - lm[3])) / e,
                    ((lm[3] + lm[4]) / 2 - m)[1] / e])
    res = [np.abs(a[1] - b[1])[8:60, 8:56].mean() for a, b in zip(crops, crops[1:]) if b[0] - a[0] <= 2]
    if len(res) < 3:
        return False
    return float(np.median(res)) < PIC_RES and float(np.array(geo).std(axis=0).max()) < PIC_GEO


def _liveness(items, frames) -> float:
    """Median pixel change of the face between samples. A real webcam face moves and blinks;
    a face on a static picture (thumbnail, poster, paused frame) does not. A picture filmed
    by a moving camera moves as a whole: caught by _picture_like."""
    import cv2
    if _picture_like(items, frames):
        return 0.0
    crops = []
    for f in sorted(items, key=lambda f: f[0]):
        x, y, w, h = [int(v) for v in f[1:5]]
        img = frames[f[0]][max(0, y):y + h, max(0, x):x + w]
        if img.size:
            crops.append(cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (48, 48)).astype(np.float32))
    if len(crops) < 2:
        return 0.0
    return float(np.median([np.abs(a - b).mean() for a, b in zip(crops, crops[1:])]))


def face_clusters(faces, W):
    clusters = []
    for f in sorted(faces, key=lambda f: -f[3] * f[4]):
        cx, cy = f[1] + f[3] / 2, f[2] + f[4] / 2
        for c in clusters:
            if abs(c["cx"] - cx) < 0.06 * W and abs(c["cy"] - cy) < 0.08 * W and 0.55 < f[3] / c["w"] < 1.8:
                c["items"].append(f)
                c["frames"].add(f[0])
                break
        else:
            clusters.append({"cx": cx, "cy": cy, "w": f[3], "items": [f], "frames": {f[0]}})
    return clusters


def stable_face(faces, n_frames, W, frames=None, min_presence=0.35):
    """Cluster faces by position; the facecam is the live face that stays put."""
    clusters = []
    for f in sorted(faces, key=lambda f: -f[3] * f[4]):
        cx, cy = f[1] + f[3] / 2, f[2] + f[4] / 2
        for c in clusters:
            if abs(c["cx"] - cx) < 0.06 * W and abs(c["cy"] - cy) < 0.08 * W and 0.55 < f[3] / c["w"] < 1.8:
                c["items"].append(f)
                c["frames"].add(f[0])
                break
        else:
            clusters.append({"cx": cx, "cy": cy, "w": f[3], "items": [f], "frames": {f[0]}})
    best = None
    for c in clusters:
        cnt = len(c["frames"])
        if cnt >= max(3, int(min_presence * n_frames)):
            if frames is not None and _liveness(c["items"], frames) < LIVE_MIN:
                continue
            key = (cnt, c["w"])
            if best is None or key > best[0]:
                best = (key, c)
    if not best:
        return None, 0.0
    it = np.array([f[1:5] for f in best[1]["items"]])
    box = np.median(it, axis=0)
    return box, len(best[1]["frames"]) / max(1, n_frames)


def content_box(frames):
    """Non-black area: drops letterbox / pillarbox bars (black in every frame, same size on both sides)."""
    g = np.max(np.stack([f.max(axis=2) for f in frames]), axis=0).astype(np.float32)
    h, w = g.shape
    rows = np.where(np.percentile(g, 99.5, axis=1) > 18)[0]
    cols = np.where(np.percentile(g, 99.5, axis=0) > 18)[0]
    x0, y0, x1, y1 = 0, 0, w, h
    if len(rows) > h * 0.4:
        top, bot = rows[0], h - 1 - rows[-1]
        if top > 4 and bot > 4 and abs(top - bot) <= max(12, 0.25 * max(top, bot)):
            y0, y1 = int(rows[0]), int(rows[-1] + 1)
    if len(cols) > w * 0.4:
        lf, rt = cols[0], w - 1 - cols[-1]
        if lf > 4 and rt > 4 and abs(lf - rt) <= max(12, 0.25 * max(lf, rt)):
            x0, x1 = int(cols[0]), int(cols[-1] + 1)
    return x0, y0, x1, y1


def _maxfilt(a, r, axis):
    out = a.copy()
    for d in range(1, r + 1):
        out = np.maximum(out, np.roll(a, d, axis=axis))
        out = np.maximum(out, np.roll(a, -d, axis=axis))
    return out


def find_cam_rect(frames, face):
    """Find the webcam overlay: the largest rectangle around the face whose four sides are
    long static edges (overlay borders) or frame edges. Returns (x0, y0, x1, y1) or None."""
    import cv2
    h, w = frames[0].shape[:2]
    E = np.mean([cv2.Canny(cv2.GaussianBlur(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), (3, 3), 0), 25, 80) > 0
                 for f in frames], axis=0) >= 0.35
    Ev = _maxfilt(E, 2, 1)   # vertical lines, +-2 px tolerance
    Eh = _maxfilt(E, 2, 0)
    fx, fy, fw, fh = [float(v) for v in face]
    fy0, fy1 = int(fy), int(fy + fh)
    fx0, fx1 = int(fx), int(fx + fw)

    def cands(rng, sup, edge):
        vals = [(v, sup(v)) for v in rng]
        good = [v for v, s in vals if s >= 0.5]
        picked = []
        for v in good:   # thin out neighbours
            if not picked or abs(v - picked[-1]) > 3:
                picked.append(v)
        return [edge] + picked[:10]

    lefts = cands(range(int(fx - 0.15 * fw), int(max(0, fx - 10 * fw)), -1),
                  lambda x: Ev[fy0:fy1, x].mean(), 0)
    rights = cands(range(int(fx + 1.15 * fw), int(min(w - 1, fx + 11 * fw))),
                   lambda x: Ev[fy0:fy1, x].mean(), w)
    tops = cands(range(int(fy - 0.1 * fh), int(max(0, fy - 4 * fh)), -1),
                 lambda y: Eh[y, fx0:fx1].mean(), 0)
    bottoms = cands(range(int(fy + 1.2 * fh), int(min(h - 1, fy + 9 * fh))),
                    lambda y: Eh[y, fx0:fx1].mean(), h)

    def side(v, a, b, vertical):
        if vertical:
            if v <= 0 or v >= w:
                return 1.0
            return float(Ev[a:b, v].mean())
        if v <= 0 or v >= h:
            return 1.0
        return float(Eh[v, a:b].mean())

    best, best_key = None, None
    for l in lefts:
        for r in rights:
            bw = r - l
            if not (1.8 * fw <= bw <= 14 * fw):
                continue
            for t in tops:
                for b in bottoms:
                    bh = b - t
                    if not (1.8 * fh <= bh <= 11 * fh) or not (0.9 <= bw / bh <= 2.6):
                        continue
                    if bw * bh > 0.4 * w * h:
                        continue
                    s = min(side(l, t, b, True), side(r, t, b, True), side(t, l, r, False),
                            side(b, l, r, False))
                    edges = sum(v in (0, w) for v in (l, r)) + sum(v in (0, h) for v in (t, b))
                    s_mean = (side(l, t, b, True) + side(r, t, b, True) + side(t, l, r, False) + side(b, l, r, False)) / 4
                    key = (-edges, round(s_mean, 1), bw * bh)   # real, solid borders first, then bigger
                    if s >= 0.7 and edges <= 2 and (best_key is None or key > best_key):
                        best, best_key = (l, t, r, b), key
    return best


def fit_aspect(box, aspect, anchor, limit, face_y_frac=0.42):
    """Largest rect of given aspect (w/h) inside box, centered on anchor (x, y), within limit."""
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    if bw / bh > aspect:
        ch, cw = bh, bh * aspect
    else:
        cw, ch = bw, bw / aspect
    cx = min(max(anchor[0] - cw / 2, x0), x1 - cw)
    cy = min(max(anchor[1] - ch * face_y_frac, y0), y1 - ch)
    lx0, ly0, lx1, ly1 = limit
    cx = min(max(cx, lx0), lx1 - cw)
    cy = min(max(cy, ly0), ly1 - ch)
    return [cx, cy, cw, ch]


def best_x(frames, crop_w, region, avoid=None):
    """Horizontal position for a gameplay crop: stay near the center of the action,
    away from the facecam."""
    x0, y0, x1, y1 = region
    g = np.stack([f.mean(axis=2) for f in frames]).astype(np.float32)
    energy = np.abs(np.diff(g, axis=0)).mean(axis=(0, 1)) if len(g) > 1 else np.ones(g.shape[2])
    ed = np.abs(np.diff(g, axis=2)).mean(axis=(0, 1))
    col = energy / (energy.mean() + 1e-6) + np.concatenate([ed, ed[-1:]]) / (ed.mean() + 1e-6)
    cs = np.concatenate([[0], np.cumsum(col)])
    best, bx = None, (x0 + x1 - crop_w) / 2
    center = (x0 + x1) / 2
    for x in np.linspace(x0, x1 - crop_w, 41):
        xi = int(x)
        inside = (cs[min(len(cs) - 1, xi + int(crop_w))] - cs[xi]) / crop_w
        score = 0.12 * inside - 2.0 * abs(x + crop_w / 2 - center) / (x1 - x0)
        if avoid is not None:
            ax0, ay0, ax1, ay1 = avoid
            ov = max(0, min(x + crop_w, ax1) - max(x, ax0)) * max(0, min(y1, ay1) - max(y0, ay0))
            score -= 6.0 * ov / (crop_w * (y1 - y0))
        if best is None or score > best:
            best, bx = score, x
    return bx


def overlay_boxes(frames, rect):
    """Stream overlay graphics inside/around the webcam box: donation widgets, names, logos.
    Two clues: (1) bright pixels that never change (text drawn on top of the live camera, which
    always has some noise); (2) places where the cam border line disappears because an overlay
    (an animated logo) sits on top of it."""
    import cv2
    h, w = frames[0].shape[:2]
    x0, y0, x1, y1 = [int(v) for v in rect]
    g = np.stack([cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames]).astype(np.float32)
    sd, mn = g.std(axis=0), g.mean(axis=0)
    # robust stats: a few black / glitched frames (stream crash, scene switch) must not hide the text
    med = np.median(g, axis=0)
    mad = np.median(np.abs(g - med), axis=0) * 1.48
    m = ((med > 120) & (mad < 2.5)).astype(np.uint8)
    sub = np.zeros_like(m)
    ya, yb, xa, xb = max(0, y0 - 40), min(h, y1 + 10), max(0, x0 - 10), min(w, x1 + 10)
    sub[ya:yb, xa:xb] = m[ya:yb, xa:xb]
    closed = cv2.morphologyEx(sub * 255, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (11, 5)))
    n, lab, st, _ = cv2.connectedComponentsWithStats(closed)
    boxes = []
    for i in range(1, n):
        bx, by, bw, bh, area = st[i]
        if bh < 5 or bw < 12:
            continue
        pieces = cv2.connectedComponents(sub[by:by + bh, bx:bx + bw])[0] - 1
        fill = sub[by:by + bh, bx:bx + bw].mean()
        if pieces >= 4 and fill > 0.08 and bw / max(bh, 1) < 25:   # letters, not a lamp line
            boxes.append((bx - 3, by - 3, bx + bw + 3, by + bh + 3))
    # animated colourful graphics (logos, alerts): saturated, bright on average, changing a lot
    hsv = np.stack([cv2.cvtColor(f, cv2.COLOR_BGR2HSV) for f in frames]).astype(np.float32)
    anim = ((hsv[..., 1].mean(axis=0) > 100) & (hsv[..., 2].mean(axis=0) > 120) & (sd > 40)).astype(np.uint8)
    sub2 = np.zeros_like(anim)
    sub2[ya:yb, xa:xb] = anim[ya:yb, xa:xb]
    sub2 = cv2.morphologyEx(sub2 * 255, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15)))
    n2, _, st2, _ = cv2.connectedComponentsWithStats(sub2)
    for i in range(1, n2):
        bx, by, bw, bh, area = st2[i]
        if area >= 150:
            boxes.append((bx - 3, by - 3, bx + bw + 3, by + bh + 3))
    # border line broken by something on top of it
    E = np.mean([cv2.Canny(x.astype(np.uint8), 25, 80) > 0 for x in g], axis=0) >= 0.35
    Ev, Eh = _maxfilt(E, 2, 1), _maxfilt(E, 2, 0)
    depth_v, depth_h = int(0.25 * (y1 - y0)), int(0.25 * (x1 - x0))

    def gaps(prof):
        out, start = [], None
        for i, v in enumerate(list(prof) + [1.0]):
            if v < 0.5 and start is None:
                start = i
            elif v >= 0.5 and start is not None:
                if i - start >= 12:
                    out.append((start, i))
                start = None
        return out
    if 0 < y0 < h:
        for a, b in gaps(Eh[y0, x0:x1]):
            boxes.append((x0 + a, y0, x0 + b, y0 + depth_v))
    if 0 < y1 < h:
        for a, b in gaps(Eh[y1 - 1, x0:x1]):
            boxes.append((x0 + a, y1 - depth_v, x0 + b, y1))
    if 0 < x0 < w:
        for a, b in gaps(Ev[y0:y1, x0]):
            boxes.append((x0, y0 + a, x0 + depth_h, y0 + b))
    if 0 < x1 < w:
        for a, b in gaps(Ev[y0:y1, x1 - 1]):
            boxes.append((x1 - depth_h, y0 + a, x1, y0 + b))
    return boxes


def face_crop(face, bounds, overlays, aspect, aw, ah, x_range=None, activity=None, mults=None):
    """Face-and-shoulders crop, decided by rules in this order:
    1. the whole head with headroom and the streamer's whole movement range (x_range) are inside;
    2. no overlay graphic (donation widget, logo, name) is cut: each is fully out or fully in;
    3. nothing outside the cam box is shown, except a logo sitting on the cam border shown whole,
       and only if the area around it is calm (no chat, no game footage);
    4. then as wide as possible (less upscaling = sharper), then centred on the face.
    When no crop satisfies everything, rules are relaxed from the bottom of that list upward."""
    fx, fy, fw, fh = face
    bx0, by0, bx1, by1 = bounds
    ex0, ey0, ex1, ey1 = bx0, by0, bx1, by1
    for ox0, oy0, ox1, oy1 in overlays:
        if ox1 > bx0 and ox0 < bx1 and oy1 > by0 and oy0 < by1:
            ex0, ey0 = max(0, min(ex0, ox0)), max(0, min(ey0, oy0))
            ex1, ey1 = min(aw, max(ex1, ox1)), min(ah, max(ey1, oy1))
    if x_range is None:
        x_range = (fx + fw / 2, fx + fw / 2)
    need0, need1 = x_range[0] - 0.75 * fw, x_range[1] + 0.75 * fw
    min_w = 0.16 * aw
    cands = []
    for X0, Y0, X1, Y1 in ((bx0, by0, bx1, by1), (ex0, ey0, ex1, ey1)):
        for mult in (mults or (5.5, 5.0, 4.6, 4.2, 3.8)):
            cw = max(mult * fw, min_w)
            cw = min(cw, X1 - X0, (Y1 - Y0) * aspect)
            ch = cw / aspect
            for dx in np.linspace(-1.5 * fw, 1.5 * fw, 13):
                x = min(max(fx + fw / 2 - cw / 2 + dx, X0), X1 - cw)
                for head in (1.6, 1.3, 1.05, 0.9):
                    y = min(max(fy - head * fh, Y0), Y1 - ch)
                    if y + ch < min(fy + 1.1 * fh, Y1 - 1):   # chin inside (or the frame ends there)
                        continue
                    cut_head = max(0.0, y - (fy - 0.85 * fh)) / fh
                    cut_area, ax0, ay0, ax1, ay1 = 0.0, bx0, by0, bx1, by1
                    for ox0, oy0, ox1, oy1 in overlays:
                        iw = max(0, min(x + cw, ox1) - max(x, ox0))
                        ih = max(0, min(y + ch, oy1) - max(y, oy0))
                        frac = iw * ih / max(1.0, (ox1 - ox0) * (oy1 - oy0))
                        if frac >= 0.9:
                            crosses = (ox0 < bx0 < ox1 or ox0 < bx1 < ox1 or oy0 < by0 < oy1 or oy0 < by1 < oy1)
                            if crosses:
                                ax0, ay0, ax1, ay1 = min(ax0, ox0), min(ay0, oy0), max(ax1, ox1), max(ay1, oy1)
                        elif frac > 0.02:
                            cut_area += iw * ih
                    beyond = ((max(0, ax0 - x) + max(0, x + cw - ax1)) * ch
                              + (max(0, ay0 - y) + max(0, y + ch - ay1)) * cw) / (cw * ch)
                    busy = 0.0
                    outside = x < bx0 or y < by0 or x + cw > bx1 or y + ch > by1
                    if outside and activity is not None:
                        win = activity[max(0, int(y)):int(y + ch), max(0, int(x)):int(x + cw)].copy()
                        win[max(0, int(by0 - y)):max(0, int(by1 - y)), max(0, int(bx0 - x)):max(0, int(bx1 - x))] = 0
                        for ox0, oy0, ox1, oy1 in overlays:
                            win[max(0, int(oy0 - y)):max(0, int(oy1 - y)), max(0, int(ox0 - x)):max(0, int(ox1 - x))] = 0
                        busy = float((win > 12).sum()) / (cw * ch)
                    miss = max(0.0, (max(0, x - need0) + max(0, need1 - (x + cw))) / fw - 0.3)
                    ok_head = cut_head <= 0.15
                    ok_person = miss == 0
                    ok_overlay = cut_area == 0
                    ok_area = beyond < 0.01 and busy < 0.03
                    key = (ok_head, ok_area, ok_person, ok_overlay, -round(cut_area / (cw * ch), 3), -round(miss, 1),
                           round(cw / aw, 2), -abs(round(dx / fw, 1)), -abs(round((fy - y) / fh - 1.1, 1)))
                    cands.append((key, [x, y, cw, ch]))
    if not cands:
        cw = min(bx1 - bx0, (by1 - by0) * aspect, max(4.4 * fw, min_w))
        return fit_aspect([fx + fw / 2 - cw / 2, fy - 1.0 * fh, fx + fw / 2 + cw / 2, fy - fh + cw / aspect],
                          aspect, (fx + fw / 2, fy + fh * 0.5), bounds)
    cands.sort(key=lambda c: c[0])
    if DEBUG:
        print("    crop key", cands[-1][0], [round(v) for v in cands[-1][1]])

    return cands[-1][1]


def plan_frames(frames, faces, W, H, crowd=False) -> dict:
    """Layout for one stretch of video where the stream layout does not change.
    crowd: two or more live people of similar size are in the picture (a camera shot, not a webcam)."""
    ah, aw = frames[0].shape[:2]
    k = W / aw  # analysis -> source scale
    face, presence = stable_face(faces, len(frames), aw, frames, min_presence=0.2)
    cb = content_box(frames)
    lay = {"src_w": W, "src_h": H, "face_presence": round(presence, 2), "frames_checked": len(frames)}
    cam_rect = None
    if face is not None and (face[2] / aw > 0.22 or
                             (face[2] / aw > (0.06 if crowd else 0.10) and not find_cam_rect(frames, face))):
        mode = "fullcam"
    elif face is not None:
        mode = "facecam"
    else:
        mode = "gameplay"
    fx, fy, fw, fh = face if face is not None else (0, 0, 0, 0)
    if mode == "facecam":
        rect = find_cam_rect(frames, face)
        if rect:
            l, t, r, b = rect
            # trim 3 px inside the overlay border so no frame line leaks in
            cam_rect = [l + (3 if l > 0 else 0), t + (3 if t > 0 else 0),
                        r - (3 if r < aw else 0), b - (3 if b < ah else 0)]
        aspect = OUT_W / TOP_H
        bounds = cam_rect or [0, 0, aw, ah]
        ovl = overlay_boxes(frames, rect) if cam_rect else []   # untrimmed rect: its border lines
        xs = sorted(f[1] + f[3] / 2 for f in faces)
        x_range = (xs[int(0.02 * (len(xs) - 1))], xs[int(0.98 * (len(xs) - 1))]) if xs else None
        import cv2
        gs = np.stack([cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames])
        act = gs.astype(np.float32).std(axis=0)
        # clutter = moving pixels or edges (chat text, UI) - only plain, calm areas may be shown around the cam
        edges = cv2.Canny(np.median(gs, axis=0).astype(np.uint8), 40, 120) > 0
        act = np.where(edges, 255.0, act)
        # no cam box (green-screen cutout over the game): head and shoulders only, the rest is gameplay
        cam = face_crop(face, bounds, ovl, aspect, aw, ah, x_range, act,
                        None if cam_rect else (4.4, 4.0, 3.6))
        if DEBUG:
            print("    rect", [round(v * k) for v in rect] if cam_rect else None, "ovl", [[round(v * k) for v in b] for b in ovl],
                  "xr", [round(v * k) for v in x_range] if x_range else None, "face", [round(v * k) for v in face],
                  "cam", [round(v * k) for v in cam])
        lay["overlays"] = [[float(v * k) for v in b] for b in ovl]
        # gameplay below: full content height, aspect of the bottom panel
        gx0, gy0, gx1, gy1 = cb
        gh = gy1 - gy0
        gw = min(gx1 - gx0, gh * OUT_W / BOT_H)
        gh = gw * BOT_H / OUT_W
        avoid = cam_rect or [cam[0], cam[1], cam[0] + cam[2], cam[1] + cam[3]]
        gx = best_x(frames, gw, (gx0, gy0, gx1, gy1), avoid)
        game = [gx, gy0 + ((gy1 - gy0) - gh) / 2, gw, gh]
        lay.update(mode="facecam", cam=cam, game=game)
    elif mode == "fullcam":
        gx0, gy0, gx1, gy1 = cb
        ch = gy1 - gy0
        cw = ch * OUT_W / OUT_H
        cx = min(max(fx + fw / 2 - cw / 2, gx0), gx1 - cw)
        lay.update(mode="fullcam", full=[cx, gy0, cw, ch])
    else:
        gx0, gy0, gx1, gy1 = cb
        ch = gy1 - gy0
        cw = ch * OUT_W / OUT_H
        cx = best_x(frames, cw, cb)
        lay.update(mode="gameplay", full=[cx, gy0, cw, ch])
    if face is not None:
        lay["face"] = [float(v * k) for v in face]
    if cam_rect:
        lay["cam_rect"] = [float(v * k) for v in cam_rect]
    for key in ("cam", "game", "full"):
        if key in lay:
            x, y, w_, h_ = lay[key]
            x, y, w_, h_ = x * k, y * k, w_ * k, h_ * k
            w_, h_ = int(w_) // 2 * 2, int(h_) // 2 * 2
            x = int(min(max(0, x), W - w_))
            y = int(min(max(0, y), H - h_))
            lay[key] = [x, y, w_, h_]
    return lay




def _same(a, b, W=None):
    return a == b


def _iou(a, b):
    ax0, ay0, aw_, ah_ = a
    bx0, by0, bw_, bh_ = b
    ix = max(0, min(ax0 + aw_, bx0 + bw_) - max(ax0, bx0))
    iy = max(0, min(ay0 + ah_, by0 + bh_) - max(ay0, by0))
    inter = ix * iy
    return inter / (aw_ * ah_ + bw_ * bh_ - inter + 1e-6)


def _similar(a, b):
    if a["mode"] != b["mode"]:
        return False
    key = "cam" if a["mode"] == "facecam" else "full"
    return _iou(a[key], b[key]) > 0.5


def _refine_cut(video: Path, t_abs: float) -> float:
    """Exact layout switch: the biggest picture change within +-0.6 s (15 fps, tiny frames)."""
    t0 = max(0.0, t_abs - 0.6)
    cmd = [ffmpeg(), "-v", "error", "-ss", f"{t0:.3f}", "-t", "1.2", "-i", str(video), "-vf",
           "fps=15,scale=96:54", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]
    r = subprocess.run(cmd, capture_output=True)
    n = len(r.stdout) // (96 * 54)
    if n < 3:
        return t_abs
    fr = np.frombuffer(r.stdout[: n * 96 * 54], np.uint8).reshape(n, 54, 96).astype(np.float32)
    d = np.abs(np.diff(fr, axis=0)).mean(axis=(1, 2))
    return t0 + (int(np.argmax(d)) + 1) / 15.0


def _region_change(frames, i, box, a_rng=None, b_rng=None) -> float:
    """Picture change inside `box` (expanded) across sample boundary i. A real layout switch
    changes the cam area completely; a cut in the trailer/game around it does not."""
    import cv2
    x, y, w, h = box
    x0, y0 = int(max(0, x - REGION_K * w)), int(max(0, y - REGION_K * h))
    x1, y1 = int(x + (1 + REGION_K) * w), int(y + (1 + REGION_K) * h)

    def grab(a, b):
        fr = [frames[k][y0:y1, x0:x1] for k in range(max(0, a), min(len(frames), b))]
        fr = [cv2.resize(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), (48, 48)).astype(np.float32) for f in fr if f.size]
        return np.mean(fr, axis=0) if fr else None

    a = grab(*a_rng) if a_rng else grab(i - 2, i)
    b = grab(*b_rng) if b_rng else grab(i, i + 2)
    if a is None or b is None:
        return 255.0
    return float(np.abs(a - b).mean())


def _people_in(cams, i0, i1, ref_w):
    """Clearly live faces (people talking, not a game or trailer face) seen in >= 30 % of samples
    i0..i1, at least 0.6 x ref_w wide, one entry per person (a person who moves can be split into
    two clusters: near, never in the same sample)."""
    people = []
    for c in sorted(cams, key=lambda c: -len(c["frames"])):
        if c.get("live", 0) < PEOPLE_LIVE:
            continue
        its = [f for f in c["items"] if i0 <= f[0] < i1]
        fs = {f[0] for f in its}
        if len(fs) < max(4, 0.3 * (i1 - i0)) or np.median([f[3] for f in its]) < 0.6 * ref_w:
            continue
        cx = float(np.median([f[1] + f[3] / 2 for f in its]))
        for p in people:
            if abs(p["cx"] - cx) < 2.0 * ref_w and len(p["fs"] & fs) < 0.1 * len(fs):
                p["items"] = p["items"] + its
                p["fs"] |= fs
                break
        else:
            people.append({"cx": cx, "items": its, "fs": fs})
    return people


def plan(video: Path, t0: float, dur: float, debug_path: Path = None, prior=None) -> dict:
    """Layout for a clip. If the stream layout changes inside the clip (scene switch: game ->
    just chatting, cam moved), the clip is split into segments, each with its own layout."""
    fps = 2.0
    frames, times, W, H = grab_frames(video, t0, dur, fps)
    if not frames:
        raise RuntimeError("could not read frames from the video section")
    aw = frames[0].shape[1]
    faces = detect_faces(frames)
    n = len(frames)
    # streamer faces = clusters that stay in place for >= 25 % of the clip and are alive
    # (game characters and pictures on screen do not pass both tests)
    cams = []
    for c in face_clusters(faces, aw):
        if len(c["frames"]) >= max(4, int(0.25 * n)):
            c["live"] = _liveness(c["items"], frames)
            if c["live"] >= LIVE_MIN:
                cams.append(c)
    def near_prior(c):
        if not prior:
            return 0
        return int(abs(c["cx"] / aw - prior[0]) < 0.12 and abs(c["cy"] / frames[0].shape[0] - prior[1]) < 0.15)
    cams.sort(key=lambda c: (-near_prior(c), -len(c["frames"])))
    labels = []
    for i in range(n):
        lo, hi = max(0, i - 3), min(n, i + 4)
        lb = None
        for ci, c in enumerate(cams):
            if sum(1 for fi in c["frames"] if lo <= fi < hi) >= min(3, hi - lo):
                lb = ci
                break
        labels.append(lb)
    segs = []
    for i, lb in enumerate(labels):
        if segs and _same(segs[-1]["label"], lb, aw):
            segs[-1]["i1"] = i + 1
        else:
            segs.append({"i0": i, "i1": i + 1, "label": lb})
    min_len = int(2.5 * fps)
    changed = True
    while changed and len(segs) > 1:
        changed = False
        for j, sg in enumerate(segs):
            if sg["i1"] - sg["i0"] < min_len:
                if j == 0:
                    tgt = 1
                elif j == len(segs) - 1:
                    tgt = j - 1
                else:
                    left = segs[j - 1]["i1"] - segs[j - 1]["i0"]
                    right = segs[j + 1]["i1"] - segs[j + 1]["i0"]
                    tgt = j - 1 if left >= right else j + 1
                segs[tgt]["i0"] = min(segs[tgt]["i0"], sg["i0"])
                segs[tgt]["i1"] = max(segs[tgt]["i1"], sg["i1"])
                segs.pop(j)
                changed = True
                break
        k = 0
        while k + 1 < len(segs):
            if _same(segs[k]["label"], segs[k + 1]["label"], aw):
                segs[k]["i1"] = segs[k + 1]["i1"]
                segs.pop(k + 1)
                changed = True
            else:
                k += 1
    # drop switches where the cam area did not really change (face just not detected for a while,
    # or a face in the game/trailer showed up)
    j = 1
    while j < len(segs):
        a, b = segs[j - 1], segs[j]
        ref = a["label"] if a["label"] is not None else b["label"]
        if ref is not None:
            box = [float(v) for v in np.median([f[1:5] for f in cams[ref]["items"]], axis=0)]
            # compare well inside both segments: label edges are fuzzy by up to 3 samples
            a_rng = (max(a["i0"], b["i0"] - 8), max(a["i0"] + 1, b["i0"] - 3))
            b_rng = (min(b["i0"] + 3, b["i1"] - 1), min(b["i1"], b["i0"] + 8))
            rc = _region_change(frames, b["i0"], box, a_rng, b_rng)
            if DEBUG:
                print("    region change", b["i0"] / fps, round(rc, 1))
            if rc < 28.0:
                keep = a if a["label"] is not None and (b["label"] is None or a["i1"] - a["i0"] >= b["i1"] - b["i0"]) else b
                a["label"] = keep["label"]
                a["i1"] = b["i1"]
                segs.pop(j)
                continue
        j += 1
    out = []
    for j, sg in enumerate(segs):
        fr = frames[sg["i0"]:sg["i1"]]
        own = cams[sg["label"]]["items"] if sg["label"] is not None else []
        fc = [(f[0] - sg["i0"],) + tuple(f[1:]) for f in own if sg["i0"] <= f[0] < sg["i1"]]
        # live people of about the same size in the picture: a camera shot (IRL, interview, podcast)
        crowd = len(_people_in(cams, sg["i0"], sg["i1"], float(np.median([f[3] for f in fc])))) if fc else 0
        lay = plan_frames(fr, fc, W, H, crowd >= 2)
        s0 = 0.0 if j == 0 else _refine_cut(video, t0 + sg["i0"] / fps - 0.25) - t0
        lay["t0"] = round(max(0.0, s0), 3)
        out.append(lay)
    for j in range(len(out)):
        out[j]["t1"] = out[j + 1]["t0"] if j + 1 < len(out) else round(dur, 3)
    merged = []
    for x in out:
        if merged and _similar(merged[-1], x):
            keep = merged[-1] if merged[-1]["t1"] - merged[-1]["t0"] >= x["t1"] - x["t0"] else x
            m = dict(keep)
            m["t0"], m["t1"] = merged[-1]["t0"], x["t1"]
            merged[-1] = m
        else:
            merged.append(x)
    out = merged
    out = [x for x in out if x["t1"] - x["t0"] > 0.2]
    for x in out:
        if "face" in x:
            fx, fy, fw, fh = x["face"]
            x["face_center"] = [round((fx + fw / 2) / W, 4), round((fy + fh / 2) / H, 4)]
    res = {"segments": out, "mode": "+".join(x["mode"] for x in out), "src_w": W, "src_h": H,
           "face_presence": round(float(np.mean([x["face_presence"] for x in out])), 2)}
    if debug_path:
        import cv2
        imgs = []
        for x in out:
            mid = int(((x["t0"] + x["t1"]) / 2) * fps)
            img = frames[min(n - 1, mid)].copy()
            _debug(img, x, aw / W, None)
            imgs.append(img)
        image = np.vstack(imgs) if len(imgs) > 1 else imgs[0]
        ok, encoded = cv2.imencode(debug_path.suffix, image)
        if ok:
            debug_path.write_bytes(encoded.tobytes())
    return res


def _debug(frame, lay, s, path):
    import cv2
    img = frame
    colors = {"cam": (0, 255, 0), "game": (255, 160, 0), "full": (255, 160, 0), "face": (0, 230, 255),
              "cam_rect": (0, 0, 255)}
    for key, col in colors.items():
        if key in lay:
            x, y, w, h = [v * s for v in lay[key]] if key != "cam_rect" else [
                lay[key][0] * s, lay[key][1] * s, (lay[key][2] - lay[key][0]) * s, (lay[key][3] - lay[key][1]) * s]
            cv2.rectangle(img, (int(x), int(y)), (int(x + w), int(y + h)), col, 3)
    for b in lay.get("overlays", []):
        cv2.rectangle(img, (int(b[0] * s), int(b[1] * s)), (int(b[2] * s), int(b[3] * s)), (255, 0, 255), 2)
    cv2.putText(img, f"{lay['mode']} from {lay.get('t0', 0):.1f}s", (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.4,
                (255, 255, 255), 3)
    if path:
        ok, encoded = cv2.imencode(path.suffix, img)
        if ok:
            path.write_bytes(encoded.tobytes())
