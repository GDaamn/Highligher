from __future__ import annotations

import asyncio
import json
import math

import pytest

from backend.app.services.focus import (
    FOCUS_STRATEGY_VERSION,
    FocusAnalyzer,
    classify_page,
    contextual_focus,
    structural_word_ids,
    structured_list_word_ids,
)


class FakeDeepSeekClient:
    async def select(self, pages):
        return {
            page["page"]: {
                "essential": [word["id"] for word in page["words"] if word["text"] in {"planet", "Saturn"}],
                "context": [word["id"] for word in page["words"] if word["text"] == "distant"],
            }
            for page in pages
        }


class ConcurrentClient:
    def __init__(self):
        self.active = 0
        self.maximum = 0

    async def select(self, pages):
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        await asyncio.sleep(.02)
        self.active -= 1
        return {page["page"]: {"essential": [], "context": []} for page in pages}


class FailingClient:
    async def select(self, pages):
        raise TimeoutError("remote focus timed out")


def test_contextual_focus_preserves_reading_order():
    words = [
        {"id": 0, "text": "the"},
        {"id": 1, "text": "distant"},
        {"id": 2, "text": "planet"},
        {"id": 3, "text": "moves"},
        {"id": 4, "text": "slowly"},
    ]
    selected = contextual_focus(words, target_ratio=0.4)
    assert selected == sorted(selected)
    assert 0 not in selected


def test_contextual_focus_keeps_readable_phrases_and_negation():
    text = (
        "Strategic thinking starts with your basic skills and considers how best to use them. "
        "Sometimes strategic thinking also means knowing when not to play."
    )
    words = [{"id": index, "text": word} for index, word in enumerate(text.split())]

    selected = contextual_focus(words)
    selected_set = set(selected)
    not_id = next(word["id"] for word in words if word["text"] == "not")
    play_id = next(word["id"] for word in words if word["text"] == "play.")

    assert 0.36 <= len(selected) / len(words) <= 0.62
    assert {not_id, play_id}.issubset(selected_set)
    assert any(word_id < 12 for word_id in selected)
    assert any(word_id >= 12 for word_id in selected)
    assert all(word_id - 1 in selected_set or word_id + 1 in selected_set for word_id in selected)


def test_contextual_focus_generalizes_across_parallel_claims():
    text = (
        "The study demonstrates that rainfall declined, that farms adopted temporary measures, "
        "and that crop yields eventually recovered."
    )
    words = [{"id": index, "text": word} for index, word in enumerate(text.split())]
    selected = set(contextual_focus(words))
    kept = {words[index]["text"].strip(",.").casefold() for index in selected}

    assert {"study", "demonstrates", "rainfall", "declined", "crop", "yields", "recovered"} <= kept
    assert not {"farms", "temporary", "measures"}.intersection(kept)


def test_heuristic_focus_preserves_a_sentence_across_page_boundary():
    first_text = "There is one method of astrology that can: Jaimini"
    second_text = "Astrology. The next paragraph begins here."
    pages = [
        {
            "page": 5,
            "words": [{"id": index, "text": text} for index, text in enumerate(first_text.split())],
        },
        {
            "page": 6,
            "words": [{"id": index, "text": text} for index, text in enumerate(second_text.split())],
        },
    ]

    layers = FocusAnalyzer._heuristic_document_layers(pages)
    first_essential = {
        pages[0]["words"][word_id]["text"].strip(".,:").casefold()
        for word_id in layers[5]["essential"]
    }
    second_essential = {
        pages[1]["words"][word_id]["text"].strip(".,:").casefold()
        for word_id in layers[6]["essential"]
    }

    assert {"method", "astrology", "jaimini"} <= first_essential
    assert "astrology" in second_essential


def test_load_page_ignores_running_page_number(tmp_path):
    path = tmp_path / "page-0005.json"
    path.write_text(json.dumps({
        "number": 5,
        "height": 792,
        "words": [
            {"id": 0, "text": "Astrology", "y": 120},
            {"id": 1, "text": "5", "y": 760},
        ],
    }))

    page = FocusAnalyzer._load_page(path)

    assert page["words"] == [{"id": 0, "text": "Astrology"}]


