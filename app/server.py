"""Local web editor and AI Stream Clipper job orchestration."""
import json
from hashlib import sha256
from io import BytesIO
import logging
import os
import re
import sqlite3
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from PIL import Image, ImageOps, UnidentifiedImageError

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ["BATINI_CLIP_DATA"]).resolve()
NEON = Path(os.environ["BATINI_CLIP_RUNTIME"]).resolve() if os.environ.get("BATINI_CLIP_RUNTIME") else None
ENGINE = Path(os.environ.get("BATINI_CLIP_ENGINE", str(DATA / "engine"))).resolve()
sys.path.insert(0, str(ENGINE / "scripts"))
DLL_HANDLES = [os.add_dll_directory(path) for path in os.environ.get("BATINI_CUDA", "").split(os.pathsep) if path]

import cl_chunks
import cl_common
import cl_fetch
import cl_layout
import cl_moments
import cl_signals
import cl_transcribe
import editor

cl_common.CONFIG_DIR = DATA / "config"
cl_common.CONFIG_FILE = cl_common.CONFIG_DIR / "config.json"
cl_transcribe.CONFIG_DIR = cl_common.CONFIG_DIR
models = NEON / "data/models/models--Systran--faster-whisper-small/snapshots" if NEON else None
model = os.environ.get("BATINI_CLIP_MODEL") or (next((item.parent for item in models.glob("*/model.bin")), None) if models else None)
try:
    import ctranslate2
    cuda = bool(DLL_HANDLES) and ctranslate2.get_cuda_device_count() > 0
except RuntimeError:
    cuda = False
config = {"device": "cuda" if cuda else "cpu", "compute_type": "int8_float16",
          "model": str(model or "small"), "cpu_model": str(model or "small")}
cl_common.write_json(cl_common.CONFIG_FILE, config)

WORKER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="geracao")
MEDIA_WORKER = ThreadPoolExecutor(max_workers=2, thread_name_prefix="video")
CLIP_LOCK = threading.RLock()
TASK_LOCK = threading.RLock()
LOG = logging.getLogger("uvicorn.error")
app = FastAPI(title="Ninfa Cortes", docs_url=None, redoc_url=None, openapi_url=None)


def connect():
    return sqlite3.connect(DATA / "studio.sqlite3", timeout=20)


def get(kind, key):
    with closing(connect()) as db:
        row = db.execute("SELECT payload FROM records WHERE kind=? AND id=?", (kind, key)).fetchone()
    if not row:
        raise HTTPException(404, "Item não encontrado.")
    return json.loads(row[0])


def records(kind):
    with closing(connect()) as db:
        rows = db.execute("SELECT payload FROM records WHERE kind=? ORDER BY rowid", (kind,)).fetchall()
    return [json.loads(row[0]) for row in rows]


def put(kind, row):
    with closing(connect()) as db, db:
        db.execute("INSERT INTO records VALUES (?, ?, ?) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload",
                   (kind, row["id"], json.dumps(row, ensure_ascii=False)))


def progress(job, message, percentage, **extra):
    job.update(message=message, progress=percentage, **extra)
    put("job", job)


def clip_directory(key):
    return DATA / "clips" / key


def source_reference(path):
    path = Path(path).resolve()
    return path.relative_to(DATA).as_posix() if path.is_relative_to(DATA) else str(path)


def media_path(ref):
    path = Path(ref)
    return path if path.is_absolute() else DATA / path


def card_path(key):
    if not re.fullmatch(r"[0-9a-f]{64}", key):
        raise HTTPException(404, "Imagem não encontrada.")
    return DATA / "social-cards" / f"{key}.png"


def default_card():
    return next((row["card"] for row in records("preference") if row["id"] == "social-card"), None)


def create_clip(job, pick, source, words, layout, order, original=None, key=None):
    start, end = pick["start"], pick["end"]
    clip = {"id": key or uuid.uuid4().hex, "job": job, "title": pick["title"], "start": start,
            "end": end, "duration": round(end - start, 3), "source_path": source_reference(source), "layout": layout,
            "original": source_reference(original) if original else None, "order": order,
            "draft": {"hook": pick.get("hook", ""), "x": 50.0, "y": 65.9, "style": "palavra",
                      "revision": 0, "cues": editor.cue_list(words, start, end), "card": default_card()},
            "preview": "pending", "export": "pending"}
    put("clip", clip)
    return clip


with closing(connect()) as db, db:
    db.execute("CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, payload TEXT, PRIMARY KEY (kind,id))")
