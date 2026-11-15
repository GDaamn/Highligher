import { bookByline, metadataFromFilename } from "../lib/book-metadata.js";

export class UploadController {
  constructor({ api, form, input, dropZone, processingCard, preview, onReady, onError }) {
    this.api = api;
    this.form = form;
    this.input = input;
    this.dropZone = dropZone;
    this.processingCard = processingCard;
    this.preview = preview;
    this.previewPages = preview.querySelector("#preview-pages");
    this.previewLink = preview.querySelector("#preview-link");
    this.previewStatus = preview.querySelector("#preview-status");
    this.openHighlights = preview.querySelector("#open-highlights");
    this.onReady = onReady;
    this.onError = onError;
    this.fileName = processingCard.querySelector("#processing-file");
    this.bookMeta = processingCard.querySelector("#processing-book-meta");
    this.percent = processingCard.querySelector("#processing-percent");
    this.progress = processingCard.querySelector("#processing-progress");
    this.message = processingCard.querySelector("#processing-message");
    this.pageStack = processingCard.querySelector(".page-stack");
    this.pageTiles = [...this.pageStack.querySelectorAll("span")];
    this.displayedPercent = 0;
    this.percentAnimation = null;
    this.lastAnimatedPage = null;
    this.lastPhase = null;
    this.lastMotionProgress = -1;
    this.dragDepth = 0;
    this.processing = false;
    this.setSupportedExtensions([".pdf", ".epub"]);
    this.bind();
  }

