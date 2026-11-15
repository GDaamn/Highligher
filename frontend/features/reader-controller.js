import { PdfReplicaEngine } from "/engine/replica-engine.js";
import { FocusLayer } from "./focus-layer.js";
import { CoverLayer } from "./cover-layer.js";
import { bookByline, metadataForRecord } from "../lib/book-metadata.js";

export class ReaderController {
  constructor({ api, document: documentRecord, root }) {
    this.api = api;
    this.document = documentRecord;
    this.root = root;
    this.controls = Object.fromEntries([
      "document-title", "document-stats", "page-count", "page-number",
      "zoom-out", "zoom-in", "zoom-value", "mode-focus", "mode-original",
      "view-one", "view-two", "chapter-menu", "chapter-trigger", "chapter-label", "chapter-options",
    ].map(id => [id, root.querySelector(`#${id}`)]));
    this.stage = root.querySelector("#reader-stage");
    this.engine = new PdfReplicaEngine({
      root: root.querySelector("#engine-root"),
      baseUrl: `/documents/${documentRecord.id}/scene`,
      initialZoom: 1,
      continuous: true,
      scrollRoot: this.stage,
      editable: false,
    });
    this.resizeListener = () => this.fitCurrentView();
    this.readingMode = "focus";
    this.saveTimer = null;
    this.restoringPosition = true;
    this.chapters = [];
    this.outsideChapterListener = event => {
      if (!this.controls["chapter-menu"].contains(event.target)) this.closeChapters();
    };
    this.bindControls();
  }

  async start() {
    const metadata = metadataForRecord(this.document);
    this.controls["document-title"].textContent = metadata.title;
    this.controls["document-stats"].textContent = bookByline(metadata);
    this.controls["page-count"].textContent = `/ ${this.document.page_count}`;
    this.controls["page-number"].max = this.document.page_count;

    this.engine.addEventListener("pagechange", event => this.onPage(event.detail.pageNumber));
    this.engine.addEventListener("zoomchange", event => {
      this.controls["zoom-value"].textContent = `${Math.round(event.detail.zoom * 100)}%`;
    });
    const [preferences, structure] = await Promise.all([
      this.api.preferences(this.document.id),
      this.api.structure(this.document.id),
      this.engine.load(),
    ]);
    this.engine.setSourceVisible(false);
    this.cover = new CoverLayer({ engine: this.engine, document: this.document });
    this.cover.apply();
    this.setViewMode("spread");
    this.setChapters(structure.chapters || []);
    await this.engine.goToPage(preferences.last_page || 1);
    this.focus = new FocusLayer({
      engine: this.engine,
      api: this.api,
      documentId: this.document.id,
      root: this.root,
    });
    await this.focus.start(preferences);
    this.setReadingMode(preferences.reading_mode || "focus", { save: false });
    this.restoringPosition = false;
    globalThis.addEventListener("resize", this.resizeListener);
    document.addEventListener("pointerdown", this.outsideChapterListener);
  }

  bindControls() {
    this.controls["page-number"].onchange = event => this.engine.goToPage(event.target.value);
    this.controls["zoom-out"].onclick = () => this.engine.setZoom(this.engine.zoom - .25);
    this.controls["zoom-in"].onclick = () => this.engine.setZoom(this.engine.zoom + .25);
    this.controls["mode-focus"].onchange = event => event.target.checked && this.setReadingMode("focus");
    this.controls["mode-original"].onchange = event => event.target.checked && this.setReadingMode("original");
    this.controls["view-one"].onchange = event => event.target.checked && this.setViewMode("continuous");
    this.controls["view-two"].onchange = event => event.target.checked && this.setViewMode("spread");
    this.controls["chapter-trigger"].onclick = () => {
      const open = this.controls["chapter-options"].hidden;
      if (open) this.openChapters();
      else this.closeChapters();
    };
    this.controls["chapter-trigger"].onkeydown = event => {
      if (event.key === "Escape") this.closeChapters();
    };
  }

  onPage(pageNumber) {
    this.controls["page-number"].value = pageNumber;
    const currentChapter = this.chapters.filter(chapter => chapter.page <= pageNumber).at(-1);
    this.controls["chapter-label"].textContent = currentChapter?.label || "Chapters";
    for (const option of this.controls["chapter-options"].children) {
      const active = Number(option.dataset.page) === currentChapter?.page;
      option.classList.toggle("is-active", active);
      option.setAttribute("aria-selected", String(active));
    }
    if (!this.restoringPosition) this.saveReadingState();
  }

  setViewMode(mode) {
    this.controls["view-one"].checked = mode !== "spread";
    this.controls["view-two"].checked = mode === "spread";
    this.engine.setViewMode(mode);
    this.fitCurrentView();
  }

  setReadingMode(mode, { save = true } = {}) {
    const showOriginal = mode === "original";
    const showFocus = mode === "focus";
    this.controls["mode-focus"].checked = showFocus;
    this.controls["mode-original"].checked = showOriginal;
    this.engine.setSourceVisible(showOriginal);
    this.focus?.setOnly(showFocus);
    this.readingMode = mode;
    if (save) this.saveReadingState();
  }

  setChapters(chapters) {
    this.chapters = chapters;
    const options = this.controls["chapter-options"];
    const fragment = document.createDocumentFragment();
    for (const chapter of chapters) {
      const button = document.createElement("button");
      button.type = "button";
      button.role = "option";
      button.dataset.page = chapter.page;
      const label = document.createElement("span");
      label.textContent = chapter.label;
      const page = document.createElement("small");
      page.textContent = String(chapter.page).padStart(3, "0");
      button.append(label, page);
      button.onclick = () => {
        this.engine.goToPage(chapter.page);
        this.closeChapters();
      };
      fragment.append(button);
    }
    options.replaceChildren(fragment);
    this.controls["chapter-menu"].hidden = chapters.length === 0;
  }

  openChapters() {
    this.controls["chapter-options"].hidden = false;
    this.controls["chapter-trigger"].setAttribute("aria-expanded", "true");
  }

  closeChapters() {
    this.controls["chapter-options"].hidden = true;
    this.controls["chapter-trigger"].setAttribute("aria-expanded", "false");
  }

  saveReadingState() {
    clearTimeout(this.saveTimer);
    this.saveTimer = setTimeout(() => {
      this.api.setReadingState(this.document.id, this.engine.pageNumber, this.readingMode)
        .catch(error => globalThis.dispatchEvent(new CustomEvent("highlighter:error", { detail: error })));
    }, 350);
  }

  fitCurrentView() {
    const columns = this.controls["view-two"].checked ? 2 : 1;
    this.engine.fitToViewport(columns, this.stage.clientWidth, this.stage.clientHeight);
  }

  destroy() {
    clearTimeout(this.saveTimer);
    this.api.setReadingState(this.document.id, this.engine.pageNumber, this.readingMode).catch(() => {});
    globalThis.removeEventListener("resize", this.resizeListener);
    document.removeEventListener("pointerdown", this.outsideChapterListener);
    this.cover?.destroy();
    this.focus?.destroy();
    this.engine.destroy();
  }
}
