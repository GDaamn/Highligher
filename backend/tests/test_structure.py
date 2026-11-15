import json

from backend.app.services.structure import build_structure


def test_structure_collects_page_roles_and_chapter_markers(tmp_path):
    scene_dir = tmp_path / "scene"
    focus_dir = tmp_path / "focus"
    pages_dir = scene_dir / "pages"
    pages_dir.mkdir(parents=True)
    focus_dir.mkdir()

    scenes = {
        1: {"number": 1, "height": 800, "words": [{"id": 0, "text": "Cover", "x": 80, "y": 100, "size": 24}]},
        8: {"number": 8, "height": 800, "words": [
            {"id": 0, "text": "CHAPTER", "x": 80, "y": 100, "size": 18},
            {"id": 1, "text": "1", "x": 180, "y": 100, "size": 18},
            {"id": 2, "text": "Strategic", "x": 80, "y": 136, "size": 22},
            {"id": 3, "text": "Moves", "x": 175, "y": 136, "size": 22},
            {"id": 4, "text": "Body", "x": 80, "y": 220, "size": 11},
        ]},
    }
    for page, scene in scenes.items():
        (pages_dir / f"page-{page:04d}.json").write_text(json.dumps(scene))
        role = "front-matter" if page == 1 else "structural-title"
        (focus_dir / f"page-{page:04d}.json").write_text(json.dumps({"page": page, "pageKind": role}))

    structure = build_structure(scene_dir, focus_dir)

    assert structure.page_roles == {"1": "front-matter", "8": "structural-title"}
    assert structure.version == 3
    assert structure.chapters[0].page == 8
    assert structure.chapters[0].label == "CHAPTER 1 Strategic Moves"


def test_chapter_summary_is_not_a_navigation_entry(tmp_path):
    scene_dir = tmp_path / "scene"
    focus_dir = tmp_path / "focus"
    pages_dir = scene_dir / "pages"
    pages_dir.mkdir(parents=True)
    focus_dir.mkdir()
    scene = {"number": 12, "height": 800, "words": [
        {"id": 0, "text": "Chapter", "x": 80, "y": 100, "size": 20},
        {"id": 1, "text": "Summary", "x": 170, "y": 100, "size": 20},
        {"id": 2, "text": "Body", "x": 80, "y": 160, "size": 11},
    ]}
    (pages_dir / "page-0012.json").write_text(json.dumps(scene))
    (focus_dir / "page-0012.json").write_text(json.dumps({"page": 12, "pageKind": "reading"}))

    assert build_structure(scene_dir, focus_dir).chapters == []