for job in records("job"):
    if job["status"] in {"working", "queued"}:
        progress(job, "A geração foi interrompida. Envie o link de novo para tentar novamente.", job["progress"], status="error")
for clip in records("clip"):
    if "working" in (clip["preview"], clip["export"]):
        clip.update({k: "pending" for k in ("preview", "export") if clip[k] == "working"})
        put("clip", clip)


class NewJob(BaseModel):
    url: str | None = Field(default=None, min_length=5, max_length=500)
    path: str | None = Field(default=None, min_length=1, max_length=32767)
    count: int = Field(default=3, ge=1, le=30)


class CameraEdit(BaseModel):
    horizontal: int = Field(default=0, ge=-100, le=100)
    vertical: int = Field(default=0, ge=-100, le=100)
    zoom: int = Field(default=100, ge=100, le=150)
    pan_x: int = Field(default=0, ge=-100, le=100)
    pan_y: int = Field(default=0, ge=-100, le=100)


class CardEdit(BaseModel):
    image: str = Field(pattern=r"^[0-9a-f]{64}$")
    x: float = Field(ge=0, le=100)
    y: float = Field(ge=0, le=100)
    size: float = Field(ge=5, le=80)


class DraftEdit(BaseModel):
    revision: int = Field(ge=0)
    hook: str = Field(max_length=80)
    x: float = Field(ge=12, le=88)
    y: float = Field(ge=12, le=92)
    style: str = Field(pattern=r"^(palavra|frase)$")
    texts: list[str] = Field(max_length=200)
    camera: CameraEdit | None = None
    card: CardEdit | None = None


def video_id(url):
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password or parsed.netloc.lower() not in {
        "youtu.be", "youtube.com", "www.youtube.com", "m.youtube.com"
    }:
        raise HTTPException(400, "Cole um link de vídeo do YouTube.")
    parts = parsed.path.strip("/").split("/")
    if parsed.netloc == "youtu.be" and len(parts) == 1:
        value = parts[0]
    elif parts == ["watch"]:
        value = parse_qs(parsed.query).get("v", [""])[0]
    else:
        value = parts[1] if len(parts) == 2 and parts[0] in {"live", "shorts"} else ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        raise HTTPException(400, "Não encontrei o ID do vídeo nesse link.")
    return value


REACTIONS = re.compile(r"\b(nossa|eita|pera[ií]?|caramba|mentira|imposs[ií]vel|consegui|finalmente|errei|socorro|calma|mano|[uú]ltim[oa]|meu deus|n[aã]o acredito|que isso)\b", re.I)


def automatic_picks(words, spikes, duration, count):
    lines = cl_chunks.lines_from_words(words, max_len=11, gap=.85)
    ranked = []
    for i, line in enumerate(lines):
        start = line["s"]
        trailing = [x for x in lines[i:i + 6] if x["e"] <= start + 49]
        endline = next((x for x in trailing if x["e"] - start >= 25 and x["text"].endswith(("!", ".", "?"))),
                       next((x for x in reversed(trailing) if x["e"] - start >= 15), None))
        if not endline:
            continue
        end = min(duration, endline["e"] + .35)
        text = line["text"]
        if len(set(text.lower().split())) < 3:
            continue
        loud = max((v["z"] for v in spikes if v["speech"] and start <= v["t"] <= end), default=0)
        score = len(REACTIONS.findall(text)) * 3 + text.count("?") + text.count("!") + min(3, loud)
        score += min(1.5, len(set(text.lower().split())) / 12)
        if start < 14 or end > duration - 8:
            score -= 2
        hook = " ".join(text.split()[:5]).strip(" ,.:;")[:48]
        if len(hook.split()) < 2:
            continue
        ranked.append({"start": start, "end": end, "title": hook, "hook": hook[:39].rsplit(" ", 1)[0] if len(hook) > 39 else hook, "score": round(score, 2)})
    chosen = []
    for pick in sorted(ranked, key=lambda x: x["score"], reverse=True):
        if all(pick["end"] + 2 < x["start"] or pick["start"] > x["end"] + 2 for x in chosen):
            s, e, _, _ = cl_moments.snap(words, pick["start"], pick["end"])
            chosen.append({**pick, "start": s, "end": e})
        if len(chosen) == count:
            break
    return chosen


