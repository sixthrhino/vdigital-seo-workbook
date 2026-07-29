from seo_workbook_common.output.formatting import format_item, format_month, format_old_new


def test_title_tag_shows_new_and_old_and_keyword():
    result = format_item(
        {"new_value": "New Title", "old_value": "Old Title", "primary_keyword": "insurance"}, "title_tag"
    )
    assert "New: New Title" in result
    assert "(was: Old Title)" in result
    assert "Keyword: insurance" in result


def test_meta_description_shows_cta():
    result = format_item({"new_value": "Great meta", "cta": "Get a Quote"}, "meta_description")
    assert "CTA: Get a Quote" in result


def test_heading_change_shows_arrow():
    result = format_item({"old_tag": "h4", "new_tag": "h3", "heading_text": "Common Career Paths"}, "h2_h3_h4_tags")
    assert result == "H4 → H3: Common Career Paths"


def test_internal_link_shows_anchor_and_target():
    result = format_item(
        {"anchor_text": "our FAQs", "target_url": "https://iecrm.org/faqs/"},
        "internal_linking_to_other_pages_homepage",
    )
    assert result == '"our FAQs" → https://iecrm.org/faqs/'


def test_canonical_tag_format():
    result = format_item({"new_value": "https://kyz.com/blog/"}, "canonical_tags")
    assert result == "Canonical → https://kyz.com/blog/"


def test_image_alt_text_format():
    result = format_item({"new_value": "Golden Retriever puppy", "old_value": "dog"}, "image_alt_text")
    assert "Alt text: Golden Retriever puppy" in result
    assert "(was: dog)" in result


def test_unknown_touchpoint_falls_back_to_key_value_join():
    result = format_item({"b": "2", "a": "1"}, "geo_keywords")
    assert result == "a: 1; b: 2"


def test_format_month_renders_full_month_name_and_year():
    assert format_month("2026-06") == "June 2026"


def test_format_month_falls_back_to_raw_value_on_bad_input():
    assert format_month("not-a-month") == "not-a-month"


def test_format_old_new_splits_title_tag_into_old_and_new():
    result = format_old_new({"new_value": "New Title", "old_value": "Old Title"}, "title_tag")
    assert result == ("Old Title", "New Title")


def test_format_old_new_title_tag_with_no_old_value_shows_placeholder():
    result = format_old_new({"new_value": "New Title"}, "title_tag")
    assert result == ("—", "New Title")


def test_format_old_new_meta_description():
    result = format_old_new({"new_value": "New meta", "old_value": "Old meta"}, "meta_description")
    assert result == ("Old meta", "New meta")


def test_format_old_new_h1_tag():
    result = format_old_new({"new_value": "New H1", "old_value": "Old H1"}, "h1_tag")
    assert result == ("Old H1", "New H1")


def test_format_old_new_image_alt_text():
    result = format_old_new({"new_value": "Golden Retriever puppy", "old_value": "dog"}, "image_alt_text")
    assert result == ("dog", "Golden Retriever puppy")


def test_format_old_new_heading_change_shows_same_text_at_both_levels():
    result = format_old_new(
        {"old_tag": "h4", "new_tag": "h3", "heading_text": "Common Career Paths"}, "h2_h3_h4_tags"
    )
    assert result == ("<H4> Common Career Paths", "<H3> Common Career Paths")


def test_format_old_new_returns_none_for_internal_links():
    result = format_old_new(
        {"anchor_text": "our FAQs", "target_url": "https://iecrm.org/faqs/"},
        "internal_linking_to_other_pages_homepage",
    )
    assert result is None


def test_format_old_new_returns_none_for_canonical_tags():
    assert format_old_new({"new_value": "https://kyz.com/blog/"}, "canonical_tags") is None


def test_format_old_new_returns_none_for_unknown_touchpoint():
    assert format_old_new({"a": "1"}, "geo_keywords") is None
