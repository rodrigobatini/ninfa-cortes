const $ = (id) => document.getElementById(id);
const video = $("video");
const timeline = $("timeline");
const caption = $("caption-preview");
const cardPreview = $("card-preview");
const webview = window.chrome?.webview;
let localVideo = null;
let jobs = [], clips = [], currentJob = null, clip = null, events = [];
let stopped = false;
let dirty = false, saving = null, saveTimer = null, warningTimer = null;
let revision = 0, editVersion = 0, selectedRequest = 0, mediaSource = "", activeCue = -1;
let activeTab = "captions", cameraTimer = null, cameraRequest = 0;
const cameraDefaults = { horizontal: 0, vertical: 0, zoom: 100 };

async function api(path, method = "GET", body) {
  const response = await fetch(`/api/${path}`, {
    method, cache: "no-store", headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const json = await response.json();
  if (!response.ok) throw new Error(typeof json.detail === "string" ? json.detail : "Confira os campos e tente novamente.");
  return json;
}

function message(text) {
  const notice = $("notice");
  notice.textContent = text;
  notice.hidden = !text;
  clearTimeout(warningTimer);
  if (text) warningTimer = setTimeout(() => { notice.hidden = true; }, 7000);
}

function time(seconds) {
  const whole = Math.floor(Number(seconds) || 0);
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor(whole / 60) % 60;
  return `${hours ? `${hours}:${String(minutes).padStart(2, "0")}` : minutes}:${String(whole % 60).padStart(2, "0")}`;
}

function listProjects() {
  $("projects").replaceChildren(...jobs.map(job => new Option(job.title, job.id)));
  if (currentJob) $("projects").value = currentJob;
  const job = jobs.find(row => row.id === currentJob);
  $("remove-project").hidden = !job;
  $("remove-project").disabled = ["queued", "working"].includes(job?.status);
  $("job-progress").hidden = !job || job.status === "ready";
  if (job) {
    $("progress").value = job.progress;
    $("progress").hidden = job.status === "error";
    $("job-message").textContent = job.message;
  }
}

function listClips() {
  $("clip-count").textContent = clips.length ? `${clips.length} ${clips.length === 1 ? "corte" : "cortes"}` : "";
  const cards = clips.map(item => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `clip-card${clip?.id === item.id ? " selected" : ""}`;
    button.setAttribute("aria-pressed", String(clip?.id === item.id));
    const image = document.createElement("img");
    image.src = `/api/clips/${item.id}/poster`;
    image.alt = "";
    image.loading = "lazy";
    const text = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = item.title;
    const stamp = document.createElement("span");
    stamp.textContent = `${String(item.order).padStart(2, "0")}  ·  ${time(item.start)}  ·  ${Math.round(item.duration)} s`;
    text.append(title, stamp);
    button.append(image, text);
    button.onclick = () => selectClip(item.id).catch(error => message(error.message));
    const row = document.createElement("div");
    row.className = "clip-row";
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "icon-remove";
    remove.textContent = "×";
    remove.title = "Remover corte";
    remove.setAttribute("aria-label", `Remover corte: ${item.title}`);
    remove.onclick = () => removeClip(item).catch(error => message(error.message));
    row.append(button, remove);
    return row;
  });
  if (!cards.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    const job = jobs.find(row => row.id === currentJob);
    empty.textContent = !job ? "Escolha um vídeo para começar." :
      job.status === "ready" ? "Nenhum corte nesta lista." : "Os cortes aparecem assim que estiverem prontos.";
    cards.push(empty);
  }
  $("clip-list").replaceChildren(...cards);
}

async function selectProject(key) {
  await flush();
  currentJob = key;
  clip = null;
  selectedRequest++;
  video.pause();
  video.removeAttribute("src");
  video.load();
  mediaSource = "";
  $("clip-title").textContent = "Selecione um corte";
  $("inspector-content").hidden = true;
  $("camera-panel").hidden = true;
  $("card-panel").hidden = true;
  cardPreview.hidden = true;
  $("edit-tabs").hidden = true;
  hideCameraFrame();
  $("controls").hidden = true;
  $("play-screen").hidden = true;
  $("hook-preview").hidden = true;
  $("export").disabled = true;
  $("download").hidden = true;
  caption.textContent = "";
  setVideoStatus("Escolha um corte para revisar.");
  listProjects();
  clips = key ? await api(`jobs/${key}/clips`) : [];
  listClips();
  if (clips.length) await selectClip(clips[0].id);
}

async function removeClip(item) {
  if (!confirm(`Remover o corte “${item.title}” da lista?`)) return;
  await flush();
  const project = currentJob;
  await api(`clips/${item.id}`, "DELETE");
  if (project !== currentJob) return;
  if (clip?.id === item.id) await selectProject(project);
  else {
    clips = clips.filter(row => row.id !== item.id);
    listClips();
  }
}

$("remove-project").onclick = async () => {
  const job = jobs.find(row => row.id === currentJob);
  if (!job || !confirm(`Remover “${job.title}” e seus cortes da biblioteca? Os arquivos originais ficam no computador.`)) return;
  try {
    await flush();
    await api(`jobs/${job.id}`, "DELETE");
    jobs = jobs.filter(row => row.id !== job.id);
    if (currentJob === job.id) await selectProject(jobs[0]?.id ?? null);
    else listProjects();
  } catch (error) { message(error.message); }
};

async function selectClip(key) {
  if (clip?.id === key) return;
  await flush();
  const request = ++selectedRequest;
  const item = await api(`clips/${key}`);
  if (request !== selectedRequest) return;
  clip = item;
  revision = item.draft.revision;
  editVersion = 0;
  events = item.events;
  dirty = false;
  activeCue = -1;
  mediaSource = "";
  video.pause();
  video.removeAttribute("src");
  video.load();
  $("clip-title").textContent = item.title;
  $("hook").value = item.draft.hook;
  $("style").value = item.draft.style;
  $("x").value = item.draft.x;
  $("y").value = item.draft.y;
  $("duration").textContent = time(item.duration);
  $("current").textContent = "0:00";
  timeline.max = item.duration;
  timeline.value = 0;
  $("controls").hidden = false;
  $("edit-tabs").hidden = false;
  for (const [name, value] of Object.entries(item.draft.camera)) $("camera-" + name).value = value;
  $("camera-status").textContent = "";
  $("card-status").textContent = "";
  if (item.draft.card) {
    for (const key of ["x", "y", "size"]) $("card-" + key).value = item.draft.card[key];
  }
  drawCard();
  $("save-state").textContent = "";
  showCues();
  listClips();
  setVideoStatus("Preparando a prévia limpa…");
  updateMedia(item);
  setPosition();
  setEditTab(activeTab);
  drawFrame();
  if (item.preview !== "ready" && item.preview !== "working") {
    const update = await api(`clips/${key}/preview`, "POST");
    if (request === selectedRequest) updateMedia(update);
  }
}

function showCues() {
  $("cue-count").textContent = `${clip.draft.cues.length} trechos`;
  $("cue-list").replaceChildren(...clip.draft.cues.map((cue, index) => {
    const row = document.createElement("div");
    row.className = "cue";
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = time(cue.start);
    button.title = `Ir para ${time(cue.start)}`;
    button.onclick = () => {
      video.pause();
      if (mediaSource) video.currentTime = cue.start + .02;
      timeline.value = cue.start;
      drawFrame(cue.start + .02);
      row.querySelector("textarea").focus();
    };
    const input = document.createElement("textarea");
    input.rows = 1;
    input.maxLength = 150;
    input.value = cue.text;
    input.setAttribute("aria-label", `Legenda ${index + 1}, em ${time(cue.start)}`);
    input.oninput = () => {
      cue.text = input.value;
      markDirty();
      if (video.paused) {
        if (mediaSource) video.currentTime = cue.start + .02;
        drawFrame(cue.start + .02, input.value);
      }
    };
    row.append(button, input);
    return row;
  }));
}

function setVideoStatus(text) {
  $("video-status").textContent = text;
  $("video-status").hidden = !text;
}

function updateMedia(item) {
  if (!clip || item.id !== clip.id) return;
  for (const name of ["preview", "export", "export_revision", "preview_error", "export_error"]) clip[name] = item[name];
  clip.preview_camera_revision = item.preview_camera_revision ?? clip.preview_camera_revision ?? 0;
  if (clip.preview === "ready") {
    const url = `/api/clips/${clip.id}/media/preview?r=${clip.preview_camera_revision}`;
    if (mediaSource !== url) {
      const position = video.currentTime || Number(timeline.value) || 0;
      mediaSource = url;
      video.addEventListener("loadedmetadata", () => {
        if (mediaSource === url && position) video.currentTime = Math.min(position, video.duration - .04);
      }, { once: true });
      video.src = mediaSource;
    }
    setVideoStatus("");
    $("play-screen").hidden = !video.paused;
  } else if (clip.preview === "error") {
    setVideoStatus("Falha na prévia. Clique para tentar novamente.");
    $("video-status").onclick = async () => {
      setVideoStatus("Preparando a prévia limpa…");
      try { updateMedia(await api(`clips/${clip.id}/preview`, "POST")); } catch (error) { message(error.message); }
    };
  }
  if (clip.export === "error") $("export").title = clip.export_error || "Tente exportar novamente";
  $("export").disabled = clip.export === "working";
  $("export").textContent = clip.export === "working" ? "Exportando…" : "Exportar MP4";
  const ready = clip.export === "ready" && clip.export_revision === revision && !dirty;
  $("download").hidden = !ready;
  if (ready) $("download").href = `/api/clips/${clip.id}/media/export?download=true`;
  cameraStatus();
}

function readCamera() {
  return Object.fromEntries(Object.keys(cameraDefaults).map(key => [key, Number($("camera-" + key).value)]));
}

function cameraStatus() {
  const settings = readCamera();
  for (const [key, value] of Object.entries(settings)) $("camera-" + key + "-value").value = `${value > 0 && key !== "zoom" ? "+" : ""}${value}%`;
  if (!clip) return;
  const stale = clip.preview_camera_revision !== clip.draft.camera_revision;
  $("refresh-camera").disabled = clip.preview === "working" || (!stale && clip.preview === "ready");
  $("refresh-camera").textContent = clip.preview === "working" ? "Atualizando…" : "Atualizar prévia";
}

function hideCameraFrame() {
  clearTimeout(cameraTimer);
  cameraRequest++;
  $("camera-preview").hidden = true;
}

function drawCameraFrame(at = video.currentTime, delay = 0) {
  clearTimeout(cameraTimer);
  const req = ++cameraRequest;
  if (activeTab !== "camera" || !video.paused || !clip?.camera_ranges?.some(([start, end]) => start <= at && at < end)) {
    $("camera-preview").hidden = true;
    return;
  }
  const key = clip.id;
  const query = new URLSearchParams({ at: Math.max(0, at).toFixed(2), ...readCamera() });
  cameraTimer = setTimeout(() => {
    const img = new Image();
    img.onload = () => {
      if (req !== cameraRequest || clip?.id !== key || !video.paused) return;
      $("camera-preview").src = img.src;
      $("camera-preview").hidden = false;
    };
    img.onerror = () => { if (req === cameraRequest) $("camera-preview").hidden = true; };
    img.src = `/api/clips/${key}/camera-frame?${query}`;
  }, delay);
}

function setEditTab(tab) {
  activeTab = tab;
  const camera = tab === "camera";
  $("tab-camera").setAttribute("aria-pressed", String(camera));
  $("tab-captions").setAttribute("aria-pressed", String(tab === "captions"));
  $("tab-card").setAttribute("aria-pressed", String(tab === "card"));
  $("inspector-content").hidden = tab !== "captions" || !clip;
  $("camera-panel").hidden = !camera || !clip;
  $("card-panel").hidden = tab !== "card" || !clip;
  cardPreview.classList.toggle("is-editing", tab === "card");
  $("viewer-hint").textContent = camera ? "Ajuste a câmera com o vídeo pausado" :
    tab === "card" ? "Arraste o card na prévia" : "Arraste a legenda na prévia";
  const ranges = clip?.camera_ranges || [];
  $("camera-controls").hidden = ranges.length === 0;
  $("camera-unavailable").hidden = ranges.length > 0;
  if (camera && ranges.length) {
    video.pause();
    if (mediaSource && !ranges.some(([a, b]) => a <= video.currentTime && video.currentTime < b)) {
      video.currentTime = ranges[0][0] + .1;
    } else drawCameraFrame();
  } else hideCameraFrame();
  cameraStatus();
}

function drawCard() {
  const card = clip?.draft.card;
  cardPreview.hidden = !card;
  $("card-controls").hidden = !card;
  $("clear-default-card").hidden = !clip?.default_card;
  if (!card) {
    cardPreview.removeAttribute("src");
    return;
  }
  const source = `/api/social-cards/${card.image}`;
  if (cardPreview.getAttribute("src") !== source) cardPreview.src = source;
  const ratio = card.width / card.height;
  const width = Math.min(card.size, card.size * 16 / 9 * ratio);
  const height = width * 9 / 16 / ratio;
  cardPreview.style.width = `${width}%`;
  cardPreview.style.left = `${Math.max(width / 2, Math.min(100 - width / 2, card.x))}%`;
  cardPreview.style.top = `${Math.max(height / 2, Math.min(100 - height / 2, card.y))}%`;
  $("card-size-value").value = `${card.size}%`;
}

function setPosition() {
  for (const key of ["x", "y"]) $(`${key}-value`).value = `${Math.round(Number($(key).value))}%`;
  caption.style.left = `${$("x").value}%`;
  caption.style.top = `${$("y").value}%`;
}

function drawFrame(t = video.currentTime || 0, override = null) {
  if (!clip) return;
  $("current").textContent = time(t);
  timeline.value = t;
  const hook = $("hook-preview");
  hook.textContent = $("hook").value;
  hook.hidden = t >= 2.6 || !hook.textContent;
  const e = events.find(hit => hit.start <= t && hit.end > t);
  const cueIndex = clip.draft.cues.findIndex(c => c.start <= t && c.end > t);
  if (cueIndex !== activeCue) {
    $("cue-list").children[activeCue]?.classList.remove("active");
    $("cue-list").children[cueIndex]?.classList.add("active");
    activeCue = cueIndex;
  }
  caption.replaceChildren();
  caption.classList.toggle("drag-hint", !e && override === null && video.paused);
  if (override !== null) {
    caption.style.fontSize = "4.8cqw";
    caption.textContent = override;
  } else if (e) {
    caption.style.fontSize = `${e.size / 10.8}cqw`;
    e.lines.forEach((line, index) => {
      if (index) caption.append(e.active < 0 ? document.createElement("br") : document.createTextNode(" "));
      const word = document.createElement("span");
      word.textContent = line;
      if (index === e.active) word.className = "active";
      caption.append(word);
    });
  } else if (video.paused) caption.textContent = "Posição da legenda";
}

function markDirty() {
  if (!clip) return;
  dirty = true;
  editVersion++;
  $("save-state").textContent = "Salvando…";
  $("download").hidden = true;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => flush().catch(error => message(error.message)), 360);
}