def find_source(id_value):
    jobs = {job["id"] for job in records("job") if job.get("video_id") == id_value}
    for clip in records("clip"):
        if clip["job"] in jobs:
            source = media_path(clip["source_path"])
            if source.is_file():
                return source
    if not NEON:
        return None
    for entry in (NEON / "data").glob("*.request.json"):
        meta = cl_common.read_json(entry)
        try:
            if meta.get("window") is None and video_id(meta.get("url", "")) == id_value:
                source = entry.with_name(entry.name.split(".")[0] + ".mp4")
                if source.is_file():
                    return source
        except HTTPException:
            continue
    return None


def youtube_source(job, directory):
    import yt_dlp

    progress(job, "Lendo o vídeo do YouTube", 5)
    url = f"https://www.youtube.com/watch?v={job['video_id']}"
    meta = cl_fetch.probe_source(url)
    job["title"] = meta["title"] or job["title"]
    source = find_source(job["video_id"])
    if source:
        return source
    progress(job, "Baixando vídeo e áudio", 12)
    opts = cl_common.ytdlp_base_opts()
    opts.update({"format": "bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]",
                 "outtmpl": str(directory / "source.%(ext)s"), "merge_output_format": "mp4",
                 "noplaylist": True, "progress_hooks": [lambda p: download_progress(job, p)]})
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    source = next((p for p in directory.glob("source.*") if p.suffix in {".mp4", ".webm", ".mkv"}), None)
    if not source:
        raise RuntimeError("O YouTube não entregou uma faixa de vídeo utilizável.")
    return source


def generate(job, count):
    directory = DATA / "sources" / job["id"]
    directory.mkdir(parents=True, exist_ok=True)
    try:
        progress(job, "Lendo o vídeo de origem", 5, status="working")
        source = Path(job["local_path"]) if "local_path" in job else youtube_source(job, directory)
        if not source.is_file():
            raise RuntimeError("O arquivo de origem não está mais no local selecionado.")
        duration = cl_common.media_duration(source)
        progress(job, "Transcrevendo em português", 35)
        (directory / "media").mkdir(exist_ok=True)
        wav = directory / "media/audio16k.wav"
        editor.run_ffmpeg(["-i", source, "-vn", "-ac", "1", "-ar", "16000", wav])
        cl_transcribe.transcribe(directory, wav, language="pt", force_cpu=config["device"] == "cpu")
        cl_signals.compute(directory)
        words = cl_common.read_json(directory / "transcript/words.json", [])
        spikes = cl_common.read_json(directory / "signals/spikes.json", [])
        picks = automatic_picks(words, spikes, duration, count)
        if not picks:
            raise RuntimeError("O vídeo não tem fala suficiente para os cortes automáticos.")
        for order, pick in enumerate(picks, 1):
            progress(job, f"Enquadrando corte {order} de {len(picks)}", 65 + int(32 * (order - 1) / len(picks)))
            layout = cl_layout.plan(source, pick["start"], pick["end"] - pick["start"], None)
            clip = create_clip(job["id"], pick, source, words, layout, order)
            if order == 1:
                queue_media(clip["id"], "preview")
        progress(job, f"{len(picks)} cortes disponíveis para edição", 100, status="ready")
    except Exception as error:
        LOG.exception("Falha na geração %s", job["id"])
        progress(job, str(error).splitlines()[0][:260] or "Não foi possível gerar os cortes.", job["progress"], status="error")
    finally:
        (directory / "media/audio16k.wav").unlink(missing_ok=True)


def download_progress(job, status):
    total = status.get("total_bytes") or status.get("total_bytes_estimate") or 0
    received = status.get("downloaded_bytes", 0)
    if total and status.get("status") == "downloading":
        percent = max(job["progress"], 12 + int(21 * min(received / total, 1)))
        if percent != job["progress"]:
            progress(job, "Baixando vídeo e áudio", percent)


def media_work(key, mode, snapshot):
    try:
        source = {**snapshot, "source_path": str(media_path(snapshot["source_path"]))}
        if mode == "export" and (card := snapshot["draft"].get("card")):
            source["card_path"] = card_path(card["image"])
        editor.render(source, snapshot["draft"], clip_directory(key) / mode, preview=mode == "preview")
        status, error = "ready", None
    except Exception as exc:
        LOG.exception("Falha na renderização %s", key)
        status, error = "error", str(exc)[:260]
    with CLIP_LOCK:
        clip = get("clip", key)
        clip[mode] = status
        clip[mode + "_error"] = error
        if status == "ready" and mode == "export":
            clip["export_revision"] = snapshot["draft"]["revision"]
        if status == "ready" and mode == "preview":
            clip["preview_camera_revision"] = snapshot["draft"].get("camera_revision", 0)
        put("clip", clip)