@pytest.mark.parametrize("text", ["Part I", "CHAPTER 1", "Section IV", "Appendix A"])
def test_structural_divider_pages_remain_fully_visible(text):
    words = [{"id": index, "text": word} for index, word in enumerate(text.split())]
    assert classify_page({"number": 18, "words": words}) == ("structural-title", False)


def test_structural_heading_keeps_marker_number_and_title_line():
    scene = {
        "number": 40,
        "words": [
            {"id": 0, "text": "CHAPTER", "y": 80, "size": 16},
            {"id": 1, "text": "3", "y": 80, "size": 16},
            {"id": 2, "text": "Strategic", "y": 108, "size": 18},
            {"id": 3, "text": "Moves", "y": 108, "size": 18},
            {"id": 4, "text": "Body", "y": 180, "size": 11},
        ],
    }

    assert structural_word_ids(scene) == [0, 1, 2, 3]


def test_large_section_heading_is_preserved_without_a_chapter_keyword():
    scene = {
        "number": 4,
        "words": [
            {"id": 0, "text": "About", "y": 100, "size": 16},
            {"id": 1, "text": "Me", "y": 100, "size": 16},
            {"id": 2, "text": "This", "y": 150, "size": 12},
            {"id": 3, "text": "is", "y": 150, "size": 12},
            {"id": 4, "text": "substantive.", "y": 150, "size": 12},
        ],
    }

    assert structural_word_ids(scene) == [0, 1]


@pytest.mark.parametrize(
    "heading_words",
    [
        ["#3.", "THE", "HOT", "HAND"],
        ["#", "5.", "HERE", "I", "STAND"],
    ],
)
def test_hash_numbered_subsection_heading_is_preserved(heading_words):
    scene = {
        "number": 42,
        "words": [
            *[
                {"id": index, "text": text, "x": 60 + index * 35, "y": 100, "size": 12}
                for index, text in enumerate(heading_words)
            ],
            {"id": len(heading_words), "text": "Body", "x": 60, "y": 145, "size": 12},
        ],
    }

    assert structural_word_ids(scene) == list(range(len(heading_words)))


def test_repeated_fact_rows_are_preserved_as_complete_rows():
    entries = [
        ("ARIES-FIRE-MARS", "(April 15th - May 14th)"),
        ("TAURUS-EARTH-VENUS", "(May 15th - June 14th)"),
        ("GEMINI-AIR-MERCURY", "(June 15th - July 14th)"),
        ("CANCER-WATER-MOON", "(July 15th - August 14th)"),
    ]
    words = []
    for line_number, (label, detail) in enumerate(entries):
        x = 72.0
        for text in (label, *detail.split()):
            words.append({
                "id": len(words),
                "text": text,
                "x": x,
                "y": 120 + line_number * 18,
                "baseline": 132 + line_number * 18,
                "width": len(text) * 6,
                "height": 12,
                "font": "Book-Bold" if text == label else "Book-Regular",
            })
            x += len(text) * 6 + 5

    assert structured_list_word_ids({"number": 18, "words": words}) == [word["id"] for word in words]


def test_normal_prose_lines_are_not_forced_into_structured_focus():
    lines = [
        "A normal paragraph can wrap across several visual lines",
        "without becoming a table or a list of independent facts",
        "so semantic selection should still be applied to its words",
    ]
    words = []
    for line_number, text in enumerate(lines):
        x = 72.0
        for token in text.split():
            words.append({
                "id": len(words), "text": token, "x": x,
                "y": 120 + line_number * 15, "baseline": 132 + line_number * 15,
                "width": len(token) * 6, "height": 12, "font": "Book-Regular",
            })
            x += len(token) * 6 + 5

    assert structured_list_word_ids({"number": 18, "words": words}) == []


def test_early_substantive_section_is_not_dimmed_as_front_matter():
    text = "About Me. This section explains why I wrote this book. It continues with substantive context and examples."
    words = [{"id": index, "text": word} for index, word in enumerate(text.split())]
    assert classify_page({"number": 4, "words": words}) == ("reading", False)


