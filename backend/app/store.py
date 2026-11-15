from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from threading import RLock
from typing import Any, Iterator

from .models import DocumentRecord, PagePreferences, utc_now


class DocumentStore:
    def __init__(self, data_dir: Path):
        self.documents_dir = data_dir / "documents"
        self.documents_dir.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def directory(self, document_id: str) -> Path:
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", document_id):
            raise FileNotFoundError(document_id)
        return self.documents_dir / document_id

    def source_path(self, document_id: str, file_name: str | None = None) -> Path:
        """Return the format-preserving upload path for a document."""
        if file_name:
            suffix = Path(file_name).suffix.casefold() or ".bin"
            return self.directory(document_id) / f"source{suffix}"
        existing = sorted(self.directory(document_id).glob("source.*"))
        return existing[0] if existing else self.directory(document_id) / "source.pdf"

    def scene_dir(self, document_id: str) -> Path:
        return self.directory(document_id) / "scene"

    def focus_dir(self, document_id: str) -> Path:
        return self.directory(document_id) / "focus"

    def metadata_path(self, document_id: str) -> Path:
        return self.directory(document_id) / "document.json"

    def preferences_path(self, document_id: str) -> Path:
        return self.directory(document_id) / "preferences.json"

    def structure_path(self, document_id: str) -> Path:
        return self.directory(document_id) / "structure.json"

    def create(self, record: DocumentRecord) -> DocumentRecord:
        with self._lock:
            folder = self.directory(record.id)
            folder.mkdir(parents=True, exist_ok=False)
            self._write(record)
        return record

    def get(self, document_id: str) -> DocumentRecord:
        path = self.metadata_path(document_id)
        if not path.is_file():
            raise FileNotFoundError(document_id)
        with self._lock:
            return DocumentRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def update(self, document_id: str, **changes: Any) -> DocumentRecord:
        with self._lock:
            record = self.get(document_id)
            updated = record.model_copy(update={**changes, "updated_at": utc_now()})
            self._write(updated)
            return updated

    def delete(self, document_id: str) -> None:
        """Remove a document and all of its generated files."""
        with self._lock:
            folder = self.directory(document_id)
            if folder.exists():
                shutil.rmtree(folder)

    def get_preferences(self, document_id: str) -> PagePreferences:
        self.get(document_id)
        path = self.preferences_path(document_id)
        if not path.is_file():
            return PagePreferences()
        with self._lock:
            try:
                return PagePreferences.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return PagePreferences()

    def set_page_dimmed(self, document_id: str, page_number: int, dimmed: bool) -> PagePreferences:
        with self._lock:
            preferences = self.get_preferences(document_id)
            pages = set(preferences.dimmed_pages)
            if dimmed:
                pages.add(page_number)
            else:
                pages.discard(page_number)
            updated = preferences.model_copy(update={"dimmed_pages": sorted(pages)})
            self._write_preferences(document_id, updated)
            return updated

    def set_reading_state(self, document_id: str, page: int, mode: str) -> PagePreferences:
        with self._lock:
            preferences = self.get_preferences(document_id)
            updated = preferences.model_copy(update={"last_page": page, "reading_mode": mode})
            self._write_preferences(document_id, updated)
            return updated

    def set_word_focus(self, document_id: str, page: int, word_id: int, text: str, focused: bool) -> PagePreferences:
        """Save an exact correction and learn repeated term preferences conservatively."""
        with self._lock:
            preferences = self.get_preferences(document_id)
            overrides = {
                key: {name: list(ids) for name, ids in value.items()}
                for key, value in preferences.word_overrides.items()
            }
            page_overrides = overrides.setdefault(str(page), {"focus": [], "dim": []})
            focus_ids = set(page_overrides.get("focus", []))
            dim_ids = set(page_overrides.get("dim", []))
            previous = 1 if word_id in focus_ids else -1 if word_id in dim_ids else 0
            focus_ids.discard(word_id)
            dim_ids.discard(word_id)
            (focus_ids if focused else dim_ids).add(word_id)
            overrides[str(page)] = {"focus": sorted(focus_ids), "dim": sorted(dim_ids)}

            term = re.sub(r"[^\w'-]", "", text, flags=re.UNICODE).casefold()
            learned = dict(preferences.learned_terms)
            if term and len(term) > 2:
                learned[term] = max(-5, min(5, learned.get(term, 0) - previous + (1 if focused else -1)))
                if learned[term] == 0:
                    learned.pop(term)
            updated = preferences.model_copy(update={"word_overrides": overrides, "learned_terms": learned})
            self._write_preferences(document_id, updated)
            return updated

    def list_incomplete(self) -> list[DocumentRecord]:
        records: list[DocumentRecord] = []
        with self._lock:
            for record in self._records():
                if record.state not in {"ready", "error"} and self.source_path(record.id).is_file():
                    records.append(record)
        return records

    def list_ready(self) -> list[DocumentRecord]:
        """Return the newest complete copy of every processed document."""
        unique: dict[str, DocumentRecord] = {}
        with self._lock:
            for record in self._records():
                if record.state not in {"readable", "ready"} or not (self.scene_dir(record.id) / "manifest.json").is_file():
                    continue
                key = record.content_sha256 or record.file_name.casefold()
                existing = unique.get(key)
                if existing is None or record.updated_at > existing.updated_at:
                    unique[key] = record
        return sorted(unique.values(), key=lambda record: record.updated_at, reverse=True)

    def find_ready_by_digest(
        self,
        digest: str,
        *,
        content_bytes: int | None = None,
        focus_provider: str | None = None,
        focus_model: str | None = None,
        focus_strategy_version: int | None = None,
        exclude_id: str | None = None,
    ) -> DocumentRecord | None:
        """Find a complete saved document, backfilling hashes for older records."""
        digest = digest.lower()
        with self._lock:
            candidates: list[DocumentRecord] = []
            for record in self._records():
                if record.id == exclude_id or record.state not in {"readable", "ready"}:
                    continue
                if focus_provider and record.focus_provider != focus_provider:
                    continue
                if focus_model and record.focus_model != focus_model:
                    continue
                if focus_strategy_version is not None and record.focus_strategy_version != focus_strategy_version:
                    continue
                source = self.source_path(record.id)
                if not source.is_file() or not (self.scene_dir(record.id) / "manifest.json").is_file():
                    continue
                if content_bytes is not None and source.stat().st_size != content_bytes:
                    continue
                candidates.append(record)

            for record in candidates:
                if record.content_sha256 == digest:
                    return record

            # Documents created before persistent caching have no digest. Hash each
            # matching-size source once, then save it for all future lookups.
            for record in candidates:
                if record.content_sha256:
                    continue
                source = self.source_path(record.id)
                calculated = self._sha256(source)
                record = self.update(
                    record.id,
                    content_sha256=calculated,
                    content_bytes=source.stat().st_size,
                )
                if calculated == digest:
                    return record
        return None

    def find_resumable_by_digest(
        self,
        digest: str,
        *,
        focus_provider: str,
        focus_model: str,
        focus_strategy_version: int,
        exclude_id: str | None = None,
    ) -> DocumentRecord | None:
        """Return the newest failed copy whose rendered pages can be reused."""
        matches: list[DocumentRecord] = []
        with self._lock:
            for record in self._records():
                if record.id == exclude_id or record.state != "error" or record.content_sha256 != digest:
                    continue
                if (
                    record.focus_provider != focus_provider
                    or record.focus_model != focus_model
                    or record.focus_strategy_version != focus_strategy_version
                ):
                    continue
                if self.source_path(record.id).is_file() and (self.scene_dir(record.id) / "manifest.json").is_file():
                    matches.append(record)
        return max(matches, key=lambda record: record.updated_at, default=None)

    def _records(self) -> Iterator[DocumentRecord]:
        """Yield readable metadata records, skipping corrupt or partial files."""
        for metadata in self.documents_dir.glob("*/document.json"):
            try:
                yield DocumentRecord.model_validate_json(metadata.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue

    @staticmethod
    def _sha256(path: Path) -> str:
        hasher = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def _write(self, record: DocumentRecord) -> None:
        path = self.metadata_path(record.id)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(record.model_dump(), indent=2), encoding="utf-8")
        temporary.replace(path)

    def _write_preferences(self, document_id: str, preferences: PagePreferences) -> None:
        path = self.preferences_path(document_id)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(preferences.model_dump(), indent=2), encoding="utf-8")
        temporary.replace(path)
