"""Render picked moments into vertical 1080x1920 clips + post texts + report."""
from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import numpy as np

from cl_captions import build_ass
from cl_common import (FONTS, Timer, ffmpeg, fmt_dur, fmt_ts, lang_strings, load_config, log,
                       read_json, run, slug, video_stream, write_json, write_text)
from cl_fetch import download_section
from cl_layout import BOT_H, OUT_H, OUT_W, TOP_H, plan

PAD = 4.0  # seconds of extra video downloaded around each clip


def _pcm(path: Path, start: float, dur: float, sr: int = 8000) -> np.ndarray:
    cmd = [ffmpeg(), "-v", "error", "-ss", f"{max(0, start):.3f}", "-t", f"{dur:.3f}", "-i", str(path),
           "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "pipe:1"]
    r = subprocess.run(cmd, capture_output=True)
    return np.frombuffer(r.stdout, np.float32).copy()


def align_section(job: Path, section: Path, guess_t0: float) -> tuple:
    """Find where the downloaded section starts inside the stream by matching its audio
    against the full audio track. Returns (t0_in_stream, confidence)."""
    sr = 8000
    probe_len = 20.0
    sec = _pcm(section, 1.0, probe_len, sr)
    if len(sec) < sr * 5:
        return guess_t0, 0.0
    w0 = max(0.0, guess_t0 - 20.0)
    ref = _pcm(job / "media" / "audio16k.wav", w0, probe_len + 60.0, sr)
    if len(ref) < len(sec):
        return guess_t0, 0.0
    a = sec - sec.mean()
    b = ref - ref.mean()
    n = 1 << int(np.ceil(np.log2(len(a) + len(b))))
    corr = np.fft.irfft(np.fft.rfft(b, n) * np.conj(np.fft.rfft(a, n)), n)[: len(b) - len(a) + 1]
    cs = np.concatenate([[0.0], np.cumsum(b.astype(np.float64) ** 2)])
    win = (cs[len(a):] - cs[:-len(a)])[: len(corr)]
    e = np.sqrt(np.maximum(win, 0) * float((a ** 2).sum())) + 1e-9
    nc = corr / e
    lag = int(np.argmax(nc))
    t0 = w0 + lag / sr - 1.0
    return t0, float(nc[lag])


def encoder_args(cfg: dict):
    enc = cfg.get("encoder", "libx264")
    if enc == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p6", "-tune", "hq", "-rc", "vbr", "-cq", "19", "-b:v", "0",
                "-maxrate", "24M", "-bufsize", "48M", "-profile:v", "high", "-spatial-aq", "1"]
    if enc == "h264_videotoolbox":
        return ["-c:v", "h264_videotoolbox", "-b:v", "14M", "-profile:v", "high"]
    return ["-c:v", "libx264", "-preset", "medium", "-crf", "19", "-profile:v", "high"]


def out_fps(section: Path) -> str:
    vs = video_stream(section)
    try:
        n, d = vs.get("avg_frame_rate", "30/1").split("/")
        f = float(n) / float(d)
    except Exception:
        f = 30.0
    if 29 <= f <= 61:
        return vs.get("avg_frame_rate")
    return "30"


def _chain(lay: dict, src: str, dst: str) -> str:
    """Filter chain that turns one source segment into a 1080x1920 frame."""
    if lay["mode"] == "facecam":
        cx, cy, cw, ch = lay["cam"]
        gx, gy, gw, gh = lay["game"]
        sharpen = ",unsharp=5:5:0.45" if OUT_W / cw > 1.6 else ""
        return (f"[{src}]split=2[{src}c][{src}g];"
                f"[{src}c]crop={cw}:{ch}:{cx}:{cy},scale={OUT_W}:{TOP_H}:flags=lanczos{sharpen}[{src}t];"
                f"[{src}g]crop={gw}:{gh}:{gx}:{gy},scale={OUT_W}:{BOT_H}:flags=lanczos[{src}b];"
                f"[{src}t][{src}b]vstack=inputs=2,"
                f"drawbox=x=0:y={TOP_H - 3}:w={OUT_W}:h=6:color=black@1:t=fill,setsar=1[{dst}]")
    x, y, w, h = lay["full"]
    return f"[{src}]crop={w}:{h}:{x}:{y},scale={OUT_W}:{OUT_H}:flags=lanczos,setsar=1[{dst}]"


