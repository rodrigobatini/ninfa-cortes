const languageKey = "ninfa-language";
const english = (() => {
  try { return localStorage.getItem(languageKey) === "en-US"; } catch { return false; }
})();
const stringsEN = {
  "Idioma": "Language",
  "Link do vídeo no YouTube": "YouTube video link",
  "Cole o link do YouTube": "Paste a YouTube link",
  "Arquivo local": "Local file",
  "Usar link": "Use a link",
  "Arquivo local selecionado": "Selected local file",
  "Quantidade de cortes": "Clip count",
  "Quantidade máxima de cortes": "Maximum number of clips",
  "{n} corte": "{n} clip",
  "{n} cortes": "{n} clips",
  "Gerar cortes": "Generate clips",
  "Modo escuro": "Dark mode",
  "Alternar tema": "Toggle theme",
  "Usar tema claro": "Use light theme",
  "Usar tema escuro": "Use dark theme",
  "Encerrar": "Quit",
  "Biblioteca de cortes": "Clip library",
  "Vídeo de origem": "Source video",
  "Remover vídeo selecionado": "Remove selected video",
  "Remover vídeo da biblioteca": "Remove video from library",
  "Cortes": "Clips",
  "Remover corte": "Remove clip",
  "Remover corte: {title}": "Remove clip: {title}",
  "Remover o corte “{title}” da lista?": "Remove “{title}” from the list?",
  "Remover “{title}” e seus cortes da biblioteca? Os arquivos originais ficam no computador.":
    "Remove “{title}” and its clips from the library? The original files stay on your computer.",
  "Escolha um vídeo para começar.": "Choose a video to get started.",
  "Nenhum corte nesta lista.": "No clips in this list.",
  "Os cortes aparecem assim que estiverem prontos.": "Clips will appear here when they’re ready.",
  "Prévia do corte": "Clip preview",
  "Prévia vertical": "Vertical preview",
  "Selecione um corte": "Select a clip",
  "Prévia do clipe": "Clip preview",
  "Prévia da câmera. Arraste para mover o recorte.": "Camera preview. Drag to move the crop.",
  "Legenda na prévia. Arraste ou use as setas para mudar a posição.": "Caption preview. Drag or use arrow keys to reposition.",
  "Card social na prévia": "Social card preview",
  "Reproduzir": "Play",
  "Pausar": "Pause",
  "Escolha um corte para revisar.": "Select a clip to review.",
  "Preparando a prévia limpa…": "Preparing preview…",
  "Falha na prévia. Clique para tentar novamente.": "Preview failed. Click to try again.",
  "Posição no vídeo": "Playback position",
  "Aparar início e fim": "Trim start and end",
  "Início (s)": "Start (s)",
  "Fim (s)": "End (s)",
  "Usar ponto atual": "Use playhead",
  "Restaurar duração": "Reset duration",
  "Arraste a legenda na prévia": "Drag the caption in the preview",
  "Pause o vídeo e arraste a facecam": "Pause and drag the facecam",
  "Arraste o card na prévia": "Drag the card in the preview",
  "Ajustes do clipe": "Clip settings",
  "Personalizar": "Customize",
  "Tipo de ajuste": "Editing tabs",
  "Legendas": "Captions",
  "Câmera": "Camera",
  "Card social": "Social card",
  "Nome do clipe": "Clip name",
  "Nome na biblioteca e no arquivo": "Library and download name",
  "Hook de abertura": "Opening hook",
  "Texto de abertura": "Opening text",
  "Destaque": "Caption style",
  "Palavra por palavra": "Word by word",
  "Frase simples": "Plain phrases",
  "Posição": "Position",
  "Posição da legenda": "Caption position",
  "Restaurar": "Reset",
  "Horizontal": "Horizontal",
  "Vertical": "Vertical",
  "Aplicar posição aos outros cortes": "Apply position to other clips",
  "Posição aplicada a {n} deste vídeo.": "Position applied to {n} from this video.",
  "Todos os cortes deste vídeo já usam esta posição.": "All clips from this video already use this position.",
  "Texto da legenda": "Caption text",
  "{n} trechos": "{n} segments",
  "Ir para {time}": "Go to {time}",
  "Legenda {n}, em {time}": "Caption {n}, at {time}",
  "Enquadramento da facecam": "Facecam framing",
  "Este corte não tem uma facecam separada para ajustar.": "This clip has no separate facecam to adjust.",
  "Arraste a facecam na prévia pausada ou mova o recorte pelos controles. O gameplay mantém o mesmo enquadramento.":
    "Drag the facecam while the preview is paused, or use the crop controls. Gameplay framing stays in place.",
  "Recorte horizontal": "Horizontal crop",
  "Recorte vertical": "Vertical crop",
  "Ajustes finos do rosto": "Fine face adjustments",
  "Altura do rosto": "Face height",
  "Mais abaixo": "Lower",
  "Mais acima": "Higher",
  "Posição horizontal": "Horizontal position",
  "Posição vertical": "Vertical position",
  "Esquerda": "Left",
  "Direita": "Right",
  "Atualizar prévia": "Refresh preview",
  "Atualizando…": "Refreshing…",
  "Aplicar aos outros cortes": "Apply to other clips",
  "Enquadramento copiado para {n}.": "Framing copied to {n}.",
  "Os outros cortes já usam esse ajuste ou não têm facecam separada.": "Other clips already use this framing or have no separate facecam.",
  "Card social ou CTA": "Social card or CTA",
  "A imagem aparece do começo ao fim do corte.": "The image stays visible for the entire clip.",
  "Imagem do card": "Card image",
  "Escolher imagem": "Choose image",
  "Tamanho": "Size",
  "Aplicar aos cortes deste vídeo": "Apply to clips from this video",
  "Usar em novos cortes": "Use for new clips",
  "Remover deste corte": "Remove from this clip",
  "Desativar card para novos cortes": "Disable card for new clips",
  "Card aplicado a {n} deste vídeo.": "Card applied to {n} from this video.",
  "Todos os cortes deste vídeo já usam esse card.": "All clips from this video already use this card.",
  "Os próximos cortes começam com este card.": "New clips will start with this card.",
  "Card padrão desativado.": "Default card turned off.",
  "Baixar MP4": "Download MP4",
  "Exportar MP4": "Export MP4",
  "Exportando…": "Exporting…",
  "Tente exportar novamente": "Try exporting again",
  "Não foi possível exportar.": "Export failed.",
  "Estúdio encerrado": "Studio closed",
  "Para abrir de novo, dê dois cliques em": "To reopen, double-click",
  "Salvando…": "Saving…",
  "Salvo": "Saved",
  "Falha ao salvar": "Save failed",
  "Confira os campos e tente novamente.": "Check the fields and try again.",
  "Escolha um início e um fim dentro do corte.": "Choose a start and end within the clip.",
  "Não foi possível carregar a imagem.": "Could not load the image.",
  "Novo vídeo": "New video",
  "Na fila de geração": "Queued",
  "Lendo o vídeo do YouTube": "Reading YouTube video",
  "Lendo o vídeo de origem": "Reading source video",
  "Baixando vídeo e áudio": "Downloading video and audio",
  "Transcrevendo em português": "Transcribing in Portuguese",
  "Enquadrando corte {current} de {total}": "Framing clip {current} of {total}",
  "{n} cortes disponíveis para edição": "{n} clips ready to edit",
  "Item não encontrado.": "Item not found.",
  "Imagem não encontrada.": "Image not found.",
  "A geração foi interrompida. Envie o link de novo para tentar novamente.": "Generation was interrupted. Submit the source again to retry.",
  "Cole um link de vídeo do YouTube.": "Paste a YouTube video link.",
  "Não encontrei o ID do vídeo nesse link.": "Could not find the video ID in that link.",
  "O YouTube não entregou uma faixa de vídeo utilizável.": "YouTube did not provide a usable video track.",
  "O arquivo de origem não está mais no local selecionado.": "The source file is no longer in the selected location.",
  "O vídeo não tem fala suficiente para os cortes automáticos.": "The video does not have enough speech to generate clips.",
  "Não foi possível gerar os cortes.": "Could not generate clips.",
  "O estúdio está encerrando.": "The studio is shutting down.",
  "Escolha um link do YouTube ou um arquivo local.": "Choose a YouTube link or a local file.",
  "Selecione um arquivo de vídeo local válido.": "Select a valid local video file.",
  "Aguarde o processamento antes de remover este vídeo.": "Wait for processing to finish before removing this video.",
  "Abra o aplicativo pelo atalho Ninfa Cortes para poder encerrar aqui.": "Open the app from the Ninfa Cortes shortcut to quit here.",
  "Ainda há vídeos sendo processados. Aguarde terminar e clique em Encerrar de novo.": "Videos are still processing. Wait for them to finish, then click Quit again.",
  "Aguarde o processamento antes de remover este corte.": "Wait for processing to finish before removing this clip.",
  "Digite um nome para o clipe.": "Enter a clip name.",
  "O rascunho foi alterado em outra aba. Atualize a página.": "The draft changed in another tab. Refresh the page.",
  "Cada trecho da legenda pode ter até 150 caracteres.": "Each caption segment can contain up to 150 characters.",
  "Escolha um início e um fim válidos dentro do corte.": "Choose a valid start and end within the clip.",
  "A imagem pode ter até 8 MB.": "The image must be 8 MB or smaller.",
  "Use uma imagem estática de até 16 megapixels.": "Use a still image of up to 16 megapixels.",
  "Escolha uma imagem PNG, JPG ou WebP válida.": "Choose a valid PNG, JPG, or WebP image.",
  "Adicione um card primeiro.": "Add a card first.",
  "Não consegui carregar o quadro da câmera.": "Could not load the camera frame.",
  "A câmera separada não aparece neste momento.": "A separate facecam is not visible at this time.",
  "O vídeo ainda não está pronto.": "The video is not ready yet.",
  "Exporte novamente para incluir as alterações do rascunho.": "Export again to include the draft changes.",
  "O FFmpeg não conseguiu decodificar este vídeo AV1. Tente uma versão em H.264.": "FFmpeg could not decode this AV1 video. Try an H.264 version.",
  "O FFmpeg não conseguiu concluir o vídeo.": "FFmpeg could not finish the video.",
};

