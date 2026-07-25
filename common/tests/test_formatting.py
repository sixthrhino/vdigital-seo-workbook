from seo_workbook_common.output.formatting import format_item


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
