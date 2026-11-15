from replica import convert_pdf


def test_converter_has_public_import() -> None:
    assert callable(convert_pdf)
