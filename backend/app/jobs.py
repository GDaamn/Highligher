from __future__ import annotations

import asyncio
import json
from math import ceil
from pathlib import Path

from replica import convert_document

from .config import Settings
from .services.jev import JevFocusClient
from .services.deepseek import DeepSeekFocusClient
from .services.focus import FOCUS_STRATEGY_VERSION, INITIAL_FOCUS_RATIO, FocusAnalyzer
from .services.structure import build_structure
from .store import DocumentStore


class JobManager:
    def __init__(self, settings: Settings, store: DocumentStore):
        self.settings = settings
        self.store = store
        self._tasks: dict[str, asyncio.Task] = {}

    def start(self, document_id: str) -> None:
        if document_id in self._tasks:
            return
        task = asyncio.create_task(self._process(document_id), name=f"document-{document_id}")
        self._tasks[document_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(document_id, None))

    async def cancel(self, document_id: str) -> None:
        """Stop processing before its document directory is removed."""
        task = self._tasks.get(document_id)
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def resume_incomplete(self) -> None:
        for document in self.store.list_ready():
            if (document.state == "ready" and document.focus_provider == self.settings.focus_provider
                    and self.settings.focus_provider in {"heuristic", "jev"}
                    and document.focus_strategy_version != FOCUS_STRATEGY_VERSION):
                self.start(document.id)
        for document in self.store.list_incomplete():
            self.start(document.id)

    async def _process(self, document_id: str) -> None:
        try:
            manifest_path = self.store.scene_dir(document_id) / "manifest.json"
            if manifest_path.is_file():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            else:
                self.store.update(document_id, state="converting", stage="Rebuilding pages", message="Extracting layout and editable words", progress=0.04)

                def conversion_progress(current: int, total: int) -> None:
                    if current == total or current % 5 == 0:
                        self.store.update(document_id, progress=0.05 + 0.42 * current / total, message=f"Rebuilding page {current} of {total}")

                def source_progress(stage: str, current: int, total: int) -> None:
                    if not total:
                        return
                    if stage == "render":
                        value = 0.47 + 0.08 * current / total
                        message = f"Rendering original page {current} of {total}"
                    else:
                        value = 0.55 + 0.05 * current / total
                        message = f"Optimizing page preview {current} of {total}"
                    self.store.update(document_id, progress=value, message=message)

                manifest = await asyncio.to_thread(
                    convert_document,
                    self.store.source_path(document_id),
                    self.store.scene_dir(document_id),
                    render_source=True,
                    mode="auto",
                    progress=conversion_progress,
                    source_progress=source_progress,
                )
            self.store.update(
                document_id,
                state="focusing",
                stage="Finding focus words",
                message="Identifying visual reading anchors",
                progress=0.62,
                page_count=manifest["pageCount"],
                editable_word_count=manifest["editableWordCount"],
                focus_provider=self.settings.focus_provider,
                focus_model=self.focus_model,
                focus_strategy_version=FOCUS_STRATEGY_VERSION,
            )
            analyzer = self._focus_analyzer(document_id)
            reader_open = False

            def focus_progress(current: int, total: int) -> None:
                message = (
                    f"Reading is ready · {self.settings.focus_concurrency} parallel batches · {current} of {total}"
                    if reader_open
                    else f"Preparing the first pages · {self.settings.focus_concurrency} parallel batches · {current} of {total}"
                )
                self.store.update(document_id, progress=0.62 + 0.36 * current / total, message=message)

            async def open_for_reading() -> None:
                nonlocal reader_open
                structure = await asyncio.to_thread(build_structure, self.store.scene_dir(document_id), self.store.focus_dir(document_id))
                self.store.structure_path(document_id).write_text(structure.model_dump_json(indent=2), encoding="utf-8")
                first_pages = max(1, ceil(manifest["pageCount"] * INITIAL_FOCUS_RATIO))
                reader_open = True
                self.store.update(
                    document_id,
                    state="readable",
                    stage="Ready to read",
                    message=f"First {first_pages} pages ready · continuing in the background",
                    focus_provider=self.settings.focus_provider,
                    focus_model=self.focus_model,
                    focus_strategy_version=FOCUS_STRATEGY_VERSION,
                )

            focus_count = await analyzer.analyze(
                self.store.scene_dir(document_id),
                self.store.focus_dir(document_id),
                focus_progress,
                initial_ready=open_for_reading,
            )
            structure = await asyncio.to_thread(build_structure, self.store.scene_dir(document_id), self.store.focus_dir(document_id))
            self.store.structure_path(document_id).write_text(structure.model_dump_json(indent=2), encoding="utf-8")
            self.store.update(
                document_id,
                state="ready",
                stage="Ready",
                message="Your editable speed-reading document is ready",
                progress=1,
                focus_word_count=focus_count,
                focus_provider=self.settings.focus_provider,
                focus_model=self.focus_model,
                focus_strategy_version=FOCUS_STRATEGY_VERSION,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.store.update(
                document_id,
                state="error",
                stage="Could not process document",
                message="The document could not be completed",
                error=self._error_message(error),
            )

    @classmethod
    def _error_message(cls, error: BaseException) -> str:
        if isinstance(error, BaseExceptionGroup) and error.exceptions:
            return cls._error_message(error.exceptions[0])
        return str(error) or error.__class__.__name__

    def _focus_analyzer(self, document_id: str) -> FocusAnalyzer:
        client = None
        if self.settings.focus_provider == "deepseek":
            if not self.settings.deepseek_api_key:
                raise RuntimeError("DEEPSEEK_API_KEY is not configured on the server.")
            client = DeepSeekFocusClient(
                api_key=self.settings.deepseek_api_key,
                base_url=self.settings.deepseek_base_url,
                model=self.settings.deepseek_model,
                user_id=f"booklamp-{document_id}",
            )
        if self.settings.focus_provider == "jev":
            if not self.settings.openrouter_api_key:
                raise RuntimeError("OPENROUTER_API_KEY is required for Jev sentence analysis.")
            client = JevFocusClient(self.settings.openrouter_api_key, self.settings.jev_model)
        return FocusAnalyzer(
            provider=self.settings.focus_provider,
            model=self.focus_model,
            batch_characters=self.settings.focus_batch_characters,
            concurrency=self.settings.focus_concurrency,
            client=client,
        )

    @property
    def focus_model(self) -> str:
        if self.settings.focus_provider == "jev":
            return self.settings.jev_model
        return self.settings.deepseek_model if self.settings.focus_provider == "deepseek" else "local-heuristic"
