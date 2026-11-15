import { HighlighterApi } from "./lib/api.js";
import { ReaderController } from "./features/reader-controller.js";
import { UploadController } from "./features/upload-controller.js";
import { LibraryDock } from "./features/library-dock.js";

const api = new HighlighterApi();
const uploadView = document.querySelector("#upload-view");
const readerView = document.querySelector("#reader-view");
const warning = document.querySelector("#configuration-warning");
const toast = document.querySelector("#toast");
const uploadForm = document.querySelector("#upload-form");
const processingCard = document.querySelector("#processing-card");
const pdfInput = document.querySelector("#pdf-file");
let reader = null;
let openingDocument = false;

function showUploadView() {
  reader?.destroy();
  reader = null;
  readerView.hidden = true;
  readerView.classList.remove("is-entering");
  uploadView.hidden = false;
  uploadView.classList.remove("is-leaving");
  document.body.classList.remove("reading");
  uploadForm.hidden = false;
  processingCard.hidden = true;
  pdfInput.value = "";
  library.setActive(null);
  library.refresh();
}

function notify(message) {
  toast.textContent = message;
  toast.classList.add("visible");
  clearTimeout(notify.timer);
  notify.timer = setTimeout(() => toast.classList.remove("visible"), 2200);
}

globalThis.addEventListener("highlighter:error", event => {
  notify(event.detail?.message || "The page preference could not be saved.");
});
globalThis.addEventListener("highlighter:notice", event => notify(event.detail));

async function openReader(documentRecord, pdf = null) {
  if (openingDocument) return;
  openingDocument = true;
  const isLeavingHome = !uploadView.hidden;
  if (isLeavingHome) {
    uploadView.classList.add("is-leaving");
    await new Promise(resolve => setTimeout(resolve, 260));
  }
  reader?.destroy();
  uploadView.hidden = true;
  uploadView.classList.remove("is-leaving");
  readerView.hidden = false;
  readerView.classList.add("is-entering");
  document.body.classList.add("reading");
  try {
    const device = documentRecord.device ? await import("./features/device-pdf.js") : null;
    reader = new ReaderController({ api, document: documentRecord, root: readerView, device, pdf });
    await reader.start();
    library.setActive(documentRecord.id);
    requestAnimationFrame(() => readerView.classList.remove("is-entering"));
  } catch (error) {
    notify(error.message);
    console.error(error);
    showUploadView();
  } finally {
    openingDocument = false;
  }
}

const library = new LibraryDock({
  api,
  root: document.querySelector("#library-dock"),
  onSelect: openReader,
  onRemove: documentRecord => {
    notify(`${documentRecord.display_title || documentRecord.file_name} removed from library.`);
  },
  onError: error => notify(error.message),
});
library.refresh();

uploadView.addEventListener("pointermove", event => {
  const bounds = uploadView.getBoundingClientRect();
  uploadView.style.setProperty("--pointer-x", `${event.clientX - bounds.left}px`);
  uploadView.style.setProperty("--pointer-y", `${event.clientY - bounds.top}px`);
});

const uploader = new UploadController({
  api,
  form: uploadForm,
  input: pdfInput,
  dropZone: document.querySelector("#drop-zone"),
  processingCard,
  onReady: async (documentRecord, pdf) => {
    library.refresh();
    await openReader(documentRecord, pdf);
  },
  onError: error => notify(error.message),
});

document.querySelector("#new-document").addEventListener("click", () => {
  showUploadView();
});

api.health().then(health => {
  uploader.setSupportedExtensions(health.supported_extensions || [".pdf", ".epub"]);
}).catch(() => {
  warning.textContent = "The processing server is offline.";
  warning.hidden = false;
});
