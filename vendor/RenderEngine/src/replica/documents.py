"""Extensible document-format registry for Replica scene conversion."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class UnsupportedDocumentError(ValueError):
    """Raised when no registered adapter can read a document."""


@dataclass(frozen=True, slots=True)
class DocumentMetadata:
    title: str = ""
    author: str = ""
    year: int | None = None


@dataclass(frozen=True, slots=True)
class DocumentAdapter:
    name: str
    extensions: frozenset[str]
    media_types: frozenset[str]
    matches: Callable[[Path], bool]
    convert: Callable[..., dict[str, Any]]
    inspect: Callable[[Path], DocumentMetadata]


class DocumentRegistry:
    """A collection of document adapters with deterministic dispatch."""

    def __init__(self, adapters: tuple[DocumentAdapter, ...] = ()) -> None:
        self._adapters: dict[str, DocumentAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: DocumentAdapter, *, replace: bool = False) -> None:
        key = adapter.name.casefold()
        if key in self._adapters and not replace:
            raise ValueError(f"A document adapter named '{adapter.name}' is already registered.")
        self._adapters[key] = adapter

    def get(self, name: str) -> DocumentAdapter | None:
        return self._adapters.get(name.casefold())

    def all(self) -> tuple[DocumentAdapter, ...]:
        return tuple(self._adapters.values())

    def supported_extensions(self) -> frozenset[str]:
        return frozenset(
            extension for adapter in self.all() for extension in adapter.extensions
        )

    def supported_media_types(self) -> frozenset[str]:
        return frozenset(
            media_type for adapter in self.all() for media_type in adapter.media_types
        )

    def supports_filename(self, file_name: str) -> bool:
        return Path(file_name).suffix.casefold() in self.supported_extensions()

    def adapter_for(self, source: Path | str, *, format_name: str | None = None) -> DocumentAdapter:
        path = Path(source)
        if not path.is_file():
            raise FileNotFoundError(path)
        if format_name:
            selected = self.get(format_name)
            candidates = (selected,) if selected else ()
        else:
            suffix = path.suffix.casefold()
            matching = tuple(adapter for adapter in self.all() if suffix in adapter.extensions)
            candidates = matching or self.all()
        for adapter in candidates:
            if adapter and adapter.matches(path):
                return adapter
        expected = ", ".join(sorted(self.supported_extensions()))
        raise UnsupportedDocumentError(f"Unsupported or invalid document. Expected: {expected}.")

    def detect_format(self, source: Path | str) -> str:
        return self.adapter_for(source).name

    def inspect(self, source: Path | str) -> DocumentMetadata:
        path = Path(source)
        return self.adapter_for(path).inspect(path)

    def convert_document(
        self,
        source: Path | str,
        output: Path | str = Path("book"),
        *,
        format_name: str | None = None,
        **options: Any,
    ) -> dict[str, Any]:
        path = Path(source)
        return self.adapter_for(path, format_name=format_name).convert(
            path, Path(output), **options
        )


_DEFAULT_REGISTRY = DocumentRegistry()
_DEFAULTS_REGISTERED = False


def register_adapter(adapter: DocumentAdapter, *, replace: bool = False) -> None:
    """Register a converter without changing the core dispatch function."""
    _DEFAULT_REGISTRY.register(adapter, replace=replace)


def _register_defaults() -> None:
    global _DEFAULTS_REGISTERED
    if _DEFAULTS_REGISTERED:
        return
    from .converter import convert_pdf, inspect_pdf, is_pdf
    from .epub import convert_epub, inspect_epub, is_epub

    defaults = (DocumentAdapter(
        name="pdf",
        extensions=frozenset({".pdf"}),
        media_types=frozenset({"application/pdf"}),
        matches=is_pdf,
        convert=convert_pdf,
        inspect=inspect_pdf,
    ), DocumentAdapter(
        name="epub",
        extensions=frozenset({".epub"}),
        media_types=frozenset({"application/epub+zip"}),
        matches=is_epub,
        convert=convert_epub,
        inspect=inspect_epub,
    ))
    for adapter in defaults:
        if _DEFAULT_REGISTRY.get(adapter.name) is None:
            _DEFAULT_REGISTRY.register(adapter)
    _DEFAULTS_REGISTERED = True


def adapters() -> tuple[DocumentAdapter, ...]:
    _register_defaults()
    return _DEFAULT_REGISTRY.all()


def supported_extensions() -> frozenset[str]:
    _register_defaults()
    return _DEFAULT_REGISTRY.supported_extensions()


def supported_media_types() -> frozenset[str]:
    _register_defaults()
    return _DEFAULT_REGISTRY.supported_media_types()


def supports_filename(file_name: str) -> bool:
    _register_defaults()
    return _DEFAULT_REGISTRY.supports_filename(file_name)


def document_adapter(source: Path | str, *, format_name: str | None = None) -> DocumentAdapter:
    _register_defaults()
    return _DEFAULT_REGISTRY.adapter_for(source, format_name=format_name)


def detect_document_format(source: Path | str) -> str:
    _register_defaults()
    return _DEFAULT_REGISTRY.detect_format(source)


def inspect_document(source: Path | str) -> DocumentMetadata:
    _register_defaults()
    return _DEFAULT_REGISTRY.inspect(source)


def convert_document(
    source: Path | str,
    output: Path | str = Path("book"),
    *,
    format_name: str | None = None,
    **options: Any,
) -> dict[str, Any]:
    """Convert any registered input format into a Replica scene."""
    _register_defaults()
    return _DEFAULT_REGISTRY.convert_document(
        source, output, format_name=format_name, **options
    )