def test_dot_leader_contents_continuation_is_fully_preserved():
    entries = [
        "What is Atmakaraka? ........225", "Important Karakas ........229",
        "Astrology and Compatibility ........231", "Do Gemstones work? ........237",
        "Signs of Divorce ........238", "Career by Planets ........257",
    ]
    words = []
    for line, entry in enumerate(entries):
        words.extend({"id": len(words), "text": text, "y": 80 + line * 22} for text in entry.split())

    assert classify_page({"number": 3, "words": words}) == ("contents", False)


@pytest.mark.parametrize(
    ("number", "text", "kind", "dim_page"),
    [
        (1, "The Art of Strategy", "front-matter", True),
        (6, "Copyright 2008 All rights reserved " + "legal " * 100, "copyright", True),
        (8, "CONTENTS Preface 7 Introduction 12 Chapter One 25", "contents", False),
        (9, "12. Voting 13. Incentives 14. Case Studies Further Reading Workouts Notes", "contents", False),
    ],
)
def test_page_focus_treatment(number, text, kind, dim_page):
    words = [{"id": index, "text": word} for index, word in enumerate(text.split())]
    assert classify_page({"number": number, "words": words}) == (kind, dim_page)


def test_body_page_with_quote_or_table_values_remains_eligible():
    text = ("The report explains the comparison. \"Revenue doubled because demand increased.\" "
            "Region Revenue Growth North 120 24 percent South 90 11 percent. ") * 8
    words = [{"id": index, "text": word} for index, word in enumerate(text.split())]
    assert classify_page({"number": 18, "words": words}) == ("reading", False)


def test_list_style_contents_without_contents_heading_is_detected():
    entries = [
        "Chapter 1: Introduction", "Chapter 2: Market Structure", "Risk and Reward",
        "Setting Stops and Targets", "Chapter Summary", "Chapter 3: Analysis",
        "Revenue", "Earnings per Share", "Return on Equity", "Chapter Summary",
    ]
    words = []
    word_id = 0
    for line_number, entry in enumerate(entries):
        for text in entry.split():
            words.append({"id": word_id, "text": text, "y": 80 + line_number * 24})
            word_id += 1

    assert classify_page({"number": 7, "words": words}) == ("contents", False)


def test_figures_list_is_dimmed_by_default():
    words = [{"id": index, "text": text} for index, text in enumerate("FIGURES LIST 1 Chart diagram 2 World map 3 Planetary circles".split())]
    assert classify_page({"number": 9, "words": words}) == ("figures-list", True)


def test_publisher_catalogue_is_only_back_matter_near_the_end():
    words = [{"id": index, "text": word} for index, word in enumerate("Titles from The Wessex Astrologer Other Books".split())]
    assert classify_page({"number": 229, "pageCount": 231, "words": words}) == ("back-matter", True)
    assert classify_page({"number": 40, "pageCount": 231, "words": words}) == ("reading", False)


@pytest.mark.asyncio
async def test_final_index_dims_its_unlabelled_continuations(tmp_path):
    pages_dir = tmp_path / "scene" / "pages"
    pages_dir.mkdir(parents=True)
    pages = {
        98: "Index strategy 12 planning 18 decisions 42",
        99: "Smith John 12 18 Taylor Anne 21 44",
        100: "Author Name Book Title Another Author Another Title",
    }
    for number, text in pages.items():
        words = [{"id": index, "text": word} for index, word in enumerate(text.split())]
        (pages_dir / f"page-{number:04d}.json").write_text(json.dumps({"number": number, "words": words}))

    analyzer = FocusAnalyzer(provider="deepseek", model="test", batch_characters=10_000, client=FakeDeepSeekClient())
    await analyzer.analyze(tmp_path / "scene", tmp_path / "focus")

    index = json.loads((tmp_path / "focus" / "page-0098.json").read_text())
    continuation = json.loads((tmp_path / "focus" / "page-0099.json").read_text())
    catalogue = json.loads((tmp_path / "focus" / "page-0100.json").read_text())
    assert index["pageKind"] == "index" and index["dimPage"] is True
    assert continuation["pageKind"] == "back-matter" and continuation["dimPage"] is True
    assert catalogue["pageKind"] == "back-matter" and catalogue["dimPage"] is True


