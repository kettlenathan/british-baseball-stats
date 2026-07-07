from scraper.discovery import resolve_fetch_code


def test_d2_resolves_to_aaa_before_2026():
    assert resolve_fetch_code("d2", 2021) == "aaa"
    assert resolve_fetch_code("d2", 2025) == "aaa"


def test_d2_resolves_to_itself_from_2026():
    assert resolve_fetch_code("d2", 2026) == "d2"
    assert resolve_fetch_code("d2", 2027) == "d2"


def test_d3_resolves_to_aa_before_2026():
    assert resolve_fetch_code("d3", 2024) == "aa"


def test_d4_resolves_to_a_before_2026():
    assert resolve_fetch_code("d4", 2021) == "a"
    assert resolve_fetch_code("d4", 2025) == "a"


def test_d4_resolves_to_itself_from_2026():
    assert resolve_fetch_code("d4", 2026) == "d4"


def test_d5_is_never_remapped():
    assert resolve_fetch_code("d5", 2021) == "d5"


def test_unmapped_codes_are_identity():
    assert resolve_fetch_code("nbl", 2021) == "nbl"
