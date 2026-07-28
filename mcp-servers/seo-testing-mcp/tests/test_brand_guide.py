"""Tests for mcp-server/tools/brand_guide.py"""

import httpx
import pytest
import respx

from seo_testing_mcp.tools.brand_guide import check_brand_guide, parse_brand_guide, brand_guide_manual_notes
import seo_testing_mcp.tools.brand_guide as brand_guide_module
from conftest import make_html

URL = "https://example.com/article"


def _mock(html, url=URL, status=200):
    respx.get(url).mock(return_value=httpx.Response(status, text=html))


# ---------------------------------------------------------------------------
# parse_brand_guide — pure Python, no HTTP
# ---------------------------------------------------------------------------

class TestParseBrandGuide:
    def test_returns_dict_with_expected_keys(self):
        result = parse_brand_guide("Branding\tAcme Corp")
        assert isinstance(result, dict)
        for key in ("branding", "cta_urls", "cta_phones", "voice_tone", "writing_rules", "geo", "excluded_cities"):
            assert key in result

    def test_parses_branding_section(self):
        guide = "Branding\tAcme Corp — premium outdoor gear"
        result = parse_brand_guide(guide)
        assert any("Acme Corp" in item for item in result.get("branding", []))

    def test_parses_excluded_cities(self):
        guide = "Chicago & Dallas → client cannot service those areas"
        result = parse_brand_guide(guide)
        assert len(result["excluded_cities"]) > 0

    def test_empty_returns_empty_structure(self):
        result = parse_brand_guide("")
        assert isinstance(result, dict)
        assert result["branding"] == []
        assert result["excluded_cities"] == []

    def test_parses_geo(self):
        guide = "Geo Targeting\tPhoenix, AZ"
        result = parse_brand_guide(guide)
        assert result["geo"] == ["Phoenix, AZ"]

    def test_parses_multiple_geo_targeting_lines(self):
        # Real-world shape: a Geo Targeting cell copy-pasted from Sheets with
        # one city per line keeps its trailing-tab column padding on every
        # line but the first — all of them should still be captured, not
        # just the first line.
        guide = "Geo Targeting\tTempe, AZ\t\nQueen Creek, AZ\t\t\nGilbert, AZ\t"
        result = parse_brand_guide(guide)
        assert result["geo"] == ["Tempe, AZ", "Queen Creek, AZ", "Gilbert, AZ"]

    def test_geo_lines_deduplicated(self):
        guide = "Geo Targeting\tTempe, AZ\t\nTempe, AZ\t"
        result = parse_brand_guide(guide)
        assert result["geo"] == ["Tempe, AZ"]

    def test_parses_negative_words_split_on_comma(self):
        guide = "Negative Words\tToxic products, low toxicity"
        result = parse_brand_guide(guide)
        assert result["negative_words"] == ["Toxic products", "low toxicity"]

    def test_words_to_avoid_label_maps_to_negative_words(self):
        guide = "Words to Avoid\tguarantee"
        result = parse_brand_guide(guide)
        assert "guarantee" in result["negative_words"]

    def test_long_negative_words_note_routed_to_writing_rules(self):
        note = "Client prefers to have both states mentioned somewhere in the body copy"
        guide = f"Negative Words\t{note}"
        result = parse_brand_guide(guide)
        assert result["negative_words"] == []
        assert note in result["writing_rules"]

    def test_negative_words_deduplicated(self):
        guide = "Negative Words\ttoxic, toxic"
        result = parse_brand_guide(guide)
        assert result["negative_words"] == ["toxic"]


# ---------------------------------------------------------------------------
# check_brand_guide
# ---------------------------------------------------------------------------

