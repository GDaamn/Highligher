import { analyzeScanPage, normalizeWordText, ReplicaTextLayout, resolveTypography } from "./layout.js?module=scan";

function clamp(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, value));
}

function safeParse(value, fallback) {
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : fallback;
  } catch {
    return fallback;
  }
}

function defaultStorage() {
  try { return globalThis.localStorage; } catch { return null; }
}

function pageFile(number) {
  return `pages/page-${String(number).padStart(4, "0")}.json`;
}

function sourcePageFile(number) {
  return `source-pages/page-${String(number).padStart(4, "0")}.webp`;
}

export function isEmbeddedScanSource(image, page) {
  const edgeTolerance = 1.5;
  const coverageTolerance = .98;
  return Math.abs(Number(image.x) || 0) <= edgeTolerance
    && Math.abs(Number(image.y) || 0) <= edgeTolerance
    && Number(image.width) >= Number(page.width) * coverageTolerance
    && Number(image.height) >= Number(page.height) * coverageTolerance;
}

export function hasOriginalLayer(manifest) {
  return manifest?.sourcePreview !== false || manifest?.mode === "scan";
}

export function scanSourceRelativePath(manifest, page) {
  if (manifest?.mode !== "scan") return null;
  if (manifest.sourcePreview) return sourcePageFile(page.number);
  return (page.images || []).find(image => isEmbeddedScanSource(image, page))?.src || null;
}

export class ReplicaEngine extends EventTarget {
  constructor({ root, baseUrl = "book", storage, fetcher, initialZoom = 1.25, cacheSize = 8, continuous = false, scrollRoot = null, editable = true } = {}) {
    super();
    if (!root || root.nodeType !== 1) throw new TypeError("ReplicaEngine requires a root Element.");
    this.root = root;
    this.document = root.ownerDocument;
    this.storage = storage === undefined ? defaultStorage() : storage;
    this.fetcher = fetcher || globalThis.fetch?.bind(globalThis);
    if (!this.fetcher) throw new TypeError("ReplicaEngine requires a fetcher in this environment.");
    this.cacheSize = Math.max(1, Number(cacheSize) || 8);
    this.baseUrl = baseUrl;
    this.manifest = null;
    this.pageData = null;
    this.pageNumber = 1;
    this.zoom = clamp(Number(initialZoom) || 1.25, 0.5, 3);
    this.sourceVisible = false;
    this.exactFit = true;
    this.edits = {};
    this.pageCache = new Map();
    this.requestVersion = 0;
    this.continuous = Boolean(continuous);
    this.spread = false;
    this.scrollRoot = scrollRoot;
    this.editable = Boolean(editable);
    this.pageViews = new Map();
    this.visiblePages = new Map();
    this.pageObserver = null;

    this.shell = this.document.createElement("div");
    this.shell.className = "replica-engine-shell";
    this.page = this.document.createElement("div");
    this.page.className = "replica-page replica-exact-fit";
    this.shell.append(this.page);
    this.stream = this.document.createElement("div");
    this.stream.className = "replica-page-stream";
    this.root.replaceChildren(this.continuous ? this.stream : this.shell);
    this.root.classList.toggle("replica-readonly", !this.editable);
    this.layout = new ReplicaTextLayout(this.page);
  }

  url(relativePath) {
    const base = new URL(this.baseUrl.replace(/\/?$/, "/"), this.document.baseURI);
    return new URL(relativePath, base).toString();
  }

  emit(type, detail = {}) {
    const CustomEventClass = this.document.defaultView.CustomEvent;
    this.dispatchEvent(new CustomEventClass(type, { detail }));
  }

  async fetchJson(relativePath) {
    const response = await this.fetcher(this.url(relativePath));
    if (!response.ok) throw new Error(`Could not load ${relativePath} (${response.status}).`);
    return response.json();
  }

