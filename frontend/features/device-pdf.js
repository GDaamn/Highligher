import { GlobalWorkerOptions, getDocument, Util } from "/vendor/pdfjs/pdf.min.mjs";
import { PdfReplicaEngine } from "/engine/replica-engine.js";
import { cacheDevicePage, changePreferences, readDevice, saveDeviceBook } from "../lib/device-library.js";
import { metadataFromFilename } from "../lib/book-metadata.js";

GlobalWorkerOptions.workerSrc = "/vendor/pdfjs/pdf.worker.min.mjs";

async function openBytes(bytes) {
  return getDocument({
    data: new Uint8Array(bytes), isEvalSupported: false,
    cMapUrl: "/vendor/pdfjs/cmaps/", cMapPacked: true,
    standardFontDataUrl: "/vendor/pdfjs/standard_fonts/", wasmUrl: "/vendor/pdfjs/wasm/",
    iccUrl: "/vendor/pdfjs/iccs/",
  }).promise;
}

export async function importPdf(file) {
  if (file.size > 100 * 1024 * 1024) throw new Error("Choose a PDF smaller than 100 MB.");
  const bytes = await file.arrayBuffer();
  // Start hashing before PDF.js transfers the buffer to its worker.
  const fingerprint = crypto.subtle.digest("SHA-256", bytes);
  const pdf = await openBytes(bytes);
  try {
    const digest = new Uint8Array(await fingerprint);
    const id = "device-" + Array.from(digest, byte => byte.toString(16).padStart(2, "0")).join("");
    const existing = await readDevice("books", id);
    if (existing) return { record: existing, pdf };
    const metadata = metadataFromFilename(file.name);
    const embedded = await pdf.getMetadata().catch(() => null);
    const record = { id, file_name: file.name, display_title: metadata.title,
      author: embedded?.info?.Author || metadata.author, publication_year: metadata.year,
      page_count: pdf.numPages, state: "ready", device: true, created_at: new Date().toISOString() };
    await saveDeviceBook(record, file);
    return { record, pdf };
  } catch (error) {
    await pdf.destroy();
    throw error;
  }
}

function wordBoxes(content, viewport, context) {
  const words = [];
  for (const item of content.items) {
    if (!item.str?.trim()) continue;
    const matrix = Util.transform(viewport.transform, item.transform);
    const height = Math.hypot(matrix[2], matrix[3]);
    if (!height || !item.width) continue;
    const style = content.styles[item.fontName] || {};
    context.font = height + "px " + (style.fontFamily || "sans-serif");
    const angle = Math.atan2(matrix[1], matrix[0]);
    const ascent = height * (style.ascent ?? 0.8);
    const spans = item.str.match(/\s+|\S+/gu);
    const widths = spans.map(span => context.measureText(span).width);
    const scale = item.width * viewport.scale / (widths.reduce((a, b) => a + b, 0) || 1);
    let advance = 0;
    spans.forEach((span, index) => {
      const width = widths[index] * scale;
      const offset = item.dir === "rtl" ? item.width * viewport.scale - advance - width : advance;
      if (span.trim()) words.push({
        id: words.length, text: span, width, height, angle,
        x: matrix[4] + Math.cos(angle) * offset + Math.sin(angle) * ascent,
        y: matrix[5] + Math.sin(angle) * offset - Math.cos(angle) * ascent,
      });
      advance += width;
    });
  }
  return words;
}

