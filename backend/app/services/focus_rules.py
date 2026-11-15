from __future__ import annotations

import re
from typing import Any


STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "for", "from",
    "had", "has", "have", "he", "her", "his", "i", "if", "in", "into", "is", "it",
    "its", "of", "on", "or", "our", "she", "that", "the", "their", "there", "they",
    "this", "to", "was", "we", "were", "which", "who", "will", "with", "you", "your",
}

NEGATIONS = {"no", "not", "never", "neither", "nor", "without", "cannot", "can't", "won't", "don't", "doesn't", "isn't", "aren't"}
CONTEXT_WORDS = {
    "because", "but", "although", "however", "if", "instead", "may", "might", "must",
    "should", "so", "therefore", "though", "unless", "until", "when", "whereas", "while",
    "will", "would", "yet",
}
STRUCTURAL_LABELS = {
    "appendix", "book", "chapter", "epilogue", "introduction", "part",
    "prologue", "section", "unit",
}
PRESERVED_PAGE_KINDS = {"contents", "structural-title"}
NON_READING_LABELS = {
    "contents", "table of contents", "copyright", "all rights reserved", "also by",
    "dedication", "title page",
}
BACK_MATTER_OPENINGS = (
    "also available", "books from", "books published by", "catalog", "catalogue",
    "more titles", "other books", "other titles", "titles available", "titles from",
)


def normalized_word(word: dict[str, Any]) -> str:
    return re.sub(r"[^\w'-]", "", str(word.get("text", "")), flags=re.UNICODE).casefold()


def _focus_score(word: dict[str, Any]) -> float:
    text = re.sub(r"[^\w'-]", "", str(word.get("text", "")), flags=re.UNICODE)
    normalized = text.casefold()
    score = 0.0
    if normalized in NEGATIONS:
        score += 5
    elif normalized in CONTEXT_WORDS:
        score += 2
    elif normalized and normalized not in STOP_WORDS:
        score += 3
    if len(text) >= 7:
        score += 1.5
    elif len(text) >= 4:
        score += 0.5
    if any(character.isdigit() for character in text):
        score += 2
    if text[:1].isupper():
        score += 0.5
    return score


def _sentence_spans(words: list[dict[str, Any]], maximum_words: int = 80) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    for index, word in enumerate(words):
        text = str(word.get("text", "")).strip()
        sentence_end = bool(re.search(r"[.!?](?:[\"'”’\)\]]+)?$", text))
        reached_limit = index - start + 1 >= maximum_words
        if sentence_end or reached_limit:
            spans.append((start, index + 1))
            start = index + 1
    if start < len(words):
        spans.append((start, len(words)))
    return spans


def _clause_skeleton(words: list[dict[str, Any]], start: int, end: int) -> set[int]:
    normalized = {index: normalized_word(words[index]) for index in range(start, end)}
    copulas = {"is", "are", "was", "were", "means", "becomes"}
    filler = STOP_WORDS | {"such", "that", "they", "to", "be"}
    copula = next((index for index in range(start, end) if normalized[index] in copulas), None)
    if copula is None:
        ranked = sorted(range(start, end), key=lambda index: _focus_score(words[index]), reverse=True)
        return set(ranked[: min(4, len(ranked))])

    subject = [index for index in range(start, copula) if normalized[index] not in filler]
    complement = [index for index in range(copula + 1, end) if normalized[index] not in filler]
    selected = set(subject[-2:])
    if subject and subject[0] > start and normalized[subject[0] - 1] in {"our", "your", "their", "his", "her", "its", "we", "you", "they", "this", "these"}:
        selected.add(subject[0] - 1)
    selected.update(complement[:3] if not subject else complement[:2])
    selected.update(complement[-2:])
    selected.add(copula)
    return selected