function flush() {
  clearTimeout(saveTimer);
  if (!saving && dirty && clip) saving = saveChanges().finally(() => { saving = null; });
  return saving || Promise.resolve();
}

async function saveChanges() {
  try {
    while (dirty && clip) {
      const key = clip.id;
      const version = editVersion;
      const body = { revision, hook: $("hook").value, x: Number($("x").value), y: Number($("y").value),
        style: $("style").value, texts: clip.draft.cues.map(c => c.text), camera: readCamera(), card: clip.draft.card ?? null };
      const result = await api(`clips/${key}/draft`, "PUT", body);
      if (!clip || clip.id !== key) return;
      revision = result.draft.revision;
      clip.draft.camera_revision = result.draft.camera_revision;
      if (editVersion === version) {
        dirty = false;
        events = result.events;
        $("save-state").textContent = "Salvo";
        updateMedia(result);
        drawFrame();
      }
    }
  } catch (error) {
    $("save-state").textContent = "Falha ao salvar";
    throw error;
  }
}

function selectLocalVideo(path) {
  localVideo = path;
  const source = $("source-url");
  source.type = path ? "text" : "url";
  source.value = path ? path.replaceAll("\\", "/").split("/").at(-1) : "";
  source.readOnly = Boolean(path);
  source.required = !path;
  source.setAttribute("aria-label", path ? "Arquivo local selecionado" : "Link do vídeo no YouTube");
  $("clear-local").hidden = !path;
  $("pick-local").hidden = !webview || Boolean(path);
}