  async load(baseUrl = this.baseUrl) {
    this.pageObserver?.disconnect();
    this.pageObserver = null;
    this.requestVersion += 1;
    this.baseUrl = baseUrl;
    this.pageCache.clear();
    this.manifest = await this.fetchJson("manifest.json");
    if (this.manifest.schemaVersion !== 1) throw new Error(`Unsupported Replica schema version ${this.manifest.schemaVersion}.`);
    if (!Number.isInteger(this.manifest.pageCount) || this.manifest.pageCount < 1) throw new Error("The Replica manifest has an invalid pageCount.");
    this.manifest.mode ||= "digital";
    this.storageKey = `replica-edits:${this.manifest.id || this.manifest.title}:${this.manifest.pageCount}`;
    this.edits = this.editable ? this.loadEdits() : {};
    this.sourceVisible = this.manifest.mode === "scan";
    this.page.classList.toggle("replica-scan-page", this.manifest.mode === "scan");
    this.setSourceVisible(this.sourceVisible, { render: false });
    if (this.document.fonts?.ready) await this.document.fonts.ready;
    this.emit("manifest", { manifest: this.manifest });
    if (this.continuous) await this.loadContinuous();
    else await this.goToPage(1);
    this.emit("ready", { manifest: this.manifest, page: this.pageData });
    return this;
  }

  async loadContinuous() {
    const firstPage = await this.getPage(1);
    this.stream.replaceChildren();
    this.pageViews.clear();
    this.visiblePages.clear();
    for (let number = 1; number <= this.manifest.pageCount; number += 1) {
      const slot = this.document.createElement("section");
      slot.className = "replica-page-slot";
      slot.dataset.pageNumber = number;
      slot.setAttribute("aria-label", `Page ${number}`);
      const shell = this.document.createElement("div");
      shell.className = "replica-engine-shell";
      const placeholder = this.document.createElement("div");
      placeholder.className = "replica-page-placeholder";
      placeholder.textContent = String(number).padStart(3, "0");
      shell.append(placeholder);
      slot.append(shell);
      this.stream.append(slot);
      this.pageViews.set(number, { slot, shell, page: null, layout: null, data: null, loading: null });
    }
    this.applyViewMode();
    this.applyZoom();
    await this.ensurePageRendered(1, firstPage);
    const Observer = this.document.defaultView.IntersectionObserver;
    if (Observer) {
      this.pageObserver = new Observer(entries => this.onPageIntersections(entries), {
        root: this.scrollRoot,
        rootMargin: "120% 30%",
        threshold: [0, .15, .4, .7],
      });
      for (const view of this.pageViews.values()) this.pageObserver.observe(view.slot);
    } else {
      await this.ensurePageRendered(2);
    }
    this.activatePage(1);
  }

  onPageIntersections(entries) {
    for (const entry of entries) {
      const number = Number(entry.target.dataset.pageNumber);
      if (entry.isIntersecting) {
        this.visiblePages.set(number, entry.target);
        this.ensurePageRendered(number);
      } else {
        this.visiblePages.delete(number);
      }
    }
    const rootBounds = entries[0]?.rootBounds;
    const center = rootBounds ? rootBounds.top + rootBounds.height / 2 : this.document.defaultView.innerHeight / 2;
    let nearest = null;
    for (const [number, slot] of this.visiblePages) {
      const bounds = slot.getBoundingClientRect();
      const distance = Math.abs(bounds.top + Math.min(bounds.height, this.document.defaultView.innerHeight) / 2 - center);
      if (!nearest || distance < nearest.distance) nearest = { number, distance };
    }
    if (nearest) this.activatePage(nearest.number);
  }

  async ensurePageRendered(number, suppliedData = null) {
    const view = this.pageViews.get(number);
    if (!view || view.page) return view?.page;
    if (view.loading) return view.loading;
    view.loading = (async () => {
      const data = suppliedData || await this.getPage(number);
      const page = this.document.createElement("div");
      page.className = "replica-page replica-exact-fit";
      page.classList.toggle("replica-scan-page", this.manifest.mode === "scan");
      view.page = page;
      view.data = data;
      view.layout = new ReplicaTextLayout(page);
      view.shell.replaceChildren(page);
      this.renderPage(data, view);
      if (number === this.pageNumber) {
        this.page = page;
        this.pageData = data;
        this.layout = view.layout;
      }
      this.emit("pagerender", { pageNumber: number, page, data });
      return page;
    })().finally(() => { view.loading = null; });
    return view.loading;
  }

