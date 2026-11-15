import { bookByline, metadataForRecord } from "../lib/book-metadata.js";

const COVER_COLORS = ["#ff5b35", "#f2c94c", "#4a80d8", "#8ebf55", "#d9d8d0"];

function identity(documentRecord) {
  const metadata = metadataForRecord(documentRecord);
  return {
    title: metadata.title,
    byline: bookByline(metadata),
  };
}

function colorFor(value) {
  const number = [...value].reduce((total, character) => total + character.charCodeAt(0), 0);
  return COVER_COLORS[number % COVER_COLORS.length];
}

export class LibraryDock {
  constructor({ api, root, onSelect, onRemove = () => {}, onError = () => {} }) {
    this.api = api;
    this.root = root;
    this.onSelect = onSelect;
    this.onRemove = onRemove;
    this.onError = onError;
    this.track = root.querySelector("#library-track");
    this.caption = root.querySelector("#library-caption");
    this.documents = [];
    this.activeId = null;
    this.drag = null;
    this.removeTimer = null;
    this.suppressClick = false;
    this.bind();
  }

  bind() {
    this.track.addEventListener("pointermove", event => this.magnify(event));
    this.track.addEventListener("pointerleave", () => this.resetMagnification());
    this.track.addEventListener("wheel", event => {
      if (Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return;
      event.preventDefault();
      this.track.scrollTop += event.deltaY;
    }, { passive: false });
    document.addEventListener("dragover", event => this.moveDrag(event));
    document.addEventListener("drop", event => this.dropDrag(event));
    this.root.addEventListener("pointerleave", () => this.root.classList.remove("is-selection-collapsed"));
    this.root.querySelector(".library-handle")?.addEventListener("pointerenter", () => {
      this.root.classList.remove("is-selection-collapsed");
    });
  }

  async refresh() {
    try {
      const seen = new Set();
      this.documents = (await this.api.library()).filter(documentRecord => {
        const { title, byline } = identity(documentRecord);
        const key = `${title}\u0000${byline}`.toLocaleLowerCase();
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      });
      this.render();
    } catch (error) {
      this.onError(error);
    }
  }

  render() {
    const fragment = document.createDocumentFragment();
    this.documents.forEach(documentRecord => {
      const { title, byline } = identity(documentRecord);
      const button = document.createElement("button");
      button.className = "library-book";
      button.type = "button";
      button.draggable = true;
      button.setAttribute("role", "listitem");
      button.dataset.documentId = documentRecord.id;
      button.setAttribute("aria-label", byline ? `${title}, ${byline}` : title);
      button.style.setProperty("--cover-color", colorFor(documentRecord.id));

      const fallback = document.createElement("span");
      fallback.className = "library-cover-fallback";
      fallback.textContent = title.split(/\s+/).slice(0, 2).map(word => word[0]).join("").toUpperCase();
      button.append(fallback);
      if (documentRecord.thumbnail || !documentRecord.device) {
        const cover = document.createElement("img");
        cover.alt = "";
        cover.loading = "lazy";
        cover.draggable = false;
        cover.src = documentRecord.thumbnail || `/documents/${documentRecord.id}/scene/source-pages/page-0001.webp`;
        cover.addEventListener("error", () => cover.remove(), { once: true });
        button.append(cover);
      }
      button.addEventListener("click", () => {
        if (this.suppressClick) return;
        this.collapseAfterSelection(button);
        this.onSelect(documentRecord);
      });
      button.addEventListener("dragstart", event => this.beginDrag(event, button, documentRecord));
      button.addEventListener("dragend", () => this.endDrag());
      button.addEventListener("focus", () => this.showCaption(title, byline));
      button.addEventListener("blur", () => this.resetMagnification());
      fragment.append(button);
    });
    this.track.replaceChildren(fragment);
    this.root.hidden = this.documents.length === 0;
    this.setActive(this.activeId);
  }

  magnify(event) {
    const bounds = this.track.getBoundingClientRect();
    const pointer = event.clientY - bounds.top + this.track.scrollTop;
    const books = [...this.track.querySelectorAll(".library-book")];
    let nearest = null;
    books.forEach((book, index) => {
      const center = book.offsetTop + book.offsetHeight / 2;
      const distance = Math.abs(pointer - center);
      if (!nearest || distance < nearest.distance) nearest = { book, distance, index, center };
    });
    if (nearest) {
      const activeTop = nearest.book.offsetTop;
      const localX = Math.max(-1, Math.min(1, (event.clientX - bounds.left) / bounds.width * 2 - 1));
      const localY = Math.max(-1, Math.min(1, (pointer - activeTop) / nearest.book.offsetHeight * 2 - 1));
      books.forEach((book, index) => {
        const offset = index - nearest.index;
        const distance = Math.abs(offset);
        const isFocused = offset === 0;
        book.classList.toggle("is-dock-focus", isFocused);
        book.style.setProperty("--dock-scale", isFocused ? "1.105" : distance === 1 ? ".965" : ".94");
        book.style.setProperty("--dock-shift-x", isFocused ? "17px" : `${Math.max(-10, -4 - distance * 2)}px`);
        book.style.setProperty("--dock-shift-y", isFocused ? "0px" : `${Math.sign(offset) * (18 + Math.min(distance, 3) * 7)}px`);
        book.style.setProperty("--dock-depth", isFocused ? "48px" : `${Math.max(0, 18 - distance * 8)}px`);
        book.style.setProperty("--dock-rotate-x", isFocused ? `${(-localY * 3.2).toFixed(2)}deg` : "0deg");
        book.style.setProperty("--dock-rotate-y", isFocused ? `${(-8 + localX * 3).toFixed(2)}deg` : "0deg");
        book.style.zIndex = isFocused ? "40" : String(20 - Math.min(distance, 10));
      });
      const record = this.documents.find(item => item.id === nearest.book.dataset.documentId);
      if (record) {
        const { title, byline } = identity(record);
        this.showCaption(title, byline);
      }
    }
  }

  showCaption(title, byline) {
    this.caption.replaceChildren();
    const strong = document.createElement("strong");
    strong.textContent = title;
    const span = document.createElement("span");
    span.textContent = byline;
    this.caption.append(strong, span);
    this.root.classList.add("has-caption");
  }

  resetMagnification() {
    for (const book of this.track.querySelectorAll(".library-book")) {
      book.classList.remove("is-dock-focus");
      for (const property of ["--dock-scale", "--dock-shift-x", "--dock-shift-y", "--dock-depth", "--dock-rotate-x", "--dock-rotate-y"]) {
        book.style.removeProperty(property);
      }
      book.style.removeProperty("z-index");
    }
    this.root.classList.remove("has-caption");
  }

  collapseAfterSelection(button) {
    this.resetMagnification();
    button.blur();
    this.root.classList.add("is-selection-collapsed");
  }

  beginDrag(event, book, documentRecord) {
    this.cancelDrag();
    this.suppressClick = true;
    this.drag = { book, documentRecord, armed: false, readyToRemove: false };
    this.root.classList.add("is-dragging");
    book.classList.add("is-drag-source");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("application/x-highlighter-book", documentRecord.id);

    const emptyImage = document.createElement("canvas");
    emptyImage.width = 1;
    emptyImage.height = 1;
    emptyImage.className = "library-native-drag-shim";
    document.body.append(emptyImage);
    event.dataTransfer.setDragImage(emptyImage, 0, 0);
    this.drag.nativeDragShim = emptyImage;

    const ghost = book.cloneNode(true);
    ghost.className = "library-drag-ghost";
    ghost.draggable = false;
    ghost.removeAttribute("role");
    ghost.removeAttribute("aria-label");
    const deleteIcon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    deleteIcon.classList.add("library-delete-icon");
    deleteIcon.setAttribute("viewBox", "0 0 24 24");
    deleteIcon.setAttribute("aria-hidden", "true");
    const deletePath = document.createElementNS("http://www.w3.org/2000/svg", "path");
    deletePath.setAttribute("d", "M3 6h18M8 6V3h8v3M5 6l1 15h12l1-15M9 10v7M15 10v7");
    deleteIcon.append(deletePath);
    ghost.append(deleteIcon);
    document.body.append(ghost);
    this.drag.ghost = ghost;
    this.positionGhost(event);
  }

  moveDrag(event) {
    if (!this.drag) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    this.positionGhost(event);
    const bounds = this.root.getBoundingClientRect();
    const outside = event.clientX < bounds.left || event.clientX > bounds.right + 8 || event.clientY < bounds.top || event.clientY > bounds.bottom;
    this.setRemoveArmed(outside);
  }

  positionGhost(event) {
    if (!this.drag?.ghost) return;
    this.drag.ghost.style.left = `${event.clientX}px`;
    this.drag.ghost.style.top = `${event.clientY}px`;
  }

  setRemoveArmed(armed) {
    if (!this.drag || armed === this.drag.armed) return;
    this.drag.armed = armed;
    this.root.classList.toggle("is-remove-armed", armed);
    this.root.classList.remove("is-remove-ready");
    this.drag.ghost?.classList.remove("is-remove-ready");
    this.drag.readyToRemove = false;
    clearTimeout(this.removeTimer);
    this.removeTimer = null;
    if (armed) {
      this.removeTimer = setTimeout(() => this.markRemoveReady(), 850);
    }
  }

  markRemoveReady() {
    if (!this.drag?.armed) return;
    this.drag.readyToRemove = true;
    this.root.classList.add("is-remove-ready");
    this.drag.ghost?.classList.add("is-remove-ready");
  }

  async removeDraggedDocument() {
    if (!this.drag?.readyToRemove) return;
    const documentRecord = this.drag.documentRecord;
    this.cancelDrag();
    this.root.classList.add("is-removing");
    try {
      await this.api.remove(documentRecord.id);
      this.documents = this.documents.filter(item => item.id !== documentRecord.id);
      if (this.activeId === documentRecord.id) this.activeId = null;
      this.render();
      this.onRemove(documentRecord);
    } catch (error) {
      this.onError(error);
    } finally {
      this.suppressClick = false;
      this.root.classList.remove("is-removing");
    }
  }

  dropDrag(event) {
    if (!this.drag) return;
    event.preventDefault();
    if (this.drag.readyToRemove && this.drag.armed) {
      void this.removeDraggedDocument();
      return;
    }
    this.cancelDrag();
    setTimeout(() => { this.suppressClick = false; }, 0);
  }

  endDrag() {
    if (!this.drag) return;
    this.cancelDrag();
    setTimeout(() => { this.suppressClick = false; }, 0);
  }

  cancelDrag() {
    clearTimeout(this.removeTimer);
    this.removeTimer = null;
    if (this.drag) {
      const { book, ghost, nativeDragShim } = this.drag;
      book.classList.remove("is-drag-source");
      ghost?.remove();
      nativeDragShim?.remove();
    }
    this.root.classList.remove("is-dragging", "is-remove-armed", "is-remove-ready");
    this.drag = null;
  }

  setActive(documentId) {
    this.activeId = documentId || null;
    for (const book of this.track.querySelectorAll(".library-book")) {
      book.classList.toggle("is-active", book.dataset.documentId === this.activeId);
    }
  }
}