if (webview) {
  $("pick-local").hidden = false;
  $("pick-local").onclick = () => webview.postMessage("pick-local-video");
  $("clear-local").onclick = () => selectLocalVideo(null);
  webview.addEventListener("message", (event) => {
    if (typeof event.data === "string" && event.data.startsWith("local-video:")) {
      selectLocalVideo(event.data.slice("local-video:".length));
    }
  });
}

$("new-job").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  try {
    await flush();
    const source = localVideo ? { path: localVideo } : { url: $("source-url").value };
    const job = await api("jobs", "POST", { ...source, count: Number($("count").value) });
    jobs.unshift(job);
    selectLocalVideo(null);
    await selectProject(job.id);
  } catch (error) { message(error.message); }
  finally { button.disabled = false; }
});
$("projects").onchange = (event) => selectProject(event.target.value).catch(error => message(error.message));
$("tab-captions").onclick = () => setEditTab("captions");
$("tab-camera").onclick = () => setEditTab("camera");
$("tab-card").onclick = () => setEditTab("card");
$("card-file").onchange = async (event) => {
  const input = event.currentTarget;
  const file = input.files[0];
  const key = clip?.id;
  if (!file || !key) return;
  input.disabled = true;
  try {
    const response = await fetch("/api/social-cards", { method: "POST", body: file });
    const image = await response.json();
    if (!response.ok) throw new Error(image.detail || "Não foi possível carregar a imagem.");
    if (clip?.id !== key) return;
    const { x = 82, y = 16, size = 28 } = clip.draft.card ?? {};
    clip.draft.card = { ...image, x, y, size };
    for (const name of ["x", "y", "size"]) $("card-" + name).value = clip.draft.card[name];
    $("card-status").textContent = "";
    drawCard();
    markDirty();
  } catch (error) { message(error.message); }
  finally { input.value = ""; input.disabled = false; }
};
for (const key of ["x", "y", "size"]) $("card-" + key).oninput = (event) => {
  if (!clip?.draft.card) return;
  clip.draft.card[key] = Number(event.currentTarget.value);
  drawCard();
  markDirty();
};
$("remove-card").onclick = () => {
  if (!clip) return;
  clip.draft.card = null;
  $("card-status").textContent = "";
  drawCard();
  markDirty();
};
async function applyCard(button, path, method, status) {
  const key = clip?.id;
  if (!key) return;
  button.disabled = true;
  try {
    await flush();
    const result = await api(`clips/${key}/${path}`, method);
    if (clip?.id !== key) return;
    if ("card" in result) clip.default_card = result.card;
    $("card-status").textContent = status(result);
    drawCard();
  } catch (error) { message(error.message); }
  finally { button.disabled = false; }
}
$("copy-card").onclick = (event) => applyCard(event.currentTarget, "card-batch", "POST",
  ({ updated }) => updated ? `Card aplicado a ${updated} corte(s) deste vídeo.` : "Todos os cortes deste vídeo já usam esse card.");