def queue_media(key, mode):
    with TASK_LOCK, CLIP_LOCK:
        if getattr(app.state, "stopping", False):
            raise HTTPException(409, "O estúdio está encerrando.")
        clip = get("clip", key)
        fresh = (clip.get("preview_camera_revision", 0) == clip["draft"].get("camera_revision", 0)) if mode == "preview" else clip.get("export_revision") == clip["draft"]["revision"]
        if clip[mode] == "working" or (clip[mode] == "ready" and fresh):
            return
        clip[mode] = "working"
        put("clip", clip)
    MEDIA_WORKER.submit(media_work, key, mode, clip)


@app.middleware("http")
async def local_only(request: Request, proceed):
    origin = request.headers.get("origin")
    host = request.headers.get("host", "")
    if host.split(":")[0] not in {"127.0.0.1", "localhost"} or (origin and origin not in {f"http://{host}", f"https://{host}"}):
        return JSONResponse({"detail": "Esta interface só aceita pedidos locais."}, status_code=403)
    return await proceed(request)


@app.get("/api/jobs")
def list_jobs():
    return [public_job(job) for job in reversed(records("job")) if not job.get("internal") and not job.get("removed")]


def public_job(job):
    return {key: value for key, value in job.items() if key != "local_path"}


@app.post("/api/jobs", status_code=202)
def new_job(body: NewJob):
    key = uuid.uuid4().hex
    if bool(body.url) == bool(body.path):
        raise HTTPException(400, "Escolha um link do YouTube ou um arquivo local.")
    if body.path:
        raw = Path(body.path)
        try:
            local = raw.is_absolute() and (os.name != "nt" or re.fullmatch(r"[A-Za-z]:", raw.drive))
            source = raw.resolve(strict=True) if local else None
        except (OSError, ValueError):
            source = None
        if not source or not source.is_file() or source.suffix.lower() not in {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".mts", ".m2ts"}:
            raise HTTPException(400, "Selecione um arquivo de vídeo local válido.")
        origin = {"local_path": str(source), "title": source.stem}
    else:
        origin = {"video_id": video_id(body.url), "title": "Novo vídeo"}
    job = {"id": key, **origin, "created": time.time(),
           "status": "queued", "progress": 0, "message": "Na fila de geração"}
    with TASK_LOCK:
        if getattr(app.state, "stopping", False):
            raise HTTPException(409, "O estúdio está encerrando.")
        put("job", job)
        WORKER.submit(generate, job, body.count)
    return public_job(job)


@app.delete("/api/jobs/{key}")
def remove_job(key: str):
    with TASK_LOCK, CLIP_LOCK:
        job = get("job", key)
        if job["status"] in {"queued", "working"} or any(
            clip["job"] == key and "working" in (clip["preview"], clip["export"]) for clip in records("clip")
        ):
            raise HTTPException(409, "Aguarde o processamento antes de remover este vídeo.")
        job["removed"] = True
        put("job", job)
    return {"removed": True}


@app.get("/api/health")
def health():
    return {"app": "batini-cortes", "device": config["device"]}


@app.post("/api/shutdown")
async def shutdown():
    stop = getattr(app.state, "stop", None)
    if not stop:
        raise HTTPException(409, "Abra o aplicativo pelo atalho Ninfa Cortes para poder encerrar aqui.")
    with TASK_LOCK:
        if any(job["status"] in {"working", "queued"} for job in records("job")) or any(
            "working" in (clip["preview"], clip["export"]) for clip in records("clip")
        ):
            raise HTTPException(409, "Ainda há vídeos sendo processados. Aguarde terminar e clique em Encerrar de novo.")
        app.state.stopping = True
        stop()
    return {"stopped": True}


def public_clip(clip):
    return {k: v for k, v in clip.items() if k not in {"source_path", "original", "layout"}}


@app.get("/api/jobs/{key}/clips")
def list_clips(key: str):
    return [{k: v for k, v in public_clip(clip).items() if k != "draft"}
            for clip in sorted(records("clip"), key=lambda c: c["order"]) if clip["job"] == key and not clip.get("removed")]


@app.delete("/api/clips/{key}")
def remove_clip(key: str):
    with TASK_LOCK, CLIP_LOCK:
        clip = get("clip", key)
        if "working" in (clip["preview"], clip["export"]):
            raise HTTPException(409, "Aguarde o processamento antes de remover este corte.")
        clip["removed"] = True
        put("clip", clip)
    return {"removed": True}