def _parallel_clause_skeleton(words: list[dict[str, Any]], start: int, end: int) -> set[int] | None:
    normalized = {index: normalized_word(words[index]) for index in range(start, end)}
    markers = [index for index in range(start, end) if normalized[index] == "that"]
    if len(markers) < 2:
        return None

    clauses: list[tuple[int, int]] = [(start, markers[0])]
    for marker_index, marker in enumerate(markers):
        right = markers[marker_index + 1] if marker_index + 1 < len(markers) else end
        left = marker + 1
        while left < right and normalized[left] in {"and", "but"}:
            left += 1
        if left < right:
            clauses.append((left, right))

    chosen = clauses if len(clauses) <= 3 else [clauses[0], clauses[1], clauses[-1]]
    selected: set[int] = set()
    for left, right in chosen:
        selected.update(_clause_skeleton(words, left, right))
    return selected


def classify_page(scene: dict[str, Any]) -> tuple[str, bool]:
    """Return a page kind and whether focus mode should dim the whole page."""
    words = scene.get("words", [])
    number = int(scene.get("number", 0))
    page_count = int(scene.get("pageCount", 0))
    if not words:
        return "blank", True

    text = " ".join(str(word.get("text", "")) for word in words)
    normalized = re.sub(r"\s+", " ", text).strip().casefold()
    opening = " ".join(normalized.split()[:30])
    trailing_window = max(12, round(page_count * .08)) if page_count else 0
    near_document_end = bool(page_count and number >= page_count - trailing_window + 1)

    if near_document_end and opening.startswith(BACK_MATTER_OPENINGS):
        return "back-matter", True
    if opening.startswith(("figures list", "list of figures", "list of illustrations", "illustrations list")):
        return "figures-list", True
    if "copyright" in opening or "all rights reserved" in normalized[:500]:
        return "copyright", True

    numbered_entries = sum(bool(re.fullmatch(r"\d+[.]?", token)) for token in normalized.split())
    chapter_entries = len(re.findall(r"\bchapter\s+\d+", normalized))
    dot_leader_entries = sum(bool(re.search(r"\.{4,}", str(word.get("text", "")))) for word in words)
    sentence_endings = sum(bool(re.search(r"[.!?][\"'”’\)]?$", str(word.get("text", "")))) for word in words)
    visual_lines = {round(float(word.get("y", 0)) / 2) for word in words if "y" in word}
    average_words_per_line = len(words) / max(1, len(visual_lines))
    list_like_navigation = (
        number <= 30
        and len(words) <= 180
        and len(visual_lines) >= 8
        and average_words_per_line <= 8
        and sentence_endings <= max(2, len(visual_lines) // 8)
    )
    navigation_terms = {"chapter", "part", "preface", "introduction", "notes", "index", "workouts", "reading"}
    has_navigation_terms = bool(navigation_terms.intersection(normalized.split()))
    looks_like_contents = (
        opening.startswith("contents")
        or opening.startswith("table of contents")
        or (number <= 20 and len(words) <= 120 and numbered_entries >= 2 and has_navigation_terms)
        or (number <= 30 and len(words) <= 180 and chapter_entries >= 2)
        or (number <= 30 and len(words) <= 220 and len(visual_lines) >= 5 and dot_leader_entries >= 3)
        or list_like_navigation
    )
    if looks_like_contents:
        return "contents", False
    opening_words = normalized.split()[:12]
    if len(words) <= 40 and opening_words and opening_words[0] in STRUCTURAL_LABELS:
        return "structural-title", False
    if opening.startswith("index") and len(words) < 220:
        return "index", True
    if any(opening.startswith(label) for label in NON_READING_LABELS):
        return "front-matter", True
    if number <= 10 and len(words) <= 90 and sentence_endings <= 1:
        return "front-matter", True
    return "reading", False


def structural_word_ids(scene: dict[str, Any]) -> list[int]:
    """Keep chapter/part/section markers and their adjacent title line visible."""
    words = scene.get("words", [])
    selected: set[int] = set()
    sizes = sorted(float(word.get("size", word.get("height", 0))) for word in words if float(word.get("size", word.get("height", 0))) > 0)
    if sizes:
        median_size = sizes[len(sizes) // 2]
        heading_size = max(median_size + 2, median_size * 1.25)
        for heading in words:
            size = float(heading.get("size", heading.get("height", 0)))
            if size < heading_size:
                continue
            heading_y = float(heading.get("y", 0))
            selected.update(
                int(word["id"]) for word in words
                if abs(float(word.get("y", 0)) - heading_y) <= max(2.0, size * .3)
            )

    for marker in words:
        if normalized_word(marker) not in STRUCTURAL_LABELS:
            continue
        marker_y = float(marker.get("y", 0))
        marker_size = max(1.0, float(marker.get("size", marker.get("height", 12))))
        same_line = [
            word for word in words
            if abs(float(word.get("y", 0)) - marker_y) <= max(2.0, marker_size * .3)
        ]
        selected.update(int(word["id"]) for word in same_line)
        later_lines = sorted({
            float(word.get("y", 0)) for word in words
            if marker_y + marker_size * .55 < float(word.get("y", 0)) <= marker_y + marker_size * 3.2
        })
        if later_lines:
            next_line = [
                word for word in words
                if abs(float(word.get("y", 0)) - later_lines[0]) <= max(2.0, marker_size * .35)
            ]
            if len(next_line) <= 14:
                selected.update(int(word["id"]) for word in next_line)

    for marker in words:
        marker_y = float(marker.get("y", 0))
        marker_size = max(1.0, float(marker.get("size", marker.get("height", 12))))
        same_line = sorted(
            (word for word in words if abs(float(word.get("y", 0)) - marker_y) <= max(2.0, marker_size * .3)),
            key=lambda word: (float(word.get("x", 0)), int(word["id"])),
        )
        line_text = " ".join(str(word.get("text", "")).strip() for word in same_line).strip()
        if 2 <= len(same_line) <= 14 and re.match(r"^#\s*\d{1,3}(?:[.)])?(?:\s|$)", line_text):
            selected.update(int(word["id"]) for word in same_line)
    return sorted(selected)


def structured_list_word_ids(scene: dict[str, Any]) -> list[int]:
    """Keep complete rows in bullets, compact tables, and repeated fact lists."""
    words = scene.get("words", [])
    if len(words) < 6:
        return []

    lines: list[list[dict[str, Any]]] = []
    line_baselines: list[float] = []
    ordered = sorted(
        words,
        key=lambda word: (
            float(word.get("baseline", float(word.get("y", 0)) + float(word.get("height", 0)) * .75)),
            float(word.get("x", 0)),
        ),
    )
    for word in ordered:
        baseline = float(word.get("baseline", float(word.get("y", 0)) + float(word.get("height", 0)) * .75))
        if lines and abs(baseline - line_baselines[-1]) <= 4:
            lines[-1].append(word)
            line_baselines[-1] = sum(
                float(item.get("baseline", float(item.get("y", 0)) + float(item.get("height", 0)) * .75))
                for item in lines[-1]
            ) / len(lines[-1])
        else:
            lines.append([word])
            line_baselines.append(baseline)

    rows: list[dict[str, Any]] = []
    marker_pattern = re.compile(r"^(?:[•●▪◦‣·*-]|\(?\d{1,3}[.)]|[A-Za-z][.)])$")
    for line, baseline in zip(lines, line_baselines, strict=True):
        line.sort(key=lambda word: float(word.get("x", 0)))
        if not 2 <= len(line) <= 18:
            rows.append({"words": line, "baseline": baseline, "candidate": False})
            continue
        texts = [str(word.get("text", "")).strip() for word in line]
        joined = " ".join(texts)
        fonts = [str(word.get("font", "")).casefold() for word in line]
        lead_is_emphasized = "bold" in fonts[0] and any("bold" not in font for font in fonts[1:])
        has_marker = bool(marker_pattern.fullmatch(texts[0]))
        has_separator = bool(re.search(r"(?:[:=–—]|\w-\w)", joined))
        has_parenthetical_data = "(" in joined and ")" in joined and bool(re.search(r"\d", joined))
        gaps = [
            float(right.get("x", 0)) - (float(left.get("x", 0)) + float(left.get("width", 0)))
            for left, right in zip(line, line[1:])
        ]
        rows.append({
            "words": line,
            "baseline": baseline,
            "left": min(float(word.get("x", 0)) for word in line),
            "candidate": has_marker or lead_is_emphasized or has_separator or has_parenthetical_data or any(gap >= 28 for gap in gaps),
        })

    selected: set[int] = set()
    run: list[dict[str, Any]] = []

    def preserve_run() -> None:
        if len(run) < 3:
            return
        lefts = [float(row["left"]) for row in run]
        if max(lefts) - min(lefts) <= 24:
            selected.update(int(word["id"]) for row in run for word in row["words"])

    for row in rows:
        if not row["candidate"]:
            preserve_run()
            run = []
            continue
        if run and float(row["baseline"]) - float(run[-1]["baseline"]) > 30:
            preserve_run()
            run = []
        run.append(row)
    preserve_run()
    return sorted(selected)


def contextual_focus(words: list[dict[str, Any]], target_ratio: float = 0.46) -> list[int]:
    """Select readable phrase fragments rather than disconnected keywords."""
    if not words:
        return []
    selected: set[int] = set()
    scores = [_focus_score(word) for word in words]
    for start, end in _sentence_spans(words):
        length = end - start
        budget = min(length, max(1, round(length * target_ratio)))
        if length >= 4:
            budget = max(2, budget)
        clause_skeleton = _parallel_clause_skeleton(words, start, end)
        if clause_skeleton is not None:
            selected.update(clause_skeleton)
            continue
        normalized_sentence = {normalized_word(words[index]) for index in range(start, end)}
        copulas = {"is", "are", "was", "were", "means", "becomes"}
        local: set[int] = _clause_skeleton(words, start, end) if normalized_sentence.intersection(copulas) else set()
        budget = max(budget, len(local))

        for index in range(start, end):
            if normalized_word(words[index]) not in NEGATIONS:
                continue
            local.add(index)
            if index + 1 < end:
                local.add(index + 1)
                if index + 2 < end:
                    local.add(index + 2)
            elif index > start:
                local.add(index - 1)

        candidates: list[tuple[float, int, int]] = []
        maximum_window = min(8, length, budget)
        minimum_window = 3 if maximum_window >= 3 else maximum_window
        for width in range(minimum_window, maximum_window + 1):
            for left in range(start, end - width + 1):
                right = left + width
                window_score = sum(scores[left:right])
                content_count = sum(normalized_word(words[index]) not in STOP_WORDS for index in range(left, right))
                candidates.append((window_score / width + content_count * 0.35 + width * 0.08, left, right))
        candidates.sort(reverse=True)

        while len(local) < budget:
            remaining = budget - len(local)
            best: tuple[float, list[int]] | None = None
            for density, left, right in candidates:
                additions = [index for index in range(left, right) if index not in local]
                if not additions or len(additions) > remaining:
                    continue
                adjacency = any(index - 1 in local or index + 1 in local for index in additions)
                value = density + (0.4 if adjacency else 0)
                if best is None or value > best[0]:
                    best = (value, additions)
            if best is None:
                unselected = [index for index in range(start, end) if index not in local]
                if not unselected:
                    break
                adjacent = [index for index in unselected if index - 1 in local or index + 1 in local]
                local.add(max(adjacent or unselected, key=lambda index: scores[index]))
            else:
                local.update(best[1])

        ordered_local = sorted(local)
        for left, right in zip(ordered_local, ordered_local[1:]):
            if right - left - 1 <= 3:
                local.update(range(left, right + 1))
        selected.update(local)
    return [int(word["id"]) for index, word in enumerate(words) if index in selected]