$("save-default-card").onclick = (event) => applyCard(event.currentTarget, "card-default?enabled=true", "PUT",
  () => "Os próximos cortes começam com este card.");
$("clear-default-card").onclick = (event) => applyCard(event.currentTarget, "card-default?enabled=false", "PUT",
  () => "Card padrão desativado.");
let cardDrag = null;
cardPreview.onpointerdown = (event) => {
  if (!clip?.draft.card || activeTab !== "card") return;
  const x = Number.parseFloat(cardPreview.style.left);
  const y = Number.parseFloat(cardPreview.style.top);
  cardDrag = { x, y, key: clip.id, startX: event.clientX, startY: event.clientY };
  cardPreview.setPointerCapture(event.pointerId);
  event.preventDefault();
};
cardPreview.onpointermove = (event) => {
  if (!cardDrag || clip?.id !== cardDrag.key || !clip.draft.card) return;
  const { width, height } = $("phone").getBoundingClientRect();
  clip.draft.card.x = Math.max(0, Math.min(100, cardDrag.x + (event.clientX - cardDrag.startX) / width * 100));
  clip.draft.card.y = Math.max(0, Math.min(100, cardDrag.y + (event.clientY - cardDrag.startY) / height * 100));
  $("card-x").value = clip.draft.card.x;
  $("card-y").value = clip.draft.card.y;
  drawCard();
};
cardPreview.onpointerup = cardPreview.onpointercancel = () => {
  if (cardDrag?.key === clip?.id) markDirty();
  cardDrag = null;
};
for (const key of Object.keys(cameraDefaults)) $("camera-" + key).oninput = () => {
  video.pause();
  cameraStatus();
  drawCameraFrame(video.currentTime, 135);
  markDirty();
};
$("reset-camera").onclick = () => {
  for (const [key, value] of Object.entries(cameraDefaults)) $("camera-" + key).value = value;
  cameraStatus();
  drawCameraFrame();
  markDirty();
};
async function refreshCamera() {
  if (!clip) return;
  const key = clip.id;
  await flush();
  updateMedia(await api(`clips/${key}/preview`, "POST"));
}
$("refresh-camera").onclick = () => refreshCamera().catch(error => message(error.message));
$("apply-camera").onclick = async () => {
  if (!clip) return;
  const key = clip.id;
  const button = $("apply-camera");
  button.disabled = true;
  try {
    await flush();
    const result = await api(`clips/${key}/camera-batch`, "POST");
    if (clip?.id === key) $("camera-status").textContent = result.updated ? `Enquadramento copiado para ${result.updated} ${result.updated === 1 ? "corte" : "cortes"}.` : "Os outros cortes já usam esse ajuste ou não têm facecam separada.";
  } catch (error) { message(error.message); }
  finally { button.disabled = false; }
};
$("style").onchange = markDirty;
$("hook").oninput = () => { markDirty(); drawFrame(); };
for (const key of ["x", "y"]) $(key).oninput = () => { setPosition(); markDirty(); };
$("reset-position").onclick = () => {
  $("x").value = 50;
  $("y").value = 65.9;
  setPosition();
  markDirty();
};
let pointer = null;
caption.onpointerdown = (event) => {
  if (!clip) return;
  pointer = { x: event.clientX, y: event.clientY, ox: Number($("x").value), oy: Number($("y").value) };
  caption.setPointerCapture(event.pointerId);
  event.preventDefault();
};
caption.onpointermove = (event) => {
  if (!pointer) return;
  const phone = $("phone").getBoundingClientRect();
  $("x").value = Math.min(88, Math.max(12, pointer.ox + (event.clientX - pointer.x) / phone.width * 100));
  $("y").value = Math.min(92, Math.max(12, pointer.oy + (event.clientY - pointer.y) / phone.height * 100));
  setPosition();
};
caption.onpointerup = caption.onpointercancel = () => { if (pointer) markDirty(); pointer = null; };
caption.onkeydown = (event) => {
  const moves = { ArrowLeft: ["x", -1], ArrowRight: ["x", 1], ArrowUp: ["y", -1], ArrowDown: ["y", 1] };
  if (!clip || !moves[event.key]) return;
  const [key, delta] = moves[event.key];
  const input = $(key);
  input.value = Math.max(Number(input.min), Math.min(Number(input.max), Number(input.value) + delta));
  setPosition(); markDirty(); event.preventDefault();
};

