from replica import convert_pdf


def test_converter_has_public_import() -> None:
    assert callable(convert_pdf)


def test_preview_batches_preserve_page_order(tmp_path):
    from PIL import Image
    import pytest
    from replica.converter import find_poppler, render_sources

    renderer = find_poppler()
    if not renderer:
        pytest.skip("Poppler is required for PDF previews.")
    colors = ["red", "green", "blue", "black", "white"]
    pages = [Image.new("RGB", (64, 64), color) for color in colors]
    pdf = tmp_path / "colors.pdf"
    pages[0].save(pdf, save_all=True, append_images=pages[1:])
    completed = []
    def progress(stage, current, total):
        if stage == "encode":
            completed.append(current)

    assert render_sources(pdf, tmp_path, 72, len(pages), renderer, progress)
    assert completed == [1, 2, 3, 4, 5]
    for number, original in enumerate(pages, 1):
        with Image.open(tmp_path / "source-pages" / f"page-{number:04d}.webp") as preview:
            expected = original.getpixel((32, 32))
            actual = preview.convert("RGB").getpixel((32, 32))
            assert max(abs(a - b) for a, b in zip(expected, actual)) < 8
    assert not list((tmp_path / "source-pages").glob("*.ppm"))
