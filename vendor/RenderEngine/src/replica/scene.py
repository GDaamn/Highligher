"""Shared Replica scene contract and filesystem utilities."""

from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal


SCHEMA_VERSION = 1
RenderMode = Literal["digital", "scan"]


def create_manifest(
    *,
    document_id: str,
    title: str,
    format_name: str,
    page_count: int,
    editable_word_count: int,
    mode: RenderMode = "digital",
    fonts: Mapping[str, Any] | None = None,
    source_preview: bool = False,
    author: str = "",
    publication_year: int | None = None,
) -> dict[str, Any]:
    """Create the canonical manifest emitted by every document adapter."""
    manifest: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "id": document_id,
        "title": title,
        "format": format_name,
        "pageCount": page_count,
        "fonts": dict(fonts or {}),
        "mode": mode,
        "sourcePreview": source_preview,
        "editableWordCount": editable_word_count,
    }
    if author:
        manifest["author"] = author
    if publication_year is not None:
        manifest["publicationYear"] = publication_year
    return manifest


def write_json(path: Path, value: Any, *, pretty: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2 if pretty else None,
            separators=None if pretty else (",", ":"),
        ),
        encoding="utf-8",
    )


def write_page(output: Path, page: Mapping[str, Any]) -> None:
    write_json(output / "pages" / f"page-{page['number']:04d}.json", page)


def write_manifest(output: Path, manifest: Mapping[str, Any]) -> None:
    write_json(output / "manifest.json", manifest, pretty=True)


def _is_scene_directory(path: Path) -> bool:
    try:
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(manifest, dict) and manifest.get("schemaVersion") == SCHEMA_VERSION


@contextmanager
def scene_directory(destination: Path) -> Iterator[Path]:
    """Build a complete scene off to the side, then install it as one unit.

    A destination passed to a converter is owned by Replica. Keeping all writes
    in a staging directory prevents stale assets and preserves the previous
    valid scene if conversion fails.
    """
    destination = destination.absolute()
    if destination.exists():
        if not destination.is_dir():
            raise ValueError(f"Scene destination is not a directory: {destination}")
        if any(destination.iterdir()) and not _is_scene_directory(destination):
            raise ValueError(
                f"Refusing to replace a non-empty directory that is not a Replica scene: {destination}"
            )
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
    backup: Path | None = None
    try:
        yield staging
        if destination.exists():
            backup = Path(tempfile.mkdtemp(prefix=f".{destination.name}-backup-", dir=destination.parent))
            backup.rmdir()
            destination.replace(backup)
        staging.replace(destination)
        if backup:
            shutil.rmtree(backup, ignore_errors=True)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        if backup and backup.exists() and not destination.exists():
            backup.replace(destination)
        raise
