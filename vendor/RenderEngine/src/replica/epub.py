"""EPUB adapter that paginates structured HTML into editable Replica scenes."""

from __future__ import annotations

import hashlib
import posixpath
import re
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree

from PIL import Image

from .documents import DocumentMetadata
from .scene import create_manifest, scene_directory, write_manifest, write_page


PAGE_WIDTH = 612.0
PAGE_HEIGHT = 792.0
MARGIN_X = 58.0
MARGIN_Y = 58.0
CONTENT_WIDTH = PAGE_WIDTH - MARGIN_X * 2
CONTENT_BOTTOM = PAGE_HEIGHT - MARGIN_Y


@dataclass(frozen=True, slots=True)
class ContentBlock:
    kind: str
    text: str = ""
    source: str = ""


class ChapterParser(HTMLParser):
    BLOCKS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote", "pre"}
    IGNORED = {"head", "script", "style", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[ContentBlock] = []
        self.kind = ""
        self.parts: list[str] = []
        self.ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if tag in self.IGNORED:
            self.ignored_depth += 1
            return
        if self.ignored_depth:
            return
        if tag in self.BLOCKS:
            self._flush()
            self.kind = tag
        elif tag == "br":
            self.parts.append("\n")
        elif tag == "img":
            self._flush()
            values = dict(attrs)
            source = values.get("src") or values.get("href") or ""
            if source:
                self.blocks.append(ContentBlock("image", values.get("alt") or "", source))

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in self.IGNORED:
            self.ignored_depth = max(0, self.ignored_depth - 1)
        elif not self.ignored_depth and tag in self.BLOCKS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if not self.ignored_depth:
            self.parts.append(data)

    def close(self) -> None:
        super().close()
        self._flush()

    def _flush(self) -> None:
        text = re.sub(r"[ \t\r\f\v]+", " ", "".join(self.parts))
        text = re.sub(r" *\n *", "\n", text).strip()
        if text:
            self.blocks.append(ContentBlock(self.kind or "p", text))
        self.kind = ""
        self.parts.clear()


def is_epub(path: Path) -> bool:
    if not zipfile.is_zipfile(path):
        return False
    try:
        with zipfile.ZipFile(path) as archive:
            return archive.read("mimetype").strip() == b"application/epub+zip"
    except (KeyError, OSError, zipfile.BadZipFile):
        return False


def _package(archive: zipfile.ZipFile) -> tuple[str, ElementTree.Element]:
    container = ElementTree.fromstring(archive.read("META-INF/container.xml"))
    rootfile = container.find(".//{*}rootfile")
    if rootfile is None or not rootfile.get("full-path"):
        raise ValueError("EPUB container has no package document.")
    path = rootfile.get("full-path", "")
    return path, ElementTree.fromstring(archive.read(path))


def _year(value: str) -> int | None:
    match = re.search(r"\b(18\d{2}|19\d{2}|20\d{2})\b", value)
    return int(match.group(1)) if match else None


def _text(root: ElementTree.Element, name: str) -> str:
    element = root.find(f".//{{*}}{name}")
    return " ".join("".join(element.itertext()).split()) if element is not None else ""


def inspect_epub(path: Path) -> DocumentMetadata:
    with zipfile.ZipFile(path) as archive:
        _, package = _package(archive)
    return DocumentMetadata(
        title=_text(package, "title"),
        author=_text(package, "creator"),
        year=_year(_text(package, "date")),
    )


def _archive_path(base: str, reference: str) -> str:
    clean = unquote(urlsplit(reference).path)
    return posixpath.normpath(posixpath.join(posixpath.dirname(base), clean)).lstrip("/")


def _chapter_paths(package: ElementTree.Element, package_path: str) -> list[str]:
    items: dict[str, dict[str, str]] = {}
    for item in package.findall(".//{*}manifest/{*}item"):
        identifier = item.get("id")
        href = item.get("href")
        if identifier and href:
            items[identifier] = {
                "path": _archive_path(package_path, href),
                "media_type": item.get("media-type", ""),
            }
    spine = [item.get("idref", "") for item in package.findall(".//{*}spine/{*}itemref")]
    chapters = [items[item_id]["path"] for item_id in spine if item_id in items]
    if not chapters:
        chapters = [item["path"] for item in items.values() if item["media_type"] in {"application/xhtml+xml", "text/html"}]
    return chapters


def _blocks(archive: zipfile.ZipFile, chapter_path: str) -> list[ContentBlock]:
    parser = ChapterParser()
    parser.feed(archive.read(chapter_path).decode("utf-8", errors="replace"))
    parser.close()
    return parser.blocks


def _word_width(text: str, size: float) -> float:
    wide = sum(character in "MW@%" for character in text)
    narrow = sum(character in "ilI.,'`" for character in text)
    return max(size * .28, size * (.52 * len(text) + .18 * wide - .18 * narrow))


def _style(kind: str) -> tuple[float, float, float, str]:
    styles = {
        "h1": (25, 31, 18, "Arial"),
        "h2": (21, 27, 15, "Arial"),
        "h3": (18, 23, 12, "Arial"),
        "h4": (15, 20, 10, "Arial"),
        "h5": (13, 18, 8, "Arial"),
        "h6": (12, 17, 7, "Arial"),
        "blockquote": (12, 18, 10, "Georgia"),
        "pre": (10, 15, 8, "monospace"),
    }
    return styles.get(kind, (12, 17, 8, "Georgia"))


def _save_image(archive: zipfile.ZipFile, chapter_path: str, reference: str, output: Path) -> tuple[str, float, float] | None:
    member = _archive_path(chapter_path, reference)
    try:
        data = archive.read(member)
    except KeyError:
        return None
    suffix = PurePosixPath(member).suffix.casefold()
    if suffix not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}:
        suffix = ".bin"
    name = f"epub-{hashlib.sha1(member.encode()).hexdigest()[:12]}{suffix}"
    target = output / "assets" / "images" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file():
        target.write_bytes(data)
    width, height = 360.0, 240.0
    if suffix != ".svg":
        try:
            with Image.open(target) as image:
                width, height = map(float, image.size)
        except OSError:
            pass
    scale = min(1.0, CONTENT_WIDTH / max(1, width), 300 / max(1, height))
    return f"assets/images/{name}", width * scale, height * scale


