from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from replica import UnsupportedDocumentError, detect_document_format, supported_extensions, supports_filename

from .config import Settings
from .jobs import JobManager
from .models import (
    DocumentRecord, DocumentStructure, FocusAvailability, HealthResponse, PageDimRequest,
    PagePreferences, ReadingStateRequest, UploadResponse, WordFocusRequest,
)
from .services.metadata import extract_book_metadata
from .services.focus_rules import PRESERVED_PAGE_KINDS
from .services.jev import JevFocusClient
from .services.focus import FOCUS_STRATEGY_VERSION, FocusAnalyzer
from .services.structure import STRUCTURE_VERSION, build_structure
from .store import DocumentStore


settings = Settings.from_environment()
settings.data_dir.mkdir(parents=True, exist_ok=True)
store = DocumentStore(settings.data_dir)
jobs = JobManager(settings, store)
# Cache only model selections. Apply reading preferences separately on every response.
jev_page_ids = OrderedDict()
JEV_CACHE_PAGES = 512

@asynccontextmanager
async def lifespan(_: FastAPI):
    jobs.resume_incomplete()
    yield


app = FastAPI(title="Highlighter Document API", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def prevent_stale_app_assets(request: Request, call_next):
    response = await call_next(request)
    if not request.url.path.startswith(("/api/", "/documents/")):
        response.headers["Cache-Control"] = "no-store"
    return response


async def _document_or_404(document_id: str) -> DocumentRecord:
    try:
        return await asyncio.to_thread(store.get, document_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Document not found.") from error


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        focus_provider=settings.focus_provider,
        openrouter_configured=bool(settings.openrouter_api_key),
        supported_extensions=sorted(supported_extensions()),
    )


@app.get("/api/library", response_model=list[DocumentRecord])
async def document_library() -> list[DocumentRecord]:
    return await asyncio.to_thread(store.list_ready)


@app.post("/api/documents", response_model=UploadResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_document(file: UploadFile = File(...)) -> UploadResponse:
    file_name = Path(file.filename or "document").name
    if not supports_filename(file_name):
        formats = ", ".join(sorted(supported_extensions()))
        raise HTTPException(status_code=415, detail=f"Please upload a supported document: {formats}.")
    document_id = uuid.uuid4().hex
    store.create(DocumentRecord(id=document_id, file_name=file_name))
    destination = store.source_path(document_id, file_name)
    written = 0
    hasher = hashlib.sha256()
    try:
        with destination.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > settings.max_upload_bytes:
                    raise HTTPException(status_code=413, detail="The document exceeds the configured upload limit.")
                hasher.update(chunk)
                output.write(chunk)
        if not written:
            raise HTTPException(status_code=415, detail="The uploaded document is empty.")
        try:
            detect_document_format(destination)
        except UnsupportedDocumentError as error:
            raise HTTPException(status_code=415, detail=str(error)) from error
    except Exception:
        store.delete(document_id)
        raise
    finally:
        await file.close()
    digest = hasher.hexdigest()
    cached = await asyncio.to_thread(
        store.find_ready_by_digest,
        digest,
        content_bytes=written,
        focus_provider=settings.focus_provider,
        focus_model=jobs.focus_model,
        focus_strategy_version=FOCUS_STRATEGY_VERSION,
        exclude_id=document_id,
    )
    if cached:
        store.delete(document_id)
        return UploadResponse(document=cached, status_url=f"/api/documents/{cached.id}", cached=True)
    resumable = await asyncio.to_thread(
        store.find_resumable_by_digest,
        digest,
        focus_provider=settings.focus_provider,
        focus_model=jobs.focus_model,
        focus_strategy_version=FOCUS_STRATEGY_VERSION,
        exclude_id=document_id,
    )
    if resumable:
        store.delete(document_id)
        resumable = store.update(
            resumable.id,
            state="focusing",
            stage="Resuming focus analysis",
            message="Continuing from the last completed page batch",
            error=None,
        )
        jobs.start(resumable.id)
        return UploadResponse(document=resumable, status_url=f"/api/documents/{resumable.id}", cached=True)
    metadata = extract_book_metadata(destination, file_name)
    record = store.update(
        document_id,
        display_title=metadata.title,
        author=metadata.author,
        publication_year=metadata.year,
        content_sha256=digest,
        content_bytes=written,
    )
    jobs.start(document_id)
    return UploadResponse(document=record, status_url=f"/api/documents/{document_id}")


@app.get("/api/documents/lookup", response_model=UploadResponse)
async def lookup_document(sha256: str, size: int | None = None) -> UploadResponse:
    if len(sha256) != 64 or any(character not in "0123456789abcdefABCDEF" for character in sha256):
        raise HTTPException(status_code=400, detail="Invalid document fingerprint.")
    cached = await asyncio.to_thread(
        store.find_ready_by_digest,
        sha256,
        content_bytes=size,
        focus_provider=settings.focus_provider,
        focus_model=jobs.focus_model,
        focus_strategy_version=FOCUS_STRATEGY_VERSION,
    )
    if cached is None:
        raise HTTPException(status_code=404, detail="Document is not in the saved library.")
    return UploadResponse(document=cached, status_url=f"/api/documents/{cached.id}", cached=True)


@app.get("/api/documents/{document_id}", response_model=DocumentRecord)
async def document_status(document_id: str) -> DocumentRecord:
    return await _document_or_404(document_id)


@app.delete("/api/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(document_id: str) -> Response:
    await _document_or_404(document_id)
    await jobs.cancel(document_id)
    await asyncio.to_thread(store.delete, document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/documents/{document_id}/preferences", response_model=PagePreferences)
async def document_preferences(document_id: str) -> PagePreferences:
    try:
        return await asyncio.to_thread(store.get_preferences, document_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Document not found.") from error


@app.put("/api/documents/{document_id}/preferences/pages/{page_number}", response_model=PagePreferences)
async def set_page_dimmed(document_id: str, page_number: int, request: PageDimRequest) -> PagePreferences:
    document = await _document_or_404(document_id)
    if page_number < 1 or (document.page_count is not None and page_number > document.page_count):
        raise HTTPException(status_code=400, detail="Page number is outside this document.")
    return await asyncio.to_thread(store.set_page_dimmed, document_id, page_number, request.dimmed)


@app.put("/api/documents/{document_id}/preferences/reading", response_model=PagePreferences)
async def set_reading_state(document_id: str, request: ReadingStateRequest) -> PagePreferences:
    document = await _document_or_404(document_id)
    if document.page_count and request.page > document.page_count:
        raise HTTPException(status_code=400, detail="Page number is outside this document.")
    return await asyncio.to_thread(store.set_reading_state, document_id, request.page, request.mode)


@app.put("/api/documents/{document_id}/focus/{page_number}/words/{word_id}")
async def set_word_focus(document_id: str, page_number: int, word_id: int, request: WordFocusRequest) -> JSONResponse:
    scene_path = store.scene_dir(document_id) / "pages" / f"page-{page_number:04d}.json"
    if not scene_path.is_file():
        raise HTTPException(status_code=404, detail="Page not found.")
    scene = json.loads(scene_path.read_text(encoding="utf-8"))
    word = next((item for item in scene.get("words", []) if int(item["id"]) == word_id), None)
    if word is None:
        raise HTTPException(status_code=404, detail="Word not found.")
    await asyncio.to_thread(store.set_word_focus, document_id, page_number, word_id, str(word.get("text", "")), request.focused)
    return JSONResponse(await asyncio.to_thread(_focus_payload, document_id, page_number))


@app.get("/api/documents/{document_id}/structure", response_model=DocumentStructure)
async def document_structure(document_id: str) -> DocumentStructure:
    await _document_or_404(document_id)
    path = store.structure_path(document_id)
    if path.is_file():
        cached = DocumentStructure.model_validate_json(path.read_text(encoding="utf-8"))
        if cached.version == STRUCTURE_VERSION:
            return cached
    structure = await asyncio.to_thread(build_structure, store.scene_dir(document_id), store.focus_dir(document_id))
    path.write_text(structure.model_dump_json(indent=2), encoding="utf-8")
    return structure


@app.get("/api/documents/{document_id}/focus/{page_number}")
async def page_focus(document_id: str, page_number: int) -> JSONResponse:
    path = store.focus_dir(document_id) / f"page-{page_number:04d}.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Focus data is not ready for this page.")
    return JSONResponse(await asyncio.to_thread(_focus_payload, document_id, page_number))


@app.post("/api/documents/{document_id}/focus/{page_number}/openrouter")
async def openrouter_page(document_id: str, page_number: int, request: Request):
    document = await _document_or_404(document_id)
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token or not token.isascii() or any(ord(c) <= 32 or ord(c) >= 127 for c in token):
        raise HTTPException(400, "Enter an OpenRouter API key.")
    focus_path = store.focus_dir(document_id) / f"page-{page_number:04d}.json"
    if page_number < 1 or not focus_path.is_file():
        raise HTTPException(404, "Page highlights are still being prepared.")
    payload = json.loads(focus_path.read_text())
    if not payload.get("dimPage") and payload.get("pageKind") not in PRESERVED_PAGE_KINDS:
        scene_path = store.scene_dir(document_id) / "pages" / focus_path.name
        cache_key = (scene_path, scene_path.stat().st_mtime_ns, settings.jev_model, FOCUS_STRATEGY_VERSION)
        selected = jev_page_ids.get(cache_key)
        if selected is None:
            page = await asyncio.to_thread(FocusAnalyzer._load_page, scene_path, document.page_count)
            client = JevFocusClient(token, settings.jev_model)
            try:
                layers = await client.select([page])
            except httpx.HTTPStatusError as error:
                code = error.response.status_code
                message = {401: "OpenRouter rejected the API key.", 403: "This key cannot access Jev.",
                           402: "OpenRouter requires additional credit.", 429: "OpenRouter rate limit reached."}
                raise HTTPException(502, message.get(code, "OpenRouter could not score this page.")) from None
            except (httpx.RequestError, ValueError, KeyError, TypeError):
                raise HTTPException(502, "OpenRouter could not return sentence scores.") from None
            finally:
                await client.close()
            selected = sorted(set(layers[page_number]["essential"]) | set(page["checkpointWordIds"]))
            jev_page_ids[cache_key] = selected
            if len(jev_page_ids) > JEV_CACHE_PAGES:
                jev_page_ids.popitem(last=False)
        else:
            jev_page_ids.move_to_end(cache_key)
        payload.update(focusWordIds=selected, contextWordIds=[], provider="jev", model=settings.jev_model)
    return JSONResponse(await asyncio.to_thread(_focus_payload, document_id, page_number, payload), headers={"Cache-Control": "no-store"})


@app.get("/api/documents/{document_id}/focus-ready", response_model=FocusAvailability)
async def focus_availability(document_id: str) -> FocusAvailability:
    document = await _document_or_404(document_id)
    pages = sorted(
        int(path.stem.removeprefix("page-"))
        for path in store.focus_dir(document_id).glob("page-*.json")
    )
    return FocusAvailability(pages=pages, complete=document.state == "ready")


def _focus_payload(document_id: str, page_number: int, payload: dict | None = None) -> dict:
    focus_path = store.focus_dir(document_id) / f"page-{page_number:04d}.json"
    if payload is None:
        payload = json.loads(focus_path.read_text(encoding="utf-8"))
    preferences = store.get_preferences(document_id)
    scene_path = store.scene_dir(document_id) / "pages" / f"page-{page_number:04d}.json"
    scene = json.loads(scene_path.read_text(encoding="utf-8")) if preferences.learned_terms and scene_path.is_file() else {"words": []}
    focused = set(payload.get("focusWordIds", []))
    contextual = set(payload.get("contextWordIds", []))
    for word in scene.get("words", []):
        term = re.sub(r"[^\w'-]", "", str(word.get("text", "")), flags=re.UNICODE).casefold()
        learned = preferences.learned_terms.get(term, 0)
        word_id = int(word["id"])
        if learned >= 2:
            focused.add(word_id)
            contextual.discard(word_id)
        elif learned <= -2:
            focused.discard(word_id)
            contextual.discard(word_id)
    overrides = preferences.word_overrides.get(str(page_number), {})
    forced_focus = set(overrides.get("focus", []))
    forced_dim = set(overrides.get("dim", []))
    focused = (focused | forced_focus) - forced_dim
    contextual = contextual - focused - forced_dim
    payload["focusWordIds"] = sorted(focused)
    payload["contextWordIds"] = sorted(contextual)
    payload["correctedWordIds"] = sorted(forced_focus | forced_dim)
    return payload


app.mount("/documents", StaticFiles(directory=store.documents_dir, check_dir=False), name="documents")
app.mount("/engine", StaticFiles(directory=settings.engine_dir), name="engine")
app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")
