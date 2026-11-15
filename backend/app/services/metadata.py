from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from replica import inspect_document


ARCHIVE_NOISE = re.compile(
    r"\b(?:anna'?s\s+archive|internet\s+archive|archive\s+org|z\s+library|libgen|pdf|ebook|text|scan)\b",
    re.IGNORECASE,
)
HASH = re.compile(r"\b[0-9a-f]{16,}\b", re.IGNORECASE)
YEAR = re.compile(r"\b(18\d{2}|19\d{2}|20\d{2})\b")
GENERIC_TITLES = {"document", "untitled", "chapter", "chapter 1", "jyotish books", "book"}
BAD_AUTHORS = {"windows xp", "microsoft office word", "internet archive", "unknown", "anonymous"}


@dataclass(frozen=True, slots=True)
class BookMetadata:
    title: str
    author: str | None = None
    year: int | None = None


def clean_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    value = value.translate(str.maketrans({"’": "'", "‘": "'", "‛": "'"}))
    value = value.replace("@", " at ").replace("_", " ")
    value = HASH.sub(" ", value)
    value = ARCHIVE_NOISE.sub(" ", value)
    value = re.sub(r"[^\w\s']", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def metadata_from_filename(file_name: str) -> BookMetadata:
    stem = Path(file_name).stem
    parenthetical_author = None
    title_source = stem
    for match in re.finditer(r"\(([^()]*)\)", stem):
        candidate = match.group(1)
        names = [clean_text(name) for name in re.split(r"\s*(?:,|&|\band\b)\s*", candidate, flags=re.IGNORECASE)]
        names = [name for name in names if name]
        if len(names) >= 2 and all(1 < len(name.split()) <= 6 and not any(char.isdigit() for char in name) for name in names):
            parenthetical_author = " and ".join(names)
            title_source = stem[: match.start()]
            break
    parts = [clean_text(part) for part in re.split(r"\s*(?:--+|—+|–+)\s*", title_source)]
    parts = [part for part in parts if part]
    title = parts[0] if parts else clean_text(stem) or "Untitled document"
    year_match = YEAR.search(stem)
    year = int(year_match.group(1)) if year_match else None
    author = parenthetical_author
    for part in parts[1:]:
        if author:
            break
        normalized = part.casefold()
        if YEAR.fullmatch(part) or any(character.isdigit() for character in part):
            continue
        if normalized in GENERIC_TITLES or ARCHIVE_NOISE.search(part):
            continue
        words = part.split()
        if 1 < len(words) <= 7:
            author = part
            break
    return BookMetadata(title=title, author=author, year=year)


def extract_book_metadata(document_path: Path, file_name: str) -> BookMetadata:
    inferred = metadata_from_filename(file_name)
    try:
        embedded = inspect_document(document_path)
    except Exception:
        return inferred

    embedded_title = clean_text(embedded.title)
    embedded_author = clean_text(embedded.author)
    title = inferred.title
    if title.casefold() in GENERIC_TITLES and embedded_title.casefold() not in GENERIC_TITLES:
        title = embedded_title
    author = inferred.author
    if not author and embedded_author and embedded_author.casefold() not in BAD_AUTHORS:
        author = embedded_author
    return BookMetadata(title=title, author=author, year=inferred.year or embedded.year)