function play() {
  if (!mediaSource) return;
  if (video.paused && clip && (dirty || clip.preview_camera_revision !== clip.draft.camera_revision)) {
    flush().then(() => clip.preview_camera_revision !== clip.draft.camera_revision ? refreshCamera() : video.play()).catch(error => message(error.message));
  } else if (video.paused) video.play().catch(error => message(error.message));
  else video.pause();
}
$("play").onclick = $("play-screen").onclick = play;
video.onclick = play;
video.addEventListener("play", () => { hideCameraFrame(); $("play").textContent = "Ⅱ"; $("play").ariaLabel = "Pausar"; $("play-screen").hidden = true; drawFrame(); });
video.addEventListener("pause", () => { $("play").textContent = "▶"; $("play").ariaLabel = "Reproduzir"; drawFrame(); drawCameraFrame(); });
video.addEventListener("seeked", () => { drawFrame(); drawCameraFrame(); });
video.addEventListener("timeupdate", () => drawFrame());
if (video.requestVideoFrameCallback) {
  const update = () => { if (!video.paused) drawFrame(); video.requestVideoFrameCallback(update); };
  video.requestVideoFrameCallback(update);
}
timeline.oninput = () => { if (mediaSource) video.currentTime = Number(timeline.value); drawFrame(Number(timeline.value)); };
$("export").onclick = async () => {
  if (!clip) return;
  const key = clip.id;
  try { await flush(); updateMedia(await api(`clips/${key}/export`, "POST")); }
  catch (error) { message(error.message); }
};