def filter_graph(lay: dict, ass_rel: str) -> str:
    tail = f"format=yuv420p,ass=filename={ass_rel}:fontsdir=fonts[v]"
    segs = lay["segments"]
    if len(segs) == 1:
        return f"[0:v]null[s0];" + _chain(segs[0], "s0", "o0") + f";[o0]{tail}"
    parts = [f"[0:v]split={len(segs)}" + "".join(f"[i{k}]" for k in range(len(segs)))]
    for k, sg in enumerate(segs):
        parts.append(f"[i{k}]trim=start={sg['t0']:.3f}:end={sg['t1']:.3f},setpts=PTS-STARTPTS[s{k}]")
        parts.append(_chain(sg, f"s{k}", f"o{k}"))
    parts.append("".join(f"[o{k}]" for k in range(len(segs))) + f"concat=n={len(segs)}:v=1:a=0[cat]")
    parts.append(f"[cat]{tail}")
    return ";".join(parts)


def contact_sheet(mp4: Path, out: Path, dur: float):
    run([ffmpeg(), "-v", "error", "-y", "-i", str(mp4), "-vf",
         f"fps=12/{dur:.2f},scale=270:480,tile=6x2:padding=6:color=white", "-frames:v", "1",
         "-q:v", "3", str(out)])


def post_texts(pick: dict, meta: dict, lang: str) -> dict:
    s = lang_strings(lang)
    tags = [t if t.startswith("#") else "#" + t for t in (pick.get("hashtags") or [])]
    tags = [t.replace(" ", "") for t in tags if len(t) > 1]
    for t in s["default_tags"]:
        if len(tags) >= 6:
            break
        if t.lower() not in [x.lower() for x in tags]:
            tags.append(t)
    credit = s["credit"].format(channel=meta.get("channel") or "?") if meta.get("channel") else ""
    src = s["credit_src"].format(url=meta["url"]) if meta.get("url") else ""
    cap = (pick.get("caption") or "").strip()
    title = (pick.get("title") or pick.get("hook") or "").strip()
    shorts_title = title if len(title) <= 95 else title[:92].rstrip() + "..."
    out = {
        "youtube_shorts": {"title": shorts_title,
                           "description": "\n\n".join(x for x in [cap, credit, src, " ".join(tags[:4] + [s["shorts_tag"]])] if x)},
        "tiktok": {"caption": " ".join(x for x in [cap, credit, " ".join(tags[:5])] if x)},
        "instagram_reels": {"caption": "\n\n".join(x for x in [cap, credit, " ".join(tags[:5])] if x)},
    }
    x_tags = " ".join(tags[:2])
    room = 280 - len(x_tags) - 2
    x_text = cap if len(cap) <= room else cap[: room - 3].rstrip() + "..."
    out["x"] = {"post": f"{x_text}\n\n{x_tags}".strip()}
    return out


def post_markdown(p: dict, pt: dict, clip_name: str) -> str:
    return "\n".join([
        f"# {clip_name}", "",
        f"Hook: {p.get('hook', '')}", f"Moment: {fmt_ts(p['start'])} - {fmt_ts(p['end'])}, score {p.get('score')}",
        f"Why: {p.get('why', '')}", "",
        "## YouTube Shorts", f"Title: {pt['youtube_shorts']['title']}", "", pt["youtube_shorts"]["description"], "",
        "## TikTok", pt["tiktok"]["caption"], "",
        "## Instagram Reels", pt["instagram_reels"]["caption"], "",
        "## X", pt["x"]["post"], "",
    ])


