"""Command-line interface for document-to-scene conversion."""

from __future__ import annotations

import argparse
from pathlib import Path

from .documents import convert_document, document_adapter, supported_extensions


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "document",
        type=Path,
        help=f"Input document ({', '.join(sorted(supported_extensions()))})",
    )
    parser.add_argument("--output", type=Path, default=Path("book"))
    parser.add_argument("--render-source", action="store_true")
    parser.add_argument("--preview-dpi", type=int, default=120)
    parser.add_argument("--mode", choices=["auto", "digital", "scan"], default="auto")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    adapter = document_adapter(args.document)
    uses_pdf_options = args.render_source or args.preview_dpi != 120 or args.mode != "auto"
    if adapter.name != "pdf" and uses_pdf_options:
        parser.error("--render-source, --preview-dpi, and --mode are available only for PDF input")

    def report(current: int, total: int) -> None:
        if current == 1 or current == total or current % 10 == 0:
            print(f"Converted item {current}/{total}")

    options = {"progress": None if args.quiet else report}
    if adapter.name == "pdf":
        options.update(
            render_source=args.render_source,
            preview_dpi=args.preview_dpi,
            mode=args.mode,
        )
    manifest = convert_document(args.document, args.output, **options)
    if not manifest["editableWordCount"]:
        print("Warning: no text layer was found; the scene will not be editable until OCR is added.")
