"""
Tests for resolve_checks_for_opt_note — the opt_note → auto_checks dispatch.
"""

import pytest
from seo_testing_mcp.app import resolve_checks_for_opt_note


def checks(opt_note):
    return resolve_checks_for_opt_note(opt_note)["auto_checks"]


def matched(opt_note):
    return resolve_checks_for_opt_note(opt_note)["matched_optimizations"]


def questions(opt_note):
    return resolve_checks_for_opt_note(opt_note)["guided_questions"]


# ---------------------------------------------------------------------------
# Shorthands
# ---------------------------------------------------------------------------

class TestCoreOpt:
    def test_triggers_title_meta_h1_headings(self):
        result = checks("Core Opt: Title Tag + Meta Description + H1")
        assert "seo_check_title" in result
        assert "seo_check_meta_description" in result
        assert "seo_check_h1" in result
        assert "seo_check_headings" in result

    def test_case_insensitive(self):
        assert "seo_check_title" in checks("core opt")

    def test_no_schema_for_core_opt(self):
        assert "seo_check_schema" not in checks("core opt")


class TestDeepOpt:
    def test_triggers_schema_and_links(self):
        result = checks("Deep Opt")
        assert "seo_check_schema" in result
        assert "elements_check_links" in result

    def test_includes_core_opt_checks(self):
        result = checks("Deep Opt")
        assert "seo_check_title" in result
        assert "seo_check_h1" in result

    def test_deep_touch_alias(self):
        result = checks("Deep Touch optimization")
        assert "seo_check_schema" in result

    def test_llm_alias(self):
        result = checks("LLM optimization pass")
        assert "seo_check_schema" in result

    def test_multi_platform_alias(self):
        result = checks("multi-platform content update")
        assert "seo_check_schema" in result


# ---------------------------------------------------------------------------
# Keyword aliases
# ---------------------------------------------------------------------------

class TestSchemaAlias:
    def test_schema_word_triggers_schema_check(self):
        assert "seo_check_schema" in checks("Added FAQ schema markup")

    def test_schema_markup_opt_name_also_works(self):
        assert "seo_check_schema" in checks("Schema Markup update")


class TestSitemapAlias:
    def test_sitemap_triggers_xml_and_html_sitemap(self):
        result = checks("Updated sitemap")
        assert "tech_check_xml_sitemap" in result
        assert "tech_check_html_sitemap_footer" in result

    def test_xml_sitemaps_opt_name_also_works(self):
        result = checks("XML Sitemaps submission")
        assert "tech_check_xml_sitemap" in result
        assert "tech_check_html_sitemap_footer" in result


class TestH1Alias:
    def test_h1_alone_triggers_h1_check(self):
        assert "seo_check_h1" in checks("Updated H1 heading")

    def test_h2_alias(self):
        assert "seo_check_headings" in checks("Added new H2 subheadings")

    def test_h3_alias(self):
        assert "seo_check_headings" in checks("H3 structure updated")


class TestAltTextAlias:
    def test_alt_text_triggers_image_check(self):
        assert "elements_check_images" in checks("Fixed missing alt text")

    def test_alt_tag_alias(self):
        assert "elements_check_images" in checks("alt tag optimization")

    def test_missing_alt_alias(self):
        assert "elements_check_images" in checks("missing alt attributes fixed")


class TestGrammarAlias:
    def test_grammar_triggers_grammar_check(self):
        assert "content_check_grammar" in checks("Grammar pass on this page")

    def test_proofread_alias(self):
        assert "content_check_grammar" in checks("Proofread content for typos")

    def test_realistic_shorthand_note_does_not_trigger_grammar(self):
        # regression guard for the exact opt_note shape a real workbook used —
        # confirms shorthand notes without "grammar"/"proofread" correctly
        # stay out of scope rather than silently matching something broader
        note = "Core Optimiations: H1 + Add 50-100 words of content, Links"
        assert "content_check_grammar" not in checks(note)


class TestPhoneCtaAlias:
    def test_phone_triggers_links_check(self):
        assert "elements_check_links" in checks("Updated phone number CTA")

    def test_cta_alone_triggers_links_check(self):
        assert "elements_check_links" in checks("CTA optimization")

    def test_call_to_action_alias(self):
        assert "elements_check_links" in checks("call to action update")

    def test_action_link_alias(self):
        assert "elements_check_links" in checks("action link added")


# ---------------------------------------------------------------------------
# Optimization name substring matching
# ---------------------------------------------------------------------------

class TestSubstringMatching:
    def test_canonical_tags_name_matches(self):
        assert "seo_check_canonical" in checks("Canonical Tags reviewed")

    def test_robots_txt_name_matches(self):
        assert "tech_check_robots_txt" in checks("Robots.txt Check completed")

    def test_google_maps_name_matches(self):
        assert "elements_check_google_maps" in checks("Google Maps Embedding added")

    def test_youtube_name_matches(self):
        assert "elements_check_youtube" in checks("YouTube Video Embedding")

    def test_toc_name_matches(self):
        assert "elements_check_toc" in checks("TOC (Table of Contents) updated")

    def test_faq_name_matches(self):
        assert "elements_check_faq" in checks("Section Design & FAQ Content")

    def test_grammar_syntax_polish_name_matches(self):
        assert "content_check_grammar" in checks("Grammar, Syntax & Polish reviewed")


# ---------------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------------

class TestFallback:
    def test_unrecognized_note_returns_core_checks(self):
        result = checks("General page update and cleanup")
        assert "seo_check_title" in result
        assert "seo_check_meta_description" in result
        assert "seo_check_h1" in result

    def test_empty_string_fallback(self):
        result = checks("")
        assert len(result) > 0


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

class TestDeduplication:
    def test_no_duplicate_checks(self):
        result = checks("Core Opt: Title Tag + Meta Description + H1 Tag + Schema")
        assert len(result) == len(set(result))

    def test_no_duplicate_guided_questions(self):
        result = questions("Core Opt + Schema Markup")
        assert len(result) == len(set(result))


# ---------------------------------------------------------------------------
# guided_questions present for manual-only optimizations
# ---------------------------------------------------------------------------

class TestGuidedQuestions:
    def test_brand_integrity_has_questions(self):
        result = questions("Brand Integrity review")
        assert len(result) > 0
        assert any("Brand Guide" in q for q in result)

    def test_schema_has_guided_questions(self):
        result = questions("Schema Markup added")
        assert any("Rich Results" in q or "schema" in q.lower() for q in result)