def convert_epub(
    epub: Path | str,
    output: Path | str = Path("book"),
    *,
    progress=None,
) -> dict:
    """Convert an EPUB spine into fixed, editable Replica pages."""
    epub = Path(epub)
    if not is_epub(epub):
        raise ValueError("The input is not a valid EPUB file.")
    destination = Path(output)
    with scene_directory(destination) as output:
        return _convert_epub(epub, output, progress=progress)


def _convert_epub(epub: Path, output: Path, *, progress=None) -> dict:
    """Write an EPUB into an empty, private scene directory."""

    pages: list[dict] = []
    page = None
    cursor_y = MARGIN_Y
    word_id = 0

    def new_page() -> None:
        nonlocal page, cursor_y, word_id
        page = {"number": len(pages) + 1, "width": PAGE_WIDTH, "height": PAGE_HEIGHT, "words": [], "images": [], "lines": []}
        pages.append(page)
        cursor_y = MARGIN_Y
        word_id = 0

    def ensure_space(height: float) -> None:
        if page is None or (cursor_y + height > CONTENT_BOTTOM and (page["words"] or page["images"])):
            new_page()

    with zipfile.ZipFile(epub) as archive:
        package_path, package = _package(archive)
        chapters = _chapter_paths(package, package_path)
        for chapter_index, chapter_path in enumerate(chapters):
            if chapter_index and page and (page["words"] or page["images"]):
                new_page()
            for block in _blocks(archive, chapter_path):
                if block.kind == "image":
                    image = _save_image(archive, chapter_path, block.source, output)
                    if image:
                        source, width, height = image
                        ensure_space(height + 16)
                        page["images"].append({"src": source, "x": round((PAGE_WIDTH - width) / 2, 4), "y": round(cursor_y, 4), "width": round(width, 4), "height": round(height, 4)})
                        cursor_y += height + 16
                    continue

                size, line_height, spacing, font = _style(block.kind)
                indent = 22.0 if block.kind in {"li", "blockquote"} else 0.0
                tokens = block.text.replace("\n", " __REPLICA_LINE_BREAK__ ").split()
                if block.kind == "li":
                    tokens.insert(0, "•")
                lines: list[list[str]] = [[]]
                width = 0.0
                for token in tokens:
                    if token == "__REPLICA_LINE_BREAK__":
                        lines.append([])
                        width = 0.0
                        continue
                    token_width = _word_width(token, size)
                    gap = size * .3 if lines[-1] else 0.0
                    if lines[-1] and width + gap + token_width > CONTENT_WIDTH - indent:
                        lines.append([])
                        width = 0.0
                        gap = 0.0
                    lines[-1].append(token)
                    width += gap + token_width
                lines = [line for line in lines if line]
                if not lines:
                    continue
                ensure_space(len(lines) * line_height + spacing)
                for line in lines:
                    ensure_space(line_height)
                    x = MARGIN_X + indent
                    for token in line:
                        width = _word_width(token, size)
                        page["words"].append({
                            "id": word_id, "text": token, "x": round(x, 4), "y": round(cursor_y, 4),
                            "width": round(width, 4), "height": size, "baseline": round(cursor_y + size, 4),
                            "font": font, "size": size, "color": "#000000",
                        })
                        word_id += 1
                        x += width + size * .3
                    cursor_y += line_height
                cursor_y += spacing
            if progress:
                progress(chapter_index + 1, len(chapters))

        metadata = DocumentMetadata(
            title=_text(package, "title"),
            author=_text(package, "creator"),
            year=_year(_text(package, "date")),
        )

    if not pages:
        new_page()
    for record in pages:
        write_page(output, record)
    manifest = create_manifest(
        document_id=re.sub(r"[^a-z0-9]+", "-", epub.stem.casefold()).strip("-") or "book",
        title=metadata.title or epub.stem,
        author=metadata.author,
        publication_year=metadata.year,
        format_name="epub",
        page_count=len(pages),
        editable_word_count=sum(len(record["words"]) for record in pages),
    )
    write_manifest(output, manifest)
    return manifest
