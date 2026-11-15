#!/usr/bin/env python3
"""Convert a positioned-text PDF into the editable replica scene format."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pdfplumber
from PIL import Image
from pypdf import PdfReader

from .documents import DocumentMetadata
from .scene import create_manifest, scene_directory, write_manifest, write_page


def is_pdf(path: Path) -> bool:
    try:
        with path.open("rb") as source:
            return source.read(5) == b"%PDF-"
    except OSError:
        return False


def inspect_pdf(path: Path) -> DocumentMetadata:
    try:
        metadata = PdfReader(str(path)).metadata or {}
    except Exception:
        return DocumentMetadata()
    return DocumentMetadata(
        title=str(metadata.get("/Title") or ""),
        author=str(metadata.get("/Author") or ""),
    )


def slug(value: str) -> str:
    value = value.split("+")[-1]
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "font"


def resolve(obj):
    return obj.get_object() if hasattr(obj, "get_object") else obj


def font_descriptor(font):
    descriptor = resolve(font.get("/FontDescriptor")) if font.get("/FontDescriptor") else None
    if descriptor:
        return descriptor
    descendants = resolve(font.get("/DescendantFonts")) if font.get("/DescendantFonts") else []
    if descendants:
        descendant = resolve(descendants[0])
        return resolve(descendant.get("/FontDescriptor")) if descendant.get("/FontDescriptor") else None
    return None


def extract_fonts(reader: PdfReader, output: Path) -> dict[str, dict]:
    fonts_dir = output / "assets" / "fonts"
    fonts_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, dict] = {}

    for page in reader.pages:
        resources = resolve(page.get("/Resources")) or {}
        fonts = resolve(resources.get("/Font")) or {}
        for _, reference in fonts.items():
            font = resolve(reference)
            base = str(font.get("/BaseFont", "Unknown")).lstrip("/")
            if base in result:
                continue

            record = {"family": base, "file": None, "format": None}
            descriptor = font_descriptor(font)
            if descriptor:
                for key, extension, css_format in (
                    ("/FontFile2", ".ttf", "truetype"),
                    ("/FontFile3", ".otf", "opentype"),
                    ("/FontFile", ".pfb", "type1"),
                ):
                    stream_ref = descriptor.get(key)
                    if not stream_ref:
                        continue
                    stream = resolve(stream_ref)
                    filename = f"{slug(base)}{extension}"
                    (fonts_dir / filename).write_bytes(stream.get_data())
                    record.update(
                        file=f"assets/fonts/{filename}",
                        format=css_format,
                    )
                    break
            result[base] = record
    return result


def save_page_images(pdf_page, plumber_page, output: Path, page_number: int) -> list[dict]:
    image_dir = output / "assets" / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    extracted = {image.name: image for image in pdf_page.images}
    records = []

    for index, item in enumerate(plumber_page.images, 1):
        name = str(item.get("name", ""))
        candidate = next(
            (value for key, value in extracted.items() if key == name or key.rsplit(".", 1)[0] == name),
            None,
        )
        if candidate is None:
            continue
        extension = Path(candidate.name).suffix or ".png"
        filename = f"page-{page_number:04d}-{index:02d}{extension}"
        path = image_dir / filename
        try:
            candidate.image.save(path)
        except Exception:
            path.write_bytes(candidate.data)
        records.append(
            {
                "src": f"assets/images/{filename}",
                "x": round(float(item["x0"]), 4),
                "y": round(float(item["top"]), 4),
                "width": round(float(item["width"]), 4),
                "height": round(float(item["height"]), 4),
            }
        )
    return records


def color_to_css(value) -> str:
    if value is None:
        return "#000000"
    if isinstance(value, (int, float)):
        channel = max(0, min(255, round(float(value) * 255)))
        return f"#{channel:02x}{channel:02x}{channel:02x}"
    if len(value) == 1:
        channel = max(0, min(255, round(float(value[0]) * 255)))
        return f"#{channel:02x}{channel:02x}{channel:02x}"
    if len(value) >= 3:
        channels = [max(0, min(255, round(float(x) * 255))) for x in value[:3]]
        return "#" + "".join(f"{x:02x}" for x in channels)
    return "#000000"


def normalize_symbol_text(font_name: str, value: str) -> str:
    """Translate common PDF symbol-font private-use codes to real Unicode."""
    normalized_font = font_name.lower()
    if "symbol" in normalized_font:
        value = value.replace("\uf0b7", "\u2022")
    if "wingdings" in normalized_font:
        value = value.replace("\uf04a", "\u263a")
    return value


def extract_words(page) -> list[dict]:
    words = page.extract_words(
        x_tolerance=1.5,
        y_tolerance=2,
        keep_blank_chars=False,
        use_text_flow=False,
        extra_attrs=["fontname", "size"],
        return_chars=True,
    )
    result = []
    for index, word in enumerate(words):
        chars = word.pop("chars")
        first = chars[0]
        matrix = first.get("matrix") or (1, 0, 0, 1, first["x0"], page.height - first["bottom"])
        font_name = str(word.get("fontname", "serif"))
        result.append(
            {
                "id": index,
                "text": normalize_symbol_text(font_name, word["text"]),
                "x": round(float(word["x0"]), 4),
                "y": round(float(word["top"]), 4),
                "width": round(float(word["width"]), 4),
                "height": round(float(word["height"]), 4),
                "baseline": round(float(page.height - matrix[5]), 4),
                "font": font_name,
                "size": round(float(word.get("size", word["height"])), 4),
                "color": color_to_css(first.get("non_stroking_color")),
            }
        )
    return result


def extract_lines(page) -> list[dict]:
    records = []
    for line in page.lines:
        records.append(
            {
                "x1": round(float(line["x0"]), 4),
                "y1": round(float(line["top"]), 4),
                "x2": round(float(line["x1"]), 4),
                "y2": round(float(line["bottom"]), 4),
                "width": round(float(line.get("linewidth") or 0.5), 4),
                "color": color_to_css(line.get("stroking_color")),
            }
        )
    return records


def detect_document_mode(document) -> str:
    sample_count = min(12, len(document.pages))
    scanned_pages = 0
    for page in document.pages[:sample_count]:
        page_area = float(page.width * page.height)
        has_full_page_image = any(
            float(image.get("width", 0) * image.get("height", 0)) >= page_area * 0.85
            for image in page.images
        )
        if has_full_page_image:
            scanned_pages += 1
    return "scan" if scanned_pages >= max(1, sample_count * 0.75) else "digital"


def find_poppler() -> str | None:
    configured = os.getenv("REPLICA_PDFTOPPM")
    if configured:
        path = Path(configured).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"REPLICA_PDFTOPPM does not point to a file: {path}")
        return str(path)
    return shutil.which("pdftoppm")


def render_sources(
    pdf: Path,
    output: Path,
    dpi: int,
    total_pages: int,
    executable: str,
    progress=None,
) -> bool:
    target = output / "source-pages"
    target.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [executable, "-png", "-r", str(dpi), str(pdf), str(target / "page")],
    )
    last_rendered = -1
    while process.poll() is None:
        rendered = len(list(target.glob("page-*.png")))
        if progress and rendered != last_rendered:
            progress("render", rendered, total_pages)
            last_rendered = rendered
        time.sleep(0.2)
    if process.returncode:
        raise subprocess.CalledProcessError(process.returncode, process.args)
    paths = sorted(target.glob("page-*.png"))
    if progress:
        progress("render", len(paths), total_pages)
    for index, path in enumerate(paths, 1):
        number = int(path.stem.rsplit("-", 1)[1])
        final = target / f"page-{number:04d}.webp"
        with Image.open(path) as image:
            image.save(final, "WEBP", quality=82, method=2)
        path.unlink()
        if progress and (index == len(paths) or index % 5 == 0):
            progress("encode", index, len(paths))
    return bool(paths)


def convert_pdf(
    pdf: Path | str,
    output: Path | str = Path("book"),
    *,
    render_source: bool = False,
    preview_dpi: int = 120,
    mode: str = "auto",
    progress=None,
    source_progress=None,
) -> dict:
    """Convert a PDF to the versioned Replica scene format.

    `progress`, when provided, receives `(current_page, total_pages)`.
    """
    pdf = Path(pdf)
    if mode not in {"auto", "digital", "scan"}:
        raise ValueError("mode must be 'auto', 'digital', or 'scan'")
    if not pdf.is_file():
        raise FileNotFoundError(pdf)

    destination = Path(output)
    with scene_directory(destination) as output:
        return _convert_pdf(
            pdf,
            output,
            render_source=render_source,
            preview_dpi=preview_dpi,
            mode=mode,
            progress=progress,
            source_progress=source_progress,
        )


def _convert_pdf(
    pdf: Path,
    output: Path,
    *,
    render_source: bool,
    preview_dpi: int,
    mode: str,
    progress=None,
    source_progress=None,
) -> dict:
    """Write a PDF into an empty, private scene directory."""

    reader = PdfReader(str(pdf))
    with pdfplumber.open(str(pdf)) as document:
        document_mode = detect_document_mode(document) if mode == "auto" else mode
        should_render_source = render_source or document_mode == "scan"
        preview_renderer = find_poppler() if should_render_source else None
        if should_render_source and not preview_renderer:
            print("pdftoppm was not found; extracted page images will be used instead")
        fonts = {} if document_mode == "scan" else extract_fonts(reader, output)
        metadata_title = str(reader.metadata.title or "") if reader.metadata else ""
        metadata_author = str(reader.metadata.author or "") if reader.metadata else ""
        if not metadata_title or metadata_title.lower() in {"jyotish books", "untitled", "document"}:
            metadata_title = re.sub(r"[_ -]+text$", "", pdf.stem, flags=re.IGNORECASE)
        editable_word_count = 0
        for index, page in enumerate(document.pages):
            number = index + 1
            words = extract_words(page)
            record = {
                "number": number,
                "width": round(float(page.width), 4),
                "height": round(float(page.height), 4),
                "words": words,
                "images": save_page_images(reader.pages[index], page, output, number)
                if document_mode != "scan" or not preview_renderer else [],
                "lines": [] if document_mode == "scan" else extract_lines(page),
            }
            editable_word_count += len(words)
            write_page(output, record)
            if progress:
                progress(number, len(document.pages))

    source_preview = bool(preview_renderer) and render_sources(
        pdf,
        output,
        preview_dpi,
        len(reader.pages),
        preview_renderer,
        source_progress,
    )
    manifest = create_manifest(
        document_id=slug(pdf.stem).lower(),
        title=metadata_title,
        author=metadata_author,
        format_name="pdf",
        page_count=len(reader.pages),
        editable_word_count=editable_word_count,
        fonts=fonts,
        mode=document_mode,
        source_preview=source_preview,
    )
    write_manifest(output, manifest)
    return manifest


def main() -> None:
    """Compatibility wrapper for the original CLI import path."""
    from .cli import main as cli_main

    cli_main()


if __name__ == "__main__":
    main()
