"""Resolve a browser's private library from its persistent bearer cookie."""

import hashlib
import os
import re
import secrets
from pathlib import Path
from weakref import WeakValueDictionary

from fastapi import HTTPException, Request

from .config import Settings
from .jobs import JobManager
from .store import DocumentStore


class Libraries:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.data_dir / "libraries"
        self.active: WeakValueDictionary[Path, JobManager] = WeakValueDictionary()

    def manager(self, directory: Path) -> JobManager:
        manager = self.active.get(directory)
        if manager is None:
            manager = JobManager(self.settings, DocumentStore(directory))
            self.active[directory] = manager
        return manager

    def resume(self):
        if self.root.is_dir():
            for directory in self.root.iterdir():
                if directory.is_dir() and re.fullmatch(r"[0-9a-f]{64}", directory.name):
                    self.manager(directory).resume_incomplete()

    async def session(self, request: Request, call_next):
        secure = request.url.scheme == "https" or os.getenv("RENDER") == "true"
        name = "__Host-highlighter-library" if secure else "highlighter-library"
        token = request.cookies.get(name, "")
        existing = re.fullmatch(r"[0-9a-f]{64}", token)
        initialize = request.method == "GET" and request.url.path in {"/", "/index.html", "/api/library"}
        if not existing and initialize:
            token = secrets.token_hex(32)
        if existing or initialize:
            request.state.library_directory = self.root / hashlib.sha256(token.encode()).hexdigest()
        response = await call_next(request)
        response.headers["Cache-Control"] = "private, no-store"
        if not existing and initialize:
            response.set_cookie(name, token, max_age=365 * 24 * 3600, secure=secure,
                                httponly=True, samesite="lax", path="/")
        return response

    async def for_request(self, request: Request) -> JobManager:
        directory = getattr(request.state, "library_directory", None)
        if directory is None:
            raise HTTPException(403, "Open the library in this browser first.")
        return self.manager(directory)
