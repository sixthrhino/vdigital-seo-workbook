from seo_workbook_common.validators import validate_touchpoint


def test_title_tag_passes_within_limits():
    result = validate_touchpoint(
        "title_tag", [{"new_value": "Auto Insurance in Scottsdale", "primary_keyword": "auto insurance"}]
    )
    assert result.passed


def test_title_tag_fails_over_60_chars():
    result = validate_touchpoint(
        "title_tag", [{"new_value": "x" * 61, "primary_keyword": "auto insurance"}]
    )
    assert not result.passed
    assert any("60" in m for m in result.messages)


def test_title_tag_requires_primary_keyword():
    result = validate_touchpoint("title_tag", [{"new_value": "Short Title"}])
    assert not result.passed
    assert any("primary_keyword" in m for m in result.messages)


def test_title_tag_rejects_more_than_one_item():
    result = validate_touchpoint(
        "title_tag",
        [
            {"new_value": "Title A", "primary_keyword": "a"},
            {"new_value": "Title B", "primary_keyword": "b"},
        ],
    )
    assert not result.passed
    assert any("at most 1" in m for m in result.messages)


def test_meta_description_within_bounds_passes():
    result = validate_touchpoint("meta_description", [{"new_value": "x" * 140, "cta": "Get a Quote"}])
    assert result.passed


def test_meta_description_too_short_fails():
    result = validate_touchpoint("meta_description", [{"new_value": "x" * 50, "cta": "Get a Quote"}])
    assert not result.passed
    assert any("120" in m for m in result.messages)


def test_meta_description_requires_cta():
    result = validate_touchpoint("meta_description", [{"new_value": "x" * 140}])
    assert not result.passed
    assert any("cta" in m for m in result.messages)


def test_h1_tag_over_70_chars_fails():
    result = validate_touchpoint("h1_tag", [{"new_value": "x" * 71, "primary_keyword": "insurance"}])
    assert not result.passed


def test_image_alt_text_rejects_single_word():
    result = validate_touchpoint("image_alt_text", [{"new_value": "dog"}])
    assert not result.passed


def test_image_alt_text_accepts_multiple_images_independently():
    result = validate_touchpoint(
        "image_alt_text",
        [
            {"new_value": "Golden Retriever puppy playing with a red ball"},
            {"new_value": "dog"},
        ],
    )
    assert not result.passed
    assert any(m.startswith("item 2:") for m in result.messages)


def test_canonical_tags_requires_full_url():
    result = validate_touchpoint("canonical_tags", [{"new_value": "/blog/"}])
    assert not result.passed


def test_canonical_tags_accepts_full_url():
    result = validate_touchpoint("canonical_tags", [{"new_value": "https://kyz.com/blog/"}])
    assert result.passed


def test_heading_changes_validate_each_item_independently():
    result = validate_touchpoint(
        "h2_h3_h4_tags",
        [
            {"old_tag": "h4", "new_tag": "h3", "heading_text": "Common Career Paths"},
            {"old_tag": "h4", "new_tag": "h3", "heading_text": "How to use your GI benefits"},
            {"old_tag": "bogus", "new_tag": "h3", "heading_text": "Missing old tag validity"},
        ],
    )
    assert not result.passed
    assert any(m.startswith("item 3:") for m in result.messages)
    assert not any(m.startswith("item 1:") or m.startswith("item 2:") for m in result.messages)


def test_heading_change_requires_heading_text():
    result = validate_touchpoint("h2_h3_h4_tags", [{"old_tag": "h4", "new_tag": "h3"}])
    assert not result.passed
    assert any("heading_text" in m for m in result.messages)


def test_internal_link_items_validated_independently():
    result = validate_touchpoint(
        "internal_linking_to_other_pages_homepage",
        [
            {"anchor_text": "our FAQs", "target_url": "https://iecrm.org/faqs/"},
            {"anchor_text": "", "target_url": "https://iecrm.org/continued-education/"},
        ],
    )
    assert not result.passed
    assert any(m.startswith("item 2:") and "anchor_text" in m for m in result.messages)


def test_internal_link_requires_target_url():
    result = validate_touchpoint("internal_linking_to_target_page", [{"anchor_text": "learn more"}])
    assert not result.passed
    assert any("target_url" in m for m in result.messages)


def test_touchpoint_requires_at_least_one_item():
    result = validate_touchpoint("h2_h3_h4_tags", [])
    assert not result.passed
    assert any("at least 1" in m for m in result.messages)


def test_unknown_touchpoint_uses_default_validator():
    empty = validate_touchpoint("geo_keywords", [{}])
    assert not empty.passed

    filled = validate_touchpoint("geo_keywords", [{"new_value": "Scottsdale, AZ"}])
    assert filled.passed