@app.get("/api/clips/{key}")
def read_clip(key: str):
    clip = get("clip", key)
    draft = {"camera_revision": 0, "card": None, **clip["draft"]}
    draft["camera"] = {**editor.DEFAULT_CAMERA, **draft.get("camera", {})}
    ranges = [[segment["t0"], segment["t1"]] for segment in clip["layout"]["segments"] if segment["mode"] == "facecam"]
    return {**public_clip(clip), "draft": draft, "camera_ranges": ranges,
            "preview_camera_revision": clip.get("preview_camera_revision", 0), "events": caption_events_for(clip),
            "default_card": default_card()}


def caption_events_for(clip):
    return editor.caption_events(clip["draft"])


@app.put("/api/clips/{key}/draft")
async def save(key: str, body: DraftEdit):
    with CLIP_LOCK:
        clip = get("clip", key)
        draft = clip["draft"]
        if body.revision != draft["revision"]:
            raise HTTPException(409, "O rascunho foi alterado em outra aba. Atualize a página.")
        if len(body.texts) != len(draft["cues"]) or any(len(text) > 150 for text in body.texts):
            raise HTTPException(400, "Cada trecho da legenda pode ter até 150 caracteres.")
        if body.camera:
            set_camera(draft, {**editor.DEFAULT_CAMERA, **draft.get("camera", {}), **body.camera.model_dump(exclude_unset=True)})
        if "card" in body.model_fields_set:
            if body.card:
                file = card_path(body.card.image)
                if not file.is_file():
                    raise HTTPException(404, "Imagem não encontrada.")
                with Image.open(file) as image:
                    draft["card"] = {**body.card.model_dump(), "width": image.width, "height": image.height}
            else:
                draft["card"] = None
        draft.update(hook=body.hook.strip(), x=body.x, y=body.y, style=body.style, revision=draft["revision"] + 1)
        for cue, text in zip(draft["cues"], body.texts):
            cue["text"] = text.strip()
        put("clip", clip)
    return read_clip(key)


