import { metadataForRecord } from "../lib/book-metadata.js";

export class CoverLayer {
  constructor({ engine, document: documentRecord }) {
    this.engine = engine;
    this.document = documentRecord;
    this.originalVisible = false;
    this.renderListener = event => {
      if (event.detail.pageNumber === 1) this.apply();
    };
    this.sourceListener = event => this.setOriginalVisible(event.detail.visible);
    engine.addEventListener("pagerender", this.renderListener);
    engine.addEventListener("sourcechange", this.sourceListener);
  }

  apply() {
    const page = this.engine.getPageElement(1);
    if (!page || page.querySelector(".reader-generated-cover")) return;
    const { title, author, year } = metadataForRecord(this.document);
    const cover = page.ownerDocument.createElement("article");
    cover.className = "reader-generated-cover";
    cover.innerHTML = `
      <div class="reader-cover-copy">
        <h1></h1>
        <h2></h2>
        <p></p>
      </div>
    `;
    cover.querySelector("h1").textContent = title;
    cover.querySelector("h2").textContent = author ? `By ${author}` : "";
    cover.querySelector("p").textContent = year || "";
    cover.hidden = this.originalVisible;
    page.append(cover);
  }

  setOriginalVisible(visible) {
    this.originalVisible = Boolean(visible);
    const cover = this.engine.getPageElement(1)?.querySelector(".reader-generated-cover");
    if (cover) cover.hidden = this.originalVisible;
  }

  destroy() {
    this.engine.removeEventListener("pagerender", this.renderListener);
    this.engine.removeEventListener("sourcechange", this.sourceListener);
  }
}