@pytest.mark.asyncio
async def test_deepseek_results_are_validated_and_written(tmp_path):
    scene = tmp_path / "scene" / "pages"
    scene.mkdir(parents=True)
    words = [{"id": index, "text": text} for index, text in enumerate("Saturn is the distant planet with powerful gravity".split())]
    (scene / "page-0020.json").write_text(json.dumps({"number": 20, "words": words}), encoding="utf-8")
    analyzer = FocusAnalyzer(provider="deepseek", model="test-model", batch_characters=10000, client=FakeDeepSeekClient())
    total = await analyzer.analyze(tmp_path / "scene", tmp_path / "focus")
    result = json.loads((tmp_path / "focus" / "page-0020.json").read_text())
    assert total == len(result["focusWordIds"]) + len(result["contextWordIds"])
    assert 0 in result["focusWordIds"]
    assert result["provider"] == "deepseek"
    assert result["strategyVersion"] == FOCUS_STRATEGY_VERSION


@pytest.mark.asyncio
async def test_failed_remote_batch_falls_back_without_aborting_document(tmp_path):
    scene = tmp_path / "scene" / "pages"
    scene.mkdir(parents=True)
    words = [{"id": index, "text": text} for index, text in enumerate("Strategy requires careful choices under uncertainty".split())]
    (scene / "page-0020.json").write_text(json.dumps({"number": 20, "words": words}), encoding="utf-8")
    analyzer = FocusAnalyzer(provider="deepseek", model="test", batch_characters=10_000, client=FailingClient())

    total = await analyzer.analyze(tmp_path / "scene", tmp_path / "focus")

    result = json.loads((tmp_path / "focus" / "page-0020.json").read_text())
    assert total > 0
    assert result["provider"] == "local-heuristic"


@pytest.mark.asyncio
async def test_sentence_provider_cannot_leave_a_reading_page_empty(tmp_path):
    scene = tmp_path / "scene" / "pages"
    scene.mkdir(parents=True)
    text = (
        "The opening sentence introduces the topic. "
        "The central claim explains why careful choices matter. "
        "A final example supports the conclusion."
    )
    words = [{"id": index, "text": word} for index, word in enumerate(text.split())]
    (scene / "page-0020.json").write_text(
        json.dumps({"number": 20, "words": words}), encoding="utf-8"
    )
    analyzer = FocusAnalyzer(
        provider="jev",
        model="test",
        batch_characters=10_000,
        client=ConcurrentClient(),
    )

    await analyzer.analyze(tmp_path / "scene", tmp_path / "focus")

    result = json.loads((tmp_path / "focus" / "page-0020.json").read_text())
    sentence_ids = [
        set(range(0, 6)),
        set(range(6, 14)),
        set(range(14, 20)),
    ]
    selected = set(result["focusWordIds"])
    assert len(selected) >= math.ceil(len(words) * 0.5)
    assert all(not selected.intersection(sentence) or sentence <= selected for sentence in sentence_ids)
    assert result["contextWordIds"] == []


@pytest.mark.asyncio
async def test_valid_focus_cache_skips_remote_analysis(tmp_path):
    scene = tmp_path / "scene" / "pages"
    focus = tmp_path / "focus"
    scene.mkdir(parents=True)
    focus.mkdir()
    words = [{"id": 0, "text": "Strategy"}, {"id": 1, "text": "works"}]
    (scene / "page-0020.json").write_text(json.dumps({"number": 20, "words": words}), encoding="utf-8")
    cached = {
        "strategyVersion": FOCUS_STRATEGY_VERSION,
        "provider": "deepseek",
        "model": "test",
        "page": 20,
        "focusWordIds": [0],
        "contextWordIds": [1],
    }
    (focus / "page-0020.json").write_text(json.dumps(cached), encoding="utf-8")
    analyzer = FocusAnalyzer(provider="deepseek", model="test", batch_characters=10_000, client=FailingClient())

    total = await analyzer.analyze(tmp_path / "scene", focus)

    assert total == 2
    assert json.loads((focus / "page-0020.json").read_text()) == cached


