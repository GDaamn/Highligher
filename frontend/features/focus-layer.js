export class FocusLayer {
  constructor({ engine, api, documentId, root }) {
    this.engine = engine;
    this.api = api;
    this.documentId = documentId;
    this.root = root;
    this.cache = new Map();
    this.manualDimmed = new Set();
    this.readyPages = new Set();
    this.focusComplete = false;
    this.availabilityTimer = null;
    this.destroyed = false;
    this.enabled = false;
    this.activePage = engine.pageNumber;
    this.pageListener = event => {
      this.activePage = event.detail.pageNumber;
      this.applyNearby(this.activePage);
    };
    this.renderListener = event => {
      const pageNumber = event.detail.pageNumber;
      if (this.readyPages.has(pageNumber)) this.applySafely(pageNumber);
      else this.engine.getPageElement(pageNumber)?.classList.add("focus-pending");
    };
  }

  async start(preferences = null) {
    preferences ||= await this.api.preferences(this.documentId);
    this.manualDimmed = new Set(preferences.dimmed_pages || []);
    this.activePage = this.engine.pageNumber;
    await this.refreshAvailability({ apply: false });
    const visible = [this.activePage - 1, this.activePage, this.activePage + 1];
    await Promise.all(visible.map(page => this.applyIfReady(page)));
    for (const page of this.readyPages) {
      if (!visible.includes(page) && this.engine.getPageElement(page)) this.applySafely(page);
    }
    this.engine.addEventListener("pagechange", this.pageListener);
    this.engine.addEventListener("pagerender", this.renderListener);
    this.scheduleAvailabilityRefresh();
  }

  applyNearby(pageNumber) {
    for (const nearby of [pageNumber - 1, pageNumber, pageNumber + 1]) {
      if (nearby < 1 || !this.engine.getPageElement(nearby)) continue;
      if (this.readyPages.has(nearby)) this.applySafely(nearby);
      else this.engine.getPageElement(nearby)?.classList.add("focus-pending");
    }
  }

  async applyIfReady(pageNumber) {
    if (pageNumber < 1 || !this.engine.getPageElement(pageNumber)) return;
    if (this.readyPages.has(pageNumber)) await this.apply(pageNumber);
    else this.engine.getPageElement(pageNumber)?.classList.add("focus-pending");
  }

  applySafely(pageNumber) {
    this.apply(pageNumber).catch(error => {
      globalThis.dispatchEvent(new CustomEvent("highlighter:error", { detail: error }));
    });
  }

  async apply(pageNumber = this.engine.pageNumber) {
    if (this.destroyed) return;
    let page = this.engine.getPageElement(pageNumber);
    page?.classList.add("focus-pending");
    const focus = await this.load(pageNumber);
    if (this.destroyed) return;
    page = this.engine.getPageElement(pageNumber);
    if (!page) return;
    if (!focus) {
      this.readyPages.delete(pageNumber);
      this.scheduleAvailabilityRefresh();
      return;
    }
    const selected = new Set(focus.focusWordIds);
    const contextual = new Set(focus.contextWordIds || []);
    const corrected = new Set(focus.correctedWordIds || []);
    page.classList.toggle("focus-bypass", Boolean(focus.bypassFocus));
    page.classList.toggle("focus-dim-page", Boolean(focus.dimPage));
    page.classList.toggle("manual-dim-page", this.manualDimmed.has(pageNumber));
    if (!page.dataset.focusToggleBound) {
      page.dataset.focusToggleBound = "true";
      page.addEventListener("click", event => this.toggleManualDim(pageNumber, page, event));
    }
    for (const box of page.querySelectorAll(".replica-word-box")) {
      box.classList.toggle("speed-focus", focus.bypassFocus || selected.has(Number(box.dataset.wordId)));
      box.classList.toggle("speed-context", !focus.bypassFocus && contextual.has(Number(box.dataset.wordId)));
      box.classList.toggle("focus-corrected", corrected.has(Number(box.dataset.wordId)));
    }
    page.classList.remove("focus-pending");
  }

  async load(pageNumber) {
    if (!this.cache.has(pageNumber)) this.cache.set(pageNumber, this.api.focus(this.documentId, pageNumber));
    try {
      const focus = await this.cache.get(pageNumber);
      if (!focus) this.cache.delete(pageNumber);
      return focus;
    } catch (error) {
      this.cache.delete(pageNumber);
      throw error;
    }
  }

  async refreshAvailability({ apply = true } = {}) {
    const availability = await this.api.focusAvailability(this.documentId);
    const newPages = availability.pages.filter(page => !this.readyPages.has(page));
    newPages.forEach(page => this.readyPages.add(page));
    this.focusComplete = availability.complete;
    if (apply) {
      newPages.forEach(page => {
        if (this.engine.getPageElement(page)) this.applySafely(page);
      });
    }
  }

  scheduleAvailabilityRefresh() {
    if (this.destroyed || this.focusComplete || this.availabilityTimer) return;
    this.availabilityTimer = setTimeout(async () => {
      this.availabilityTimer = null;
      try {
        await this.refreshAvailability();
      } catch (error) {
        globalThis.dispatchEvent(new CustomEvent("highlighter:error", { detail: error }));
      }
      this.scheduleAvailabilityRefresh();
    }, 1000);
  }

  setOnly(enabled) {
    this.enabled = Boolean(enabled);
    this.root.classList.toggle("focus-only", Boolean(enabled));
  }

  async toggleManualDim(pageNumber, page, event) {
    if (!this.enabled || event.defaultPrevented) return;
    const selection = globalThis.getSelection?.();
    if (selection && !selection.isCollapsed) return;
    const word = event.target.closest?.(".replica-word-box");
    if (word) {
      event.stopPropagation();
      await this.toggleWord(pageNumber, word);
      return;
    }
    const dimmed = !this.manualDimmed.has(pageNumber);
    if (dimmed) this.manualDimmed.add(pageNumber);
    else this.manualDimmed.delete(pageNumber);
    page.classList.toggle("manual-dim-page", dimmed);
    try {
      await this.api.setPageDimmed(this.documentId, pageNumber, dimmed);
    } catch (error) {
      if (dimmed) this.manualDimmed.delete(pageNumber);
      else this.manualDimmed.add(pageNumber);
      page.classList.toggle("manual-dim-page", !dimmed);
      globalThis.dispatchEvent(new CustomEvent("highlighter:error", { detail: error }));
    }
  }

  async toggleWord(pageNumber, word) {
    const wordId = Number(word.dataset.wordId);
    const focused = !word.classList.contains("speed-focus");
    word.classList.toggle("speed-focus", focused);
    word.classList.toggle("speed-context", false);
    word.classList.add("focus-corrected");
    try {
      const payload = await this.api.setWordFocus(this.documentId, pageNumber, wordId, focused);
      this.cache.set(pageNumber, Promise.resolve(payload));
      await this.apply(pageNumber);
      globalThis.dispatchEvent(new CustomEvent("highlighter:notice", {
        detail: focused ? "Kept in focus" : "Moved to quiet text",
      }));
    } catch (error) {
      this.cache.delete(pageNumber);
      await this.apply(pageNumber);
      globalThis.dispatchEvent(new CustomEvent("highlighter:error", { detail: error }));
    }
  }

  destroy() {
    this.destroyed = true;
    this.engine.removeEventListener("pagechange", this.pageListener);
    this.engine.removeEventListener("pagerender", this.renderListener);
    this.cache.clear();
    this.manualDimmed.clear();
    clearTimeout(this.availabilityTimer);
    this.readyPages.clear();
    this.availabilityTimer = null;
  }
}
