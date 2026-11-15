from backend.app.models import DocumentRecord
from backend.app.store import DocumentStore


def test_document_store_round_trip(tmp_path):
    store = DocumentStore(tmp_path)
    store.create(DocumentRecord(id="abc123", file_name="example.pdf"))
    updated = store.update("abc123", state="converting", progress=0.25)
    assert updated.state == "converting"
    assert store.get("abc123").progress == 0.25


def test_document_store_delete_removes_document(tmp_path):
    store = DocumentStore(tmp_path)
    store.create(DocumentRecord(id="discard-me", file_name="invalid.pdf"))
    store.source_path("discard-me").write_bytes(b"not a pdf")

    store.delete("discard-me")

    assert not store.directory("discard-me").exists()


def test_page_dim_preferences_are_persisted_and_toggleable(tmp_path):
    store = DocumentStore(tmp_path)
    store.create(DocumentRecord(id="book", file_name="book.pdf"))

    assert store.get_preferences("book").dimmed_pages == []
    assert store.set_page_dimmed("book", 4, True).dimmed_pages == [4]
    assert store.set_page_dimmed("book", 2, True).dimmed_pages == [2, 4]
    assert DocumentStore(tmp_path).get_preferences("book").dimmed_pages == [2, 4]
    assert store.set_page_dimmed("book", 4, False).dimmed_pages == [2]


def test_reading_position_and_focus_corrections_persist(tmp_path):
    store = DocumentStore(tmp_path)
    store.create(DocumentRecord(id="book", file_name="book.pdf"))

    state = store.set_reading_state("book", 42, "original")
    corrected = store.set_word_focus("book", 42, 7, "Strategy", True)
    corrected = store.set_word_focus("book", 43, 3, "Strategy", True)

    assert state.last_page == 42
    assert state.reading_mode == "original"
    assert corrected.word_overrides["42"]["focus"] == [7]
    assert corrected.learned_terms["strategy"] == 2
    assert DocumentStore(tmp_path).get_preferences("book") == corrected


def test_incomplete_documents_can_resume_after_restart(tmp_path):
    store = DocumentStore(tmp_path)
    store.create(DocumentRecord(id="working", file_name="book.pdf", state="focusing"))
    store.source_path("working").write_bytes(b"%PDF-work")
    store.create(DocumentRecord(id="ready", file_name="done.pdf", state="ready"))
    store.source_path("ready").write_bytes(b"%PDF-done")

    assert [record.id for record in store.list_incomplete()] == ["working"]


def test_readable_document_is_listed_and_still_resumable(tmp_path):
    store = DocumentStore(tmp_path)
    store.create(DocumentRecord(id="streaming", file_name="book.pdf", state="readable"))
    store.source_path("streaming").write_bytes(b"%PDF-work")
    store.scene_dir("streaming").mkdir()
    (store.scene_dir("streaming") / "manifest.json").write_text("{}")

    assert [record.id for record in store.list_ready()] == ["streaming"]
    assert [record.id for record in store.list_incomplete()] == ["streaming"]


def test_store_finds_and_backfills_ready_document_digest(tmp_path):
    store = DocumentStore(tmp_path)
    store.create(DocumentRecord(id="saved", file_name="book.pdf", state="ready", focus_provider="heuristic"))
    store.source_path("saved").write_bytes(b"%PDF-saved-content")
    store.scene_dir("saved").mkdir()
    (store.scene_dir("saved") / "manifest.json").write_text("{}")

    digest = store._sha256(store.source_path("saved"))
    found = store.find_ready_by_digest(digest, content_bytes=18, focus_provider="heuristic")

    assert found is not None
    assert found.id == "saved"
    assert store.get("saved").content_sha256 == digest


def test_store_does_not_reuse_different_focus_provider(tmp_path):
    store = DocumentStore(tmp_path)
    store.create(DocumentRecord(id="saved", file_name="book.pdf", state="ready", focus_provider="heuristic", content_sha256="a" * 64))
    store.source_path("saved").write_bytes(b"%PDF-saved")
    store.scene_dir("saved").mkdir()
    (store.scene_dir("saved") / "manifest.json").write_text("{}")

    assert store.find_ready_by_digest("a" * 64, focus_provider="deepseek") is None


def test_store_does_not_reuse_an_older_focus_strategy(tmp_path):
    store = DocumentStore(tmp_path)
    store.create(DocumentRecord(
        id="saved",
        file_name="book.pdf",
        state="ready",
        focus_provider="deepseek",
        focus_strategy_version=1,
        content_sha256="a" * 64,
    ))
    store.source_path("saved").write_bytes(b"%PDF-saved")
    store.scene_dir("saved").mkdir()
    (store.scene_dir("saved") / "manifest.json").write_text("{}")

    assert store.find_ready_by_digest(
        "a" * 64,
        focus_provider="deepseek",
        focus_strategy_version=2,
    ) is None


def test_store_does_not_reuse_a_different_focus_model(tmp_path):
    store = DocumentStore(tmp_path)
    store.create(DocumentRecord(
        id="saved",
        file_name="book.pdf",
        state="ready",
        focus_provider="deepseek",
        focus_model="old-model",
        focus_strategy_version=2,
        content_sha256="a" * 64,
    ))
    store.source_path("saved").write_bytes(b"%PDF-saved")
    store.scene_dir("saved").mkdir()
    (store.scene_dir("saved") / "manifest.json").write_text("{}")

    assert store.find_ready_by_digest(
        "a" * 64,
        focus_provider="deepseek",
        focus_model="new-model",
        focus_strategy_version=2,
    ) is None


def test_store_lists_ready_documents_once_and_newest_first(tmp_path):
    store = DocumentStore(tmp_path)
    records = [
        DocumentRecord(id="old-copy", file_name="same.pdf", state="ready", content_sha256="a" * 64, updated_at="2026-01-01T00:00:00+00:00"),
        DocumentRecord(id="new-copy", file_name="same.pdf", state="ready", content_sha256="a" * 64, updated_at="2026-02-01T00:00:00+00:00"),
        DocumentRecord(id="other", file_name="other.pdf", state="ready", content_sha256="b" * 64, updated_at="2026-03-01T00:00:00+00:00"),
        DocumentRecord(id="working", file_name="working.pdf", state="converting"),
    ]
    for record in records:
        store.create(record)
        if record.state == "ready":
            store.scene_dir(record.id).mkdir()
            (store.scene_dir(record.id) / "manifest.json").write_text("{}")

    assert [record.id for record in store.list_ready()] == ["other", "new-copy"]
