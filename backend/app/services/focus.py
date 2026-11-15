from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from math import ceil
from pathlib import Path
from typing import Any, Protocol

from .sentence_focus import local_layers
from .focus_rules import (
    PRESERVED_PAGE_KINDS,
    classify_page,
    contextual_focus,
    structural_word_ids,
    structured_list_word_ids,
)


FOCUS_STRATEGY_VERSION = 19
INITIAL_FOCUS_RATIO = 0.3


class FocusClient(Protocol):
    async def select(self, pages: list[dict[str, Any]]) -> dict[int, dict[str, list[int]]]: ...


class FocusAnalyzer:
    """Coordinate cached, remote, and local focus analysis for a document."""

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        batch_characters: int,
        client: FocusClient | None = None,
        concurrency: int = 3,
    ):
        self.provider = provider
        self.model = model
        self.batch_characters = batch_characters
        self.client = client
        self.concurrency = max(1, concurrency)

    @property
    def cache_model(self) -> str:
        return self.model if self.provider != "heuristic" else "local-heuristic"

    async def analyze(
        self,
        scene_dir: Path,
        focus_dir: Path,
        progress: Callable[[int, int], None] | None = None,
        initial_ready: Callable[[], Awaitable[None]] | None = None,
        initial_ratio: float = INITIAL_FOCUS_RATIO,
    ) -> int:
        page_files = sorted((scene_dir / "pages").glob("page-*.json"))
        focus_dir.mkdir(parents=True, exist_ok=True)
        all_pages = [self._load_page(path, page_count=len(page_files)) for path in page_files]
        self._mark_trailing_back_matter(all_pages)
        pages_by_path = dict(zip(page_files, all_pages, strict=True))
        self._add_boundary_context(all_pages)

        cached_focus = {
            path: self._cached_focus(focus_dir / f"page-{pages_by_path[path]['page']:04d}.json")
            for path in page_files
        }
        missing_files = [path for path in page_files if cached_focus[path] is None]
        initial_page_count = max(1, ceil(len(page_files) * initial_ratio))
        initial_set = set(page_files[:initial_page_count])
        initial_batches = list(self._batches([path for path in missing_files if path in initial_set]))
        remaining_batches = list(self._batches([path for path in missing_files if path not in initial_set]))
        batches = initial_batches + remaining_batches
        semaphore = asyncio.Semaphore(self.concurrency if self.provider != "heuristic" else 1)

        heuristic_selection = None
        if self.provider == "heuristic":
            eligible = self._eligible_pages(all_pages)
            heuristic_selection = await self._select(eligible)

        async def analyze_batch(batch: list[Path]) -> int:
            pages = [pages_by_path[path] for path in batch]
            eligible_pages = self._eligible_pages(pages)
            used_provider = self.provider
            if heuristic_selection is not None:
                selected = {page["page"]: heuristic_selection.get(page["page"], {}) for page in eligible_pages}
            else:
                try:
                    async with semaphore:
                        selected = await self._select(eligible_pages) if eligible_pages else {}
                except Exception:
                    selected = self._heuristic_document_layers(eligible_pages)
                    used_provider = "local-heuristic"

            batch_focus = 0
            for page in pages:
                essential_ids, context_ids = self._page_layers(page, selected)
                record = {
                    "schemaVersion": 1,
                    "strategyVersion": FOCUS_STRATEGY_VERSION,
                    "page": page["page"],
                    "focusWordIds": essential_ids,
                    "contextWordIds": context_ids,
                    "pageKind": page["pageKind"],
                    "bypassFocus": False,
                    "dimPage": page["dimPage"],
                    "provider": used_provider,
                    "model": self.model if used_provider in {"deepseek", "jev"} else "local-heuristic",
                }
                path = focus_dir / f"page-{page['page']:04d}.json"
                path.write_text(json.dumps(record, separators=(",", ":")), encoding="utf-8")
                batch_focus += len(essential_ids) + len(context_ids)
            return batch_focus

        total_focus = sum(
            len(record.get("focusWordIds", [])) + len(record.get("contextWordIds", []))
            for record in cached_focus.values()
            if record is not None
        )
        completed = 0

        async def run_batch(batch: list[Path]) -> None:
            nonlocal completed, total_focus
            total_focus += await analyze_batch(batch)
            completed += 1
            if progress:
                progress(completed, len(batches))

        await self._run_batches(all_pages, initial_batches, remaining_batches, run_batch, initial_ready)
        return total_focus

    async def _run_batches(
        self,
        all_pages: list[dict[str, Any]],
        initial_batches: list[list[Path]],
        remaining_batches: list[list[Path]],
        run_batch: Callable[[list[Path]], Awaitable[None]],
        initial_ready: Callable[[], Awaitable[None]] | None,
    ) -> None:
        open_client = getattr(self.client, "open", None)
        close_client = getattr(self.client, "close", None)
        if open_client:
            await open_client()
        try:
            prepare_client = getattr(self.client, "prepare", None)
            if prepare_client and (initial_batches or remaining_batches):
                await prepare_client(all_pages)

            async def run_wave(wave: list[list[Path]]) -> None:
                async with asyncio.TaskGroup() as group:
                    for batch in wave:
                        group.create_task(run_batch(batch))

            await run_wave(initial_batches)
            if initial_ready:
                await initial_ready()
            await run_wave(remaining_batches)
        finally:
            if close_client:
                await close_client()

    def _cached_focus(self, path: Path) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if record.get("strategyVersion") != FOCUS_STRATEGY_VERSION:
            return None
        if record.get("provider") != self.provider or record.get("model") != self.cache_model:
            return None
        return record

    @staticmethod
    def _eligible_pages(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            page for page in pages
            if not page["dimPage"] and page["pageKind"] not in PRESERVED_PAGE_KINDS
        ]

    def _page_layers(
        self,
        page: dict[str, Any],
        selected: dict[int, dict[str, list[int]]],
    ) -> tuple[list[int], list[int]]:
        if page["dimPage"]:
            return [], []
        if page["pageKind"] in PRESERVED_PAGE_KINDS:
            return [int(word["id"]) for word in page["words"]], []

        proposed = selected.get(page["page"], {})
        essential_ids, context_ids = self._validate_layers(page["words"], proposed)
        checkpoints = set(page["checkpointWordIds"])
        essential = set(essential_ids) | checkpoints
        ordered_essential = [
            int(word["id"]) for word in page["words"] if int(word["id"]) in essential
        ]
        return ordered_essential, [word_id for word_id in context_ids if word_id not in checkpoints]

    def _batches(self, page_files: list[Path]):
        batch: list[Path] = []
        size = 0
        for path in page_files:
            scene = json.loads(path.read_text(encoding="utf-8"))
            compact_words = [{"id": word["id"], "text": word["text"]} for word in scene.get("words", [])]
            page_size = len(json.dumps(compact_words, ensure_ascii=False))
            if batch and size + page_size > self.batch_characters:
                yield batch
                batch, size = [], 0
            batch.append(path)
            size += page_size
        if batch:
            yield batch

    @staticmethod
    def _load_page(path: Path, page_count: int | None = None) -> dict[str, Any]:
        scene = json.loads(path.read_text(encoding="utf-8"))
        if page_count:
            scene["pageCount"] = page_count
        page_kind, dim_page = classify_page(scene)
        page_number = int(scene["number"])
        page_height = float(scene.get("height", 0))
        words = []
        block_starts = []
        previous = None
        for word in scene.get("words", []):
            text = str(word["text"]).strip()
            y = float(word.get("y", 0))
            is_running_page_number = (
                text.isdigit()
                and int(text) == page_number
                and page_height
                and (y < page_height * .07 or y > page_height * .9)
            )
            if not is_running_page_number:
                if previous and "y" in previous and "y" in word:
                    height = max(float(previous.get("height") or 0), float(word.get("height") or 0))
                    if height and y - float(previous["y"]) > 2 * height:
                        block_starts.append(int(word["id"]))
                previous = word
                words.append({"id": int(word["id"]), "text": word["text"]})
        return {
            "page": page_number,
            "words": words,
            "pageKind": page_kind,
            "dimPage": dim_page,
            "blockStartWordIds": block_starts,
            "checkpointWordIds": sorted(set(structural_word_ids(scene)) | set(structured_list_word_ids(scene))),
        }

    @staticmethod
    def _mark_trailing_back_matter(pages: list[dict[str, Any]]) -> None:
        if not pages:
            return
        page_count = max(int(page["page"]) for page in pages)
        trailing_start = page_count - max(12, round(page_count * .08)) + 1
        in_back_matter = False
        for page in pages:
            if int(page["page"]) < trailing_start:
                continue
            if page["pageKind"] in {"index", "back-matter"}:
                in_back_matter = True
            if in_back_matter:
                page["dimPage"] = True
                if page["pageKind"] not in {"index", "blank"}:
                    page["pageKind"] = "back-matter"

    @staticmethod
    def _add_boundary_context(pages: list[dict[str, Any]], word_limit: int = 36) -> None:
        for index, page in enumerate(pages):
            previous = pages[index - 1] if index else None
            following = pages[index + 1] if index + 1 < len(pages) else None
            page["previousPageTail"] = " ".join(str(word["text"]) for word in previous["words"][-word_limit:]) if previous else ""
            page["nextPageHead"] = " ".join(str(word["text"]) for word in following["words"][:word_limit]) if following else ""

    async def _select(self, pages: list[dict[str, Any]]) -> dict[int, dict[str, list[int]]]:
        if self.provider == "heuristic":
            return self._heuristic_document_layers(pages)
        if self.client is None:
            raise RuntimeError("DeepSeek focus analysis is not configured.")
        return await self.client.select(pages)

    @staticmethod
    def _heuristic_document_layers(pages: list[dict[str, Any]]) -> dict[int, dict[str, list[int]]]:
        return local_layers(pages)

    @staticmethod
    def _validate_layers(
        words: list[dict[str, Any]],
        proposed: dict[str, list[int]] | list[int],
    ) -> tuple[list[int], list[int]]:
        ordered_ids = [int(word["id"]) for word in words]
        valid = set(ordered_ids)
        if isinstance(proposed, list):
            proposed = {"essential": proposed, "context": []}
        essential = {int(value) for value in proposed.get("essential", []) if int(value) in valid}
        context = {int(value) for value in proposed.get("context", []) if int(value) in valid} - essential

        essential_minimum = max(1, round(len(words) * 0.24)) if words else 0
        essential_maximum = max(essential_minimum, round(len(words) * 0.46))
        total_minimum = max(essential_minimum, round(len(words) * 0.44))
        total_maximum = max(total_minimum, round(len(words) * 0.70))

        if len(essential) < essential_minimum:
            essential.update(contextual_focus(words, target_ratio=0.34))
        if len(essential) > essential_maximum:
            essential = set(contextual_focus(words, target_ratio=0.38))
        context -= essential
        if len(essential) + len(context) < total_minimum:
            expanded = contextual_focus(words, target_ratio=0.58)
            context.update(word_id for word_id in expanded if word_id not in essential)
        if len(essential) + len(context) > total_maximum:
            allowance = max(0, total_maximum - len(essential))
            ordered_context = [word_id for word_id in ordered_ids if word_id in context]
            context = set(ordered_context[:allowance])

        return (
            [word_id for word_id in ordered_ids if word_id in essential],
            [word_id for word_id in ordered_ids if word_id in context and word_id not in essential],
        )