@app.post("/api/social-cards")
async def upload_card(request: Request):
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > 8 * 1024 * 1024:
            raise HTTPException(413, "A imagem pode ter até 8 MB.")
        data.extend(chunk)
    try:
        with Image.open(BytesIO(data), formats=["PNG", "JPEG", "WEBP"]) as original:
            if original.width * original.height > 16_000_000 or getattr(original, "is_animated", False):
                raise HTTPException(400, "Use uma imagem estática de até 16 megapixels.")
            image = ImageOps.exif_transpose(original).convert("RGBA")
            image.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as error:
        raise HTTPException(400, "Escolha uma imagem PNG, JPG ou WebP válida.") from error
    encoded = BytesIO()
    image.save(encoded, format="PNG")
    payload = encoded.getvalue()
    key = sha256(payload).hexdigest()
    file = card_path(key)
    file.parent.mkdir(parents=True, exist_ok=True)
    temporary = file.with_suffix(f".{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(payload)
        os.replace(temporary, file)
    finally:
        temporary.unlink(missing_ok=True)
    return {"image": key, "width": image.width, "height": image.height}


@app.get("/api/social-cards/{key}")
def social_card(key: str):
    file = card_path(key)
    if not file.is_file():
        raise HTTPException(404, "Imagem não encontrada.")
    return FileResponse(file, media_type="image/png", headers={"Cache-Control": "private, max-age=31536000, immutable"})


@app.post("/api/clips/{key}/card-batch")
def apply_card_to_batch(key: str):
    with CLIP_LOCK, closing(connect()) as db, db:
        source = get("clip", key)
        card = source["draft"].get("card")
        if not card:
            raise HTTPException(400, "Adicione um card primeiro.")
        changes = []
        for clip in records("clip"):
            if (clip["job"] == source["job"] and clip["id"] != key and not clip.get("removed")
                    and clip["draft"].get("card") != card):
                clip["draft"]["card"] = card
                clip["draft"]["revision"] += 1
                changes.append((json.dumps(clip, ensure_ascii=False), clip["id"]))
        db.executemany("UPDATE records SET payload=? WHERE kind='clip' AND id=?", changes)
    return {"updated": len(changes)}


@app.put("/api/clips/{key}/card-default")
def set_default_card(key: str, enabled: bool):
    with CLIP_LOCK:
        card = get("clip", key)["draft"].get("card") if enabled else None
        if enabled and not card:
            raise HTTPException(400, "Adicione um card primeiro.")
        put("preference", {"id": "social-card", "card": card})
    return {"card": card}


def set_camera(draft, camera):
    if {**editor.DEFAULT_CAMERA, **draft.get("camera", {})} == {**editor.DEFAULT_CAMERA, **camera}:
        return False
    draft["camera"] = camera
    draft["camera_revision"] = draft.get("camera_revision", 0) + 1
    return True


@app.post("/api/clips/{key}/camera-batch")
async def apply_camera_to_batch(key: str):
    with CLIP_LOCK:
        source = get("clip", key)
        camera = source["draft"].get("camera", editor.DEFAULT_CAMERA)
        changed = 0
        for clip in records("clip"):
            if clip["id"] == key or clip["job"] != source["job"] or clip.get("removed") or not any(
                s["mode"] == "facecam" for s in clip["layout"]["segments"]
            ):
                continue
            if set_camera(clip["draft"], camera):
                clip["draft"]["revision"] += 1
                put("clip", clip)
                changed += 1
    return {"updated": changed}


@app.get("/api/clips/{key}/camera-frame")
def get_camera_frame(key: str, at: float = Query(ge=0, le=180), horizontal: int = Query(0, ge=-100, le=100),
                     vertical: int = Query(0, ge=-100, le=100), zoom: int = Query(100, ge=100, le=150),
                     pan_x: int = Query(0, ge=-100, le=100), pan_y: int = Query(0, ge=-100, le=100)):
    clip = get("clip", key)
    camera = CameraEdit(horizontal=horizontal, vertical=vertical, zoom=zoom, pan_x=pan_x, pan_y=pan_y).model_dump()
    try:
        source = {**clip, "source_path": str(media_path(clip["source_path"]))}
        frame = editor.camera_frame(source, camera, at)
    except Exception:
        LOG.exception("Falha ao obter quadro da câmera %s", key)
        raise HTTPException(503, "Não consegui carregar o quadro da câmera.")
    if not frame:
        raise HTTPException(404, "A câmera separada não aparece neste momento.")
    return Response(frame, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@app.post("/api/clips/{key}/{mode}", status_code=202)
async def request_media(key: str, mode: str):
    if mode not in {"preview", "export"}:
        raise HTTPException(404)
    queue_media(key, mode)
    return public_clip(get("clip", key))


@app.get("/api/clips/{key}/media/{mode}")
def send_media(key: str, mode: str, download: bool = False):
    clip = get("clip", key)
    if mode not in {"preview", "export", "original"}:
        raise HTTPException(404)
    file = media_path(clip["original"] or "") if mode == "original" else clip_directory(key) / mode / f"{mode}.mp4"
    if not file.is_file():
        raise HTTPException(404, "O vídeo ainda não está pronto.")
    if mode == "export" and clip.get("export_revision") != clip["draft"]["revision"]:
        raise HTTPException(409, "Exporte novamente para incluir as alterações do rascunho.")
    filename = f"{clip['order']:02}-{cl_common.slug(clip['title'])}.mp4" if download else None
    return FileResponse(file, media_type="video/mp4", filename=filename, headers={"Cache-Control": "no-store"})


@app.get("/api/clips/{key}/poster")
def poster(key: str):
    clip = get("clip", key)
    directory = clip_directory(key)
    file = directory / "poster.jpg"
    if not file.is_file():
        directory.mkdir(parents=True, exist_ok=True)
        original = media_path(clip["original"]) if clip["original"] else None
        use_original = original and original.is_file()
        src = original if use_original else media_path(clip["source_path"])
        offset = min(4, clip["duration"] / 3) + (0 if use_original else clip["start"])
        temporary = directory / f"{uuid.uuid4().hex}.jpg"
        try:
            editor.run_ffmpeg(["-ss", str(offset), *cl_common.video_input(src), "-frames:v", "1", "-vf", "scale=240:-2", temporary])
            os.replace(temporary, file)
        finally:
            temporary.unlink(missing_ok=True)
    return FileResponse(file, media_type="image/jpeg")


@app.get("/")
def studio_page():
    return FileResponse(ROOT / "web/index.html", headers={"Cache-Control": "no-store"})


app.mount("/fonts", StaticFiles(directory=ENGINE / "assets/fonts"))
app.mount("/", StaticFiles(directory=ROOT / "web", html=True))