  activatePage(number) {
    const target = clamp(Number(number) || 1, 1, this.manifest.pageCount);
    if (target === this.pageNumber && this.pageData?.number === target) return;
    const view = this.pageViews.get(target);
    this.pageNumber = target;
    if (view?.page) {
      this.page = view.page;
      this.pageData = view.data;
      this.layout = view.layout;
    }
    this.emit("pagechange", { pageNumber: target, page: view?.data || null });
  }

  pageEdits(number = this.pageNumber) {
    return this.edits[number] || {};
  }

  persistEdits() {
    try {
      this.storage?.setItem(this.storageKey, JSON.stringify(this.edits));
    } catch (error) {
      this.emit("storageerror", { operation: "write", error });
    }
  }

  loadEdits() {
    try {
      return safeParse(this.storage?.getItem(this.storageKey) || "{}", {});
    } catch (error) {
      this.emit("storageerror", { operation: "read", error });
      return {};
    }
  }

  async getPage(number) {
    if (this.pageCache.has(number)) {
      const cached = this.pageCache.get(number);
      this.pageCache.delete(number);
      this.pageCache.set(number, cached);
      return cached;
    }
    const page = await this.fetchJson(pageFile(number));
    this.pageCache.set(number, page);
    while (this.pageCache.size > this.cacheSize) this.pageCache.delete(this.pageCache.keys().next().value);
    return page;
  }

  async goToPage(number) {
    if (!this.manifest) throw new Error("Load a book before navigating.");
    const target = clamp(Number(number) || 1, 1, this.manifest.pageCount);
    if (this.continuous) {
      await this.ensurePageRendered(target);
      this.pageViews.get(target)?.slot.scrollIntoView({ behavior: "auto", block: "start", inline: "nearest" });
      this.activatePage(target);
      return true;
    }
    const version = ++this.requestVersion;
    const page = await this.getPage(target);
    if (version !== this.requestVersion) return false;
    this.pageNumber = target;
    this.pageData = page;
    this.renderPage();
    this.emit("pagechange", { pageNumber: target, page });
    return true;
  }

  renderWord(word, line, view = null) {
    const pageNumber = view?.data.number ?? this.pageNumber;
    const pageEdits = () => this.pageEdits(pageNumber);
    const box = this.document.createElement("span");
    box.className = "replica-word-box";
    Object.assign(box.dataset, { wordId: word.id, width: word.width, x: word.x, size: word.size, line });
    Object.assign(box.style, { left: `${word.x}px`, top: `${word.y}px`, width: `${word.width}px`, height: `${word.height}px` });

    const text = this.document.createElement("span");
    text.className = "replica-word-text";
    text.contentEditable = this.editable ? "true" : "false";
    text.spellcheck = false;
    const original = normalizeWordText(word);
    text.textContent = pageEdits()[word.id] ?? original;
    text.classList.toggle("modified", text.textContent !== original);
    const typography = resolveTypography(word.font);
    Object.assign(text.style, {
      fontFamily: typography.family,
      fontWeight: typography.weight,
      fontStyle: typography.style,
      fontSize: `${word.size}px`,
      lineHeight: `${word.height}px`,
      color: word.color,
    });
    text.addEventListener("focus", () => this.emit("selection", { word, text: text.textContent }));
    if (this.editable) {
      text.addEventListener("keydown", event => { if (event.key === "Enter") event.preventDefault(); });
      text.addEventListener("input", () => {
        this.edits[pageNumber] ||= {};
        if (text.textContent === original) delete this.edits[pageNumber][word.id];
        else this.edits[pageNumber][word.id] = text.textContent;
        text.classList.toggle("modified", text.textContent !== original);
        this.persistEdits();
        this.applyLayout(line, view);
        this.emit("edit", { pageNumber, wordId: word.id, word, text: text.textContent });
        this.emit("selection", { word, text: text.textContent });
      });
    }
    box.append(text);
    return box;
  }

