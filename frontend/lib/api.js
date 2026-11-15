export class HighlighterApi {
  routerKey = "";
  focusRevision = 0;

  async health() {
    return this.#json(fetch("/api/health"));
  }

  async library() {
    return this.#json(fetch("/api/library"));
  }

  async remove(documentId) {
    const response = await fetch(`/api/documents/${encodeURIComponent(documentId)}`, { method: "DELETE" });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || `Removal failed (${response.status}).`);
    }
  }

  upload(file, onProgress = () => {}) {
    return new Promise((resolve, reject) => {
      const request = new XMLHttpRequest();
      request.open("POST", "/api/documents");
      request.responseType = "json";
      request.upload.addEventListener("progress", event => {
        if (event.lengthComputable) onProgress(event.loaded / event.total);
      });
      request.addEventListener("load", () => {
        if (request.status >= 200 && request.status < 300) resolve(request.response);
        else reject(new Error(request.response?.detail || "Upload failed."));
      });
      request.addEventListener("error", async () => {
        try {
          const health = await fetch("/api/health", { cache: "no-store" });
          if (health.ok) {
            reject(new Error("The browser lost access to the selected file. Download or copy it to a local folder, then choose it again."));
            return;
          }
        } catch {
          // The health check failed too, so this is a genuine server connection error.
        }
        reject(new Error("The server could not be reached."));
      });
      const body = new FormData();
      body.append("file", file);
      request.send(body);
    });
  }

  async lookup(sha256, size) {
    const response = await fetch(`/api/documents/lookup?sha256=${encodeURIComponent(sha256)}&size=${size}`);
    if (response.status === 404) return null;
    return this.#json(response);
  }

  async document(documentId) {
    return this.#json(fetch(`/api/documents/${documentId}`));
  }

  async savedFocus(documentId, pageNumber) {
    return this.#json(fetch(`/api/documents/${documentId}/focus/${pageNumber}`));
  }

  async focus(documentId, pageNumber) {
    const url = `/api/documents/${documentId}/focus/${pageNumber}`;
    const response = this.routerKey
      ? await fetch(url + "/openrouter", { method: "POST", headers: { Authorization: "Bearer " + this.routerKey } })
      : await fetch(url);
    if (response.status === 404) return null;
    return this.#json(response);
  }

  async focusAvailability(documentId) {
    return this.#json(fetch(`/api/documents/${encodeURIComponent(documentId)}/focus-ready`));
  }

  async structure(documentId) {
    return this.#json(fetch(`/api/documents/${encodeURIComponent(documentId)}/structure`));
  }

  async preferences(documentId) {
    return this.#json(fetch(`/api/documents/${encodeURIComponent(documentId)}/preferences`));
  }

  async setPageDimmed(documentId, pageNumber, dimmed) {
    return this.#json(fetch(`/api/documents/${encodeURIComponent(documentId)}/preferences/pages/${pageNumber}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dimmed }),
    }));
  }

  async setReadingState(documentId, page, mode) {
    return this.#json(fetch(`/api/documents/${encodeURIComponent(documentId)}/preferences/reading`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ page, mode }),
    }));
  }

  async setWordFocus(documentId, pageNumber, wordId, focused) {
    return this.#json(fetch(`/api/documents/${encodeURIComponent(documentId)}/focus/${pageNumber}/words/${wordId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ focused }),
    }));
  }

  async waitUntilReadable(documentId, onUpdate, { interval = 700 } = {}) {
    while (true) {
      const document = await this.document(documentId);
      onUpdate(document);
      if (["readable", "ready"].includes(document.state)) return document;
      if (document.state === "error") throw new Error(document.error || document.message);
      await new Promise(resolve => setTimeout(resolve, interval));
    }
  }

  async #json(responseOrPromise) {
    const response = await responseOrPromise;
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || `Request failed (${response.status}).`);
    return body;
  }
}