def manual_layout(box: str, W: int, H: int) -> dict:
    """--facecam x:y:w:h (source pixels): use this webcam box for every clip.
    --facecam full:X : no webcam (IRL, talk show) - one 9:16 crop of the whole frame centred on source x = X
    (use it when detection followed the wrong person or a face in a picture)."""
    if box.startswith("full:"):
        cw = int(H * OUT_W / OUT_H) // 2 * 2
        x0 = int(min(max(float(box[5:]) - cw / 2, 0), W - cw)) // 2 * 2
        return {"mode": "fullcam", "full": [x0, 0, cw, H // 2 * 2], "face_presence": 1.0, "t0": 0.0}
    from cl_layout import fit_aspect
    x, y, w, h = [float(v) for v in box.split(":")]
    cam = fit_aspect([x, y, x + w, y + h], OUT_W / TOP_H, (x + w / 2, y + h * 0.45), [0, 0, W, H])
    gh = H
    gw = gh * OUT_W / BOT_H
    gx = (W - gw) / 2
    if gx < x + w and gx + gw > x:  # move the gameplay crop away from the cam
        gx = x + w if x + w + gw <= W else max(0, x - gw)
    seg = {"mode": "facecam", "cam": [int(v) // 2 * 2 for v in cam], "game": [int(gx), 0, int(gw) // 2 * 2, int(gh) // 2 * 2],
           "face_presence": 1.0, "t0": 0.0}
    return seg


def render_job(job: Path, cookies=None, cookies_file=None, only=None, facecam=None, censor=True) -> dict:
    t = Timer()
    meta = read_json(job / "job.json")
    meta.setdefault("timings", {})["render_started"] = time.time()
    write_json(job / "job.json", meta)
    chk = read_json(job / "moments.checked.json")
    if not chk:
        raise SystemExit("Run `check` first: moments.checked.json is missing")
    words = read_json(job / "transcript" / "words.json", [])
    lang = meta.get("language", "en")
    cfg = load_config()
    fonts = job / "fonts"
    fonts.mkdir(exist_ok=True)
    for f in FONTS.glob("*.ttf"):
        if not (fonts / f.name).exists():
            shutil.copy2(f, fonts / f.name)
    clips_dir = job / "clips"
    results, todo = [], []
    picks = sorted(chk["picks"], key=lambda q: q["order"])
    # pass 1: fetch the minutes we need, sync them, plan the layout
    for p in picks:
        k = p["order"]
        if only and k not in only:
            continue
        name = f"{k:02d}_{slug(p.get('title') or p.get('hook') or 'clip', 36)}"
        cdir = clips_dir / name
        cdir.mkdir(parents=True, exist_ok=True)
        a, b = p["start"], p["end"]
        dur = b - a
        log(f"  clip {k}: {fmt_ts(a)} - {fmt_ts(b)} ({dur:.1f} s)")
        try:
            ct = Timer()
            if meta["kind"] == "file":
                section, t0, conf = Path(meta["path"]), 0.0, 1.0
            else:
                sec_stem = job / "media" / "sections" / f"sec_{int(a)}_{int(b)}"
                sec_stem.parent.mkdir(parents=True, exist_ok=True)
                section = download_section(job, meta, a - PAD, b + PAD, sec_stem, cookies, cookies_file)
                t0, conf = align_section(job, section, a - PAD)
                if conf < 0.3:
                    log(f"    sync: low confidence ({conf:.2f}), using the requested cut time")
                    t0 = a - PAD
            off = max(0.0, a - t0)
            if facecam:
                vs = video_stream(section)
                seg = manual_layout(facecam, int(vs["width"]), int(vs["height"]))
                seg["t1"] = round(dur, 3)
                lay = {"segments": [seg], "mode": seg["mode"] + "(manual)", "face_presence": 1.0}
            else:
                lay = plan(section, off, dur, cdir / "layout_debug.jpg")
            todo.append(dict(p=p, k=k, name=name, cdir=cdir, section=section, off=off, dur=dur, conf=conf,
                             lay=lay, secs=ct()))
        except Exception as e:
            log(f"    FAILED: {str(e)[:400]}")
            results.append({"order": k, "name": name, "error": str(e)[:1000]})
    # the streamer's cam sits in the same place in most clips of one stream: if a clip picked
    # another face (a guest cam, a face in the video being watched), re-plan it with that prior
    if not facecam and len(todo) >= 2:
        centers = [(sg["face_center"], sg["t1"] - sg["t0"]) for x in todo for sg in x["lay"]["segments"]
                   if sg["mode"] == "facecam" and "face_center" in sg]
        best, best_w = None, 0
        for c, _ in centers:
            wsum = sum(d for c2, d in centers if abs(c2[0] - c[0]) < 0.08 and abs(c2[1] - c[1]) < 0.12)
            if wsum > best_w:
                best, best_w = c, wsum
        if best:
            for x in todo:
                off_prior = [sg for sg in x["lay"]["segments"] if sg["mode"] == "facecam" and "face_center" in sg
                             and (abs(sg["face_center"][0] - best[0]) > 0.08 or abs(sg["face_center"][1] - best[1]) > 0.12)]
                if off_prior:
                    log(f"    clip {x['k']}: cam differs from the other clips, re-checking")
                    x["lay"] = plan(x["section"], x["off"], x["dur"], x["cdir"] / "layout_debug.jpg", prior=best)
    # pass 2: captions + encode
    for x in todo:
        p, k, name, cdir, section, off, dur, conf, lay = (x[q] for q in ("p", "k", "name", "cdir", "section", "off",
                                                                        "dur", "conf", "lay"))
        a, b = p["start"], p["end"]
        ct = Timer()
        try:
            log(f"  clip {k}: layout {lay['mode']} (face in {int(lay['face_presence'] * 100)}% of frames)")
            ass = cdir / "captions.ass"
            wsel = [w for w in words if w["e"] > a and w["s"] < b]
            build_ass(wsel, a, b, p.get("hook", ""), ass, lay["segments"], censor_on=censor)
            mp4 = cdir / f"{name}.mp4"
            rel_ass = ass.relative_to(job).as_posix()
            fg = filter_graph(lay, rel_ass)
            fade_out = max(0.0, dur - 0.15)
            af = f"loudnorm=I=-14:TP=-1.5:LRA=11,afade=t=in:d=0.06,afade=t=out:st={fade_out:.2f}:d=0.15"
            base = [ffmpeg(), "-v", "error", "-y", "-ss", f"{off:.3f}", "-t", f"{dur:.3f}", "-i", str(section),
                    "-filter_complex", fg, "-map", "[v]", "-map", "0:a:0", "-r", out_fps(section)]
            tail = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-af", af, "-movflags", "+faststart",
                    str(mp4)]
            try:
                run(base + encoder_args(cfg) + tail, cwd=str(job))
            except RuntimeError:
                if cfg.get("encoder", "libx264") != "libx264":
                    log("    hardware encoder failed, retrying with libx264")
                    run(base + encoder_args({"encoder": "libx264"}) + tail, cwd=str(job))
                else:
                    raise
            contact_sheet(mp4, cdir / "sheet.jpg", dur)
            pt = post_texts(p, meta, lang)
            write_json(cdir / "post.json", pt)
            write_text(cdir / "post.md", post_markdown(p, pt, name))
            secs = round(x["secs"] + ct(), 1)
            write_json(cdir / "clip.json", {"pick": p, "layout": lay, "sync_confidence": round(conf, 3),
                                            "section_offset": round(off, 3), "seconds": secs})
            results.append({"order": k, "name": name, "mp4": str(mp4), "sheet": str(cdir / "sheet.jpg"),
                            "post": str(cdir / "post.md"), "start": a, "end": b, "duration": round(dur, 1),
                            "score": p.get("score"), "hook": p.get("hook"), "layout": lay["mode"], "seconds": secs})
            log(f"    done -> {mp4}")
        except Exception as e:
            log(f"    FAILED: {str(e)[:400]}")
            results.append({"order": k, "name": name, "error": str(e)[:1000]})
    results.sort(key=lambda r: r["order"])
    meta = read_json(job / "job.json")
    tm = meta.setdefault("timings", {})
    tm["render_finished"] = time.time()
    tm["render_sec"] = round(t(), 1)
    meta["clips"] = results
    meta["candidates_total"] = chk.get("candidates_total")
    write_json(job / "job.json", meta)
    write_report(job)
    return meta


def write_report(job: Path):
    meta = read_json(job / "job.json")
    tm = meta.get("timings", {})
    clips = meta.get("clips", [])
    ok = [c for c in clips if "mp4" in c]
    total = None
    if tm.get("prepare_started") and tm.get("render_finished"):
        total = tm["render_finished"] - tm["prepare_started"]
    pick_sec = None
    if tm.get("prepare_finished") and tm.get("render_started"):
        pick_sec = tm["render_started"] - tm["prepare_finished"]
    rep = {
        "title": meta.get("title"), "channel": meta.get("channel"), "url": meta.get("url") or meta.get("path"),
        "stream_length": fmt_ts(meta.get("duration", 0)), "stream_seconds": meta.get("duration"),
        "language": meta.get("language"), "candidates_found": meta.get("candidates_total"),
        "clips_kept": len(ok), "clips_failed": len(clips) - len(ok),
        "prepare_seconds": tm.get("prepare_sec"), "transcribe_seconds": tm.get("transcribe_sec"),
        "picking_seconds": round(pick_sec, 1) if pick_sec else None,
        "render_seconds": tm.get("render_sec"), "total_seconds": round(total, 1) if total else None,
        "clips": clips,
    }
    write_json(job / "report.json", rep)
    lines = [f"# {rep['title']}", "",
             f"- Channel: {rep['channel']}", f"- Source: {rep['url']}",
             f"- Stream length: {rep['stream_length']}", f"- Language: {rep['language']}",
             f"- Candidates found: {rep['candidates_found']}", f"- Clips kept: {rep['clips_kept']}",
             f"- Prepare (download + transcript + signals): {fmt_dur(rep['prepare_seconds'] or 0)}",
             f"- Picking (Claude reading the chunks): {fmt_dur(rep['picking_seconds'] or 0)}",
             f"- Render: {fmt_dur(rep['render_seconds'] or 0)}",
             f"- Total: {fmt_dur(rep['total_seconds'] or 0)}", "", "## Clips"]
    for c in clips:
        if "mp4" in c:
            lines += [f"{c['order']}. {c['hook']} ({fmt_ts(c['start'])}, {c['duration']} s, score {c['score']}, "
                      f"{c['layout']})", f"   {c['mp4']}"]
        else:
            lines += [f"{c['order']}. FAILED: {c.get('error', '')[:200]}"]
    write_text(job / "report.md", "\n".join(lines) + "\n")
