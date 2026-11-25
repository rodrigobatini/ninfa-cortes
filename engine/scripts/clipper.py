"""AI Stream Clipper - turns a long stream VOD into vertical clips.

Commands (run with: python -I scripts/clipper.py <command> ...):
  setup   [--no-install] [--cpu] [--cookies-from-browser firefox]
  prepare SOURCE [--out DIR] [--lang auto|en|ru] [--top 3]
  check   JOB [--top 3]
  render  JOB [--only 1,3]
  report  JOB
  sheet   VIDEO.mp4
SOURCE = YouTube / Twitch / Kick VOD link or a local video file.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cl_common import (DEFAULT_OUT, Timer, fmt_dur, fmt_ts, log, read_json, slug, utf8_console,  # noqa: E402
                       write_json)

MAX_TOP = 3  # clips per stream in this version


def job_dir_for(meta: dict, out: Path) -> Path:
    base = meta["id"]
    ch = slug(meta.get("channel") or "", 24)
    name = f"{ch}_{base}" if ch and ch != "clip" else base
    return out / name


def cmd_prepare_one(src, args) -> Path:
    import cl_chunks
    import cl_fetch
    import cl_signals
    import cl_transcribe
    t = Timer()
    started = time.time()
    log(f"== prepare: {src}")
    meta = cl_fetch.probe_source(src, args.cookies_from_browser, args.cookies)
    job = job_dir_for(meta, Path(args.out).expanduser().resolve())
    job.mkdir(parents=True, exist_ok=True)
    (job / "media").mkdir(exist_ok=True)
    old = read_json(job / "job.json", {}) or {}
    meta["timings"] = {"prepare_started": started}
    meta["source"] = src
    meta["top_n"] = args.top
    if old.get("language"):
        meta["language"] = old["language"]
    write_json(job / "job.json", meta)
    log(f"  job: {job}")
    log(f"  stream: {meta['title']} | {meta.get('channel', '')} | {fmt_ts(meta['duration'])}")
    wav = cl_fetch.download_audio(job, meta, args.cookies_from_browser, args.cookies)
    if not meta.get("duration"):
        from cl_common import media_duration
        meta["duration"] = media_duration(wav)
    tinfo = read_json(job / "transcript" / "info.json")
    forced = args.lang if args.lang != "auto" else None
    if not tinfo or (forced and tinfo.get("language") != forced):
        tt = Timer()
        tinfo = cl_transcribe.transcribe(job, wav, forced, args.cpu)
        meta["timings"]["transcribe_sec"] = round(tt(), 1)
    else:
        log("  whisper: transcript already exists")
        meta["timings"]["transcribe_sec"] = tinfo.get("seconds")
    meta["language"] = tinfo["language"]
    meta["whisper"] = {k: tinfo.get(k) for k in ("device", "model", "words")}
    cl_signals.compute(job)
    write_json(job / "job.json", meta)
    n = cl_chunks.build(job, args.chunk_minutes, args.read_all)
    meta["chunks"] = n
    meta["timings"]["prepare_finished"] = time.time()
    meta["timings"]["prepare_sec"] = round(t(), 1)
    write_json(job / "job.json", meta)
    log(f"  ready: {n} chunks in {job / 'chunks'} (prepare took {fmt_dur(t())})")
    log(f"PREPARED {job}")
    return job


def cmd_prepare(args):
    try:
        cmd_prepare_one(args.source, args)
    except SystemExit as e:
        log(f"  FAILED: {e}")
        return 1
    except Exception as e:
        import traceback
        log(f"  FAILED: {type(e).__name__}: {str(e)[:600]}")
        log(traceback.format_exc()[-1500:])
        return 1
    return 0


def cmd_check(args):
    import cl_moments
    job = Path(args.job).expanduser().resolve()
    meta = read_json(job / "job.json") or {}
    errors, warns, res = cl_moments.check(job, min(MAX_TOP, args.top or meta.get("top_n", MAX_TOP)))
    for e in errors:
        log("ERROR: " + e)
    for w in warns:
        log("WARN: " + w)
    if res:
        for p in res["picks"]:
            log(f"  pick {p['order']}: {fmt_ts(p['start'])}-{fmt_ts(p['end'])} ({p['duration']} s) score {p.get('score')}")
    log(f"Clip texts: {job / 'moments.check.md'}")
    if errors:
        log("CHECK FAILED - fix moments.json and run check again")
    elif warns:
        log(f"CHECK OK with {len(warns)} warnings - fix them or say why they are fine")
    else:
        log("CHECK OK")
    return 0 if not errors else 2


def cmd_render(args):
    import cl_render
    only = [int(x) for x in args.only.split(",")] if args.only else None
    job = Path(args.job).expanduser().resolve()
    log(f"== render: {job.name}")
    try:
        meta = cl_render.render_job(job, args.cookies_from_browser, args.cookies, only, args.facecam,
                                   not args.no_censor)
    except SystemExit as e:
        log(f"  FAILED: {e}")
        return 1
    ok = [c for c in meta.get("clips", []) if "mp4" in c]
    log(f"RENDERED {len(ok)} clips. Report: {job / 'report.md'}")
    return 0 if len(ok) == len(meta.get("clips", [])) else 1


def cmd_report(args):
    import cl_render
    job = Path(args.job).expanduser().resolve()
    cl_render.write_report(job)
    print((job / "report.md").read_text(encoding="utf-8"))
    return 0


def cmd_sheet(args):
    import cl_render
    from cl_common import media_duration
    v = Path(args.video).resolve()
    out = v.with_name(v.stem + "_sheet.jpg")
    cl_render.contact_sheet(v, out, media_duration(v))
    log(str(out))
    return 0


def main():
    utf8_console()
    ap = argparse.ArgumentParser(prog="clipper", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def cookies(p):
        p.add_argument("--cookies-from-browser", default=None,
                       help="firefox / chrome / edge: use your browser login (age-restricted, Kick)")
        p.add_argument("--cookies", default=None, help="cookies.txt file")

    p = sub.add_parser("setup")
    p.add_argument("--no-install", action="store_true")
    p.add_argument("--cpu", action="store_true")
    p.add_argument("--cookies-from-browser", default=None)

    p = sub.add_parser("prepare")
    p.add_argument("source", help="one stream link or local video file")
    p.add_argument("--out", default=str(DEFAULT_OUT))
    p.add_argument("--lang", default="auto", help="auto, en, ru, ... (language of the stream)")
    p.add_argument("--top", type=int, default=MAX_TOP, choices=range(1, MAX_TOP + 1), metavar="1-3",
                   help="clips per stream (1-3)")
    p.add_argument("--chunk-minutes", type=int, default=10)
    p.add_argument("--read-all", action="store_true", help="mark every chunk READ FIRST (thorough, more tokens)")
    p.add_argument("--cpu", action="store_true")
    cookies(p)

    p = sub.add_parser("check")
    p.add_argument("job")
    p.add_argument("--top", type=int, default=None, choices=range(1, MAX_TOP + 1), metavar="1-3")

    p = sub.add_parser("render")
    p.add_argument("job")
    p.add_argument("--only", default=None, help="render only these picks, e.g. 1,3")
    p.add_argument("--facecam", default=None,
                   help="manual webcam box x:y:w:h in source pixels (see layout_debug.jpg) if detection is wrong; full:X = one 9:16 crop centred on source x (IRL, wrong person)")
    p.add_argument("--no-censor", action="store_true", help="do not mask swear words in captions and hook")
    cookies(p)

    p = sub.add_parser("report")
    p.add_argument("job")

    p = sub.add_parser("sheet")
    p.add_argument("video")

    args = ap.parse_args()
    if args.cmd == "setup":
        import cl_setup
        return 0 if cl_setup.main(not args.no_install, args.cpu, args.cookies_from_browser) else 1
    return {"prepare": cmd_prepare, "check": cmd_check, "render": cmd_render,
            "report": cmd_report, "sheet": cmd_sheet}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main() or 0)