class TestCheckBrandGuide:
    @respx.mock
    async def test_cta_url_matches_despite_scheme_www_and_trailing_slash(self):
        html = make_html(body=(
            '<article><a href="http://www.example.com/contact/">Contact</a></article>'
        ))
        _mock(html)
        guide = "CTA\thttps://example.com/contact"
        results = await check_brand_guide(URL, parse_brand_guide(guide))
        cta = [r for r in results if r["label"] == "CTA URL"]
        assert cta and cta[0]["status"] == "pass"

    async def test_cta_url_no_soup_fallback_normalizes(self, monkeypatch):
        from seo_testing_mcp.tools.fetcher import FetchResult

        async def fake_fetch(url):
            return FetchResult(
                url=url, status_code=200, soup=None,
                body_text="Visit www.example.com/contact for a quote",
            )

        monkeypatch.setattr(brand_guide_module, "fetch_parsed", fake_fetch)
        results = await check_brand_guide(URL, parse_brand_guide("CTA\thttps://example.com/contact"))
        cta = [r for r in results if r["label"] == "CTA URL"]
        assert cta and cta[0]["status"] == "pass"

    @respx.mock
    async def test_negative_word_warns_when_found(self):
        _mock(make_html(body="<article><p>Our product is completely toxic free.</p></article>"))
        guide = "Negative Words\ttoxic"
        results = await check_brand_guide(URL, parse_brand_guide(guide))
        neg = [r for r in results if r["label"] == 'Negative Word: "toxic"']
        assert neg and neg[0]["status"] == "warn"

    @respx.mock
    async def test_negative_word_passes_when_not_found(self):
        _mock(make_html(body="<article><p>Our product is completely safe.</p></article>"))
        guide = "Negative Words\ttoxic"
        results = await check_brand_guide(URL, parse_brand_guide(guide))
        neg = [r for r in results if r["label"] == 'Negative Word: "toxic"']
        assert neg and neg[0]["status"] == "pass"

    @respx.mock
    async def test_negative_word_hyphen_space_equivalence(self):
        _mock(make_html(body="<article><p>This is a low toxicity formula.</p></article>"))
        guide = "Negative Words\tlow-toxicity"
        results = await check_brand_guide(URL, parse_brand_guide(guide))
        neg = [r for r in results if r["label"] == 'Negative Word: "low-toxicity"']
        assert neg and neg[0]["status"] == "warn"

    @respx.mock
    async def test_guide_level_reminders_are_not_repeated_per_page(self):
        # Voice & Tone / Writing Rules / Imaging don't vary by URL — repeating
        # them on every row's checks is what bloated generate_report's
        # function-call payload enough to trigger a real MALFORMED_FUNCTION_CALL
        # on a live batch. They belong in brand_guide_manual_notes instead,
        # called once per batch, not in check_brand_guide's per-row output.
        _mock(make_html(body="<article><p>Some content.</p></article>"))
        guide_text = (
            "Voice & Tone\tFriendly and professional\n"
            "Writing Rules\tNo DIY content\n"
            "Imaging\tStock photos okay"
        )
        results = await check_brand_guide(URL, parse_brand_guide(guide_text))
        labels = {r["label"] for r in results}
        assert "Voice & Tone" not in labels
        assert "Writing Rule" not in labels
        assert "Imaging" not in labels


class TestBrandGuideManualNotes:
    def test_includes_voice_tone_writing_rules_and_imaging(self):
        guide = parse_brand_guide(
            "Voice & Tone\tFriendly and professional\n"
            "Writing Rules\tNo DIY content\n"
            "Imaging\tStock photos okay"
        )
        notes = brand_guide_manual_notes(guide)
        assert any("Friendly and professional" in n for n in notes)
        assert any("No DIY content" in n for n in notes)
        assert any("Stock photos okay" in n for n in notes)

    def test_multiple_writing_rules_and_imaging_notes_all_included(self):
        guide = parse_brand_guide(
            "Writing Rules\tRule one\nRule two\n"
            "Imaging\tNote one\nNote two"
        )
        notes = brand_guide_manual_notes(guide)
        assert sum("Rule one" in n or "Rule two" in n for n in notes) == 2
        assert sum("Note one" in n or "Note two" in n for n in notes) == 2

    def test_empty_guide_returns_no_notes(self):
        guide = parse_brand_guide("")
        assert brand_guide_manual_notes(guide) == []

    def test_guide_with_only_branding_returns_no_notes(self):
        guide = parse_brand_guide("Branding\tAcme Corp")
        assert brand_guide_manual_notes(guide) == []