const stop = new Set("a an and are as at be been but by for from had has have he her his i if in into is it its of on or our she that the their there they this to was we were which who will with you your".split(" "));
function rankedWords(words) {
  const selected = [];
  let sentences = [], sentence = [];
  function rank() {
    const terms = sentences.map(s => new Set(s.map(w => w.text.toLowerCase().replace(/[^\p{L}\p{N}'-]/gu, "")).filter(t => t && !stop.has(t))));
    const freq = new Map();
    for (const set of terms) for (const t of set) freq.set(t, (freq.get(t) || 0) + 1);
    const scores = sentences.map((s, i) => {
      const text = s.map(w => w.text).join(" ");
      const shared = [...terms[i]].reduce((sum, t) => sum + Math.min(3, freq.get(t) - 1), 0);
      return { i, score: shared / Math.sqrt(Math.max(1, terms[i].size))
        + Number(/\b(because|therefore|means|creates|requires|explains|cannot|depends)\b/i.test(text))
        + (text.includes("?") ? .65 : 0) + (i === 0 ? .45 : 0) };
    });
    for (const {i} of scores.sort((a,b) => b.score-a.score).slice(0, Math.max(1, Math.round(scores.length * .32)))) {
      selected.push(...sentences[i].map(w => w.id));
    }
    sentences = [];
  }
  for (const word of words) {
    sentence.push(word);
    if (/[.!?。！？][”’"')\]]*$/.test(word.text) && !/^(Mr|Mrs|Ms|Dr|Prof|St|[A-Z])\.$/.test(word.text)) {
      sentences.push(sentence); sentence = [];
      if (sentences.length === 8) rank();
    }
  }
  if (sentence.length) sentences.push(sentence);
  if (sentences.length) rank();
  return selected;
}

export class DevicePdfEngine extends PdfReplicaEngine {
  constructor(options, record, pdf) {
    super(options);
    this.record = record;
    this.pdf = pdf;
    this.pending = new Map();
    this.urls = new Map();
  }

  async load() {
    if (!this.pdf) {
      const file = await readDevice("files", this.record.id);
      if (!file) throw new Error("This PDF was removed from this browser. Choose the file again.");
      this.pdf = await openBytes(await file.arrayBuffer());
    }
    return super.load();
  }

  async fetchJson() {
    return { schemaVersion: 1, id: this.record.id, mode: "digital", pageCount: this.pdf.numPages, sourcePreview: true };
  }

  async getPage(number) {
    const rendered = this.pageViews.get(number)?.data;
    if (rendered) return rendered;
    if (!this.pending.has(number)) {
      this.pending.set(number, this.prepare(number).finally(() => this.pending.delete(number)));
    }
    return this.pending.get(number);
  }

  async prepare(number) {
    const cached = await readDevice("pages", [this.record.id, number]);
    if (cached) return cached;
    const page = await this.pdf.getPage(number);
    const viewport = page.getViewport({scale: 1});
    const scale = Math.min(1.67, 2400 / Math.max(viewport.width, viewport.height));
    const pixels = page.getViewport({scale});
    const canvas = document.createElement("canvas");
    canvas.width = Math.ceil(pixels.width);
    canvas.height = Math.ceil(pixels.height);
    try {
      await page.render({canvas, viewport: pixels}).promise;
      const words = wordBoxes(await page.getTextContent(), viewport, canvas.getContext("2d"));
      const image = await new Promise((resolve, reject) => canvas.toBlob(
        blob => blob ? resolve(blob) : reject(new Error("Could not render PDF page.")), "image/webp", .90));
      const result = { number, width: viewport.width, height: viewport.height, words, image, focusWordIds: rankedWords(words) };
      if (number === 1) {
        const cover = document.createElement("canvas");
        cover.width = 120;
        cover.height = Math.min(200, Math.max(1, Math.round(120 * canvas.height / canvas.width)));
        cover.getContext("2d").drawImage(canvas, 0, 0, cover.width, cover.height);
        result.thumbnail = cover.toDataURL("image/webp", .8);
      }
      await cacheDevicePage(this.record.id, result).catch(() => {}); // Reading also works with a full cache.
      return result;
    } finally {
      page.cleanup();
      canvas.width = canvas.height = 0;
    }
  }

  setSourceVisible(visible) {
    // Both modes share the original artwork; CSS switches the word overlays.
    super.setSourceVisible(visible, {render: false});
  }

  onPageIntersections(entries) {
    super.onPageIntersections(entries);
    for (const entry of entries) {
      if (entry.isIntersecting) continue;
      const number = Number(entry.target.dataset.pageNumber);
      const view = this.pageViews.get(number);
      if (!view?.page || number === 1 || number === this.pageNumber) continue;
      const placeholder = document.createElement("div");
      placeholder.className = "replica-page-placeholder";
      placeholder.textContent = number;
      view.shell.replaceChildren(placeholder);
      view.page = view.data = view.layout = null;
      URL.revokeObjectURL(this.urls.get(number));
      this.urls.delete(number);
    }
  }

  renderPage(data, view) {
    const page = view.page;
    page.classList.add("device-pdf-page");
    page.classList.toggle("replica-source-visible", this.sourceVisible);
    Object.assign(page.style, {width: data.width + "px", height: data.height + "px"});
    const image = document.createElement("img");
    if (!this.urls.has(data.number)) this.urls.set(data.number, URL.createObjectURL(data.image));
    image.src = this.urls.get(data.number);
    image.className = "device-page-image";
    image.alt = "Original page " + data.number;
    const layer = document.createDocumentFragment();
    for (const word of data.words) {
      const box = document.createElement("span");
      box.className = "replica-word-box";
      box.dataset.wordId = word.id;
      box.textContent = word.text;
      Object.assign(box.style, {left: word.x+"px", top: word.y+"px", width: word.width+"px",
        height: word.height+"px", fontSize: word.height+"px", transform: `rotate(${word.angle}rad)`});
      layer.append(box);
    }
    page.replaceChildren(image, layer);
    this.applyZoom(view);
  }

  destroy() {
    super.destroy();
    for (const url of this.urls.values()) URL.revokeObjectURL(url);
    this.pdf?.destroy();
  }
}

export class DeviceReaderApi {
  constructor(engine, server) {
    this.engine = engine;
    this.health = server.health().catch(() => ({}));
    this.ai = new Map();
  }
  async preferences(id) { return await readDevice("preferences", id) || {}; }
  async structure() {
    const pdf = this.engine.pdf;
    const chapters = [];
    async function visit(items) {
      for (const item of items || []) {
        const dest = typeof item.dest === "string" ? await pdf.getDestination(item.dest) : item.dest;
        if (dest?.[0] != null) {
          const index = typeof dest[0] === "number" ? dest[0] : await pdf.getPageIndex(dest[0]);
          chapters.push({page: index + 1, label: item.title});
        }
        await visit(item.items);
      }
    }
    if (pdf) await visit(await pdf.getOutline());
    return {chapters: chapters.sort((a,b) => a.page-b.page)};
  }
  async focusAvailability() {
    return {pages: Array.from({length:this.engine.record.page_count}, (_,i) => i+1), complete:true};
  }
  async focus(id, number) {
    const data = await this.engine.getPage(number);
    let selected = data.focusWordIds;
    if (data.words.length && (await this.health).openrouter_configured) {
      if (!this.ai.has(number)) {
        const task = fetch("/api/device-focus", {method:"POST", headers:{"Content-Type":"application/json"},
          body:JSON.stringify({words:data.words.map(w => w.text)})}).then(async response => {
          if (!response.ok) throw new Error("OpenRouter unavailable. Using local highlights.");
          return (await response.json()).focusWordIds;
        }).catch(error => {
          globalThis.dispatchEvent(new CustomEvent("highlighter:error", {detail:error}));
          return data.focusWordIds;
        });
        this.ai.set(number, task);
      }
      selected = await this.ai.get(number);
    }
    const overrides = (await this.preferences(id)).words?.[number] || {};
    const focused = new Set(selected);
    for (const [word, on] of Object.entries(overrides)) {
      if (on) focused.add(Number(word)); else focused.delete(Number(word));
    }
    return {focusWordIds:[...focused], correctedWordIds:Object.keys(overrides).map(Number)};
  }
  setReadingState(id, page, mode) {
    return changePreferences(id, prefs => Object.assign(prefs,{last_page:page,reading_mode:mode}));
  }
  setPageDimmed(id, page, dimmed) {
    return changePreferences(id, prefs => {
      const pages = new Set(prefs.dimmed_pages);
      if (dimmed) pages.add(page); else pages.delete(page);
      prefs.dimmed_pages = [...pages];
    });
  }
  async setWordFocus(id, number, word, on) {
    await changePreferences(id, prefs => { prefs.words ||= {}; (prefs.words[number] ||= {})[word] = on; });
    return this.focus(id,number);
  }
}
