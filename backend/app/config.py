from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from replica import web_assets_path


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    frontend_dir: Path
    engine_dir: Path
    focus_provider: str
    max_upload_bytes: int
    focus_batch_characters: int
    focus_concurrency: int

    openrouter_api_key: str | None = None
    jev_model: str = "typesafe/jev-1.14"

    @classmethod
    def from_environment(cls) -> "Settings":
        root = Path(__file__).resolve().parents[2]
        default_engine = web_assets_path()
        openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip() or None
        return cls(
            openrouter_api_key=openrouter_key,
            jev_model=os.getenv("JEV_MODEL", "typesafe/jev-1.14"),
            data_dir=Path(os.getenv("HIGHLIGHTER_DATA_DIR", root / "data")),
            frontend_dir=root / "frontend",
            engine_dir=Path(os.getenv("REPLICA_ENGINE_DIR", default_engine)).resolve(),
            focus_provider="jev" if openrouter_key else "heuristic",
            max_upload_bytes=int(os.getenv("MAX_UPLOAD_MB", "100")) * 1024 * 1024,
            focus_batch_characters=int(os.getenv("FOCUS_BATCH_CHARACTERS", "42000")),
            focus_concurrency=max(1, int(os.getenv("FOCUS_CONCURRENCY", "24"))),
        )