  renderPage(data = this.pageData, view = null) {
    const page = view?.page || this.page;
    const layout = view?.layout || this.layout;
    page.replaceChildren();
    page.classList.toggle("replica-source-visible", this.sourceVisible);
    page.classList.toggle("replica-exact-fit", this.exactFit);
    Object.assign(page.style, { width: `${data.width}px`, height: `${data.height}px` });

    const scanSourcePath = scanSourceRelativePath(this.manifest, data);
    const scanSource = scanSourcePath ? this.url(scanSourcePath) : null;
    if (this.manifest.sourcePreview) {
      const source = this.document.createElement("img");
      source.className = "replica-source-page";
      source.src = this.url(sourcePageFile(data.number));
      source.alt = "Original page comparison";
      page.append(source);
    }

    const layer = this.document.createElement("div");
    layer.className = "replica-content-layer";
    for (const image of data.images || []) {
      const node = this.document.createElement("img");
      node.className = "replica-page-image";
      const isSource = this.manifest.mode === "scan" && isEmbeddedScanSource(image, data);
      node.classList.toggle("replica-embedded-source-page", isSource);
      node.src = this.url(image.src);
      Object.assign(node.style, { left: `${image.x}px`, top: `${image.y}px`, width: `${image.width}px`, height: `${image.height}px` });
      layer.append(node);
    }
    if (data.lines?.length) layer.append(this.renderRules(data));
    const scan = this.manifest.mode === "scan" ? analyzeScanPage(data.words, data) : null;
    if (scanSource) {
      for (const region of scan.artworkRegions) layer.append(this.renderScanRegion(scanSource, data, region, "artwork"));
      for (const region of scan.typographyRegions) layer.append(this.renderScanRegion(scanSource, data, region, "typography"));
    }
    for (const word of data.words) {
      const box = this.renderWord(word, scan?.lineAssignments.get(word.id) ?? word.id, view);
      box.classList.toggle("replica-source-typography-word", scan?.sourceWordIds.has(word.id) || false);
      layer.append(box);
    }
    page.append(layer);
    const schedule = this.document.defaultView.requestAnimationFrame || (callback => callback());
    schedule(() => layout.apply({ mode: this.manifest.mode, sourceVisible: this.sourceVisible, line: null }));
    this.applyZoom(view);
  }

  renderScanRegion(sourceUrl, page, region, role) {
    const clip = this.document.createElement("div");
    clip.className = "replica-scan-source-region";
    clip.dataset.regionRole = role;
    if (region.kind) clip.dataset.regionKind = region.kind;
    Object.assign(clip.style, {
      left: `${region.x}px`, top: `${region.y}px`, width: `${region.width}px`, height: `${region.height}px`,
    });
    const source = this.document.createElement("img");
    source.className = "replica-scan-source-fragment";
    source.src = sourceUrl;
    Object.assign(source.style, {
      left: `${-region.x}px`, top: `${-region.y}px`, width: `${page.width}px`, height: `${page.height}px`,
    });
    clip.append(source);
    return clip;
  }

  renderRules(data) {
    const namespace = "http://www.w3.org/2000/svg";
    const svg = this.document.createElementNS(namespace, "svg");
    svg.classList.add("replica-rule-layer");
    svg.setAttribute("viewBox", `0 0 ${data.width} ${data.height}`);
    for (const rule of data.lines) {
      const line = this.document.createElementNS(namespace, "line");
      const attributes = { x1: rule.x1, y1: rule.y1, x2: rule.x2, y2: rule.y2, stroke: rule.color, "stroke-width": rule.width };
      for (const [name, value] of Object.entries(attributes)) line.setAttribute(name, value);
      svg.append(line);
    }
    return svg;
  }

  applyLayout(line = null, view = null) {
    (view?.layout || this.layout).apply({ mode: this.manifest.mode, sourceVisible: this.sourceVisible, line });
  }

  setSourceVisible(visible, { render = true } = {}) {
    this.sourceVisible = Boolean(visible && hasOriginalLayer(this.manifest));
    const pages = this.continuous ? [...this.pageViews.values()].filter(view => view.page) : [{ page: this.page, layout: this.layout }];
    for (const view of pages) {
      view.page.classList.toggle("replica-source-visible", this.sourceVisible);
      if (render) view.layout.apply({ mode: this.manifest.mode, sourceVisible: this.sourceVisible, line: null });
    }
    this.emit("sourcechange", { visible: this.sourceVisible });
  }

