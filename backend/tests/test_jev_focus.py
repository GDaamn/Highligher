import json

import httpx
import pytest

from backend.app.services.jev import JevFocusClient


@pytest.mark.asyncio
async def test_jev_keeps_the_entire_sentence_across_pages():
    client = JevFocusClient("test", "typesafe/jev-1.14")
    await client.http.aclose()

    def answer(request):
        assert str(request.url) == "https://openrouter.ai/api/alpha/decisions"
        body = json.loads(request.content)
        assert body["model"] == "typesafe/jev-1.14"
        assert body["state"] == {"s0": "Who are we?", "s1": "More later."}
        return httpx.Response(200, json={"answers": {"s0": {"noul": .9}, "s1": {"noul": .1}}})

    client.http = httpx.AsyncClient(transport=httpx.MockTransport(answer))
    try:
        pages = [{"page": i, "words": [{"id": j, "text": w} for j, w in enumerate(line.split())]}
                 for i, line in enumerate(["Who are", "we? More later."], 1)]
        result = await client.select(pages)
        assert result[1]["essential"] == [0, 1]
        assert result[2]["essential"] == [0]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_jev_keeps_the_best_summary_sentence_when_all_scores_are_low():
    client = JevFocusClient("test", "typesafe/jev-1.14")
    await client.http.aclose()

    def answer(request):
        body = json.loads(request.content)
        assert body["state"] == {
            "s0": "A minor detail appears.",
            "s1": "The central claim explains the page.",
        }
        return httpx.Response(200, json={
            "answers": {"s0": {"noul": .2}, "s1": {"noul": .6}},
        })

    client.http = httpx.AsyncClient(transport=httpx.MockTransport(answer))
    page = {
        "page": 8,
        "words": [
            {"id": index, "text": word}
            for index, word in enumerate(
                "A minor detail appears. The central claim explains the page.".split()
            )
        ],
    }
    try:
        result = await client.select([page])
        assert result[8]["essential"] == [4, 5, 6, 7, 8, 9]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_reader_key_applies_to_existing_page_without_changing_saved_focus(tmp_path, monkeypatch):
    from backend.app import main
    from backend.app.models import DocumentRecord
    from backend.app.store import DocumentStore

    store = DocumentStore(tmp_path)
    manager = main.JobManager(main.settings, store)
    monkeypatch.setitem(main.app.dependency_overrides, main.libraries.for_request, lambda: manager)
    store.create(DocumentRecord(id="book", file_name="example.pdf", state="ready", page_count=20))
    scene = store.scene_dir("book") / "pages"
    scene.mkdir(parents=True)
    page = {"number": 8, "words": [{"id": i, "text": w} for i, w in enumerate("Who are we? More later.".split())]}
    (scene / "page-0008.json").write_text(json.dumps(page))
    saved = {"focusWordIds": [4], "contextWordIds": [], "pageKind": "body"}
    store.focus_dir("book").mkdir(exist_ok=True)
    path = store.focus_dir("book") / "page-0008.json"
    path.write_text(json.dumps(saved))

    calls = []

    async def score(self, pages):
        calls.append(pages)
        assert self.http.headers["Authorization"] == "Bearer sample"
        return {8: {"essential": [0, 1, 2], "context": []}}

    monkeypatch.setattr(JevFocusClient, "select", score)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as browser:
        url = "/api/documents/book/focus/8/openrouter"
        assert (await browser.post(url)).status_code == 400
        result = await browser.post(url, headers={"Authorization": "Bearer sample"})
        assert result.status_code == 200
        assert result.json()["focusWordIds"] == [0, 1, 2]
        store.set_word_focus("book", 8, 2, "we", False)
        again = await browser.post(url, headers={"Authorization": "Bearer sample"})
        assert again.json()["focusWordIds"] == [0, 1]
        assert len(calls) == 1
        assert "sample" not in result.text
        assert json.loads(path.read_text()) == saved


@pytest.mark.asyncio
async def test_reader_key_falls_back_to_a_page_summary_when_jev_selects_nothing(tmp_path, monkeypatch):
    from backend.app import main
    from backend.app.models import DocumentRecord
    from backend.app.store import DocumentStore

    store = DocumentStore(tmp_path)
    monkeypatch.setattr(main, "store", store)
    main.jev_page_ids.clear()
    store.create(DocumentRecord(id="book", file_name="example.pdf", state="ready", page_count=20))
    scene = store.scene_dir("book") / "pages"
    scene.mkdir(parents=True)
    words = [
        {"id": index, "text": word}
        for index, word in enumerate("Who are we? More details follow here.".split())
    ]
    (scene / "page-0008.json").write_text(json.dumps({"number": 8, "words": words}))
    store.focus_dir("book").mkdir(exist_ok=True)
    (store.focus_dir("book") / "page-0008.json").write_text(json.dumps({
        "focusWordIds": [], "contextWordIds": [], "pageKind": "reading",
    }))

    async def score(self, pages):
        return {8: {"essential": [], "context": []}}

    monkeypatch.setattr(JevFocusClient, "select", score)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as browser:
        result = await browser.post(
            "/api/documents/book/focus/8/openrouter",
            headers={"Authorization": "Bearer sample"},
        )

    assert result.status_code == 200
    assert result.json()["focusWordIds"] == [0, 1, 2]


def test_layout_breaks_stop_title_and_attribution_entering_opening_sentence(tmp_path):
    from backend.app.services.focus import FocusAnalyzer
    from backend.app.services.sentence_focus import passages

    rows = [(20, "CHAPTER"), (80, "“Leave now.”"), (96, "— The traveller"),
            (160, "She woke"), (176, "in the cabin."), (192, "The train had stopped.")]
    words = []
    for y, line in rows:
        for text in line.split():
            words.append({"id": len(words), "text": text, "height": 12, "y": y})
    path = tmp_path / "page.json"
    path.write_text(json.dumps({"number": 4, "words": words}))
    page = FocusAnalyzer._load_page(path)
    sentences = [" ".join(w["text"] for _, w in sentence)
                 for group in passages([page]) for sentence in group]
    assert sentences == ["CHAPTER", "“Leave now.”", "— The traveller",
                         "She woke in the cabin.", "The train had stopped."]
