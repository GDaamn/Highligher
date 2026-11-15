from backend.app.services.metadata import metadata_from_filename


def test_archive_filename_becomes_book_metadata():
    metadata = metadata_from_filename(
        "Astrolocality Astrology_ A Guide to What it is and How to -- Martin Davis -- 2018 -- The Wessex Astrologer -- 59ac06974ac489a95328b755d4d9cdcb -- Anna’s Archive.pdf"
    )

    assert metadata.title == "Astrolocality Astrology A Guide to What it is and How to"
    assert metadata.author == "Martin Davis"
    assert metadata.year == 2018


def test_hash_and_archive_suffix_are_removed():
    metadata = metadata_from_filename(
        "Astrology @ The Speed Of Light -- Kapiel Raaj -- 2011 -- astrologykrs_com -- bc07eac58bedaddb45726caa1e754c55 -- Anna’s Archive.pdf"
    )

    assert metadata.title == "Astrology at The Speed Of Light"
    assert metadata.author == "Kapiel Raaj"
    assert metadata.year == 2011


def test_apostrophes_are_preserved_and_normalized():
    metadata = metadata_from_filename("A Beginner’s Guide to Astrology -- Jane O’Connor -- 2020.pdf")

    assert metadata.title == "A Beginner's Guide to Astrology"
    assert metadata.author == "Jane O'Connor"


def test_parenthetical_multi_author_is_removed_from_title():
    metadata = metadata_from_filename(
        "The Art of Strategy A Game Theorists Guide to Success in Business and Life (Avinash K. Dixit, Barry J. Nalebuff) (Z-Library).pdf"
    )

    assert metadata.title == "The Art of Strategy A Game Theorists Guide to Success in Business and Life"
    assert metadata.author == "Avinash K Dixit and Barry J Nalebuff"