$("shutdown").onclick = async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    await flush();
    await api("shutdown", "POST");
    stopped = true;
    video.pause();
    video.removeAttribute("src");
    video.load();
    hideCameraFrame();
    message("");
    document.querySelector(".bar").hidden = true;
    document.querySelector(".studio").hidden = true;
    $("studio-stopped").hidden = false;
    window.chrome?.webview?.postMessage("studio-stopped");
  } catch (error) {
    message(error.message);
    button.disabled = false;
    window.chrome?.webview?.postMessage("studio-stop-failed");
  }
};

let polling = false;
async function poll() {
  if (stopped || polling) return;
  const job = jobs.find(row => row.id === currentJob);
  const waiting = job && ["working", "queued"].includes(job.status);
  if (!waiting && ![clip?.preview, clip?.export].includes("working")) return;
  polling = true;
  try {
    if (waiting) {
      const key = currentJob;
      jobs = await api("jobs");
      listProjects();
      const found = await api(`jobs/${key}/clips`);
      if (currentJob === key && found.length !== clips.length) {
        clips = found;
        listClips();
        if (!clip && clips.length) await selectClip(clips[0].id);
      }
    }
    if (clip && [clip.preview, clip.export].includes("working")) {
      const old = clip.export;
      const detail = await api(`clips/${clip.id}`);
      updateMedia(detail);
      if (detail.id === clip?.id && old === "working" && detail.export === "error") message(detail.export_error || "Não foi possível exportar.");
    }
  } catch (error) { message(error.message); }
  finally { polling = false; }
}

async function start() {
  jobs = await api("jobs");
  await selectProject(jobs[0]?.id || null);
  setInterval(poll, 1400);
}
start().catch(error => message(error.message));