  setExactFit(enabled) {
    this.exactFit = Boolean(enabled);
    const views = this.continuous ? [...this.pageViews.values()].filter(view => view.page) : [{ page: this.page, layout: this.layout }];
    for (const view of views) {
      view.page.classList.toggle("replica-exact-fit", this.exactFit);
      view.layout.apply({ mode: this.manifest.mode, sourceVisible: this.sourceVisible, line: null });
    }
  }

  setZoom(value) {
    this.zoom = clamp(Number(value) || 1, 0.5, 3);
    if (this.pageData) this.applyZoom();
    this.emit("zoomchange", { zoom: this.zoom });
  }

  applyZoom(targetView = null) {
    if (this.continuous) {
      const fallback = this.pageViews.get(1)?.data || this.pageData;
      if (!fallback) return;
      const views = targetView ? [targetView] : this.pageViews.values();
      for (const view of views) {
        const data = view.data || fallback;
        view.shell.style.width = `${data.width * this.zoom}px`;
        view.shell.style.height = `${data.height * this.zoom}px`;
        view.slot.style.minWidth = `${data.width * this.zoom}px`;
        view.slot.style.minHeight = `${data.height * this.zoom}px`;
        if (view.page) view.page.style.transform = `scale(${this.zoom})`;
      }
      return;
    }
    this.shell.style.width = `${this.pageData.width * this.zoom}px`;
    this.shell.style.height = `${this.pageData.height * this.zoom}px`;
    this.page.style.transform = `scale(${this.zoom})`;
  }

  setViewMode(mode) {
    this.spread = mode === "spread";
    this.applyViewMode();
    this.emit("viewmodechange", { mode: this.spread ? "spread" : "continuous" });
  }

  applyViewMode() {
    this.stream.classList.toggle("replica-spread", this.spread);
  }

  fitToWidth(columns = 1, availableWidth = this.scrollRoot?.clientWidth || this.root.clientWidth) {
    const data = this.pageViews.get(1)?.data || this.pageData;
    if (!data || !availableWidth) return;
    const gaps = columns === 2 ? 72 : 48;
    this.setZoom(clamp((availableWidth - gaps) / (data.width * columns), .5, 1.5));
  }

  fitToViewport(
    columns = 1,
    availableWidth = this.scrollRoot?.clientWidth || this.root.clientWidth,
    availableHeight = this.scrollRoot?.clientHeight || this.root.clientHeight,
  ) {
    const data = this.pageViews.get(1)?.data || this.pageData;
    if (!data || !availableWidth || !availableHeight) return;
    const stageInset = 90;
    const columnGap = columns === 2 ? 22 : 0;
    const widthScale = (availableWidth - stageInset - columnGap * (columns - 1)) / (data.width * columns);
    const heightScale = (availableHeight - stageInset) / data.height;
    this.setZoom(clamp(Math.min(widthScale, heightScale), .5, 1.5));
  }

  getPageElement(number = this.pageNumber) {
    return this.continuous ? this.pageViews.get(Number(number))?.page || null : Number(number) === this.pageNumber ? this.page : null;
  }

  resetPage(number = this.pageNumber) {
    delete this.edits[number];
    this.persistEdits();
    if (this.continuous) {
      const view = this.pageViews.get(number);
      if (view?.page) this.renderPage(view.data, view);
    } else if (number === this.pageNumber) this.renderPage();
  }

  getEdits() {
    return structuredClone(this.edits);
  }

  setEdits(edits) {
    this.edits = structuredClone(edits || {});
    this.persistEdits();
    if (this.continuous) {
      for (const view of this.pageViews.values()) if (view.page) this.renderPage(view.data, view);
    } else if (this.pageData) this.renderPage();
  }

  createEditsBlob() {
    return new Blob([JSON.stringify({ title: this.manifest.title, edits: this.edits }, null, 2)], { type: "application/json" });
  }

  destroy() {
    this.requestVersion += 1;
    this.pageObserver?.disconnect();
    this.pageCache.clear();
    this.pageViews.clear();
    this.visiblePages.clear();
    this.root.replaceChildren();
    this.root.classList.remove("replica-readonly");
  }
}

// Backward-compatible name retained for existing integrations.
export const PdfReplicaEngine = ReplicaEngine;