function t(key, values = {}) {
  if (typeof key !== "string") return "";
  const phrase = english && Object.hasOwn(stringsEN, key) ? stringsEN[key] : key;
  return phrase.replace(/\{(\w+)\}/g, (match, name) => Object.hasOwn(values, name) ? values[name] : match);
}

function clipCount(n) {
  return t(n === 1 ? "{n} corte" : "{n} cortes", { n });
}

function progressText(value) {
  const framing = /^Enquadrando corte (\d+) de (\d+)$/.exec(value);
  if (framing) return t("Enquadrando corte {current} de {total}", { current: framing[1], total: framing[2] });
  const ready = /^(\d+) cortes disponíveis para edição$/.exec(value);
  return ready ? t("{n} cortes disponíveis para edição", { n: ready[1] }) : t(value);
}

document.documentElement.lang = english ? "en-US" : "pt-BR";
if (english) {
  const nodes = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let node = nodes.nextNode(); node; node = nodes.nextNode()) {
    if (node.parentElement.closest(".brand,script,style")) continue;
    const text = node.nodeValue.trim();
    if (!text) continue;
    const count = /^(\d+) cortes?$/.exec(text);
    const localized = count ? clipCount(Number(count[1])) : t(text);
    if (localized !== text) node.nodeValue = node.nodeValue.replace(text, localized);
  }
  for (const element of document.querySelectorAll("[alt],[aria-label],[placeholder],[title]")) {
    for (const name of ["alt", "aria-label", "placeholder", "title"]) {
      if (element.hasAttribute(name)) element.setAttribute(name, t(element.getAttribute(name)));
    }
  }
}
