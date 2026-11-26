"""Caption editing and rendering helpers for AI Stream Clipper footage."""
import copy
import os
import re
import shutil
import subprocess
from difflib import SequenceMatcher
from pathlib import Path

import cl_captions as captions
import cl_render
from cl_common import video_input

WIDTH, HEIGHT = 1080, 1920
DEFAULT_CAMERA = {"horizontal": 0, "vertical": 0, "zoom": 100, "pan_x": 0, "pan_y": 0}


def adjust_camera(segment, camera):
    """Adjust the webcam crop independently of the gameplay crop."""
    camera = {**DEFAULT_CAMERA, **camera}
    if segment["mode"] != "facecam" or camera == DEFAULT_CAMERA:
        return segment
    x, y, width, height = segment["cam"]
    left, top, right, bottom = segment.get("cam_rect") or (x, y, x + width, y + height)
    left, top = max(0, int(min(left, x)) // 2 * 2), max(0, int(min(top, y)) // 2 * 2)
    right = min(segment.get("src_w", max(right, x + width)), int(max(right, x + width)) // 2 * 2)
    bottom = min(segment.get("src_h", max(bottom, y + height)), int(max(bottom, y + height)) // 2 * 2)
    crop_height = max(4, round(height * 100 / camera["zoom"] / 4) * 4) if camera["zoom"] != 100 else height
    crop_width = crop_height * 3 // 2 if camera["zoom"] != 100 else width
    crop_width = min(crop_width, right - left) // 2 * 2
    crop_height = min(crop_height, bottom - top) // 2 * 2

    def origin(centered, low, high, offset):
        centered = min(high, max(low, centered))
        limit = high if offset >= 0 else low
        shifted = centered + (limit - centered) * abs(offset) / 100
        return min(high, max(low, round(shifted / 2) * 2))

    crop_x = origin(x + (width - crop_width) / 2, left, right - crop_width, -camera["horizontal"])
    crop_y = origin(y + (height - crop_height) / 2, top, bottom - crop_height, camera["vertical"])
    if camera["pan_x"] or camera["pan_y"]:
        source_width = segment.get("src_w", right)
        source_height = segment.get("src_h", bottom)
        shifted_x = round((crop_x + width * camera["pan_x"] / 100) / 2) * 2
        shifted_y = round((crop_y + height * camera["pan_y"] / 100) / 2) * 2
        crop_x = min((source_width - crop_width) // 2 * 2, max(0, shifted_x))
        crop_y = min((source_height - crop_height) // 2 * 2, max(0, shifted_y))
    return {**segment, "cam": [crop_x, crop_y, crop_width, crop_height]}


def camera_layout(clip, draft):
    layout = copy.copy(clip["layout"])
    camera = draft.get("camera", DEFAULT_CAMERA)
    layout["segments"] = [adjust_camera(segment, camera) for segment in layout["segments"]]
    return layout


def camera_frame(clip, camera, at):
    segment = next((s for s in clip["layout"]["segments"] if s["mode"] == "facecam" and s["t0"] <= at < s["t1"]), None)
    if not segment:
        return None
    x, y, width, height = adjust_camera(segment, camera)["cam"]
    cmd = [cl_render.ffmpeg(), "-nostdin", "-v", "error", "-ss", str(round(clip["start"] + at, 3)),
           *video_input(clip["source_path"]), "-frames:v", "1", "-vf", f"crop={width}:{height}:{x}:{y},scale=540:360",
           "-c:v", "mjpeg", "-f", "image2pipe", "-"]
    result = subprocess.run(cmd, capture_output=True, timeout=12, check=True)
    return result.stdout or None


def cue_list(words, start, end):
    selected = [{"s": max(0, w["s"] - start), "e": min(end, w["e"]) - start, "w": w["w"]}
                for w in words if w["e"] > start + .02 and w["s"] < end - .02 and w["w"].strip()]
    groups, group = [], []
    for word in selected:
        if group and (len(group) == 5 or word["s"] - group[-1]["e"] > .5
                      or re.search(r"[.!?…]$", group[-1]["w"]) or word["s"] - group[0]["s"] > 2.3):
            groups.append(group)
            group = []
        group.append(word)
    if group:
        groups.append(group)
    return [{"start": round(g[0]["s"], 3), "end": round(max(g[0]["s"] + .02, min(end - start,
                g[-1]["e"] + .25, groups[i + 1][0]["s"] if i + 1 < len(groups) else end - start)), 3),
             "text": " ".join(w["w"] for w in g), "words": g} for i, g in enumerate(groups)]


def word_starts(cue, tokens):
    source = cue["words"]
    if not source:
        return [cue["start"] + (cue["end"] - cue["start"]) * i / len(tokens) for i in range(len(tokens))]
    old = [re.sub(r"[^\w]", "", w["w"].casefold()) for w in source]
    new = [re.sub(r"[^\w]", "", word.casefold()) for word in tokens]
    anchors = {j + k: max(cue["start"], source[i + k]["s"])
               for i, j, n in SequenceMatcher(None, old, new, autojunk=False).get_matching_blocks() for k in range(n)}
    anchors.setdefault(0, cue["start"])
    anchors[len(tokens)] = cue["end"]
    positions = sorted(anchors)
    return [round(anchors[a] + (anchors[b] - anchors[a]) * (i - a) / (b - a), 3)
            for a, b in zip(positions, positions[1:]) for i in range(a, b)]


def caption_events(draft):
    events = []
    available = min(860, int(2 * min(draft["x"], 100 - draft["x"]) * WIDTH / 100 - 30))
    for cue in draft["cues"]:
        tokens = cue["text"].split()
        if not tokens or cue["end"] <= cue["start"]:
            continue
        if draft["style"] == "frase":
            text = " ".join(tokens)
            for size in range(52, 19, -4):
                lines = captions.wrap(text, size, available, captions.CAP_FONT_FILE)
                if len(lines) <= 2 and all(captions.text_width(line, size) <= available for line in lines):
                    break
            events.append({"start": cue["start"], "end": cue["end"], "lines": lines, "size": size, "active": -1})
        else:
            starts = word_starts(cue, tokens)
            for pos in range(0, len(tokens), 3):
                display = [captions.STRIP.sub("", word).upper() or word for word in tokens[pos:pos + 3]]
                size = max(13, min(62, int(available / max(1, captions.text_width(" ".join(display), 62)) * 62)))
                for i in range(pos, min(pos + 3, len(tokens))):
                    end = starts[i + 1] if i + 1 < len(tokens) else cue["end"]
                    if end > starts[i]:
                        events.append({"start": starts[i], "end": end, "lines": display,
                                       "size": size, "active": i - pos})
    return events


def make_ass(clip, draft, destination):
    captions.build_ass([], 0, clip["duration"], draft["hook"], destination, clip["layout"]["segments"], censor_on=False)
    lines = destination.read_text(encoding="utf-8").splitlines()
    x = round(draft["x"] * WIDTH / 100)
    y = round(draft["y"] * HEIGHT / 100)
    for ev in caption_events(draft):
        if ev["active"] < 0:
            line = r"\N".join(captions.esc(item) for item in ev["lines"])
        else:
            line = " ".join((r"{\1c&H00F4FF&}" + captions.esc(item) + r"{\1c&HFFFFFF&}")
                            if i == ev["active"] else captions.esc(item) for i, item in enumerate(ev["lines"]))
        tag = f"{{\\an2\\pos({x},{y})\\fs{ev['size']}}}"
        lines.append(f"Dialogue: 1,{captions.ass_time(ev['start'])},{captions.ass_time(ev['end'])},Cap,,0,0,0,,{tag}{line}")
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_ffmpeg(args, directory=None):
    command = [cl_render.ffmpeg(), "-nostdin", "-v", "error", "-y", *map(str, args)]
    result = subprocess.run(command, cwd=directory, capture_output=True, encoding="utf-8", errors="replace", timeout=420)
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[-900:] or "O FFmpeg não conseguiu concluir o vídeo.")


def card_filter(graph, card):
    ratio = card["width"] / card["height"]
    width = max(1, round(min(WIDTH, HEIGHT * ratio) * card["size"] / 100))
    height = max(1, round(width / ratio))
    x = round(max(0, min(WIDTH - width, WIDTH * card["x"] / 100 - width / 2)))
    y = round(max(0, min(HEIGHT - height, HEIGHT * card["y"] / 100 - height / 2)))
    return (graph.removesuffix("[v]") + f"[base];[1:v]scale={width}:{height},format=rgba[card];"
            f"[base][card]overlay=x={x}:y={y}:eof_action=repeat:format=auto,format=yuv420p[v]")


def render(clip, draft, directory, preview=False):
    directory.mkdir(parents=True, exist_ok=True)
    layout = camera_layout(clip, draft)
    clip = {**clip, "layout": layout}
    file = directory / ("preview.mp4" if preview else "export.mp4")
    temporary = file.with_suffix(".part.mp4")
    if preview:
        graph = cl_render.filter_graph(layout, "overlay.ass").replace(
            "ass=filename=overlay.ass:fontsdir=fonts[v]", "scale=540:960[v]")
    else:
        fonts = directory / "fonts"
        fonts.mkdir(exist_ok=True)
        for font in cl_render.FONTS.glob("*.ttf"):
            if not (fonts / font.name).exists():
                shutil.copyfile(font, fonts / font.name)
        make_ass(clip, draft, directory / "overlay.ass")
        graph = cl_render.filter_graph(layout, "overlay.ass")
    source = clip["source_path"]
    args = ["-ss", f"{clip['start']:.3f}", "-t", f"{clip['duration']:.3f}", *video_input(source)]
    if not preview and (card := draft.get("card")):
        graph = card_filter(graph, card)
        args += ["-i", clip["card_path"]]
    args += ["-filter_complex", graph, "-map", "[v]", "-map", "0:a:0", "-r", "30" if preview else cl_render.out_fps(Path(source)),
            "-c:a", "aac", "-b:a", "128k" if preview else "192k", "-movflags", "+faststart"]
    try:
        encoder = ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "23" if preview else "19"]
        try:
            run_ffmpeg([*args, *encoder, temporary], directory)
        except RuntimeError:
            run_ffmpeg([*args, "-c:v", "libx264", "-preset", "fast", "-crf", "23" if preview else "19", temporary], directory)
        os.replace(temporary, file)
    finally:
        temporary.unlink(missing_ok=True)
    return file
