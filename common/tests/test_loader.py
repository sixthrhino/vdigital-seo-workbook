from pathlib import Path

import pytest

from seo_workbook_common.best_practices.loader import load_catalog, slugify

CSV_PATH = Path(__file__).resolve().parents[2] / "data" / "organic_qa_checklist.csv"


def test_csv_data_file_exists():
    assert CSV_PATH.is_file(), f"expected checked-in CSV at {CSV_PATH}"


def test_loads_all_touchpoints():
    catalog = load_catalog(CSV_PATH)
    assert len(catalog.touchpoints) == 33


def test_touchpoint_ids_are_unique():
    catalog = load_catalog(CSV_PATH)
    ids = catalog.ids()
    assert len(ids) == len(set(ids))


def test_title_tag_parsed_correctly():
    catalog = load_catalog(CSV_PATH)
    tp = catalog.get("title_tag")
    assert tp.category == "Core"
    assert tp.search_tactic == "SEO"
    assert len(tp.qa_guidelines) == 4
    assert "60 characters" in tp.qa_guidelines[1]


def test_meta_description_qa_guidelines_split_into_list():
    catalog = load_catalog(CSV_PATH)
    tp = catalog.get("meta_description")
    assert len(tp.qa_guidelines) == 4
    assert all(not g.startswith(("1.)", "2.)", "3.)", "4.)")) for g in tp.qa_guidelines)


def test_by_category_filters_correctly():
    catalog = load_catalog(CSV_PATH)
    core_touchpoints = catalog.by_category("Core")
    assert {tp.touchpoint_id for tp in core_touchpoints} == {"h1_tag", "meta_description", "title_tag"}


def test_unknown_touchpoint_raises_keyerror():
    catalog = load_catalog(CSV_PATH)
    with pytest.raises(KeyError):
        catalog.get("does_not_exist")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("H2 / H3 / H4 tags", "h2_h3_h4_tags"),
        ("CTA (Call to Action)", "cta_call_to_action"),
        ("HTML Sitemap ", "html_sitemap"),
        ("Off-site: Youtube Optimization", "off_site_youtube_optimization"),
    ],
)
def test_slugify(raw: str, expected: str):
    assert slugify(raw) == expected
