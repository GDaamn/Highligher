import { listDeviceBooks, removeDeviceBook } from "./device-library.js";

export class HighlighterApi {
  async health() {
    return this.#json(fetch("/api/health", { signal: AbortSignal.timeout(5000) }));
  }

  async library() {
    const [local, hosted] = await Promise.allSettled([
      listDeviceBooks(), this.#json(fetch("/api/library", { signal: AbortSignal.timeout(5000) })),
    ]);
    if (local.status === "rejected" && hosted.status === "rejected") throw hosted.reason;
    return [...(local.value || []), ...(hosted.value || [])];
  }

  async remove(documentId) {
    if (documentId.startsWith("device-")) return removeDeviceBook(documentId);
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
        else if ([502, 503, 504].includes(request.status)) {
          reject(new Error(`The service was unavailable during upload (HTTP ${request.status}). Check your library before trying again.`));
        } else {
          reject(new Error(request.response?.detail || `Upload failed (HTTP ${request.status}).`));
        }
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

  async document(documentId, signal) {
    return this.#json(fetch(`/api/documents/${documentId}`, { signal }));
  }

  async focus(documentId, pageNumber) {
    const response = await fetch(`/api/documents/${documentId}/focus/${pageNumber}`);
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
    let interruptions = 0;
    let lastStatus = { state: "queued", progress: 0.08 };
    while (true) {
      let document;
      try {
        document = await this.document(documentId, AbortSignal.timeout(15000));
      } catch (error) {
        const transient = error instanceof TypeError || error.name === "TimeoutError"
          || [502, 503, 504].includes(error.status);
        if (!transient) throw error;
        if (++interruptions > 6) {
          throw new Error("Upload accepted, but the server is still unavailable. Check your library after it reconnects.");
        }
        onUpdate({ ...lastStatus, message: "Upload accepted. Reconnecting to check progress…" });
        await new Promise(resolve => setTimeout(resolve, Math.min(1000 * 2 ** (interruptions - 1), 8000)));
        continue;
      }
      interruptions = 0;
      lastStatus = document;
      onUpdate(document);
      if (["readable", "ready"].includes(document.state)) return document;
      if (document.state === "error") throw new Error(document.error || document.message);
      await new Promise(resolve => setTimeout(resolve, interval));
    }
  }

  async #json(responseOrPromise) {
    const response = await responseOrPromise;
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(body.detail || `Request failed (${response.status}).`);
      error.status = response.status;
      throw error;
    }
    return body;
  }
}
