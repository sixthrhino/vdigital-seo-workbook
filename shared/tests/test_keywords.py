import pytest

from seo_workbook_common.keywords import parse_keyword_target


@pytest.mark.parametrize(
    ("raw", "expected_keyword", "expected_volume"),
    [
        ("IEC rocky mountain (100)", "IEC rocky mountain", 100),
        ("career fair (8.5K)", "career fair", 8500),
        ("college credits (1.4K)", "college credits", 1400),
        ("electrician apprenticeship (25k)", "electrician apprenticeship", 25000),
        ("electrical continuing education class online (200)", "electrical continuing education class online", 200),
        ("iecrm (900)", "iecrm", 900),
    ],
)
def test_parse_keyword_target_splits_volume(raw: str, expected_keyword: str, expected_volume: int):
    target = parse_keyword_target(raw)
    assert target.keyword == expected_keyword
    assert target.search_volume == expected_volume


@pytest.mark.parametrize("raw", ["n/a", "N/A", "Multiple locations", "how to become an electrician"])
def test_parse_keyword_target_without_volume(raw: str):
    target = parse_keyword_target(raw)
    assert target.keyword == raw
    assert target.search_volume is None