  setSupportedExtensions(extensions) {
    this.extensions = extensions.map(value => value.toLowerCase().replace(/^([^\.])/, ".$1"));
    const escaped = this.extensions.map(value => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    this.supportedDocument = new RegExp(`(?:${escaped.join("|")})$`, "i");
    this.input.accept = this.extensions.join(",");
    const names = this.extensions.map(value => value.slice(1).toUpperCase());
    const label = names.length > 1 ? `${names.slice(0, -1).join(", ")} or ${names.at(-1)}` : names[0];
    const hint = this.dropZone.querySelector("[data-supported-formats]");
    if (hint) hint.textContent = `drop ${label}, or press to browse`;
    this.formatLabel = label;
  }

  pickerTypes() {
    const accept = {};
    if (this.extensions.includes(".pdf")) accept["application/pdf"] = [".pdf"];
    if (this.extensions.includes(".epub")) accept["application/epub+zip"] = [".epub"];
    return [{ description: this.formatLabel, accept }];
  }

  accepts(file) {
    return this.supportedDocument.test(file.name);
  }

  bind() {
    this.input.addEventListener("change", () => this.input.files[0] && this.process(this.input.files[0]));
    this.dropZone.addEventListener("click", async event => {
      if (!window.showOpenFilePicker) return;
      event.preventDefault();
      try {
        const [handle] = await window.showOpenFilePicker({ multiple: false, types: this.pickerTypes() });
        this.process(await handle.getFile());
      } catch (error) {
        if (error?.name !== "AbortError") this.onError(error);
      }
    });
    this.dropZone.addEventListener("dragenter", event => {
      event.preventDefault();
      this.dragDepth += 1;
      this.dropZone.classList.add("is-dragging");
    });
    this.dropZone.addEventListener("dragover", event => {
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
    });
    this.dropZone.addEventListener("dragleave", event => {
      event.preventDefault();
      this.dragDepth = Math.max(0, this.dragDepth - 1);
      if (!this.dragDepth) this.dropZone.classList.remove("is-dragging");
    });
    this.dropZone.addEventListener("drop", async event => {
      event.preventDefault();
      this.dragDepth = 0;
      this.dropZone.classList.remove("is-dragging");
      const file = await this.fileFromDrop(event.dataTransfer);
      if (file) this.process(file);
      else this.onError(new Error(`Drop a ${this.formatLabel} file to continue.`));
    });
  }

  async fileFromDrop(dataTransfer) {
    const fallbackFiles = [...dataTransfer.files];
    const item = [...dataTransfer.items].find(candidate => {
      const file = candidate.kind === "file" ? candidate.getAsFile() : null;
      return file && this.accepts(file);
    });

    if (item?.getAsFileSystemHandle) {
      try {
        const handle = await item.getAsFileSystemHandle();
        if (handle?.kind === "file") return await handle.getFile();
      } catch {
        // Some drag sources expose only a temporary File; try that below.
      }
    }
    return fallbackFiles.find(candidate => this.accepts(candidate)) || null;
  }

  showPdf(file) {
    // An explicit PDF type also handles files whose OS supplies no MIME type.
    this.previewUrl = URL.createObjectURL(file.slice(0, file.size, "application/pdf"));
    this.previewLink.href = this.previewUrl;
    this.previewPages.data = this.previewUrl;
    this.preview.querySelector("#preview-title").textContent = this.inferredMetadata.title;
    this.preview.hidden = false;
  }

  closePdf() {
    this.preview.hidden = true;
    this.openHighlights.hidden = true;
    this.openHighlights.onclick = null;
    this.previewPages.removeAttribute("data");
    this.previewLink.removeAttribute("href");
    if (this.previewUrl) URL.revokeObjectURL(this.previewUrl);
    this.previewUrl = null;
  }

  async process(file) {
    if (this.processing) return;
    if (!this.accepts(file)) {
      this.onError(new Error(`Choose a ${this.formatLabel} file to continue.`));
      return;
    }
    this.processing = true;
    this.form.hidden = true;
    this.processingCard.hidden = false;
    this.processingCard.dataset.phase = "receive";
    this.processingCard.classList.remove("is-ready");
    this.displayedPercent = 0;
    this.lastAnimatedPage = null;
    this.lastPhase = null;
    this.lastMotionProgress = -1;
    this.inferredMetadata = metadataFromFilename(file.name);
    this.processingCard.querySelector(".pdf-badge").textContent = file.name.split(".").pop().toUpperCase();
    this.fileName.textContent = this.inferredMetadata.title;
    this.bookMeta.textContent = bookByline(this.inferredMetadata);
    try {
      if (file.name.toLowerCase().endsWith(".pdf")) this.showPdf(file);
      this.update({ state: "queued", message: "Receiving document", progress: .02 });
      const uploadFile = await this.prepareUpload(file);
      const upload = await this.api.upload(uploadFile, ratio => this.update({
        state: "uploading",
        message: ratio < 1
          ? `Uploading file · ${Math.round(ratio * 100)}% sent`
          : "Upload sent · waiting for the server",
        progress: ratio * .08,
      }));
      const result = await this.api.waitUntilReadable(upload.document.id, document => this.update(document));
      if (this.previewUrl) {
        this.previewStatus.textContent = "Highlights ready";
        this.openHighlights.hidden = false;
        this.openHighlights.onclick = () => {
          this.closePdf();
          this.onReady(result);
        };
      } else {
        await this.finish();
        await this.onReady(result);
      }
    } catch (error) {
      this.form.hidden = false;
      this.processingCard.hidden = true;
      this.processingCard.dataset.phase = "";
      this.input.value = "";
      if (this.previewUrl) {
        this.previewStatus.textContent = `Highlights unavailable. ${error.message}`;
      } else {
        this.onError(error);
      }
    } finally {
      this.processing = false;
    }
  }

  async prepareUpload(file) {
    if (!file.name.toLowerCase().endsWith(".epub")) return file;

    try {
      const bytes = await file.arrayBuffer();
      return new File([bytes], file.name, {
        type: file.type || "application/epub+zip",
        lastModified: file.lastModified,
      });
    } catch (error) {
      if (error?.name === "NotFoundError" || /file or directory could not be found/i.test(error?.message || "")) {
        throw new Error("This EPUB is not available as a local file. Download or copy it to a local folder, then choose it again.");
      }
      throw new Error(`The EPUB could not be read: ${error?.message || "unknown file error"}`);
    }
  }

  update(document) {
    const progress = Math.max(0, Math.min(1, document.progress || 0));
    const phase = this.phaseFor(document.state);
    if (this.previewUrl) {
      this.previewStatus.textContent = document.message || "Preparing highlights…";
      return;
    }
    this.setPhase(phase);
    this.message.textContent = document.message || "Working on your document";
    this.animatePercent(Math.round(progress * 100));
    this.progress.style.width = `${progress * 100}%`;
    this.updatePageStack(document);
    this.updateStageMotion(phase, progress);
    if (document.display_title) this.fileName.textContent = document.display_title;
    this.bookMeta.textContent = bookByline({
      author: document.author || this.inferredMetadata?.author,
      publication_year: document.publication_year || this.inferredMetadata?.year,
    });
  }

  phaseFor(state) {
    if (state === "converting") return "assemble";
    if (state === "focusing") return "focus";
    if (["readable", "ready"].includes(state)) return "ready";
    return "receive";
  }

  setPhase(phase) {
    if (phase === this.lastPhase) return;
    this.lastPhase = phase;
    this.lastMotionProgress = -1;
    this.processingCard.dataset.phase = phase;
    this.processingCard.classList.remove("is-receiving");
    this.pageStack.classList.remove("is-advancing", "is-complete");
  }

  updateStageMotion(phase, progress) {
    if (phase === "ready" || progress === this.lastMotionProgress) return;
    this.lastMotionProgress = progress;
    if (phase === "receive") this.restartMotion("is-receiving");
  }

  restartMotion(className) {
    this.processingCard.classList.remove(className);
    void this.processingCard.offsetWidth;
    this.processingCard.classList.add(className);
  }

  updatePageStack(document) {
    if (document.state !== "converting") {
      this.pageStack.classList.remove("is-advancing", "is-complete");
      return;
    }
    const progressMatch = String(document.message || "").match(/page\s+(\d+)\s+of\s+(\d+)/i);
    const total = Number(progressMatch?.[2] || document.page_count || 0);
    const current = Number(progressMatch?.[1] || 1);
    const start = Math.max(1, current - this.pageTiles.length + 1);
    this.pageTiles.forEach((tile, index) => {
      const page = start + index;
      tile.textContent = String(total ? Math.min(page, total) : page).padStart(3, "0");
      tile.classList.toggle("is-current", index === this.pageTiles.length - 1);
    });
    if (total && current >= total) {
      this.lastAnimatedPage = current;
      this.pageStack.classList.remove("is-advancing");
      this.pageStack.classList.add("is-complete");
      return;
    }
    const isNewPage = progressMatch && current !== this.lastAnimatedPage;
    if (isNewPage) {
      this.lastAnimatedPage = current;
      this.pageStack.classList.remove("is-advancing");
      void this.pageStack.offsetWidth;
      this.pageStack.classList.add("is-advancing");
      this.pageStack.querySelector(".is-current")?.addEventListener("animationend", () => {
        this.pageStack.classList.remove("is-advancing");
      }, { once: true });
    }
  }

  animatePercent(target) {
    if (target === this.displayedPercent) return;
    cancelAnimationFrame(this.percentAnimation);
    const start = this.displayedPercent;
    const startedAt = performance.now();
    const tick = now => {
      const ratio = Math.min(1, (now - startedAt) / 280);
      const eased = 1 - Math.pow(1 - ratio, 3);
      this.displayedPercent = Math.round(start + (target - start) * eased);
      this.percent.textContent = `${this.displayedPercent}%`;
      if (ratio < 1) this.percentAnimation = requestAnimationFrame(tick);
    };
    this.percentAnimation = requestAnimationFrame(tick);
  }

  async finish() {
    this.setPhase("ready");
    cancelAnimationFrame(this.percentAnimation);
    this.displayedPercent = 100;
    this.percent.textContent = "100%";
    this.progress.style.width = "100%";
    this.processingCard.classList.add("is-ready");
    await new Promise(resolve => setTimeout(resolve, 650));
  }
}