@pytest.mark.asyncio
async def test_focus_cache_from_another_model_is_rebuilt(tmp_path):
    scene = tmp_path / "scene" / "pages"
    focus = tmp_path / "focus"
    scene.mkdir(parents=True)
    focus.mkdir()
    words = [{"id": 0, "text": "Strategy"}, {"id": 1, "text": "works"}]
    (scene / "page-0020.json").write_text(json.dumps({"number": 20, "words": words}), encoding="utf-8")
    (focus / "page-0020.json").write_text(json.dumps({
        "strategyVersion": FOCUS_STRATEGY_VERSION,
        "provider": "deepseek",
        "model": "old-model",
        "page": 20,
        "focusWordIds": [0],
        "contextWordIds": [1],
    }), encoding="utf-8")
    analyzer = FocusAnalyzer(provider="deepseek", model="new-model", batch_characters=10_000, client=FailingClient())

    await analyzer.analyze(tmp_path / "scene", focus)

    rebuilt = json.loads((focus / "page-0020.json").read_text())
    assert rebuilt["provider"] == "local-heuristic"
    assert rebuilt["model"] == "local-heuristic"


@pytest.mark.asyncio
async def test_front_matter_is_written_as_dimmed_without_ai_call(tmp_path):
    scene = tmp_path / "scene" / "pages"
    scene.mkdir(parents=True)
    words = [{"id": index, "text": word} for index, word in enumerate("The Art of Strategy".split())]
    (scene / "page-0001.json").write_text(json.dumps({"number": 1, "words": words}), encoding="utf-8")
    client = ConcurrentClient()
    analyzer = FocusAnalyzer(provider="deepseek", model="test", batch_characters=10000, client=client)

    total = await analyzer.analyze(tmp_path / "scene", tmp_path / "focus")

    result = json.loads((tmp_path / "focus" / "page-0001.json").read_text())
    assert total == 0
    assert result["bypassFocus"] is False
    assert result["dimPage"] is True
    assert result["focusWordIds"] == []
    assert result["contextWordIds"] == []
    assert client.maximum == 0


@pytest.mark.asyncio
async def test_contents_page_remains_focusable(tmp_path):
    scene = tmp_path / "scene" / "pages"
    scene.mkdir(parents=True)
    words = [{"id": index, "text": word} for index, word in enumerate("CONTENTS Saturn 7 distant planet 12".split())]
    (scene / "page-0008.json").write_text(json.dumps({"number": 8, "words": words}), encoding="utf-8")
    analyzer = FocusAnalyzer(provider="deepseek", model="test", batch_characters=10000, client=FakeDeepSeekClient())

    await analyzer.analyze(tmp_path / "scene", tmp_path / "focus")

    result = json.loads((tmp_path / "focus" / "page-0008.json").read_text())
    assert result["pageKind"] == "contents"
    assert result["dimPage"] is False
    assert result["focusWordIds"] == [word["id"] for word in words]
    assert result["contextWordIds"] == []


@pytest.mark.asyncio
async def test_deepseek_batches_run_with_bounded_concurrency(tmp_path):
    scene = tmp_path / "scene" / "pages"
    scene.mkdir(parents=True)
    for number in range(20, 26):
        (scene / f"page-{number:04d}.json").write_text(json.dumps({"number": number, "words": [{"id": 0, "text": "planet"}]}))
    client = ConcurrentClient()
    analyzer = FocusAnalyzer(provider="deepseek", model="test", batch_characters=1, client=client, concurrency=3)

    await analyzer.analyze(tmp_path / "scene", tmp_path / "focus")

    assert client.maximum == 3


@pytest.mark.asyncio
async def test_first_thirty_percent_finish_before_background_pages(tmp_path):
    scene = tmp_path / "scene" / "pages"
    scene.mkdir(parents=True)
    for number in range(1, 11):
        (scene / f"page-{number:04d}.json").write_text(json.dumps({
            "number": number,
            "words": [{"id": 0, "text": "planet"}],
        }))
    focus_dir = tmp_path / "focus"
    checked = False

    async def initial_ready():
        nonlocal checked
        checked = True
        assert all((focus_dir / f"page-{number:04d}.json").is_file() for number in range(1, 4))
        assert not (focus_dir / "page-0004.json").exists()

    analyzer = FocusAnalyzer(
        provider="deepseek",
        model="test",
        batch_characters=1,
        client=ConcurrentClient(),
        concurrency=3,
    )
    await analyzer.analyze(tmp_path / "scene", focus_dir, initial_ready=initial_ready)

    assert checked is True
    assert (focus_dir / "page-0010.json").is_file()
