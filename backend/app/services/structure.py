from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..models import ChapterLink, DocumentStructure


MARKERS = {"appendix", "chapter", "epilogue", "introduction", "part", "prologue", "section", "unit"}
STRUCTURE_VERSION = 3


def build_structure(scene_dir: Path, focus_dir: Path) -> DocumentStructure:
    """Build a small semantic map from the layout and focus artifacts."""
    roles: dict[str, str] = {}
    chapters: list[ChapterLink] = []
    for focus_path in sorted(focus_dir.glob("page-*.json")):
        focus = json.loads(focus_path.read_text(encoding="utf-8"))
        page = int(focus["page"])
        role = str(focus.get("pageKind", "reading"))
        roles[str(page)] = role
        if role in {"contents", "front-matter", "copyright", "figures-list", "index", "back-matter", "blank"}:
            continue
        scene_path = scene_dir / "pages" / f"page-{page:04d}.json"
        if not scene_path.is_file():
            continue
        label = _chapter_label(json.loads(scene_path.read_text(encoding="utf-8")))
        if label and (not chapters or chapters[-1].label.casefold() != label.casefold()):
            chapters.append(ChapterLink(page=page, label=label))
    return DocumentStructure(version=STRUCTURE_VERSION, chapters=chapters, page_roles=roles)


def _chapter_label(scene: dict[str, Any]) -> str | None:
    words = scene.get("words", [])
    if not words:
        return None
    heights = sorted(float(word.get("size", word.get("height", 0))) for word in words)
    median = heights[len(heights) // 2] if heights else 0
    candidates = [
        word for word in words[:80]
        if _clean(word.get("text", "")) in MARKERS
        and float(word.get("y", 0)) < float(scene.get("height", 1)) * .45
        and (
            str(word.get("text", "")).strip().isupper()
            or float(word.get("size", word.get("height", 0))) >= median * 1.28
        )
    ]
    if not candidates:
        return None
    marker = candidates[0]
    y = float(marker.get("y", 0))
    size = max(1, float(marker.get("size", marker.get("height", 12))))
    same_line = _line(words, y, size)
    marker_index = next((index for index, word in enumerate(same_line) if word is marker), 0)
    same_line = same_line[marker_index:marker_index + 8]
    if len(same_line) > 1 and _clean(same_line[1].get("text", "")) in {"summary", "review", "recap"}:
        return None
    following = sorted({float(word.get("y", 0)) for word in words if y + size * .55 < float(word.get("y", 0)) <= y + size * 4})
    title_line = _line(words, following[0], size) if following else []
    label_words = same_line + (title_line if 0 < len(title_line) <= 12 else [])
    label = " ".join(str(word.get("text", "")).strip() for word in label_words).strip()
    label = re.split(r"[;•]", label, maxsplit=1)[0]
    return re.sub(r"\s+", " ", label)[:100] or None


def _line(words: list[dict[str, Any]], y: float, size: float) -> list[dict[str, Any]]:
    return sorted(
        (word for word in words if abs(float(word.get("y", 0)) - y) <= max(2, size * .35)),
        key=lambda word: float(word.get("x", 0)),
    )


def _clean(value: Any) -> str:
    return re.sub(r"[^\w-]", "", str(value), flags=re.UNICODE).casefold()
