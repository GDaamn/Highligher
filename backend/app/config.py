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
    deepseek_api_key: str | None
    deepseek_base_url: str
    deepseek_model: str
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
        provider = os.getenv("FOCUS_PROVIDER", "deepseek").strip().lower()
        if provider not in {"deepseek", "heuristic", "jev"}:
            raise ValueError("FOCUS_PROVIDER must be 'deepseek', 'jev', or 'heuristic'.")
        return cls(
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY"),
            jev_model=os.getenv("JEV_MODEL", "typesafe/jev-1.14"),
            data_dir=Path(os.getenv("HIGHLIGHTER_DATA_DIR", root / "data")),
            frontend_dir=root / "frontend",
            engine_dir=Path(os.getenv("REPLICA_ENGINE_DIR", default_engine)).resolve(),
            deepseek_api_key=os.getenv("DEEPSEEK_API_KEY") or None,
            deepseek_base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
            focus_provider=provider,
            max_upload_bytes=int(os.getenv("MAX_UPLOAD_MB", "100")) * 1024 * 1024,
            focus_batch_characters=int(os.getenv("FOCUS_BATCH_CHARACTERS", "42000")),
            focus_concurrency=max(1, int(os.getenv("FOCUS_CONCURRENCY", "24"))),
        )
