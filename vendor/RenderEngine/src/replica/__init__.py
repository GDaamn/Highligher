"""Public API for extensible Replica document conversion."""

from pathlib import Path

from .converter import convert_pdf
from .documents import (
    DocumentAdapter, DocumentMetadata, DocumentRegistry, UnsupportedDocumentError, convert_document,
    detect_document_format, inspect_document, register_adapter, supported_extensions,
    supported_media_types, supports_filename,
)
from .epub import convert_epub


def web_assets_path() -> Path:
    """Return the bundled browser renderer directory."""
    return Path(__file__).with_name("web")


__all__ = [
    "DocumentAdapter", "DocumentMetadata", "DocumentRegistry", "UnsupportedDocumentError", "convert_document",
    "convert_epub", "convert_pdf", "detect_document_format", "inspect_document",
    "register_adapter", "supported_extensions", "supported_media_types", "supports_filename",
    "web_assets_path",
]
__version__ = "0.1.0"
