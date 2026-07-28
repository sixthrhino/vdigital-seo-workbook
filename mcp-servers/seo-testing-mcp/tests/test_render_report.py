"""
Tests for render_report.render — the HTML report generator.
"""

from seo_testing_mcp.tools.render_report import render


def minimal_report(**kwargs):
    base = {"month": "June 2025", "client": "Acme", "urls": []}
    base.update(kwargs)
    return base


def url_entry(**kwargs):
    base = {
        "url": "https://example.com/",
        "verdict": "PASS",
        "opt_note": "",
        "checks": [],
        "key_issues": "",
        "recommended_fixes": "",
    }
    base.update(kwargs)
    return base


class TestBasicStructure:
    def test_returns_valid_html_string(self):
        html = render(minimal_report())
        assert html.startswith("<!DOCTYPE html>")
        assert "</html>" in html

    def test_client_name_in_output(self):
        html = render(minimal_report(client="Jones & Sons"))
        assert "Jones &amp; Sons" in html

    def test_month_in_output(self):
        html = render(minimal_report(month="October 2025"))
        assert "October 2025" in html

    def test_all_checks_passed_banner(self):
        entry = url_entry(checks=[{"label": "Title Tag", "status": "pass", "detail": "OK"}])
        html = render(minimal_report(urls=[entry]))
        assert "All automated checks passed" in html

    def test_fail_banner_when_any_check_fails(self):
        entry = url_entry(checks=[{"label": "Title Tag", "status": "fail", "detail": "Missing"}])
        html = render(minimal_report(urls=[entry]))
        assert "FAIL" in html
        assert "needs attention" in html

    def test_warn_banner_when_only_warnings(self):
        entry = url_entry(checks=[{"label": "Meta", "status": "warn", "detail": "Short"}])
        html = render(minimal_report(urls=[entry]))
        assert "warning(s)" in html


class TestCheckRows:
    def test_pass_check_rendered(self):
        entry = url_entry(checks=[{"label": "H1 Tag", "status": "pass", "detail": "Found"}])
        html = render(minimal_report(urls=[entry]))
        assert "H1 Tag" in html
        assert "PASS" in html

    def test_fail_check_rendered(self):
        entry = url_entry(checks=[{"label": "Canonical", "status": "fail", "detail": "Mismatch"}])
        html = render(minimal_report(urls=[entry]))
        assert "Canonical" in html
        assert "Mismatch" in html

    def test_xss_in_check_detail_escaped(self):
        entry = url_entry(checks=[{"label": "Title", "status": "pass", "detail": '<script>alert(1)</script>'}])
        html = render(minimal_report(urls=[entry]))
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_xss_in_url_escaped(self):
        entry = url_entry(url='https://example.com/<evil>')
        html = render(minimal_report(urls=[entry]))
        assert "<evil>" not in html


class TestOptNote:
    def test_opt_note_rendered_when_present(self):
        entry = url_entry(opt_note="Core Opt: Title + H1")
        html = render(minimal_report(urls=[entry]))
        assert "Core Opt: Title + H1" in html
        assert "Opt Note" in html

    def test_opt_note_section_absent_when_empty(self):
        entry = url_entry(opt_note="")
        html = render(minimal_report(urls=[entry]))
        assert "Opt Note" not in html


class TestKeyIssues:
    def test_key_issues_rendered(self):
        entry = url_entry(key_issues="Title too long", recommended_fixes="Shorten to <60 chars")
        html = render(minimal_report(urls=[entry]))
        assert "Title too long" in html
        assert "Shorten to" in html

    def test_key_issues_absent_when_empty(self):
        entry = url_entry(key_issues="", recommended_fixes="")
        html = render(minimal_report(urls=[entry]))
        assert "Key Issues" not in html


class TestManualChecklist:
    def test_checklist_items_rendered(self):
        entry = url_entry(manual_checklist=[
            "Primary keyword is in the title",
            "Brand name appended at end",
        ])
        html = render(minimal_report(urls=[entry]))
        assert "Manual Verification Checklist" in html
        assert "Primary keyword is in the title" in html
        assert "Brand name appended at end" in html

    def test_checklist_uses_checkbox_symbol(self):
        entry = url_entry(manual_checklist=["Check this"])
        html = render(minimal_report(urls=[entry]))
        assert "☐" in html

    def test_checklist_absent_when_empty_list(self):
        entry = url_entry(manual_checklist=[])
        html = render(minimal_report(urls=[entry]))
        assert "Manual Verification Checklist" not in html

    def test_checklist_absent_when_key_missing(self):
        entry = url_entry()
        entry.pop("manual_checklist", None)
        html = render(minimal_report(urls=[entry]))
        assert "Manual Verification Checklist" not in html

    def test_checklist_xss_escaped(self):
        entry = url_entry(manual_checklist=['<b>bold</b>'])
        html = render(minimal_report(urls=[entry]))
        assert "<b>" not in html
        assert "&lt;b&gt;" in html

    def test_multiple_urls_each_get_checklist(self):
        entries = [
            url_entry(url="https://a.com/", manual_checklist=["Check A"]),
            url_entry(url="https://b.com/", manual_checklist=["Check B"]),
        ]
        html = render(minimal_report(urls=entries))
        assert "Check A" in html
        assert "Check B" in html


class TestBrandGuideSection:
    def test_rendered_when_notes_present(self):
        html = render(minimal_report(brand_guide_notes=["Tone should be friendly and professional"]))
        assert "Brand Guide" in html
        assert "Tone should be friendly and professional" in html

    def test_absent_when_no_notes(self):
        html = render(minimal_report(brand_guide_notes=[]))
        assert "Brand Guide</div>" not in html

    def test_absent_when_key_missing(self):
        data = minimal_report()
        data.pop("brand_guide_notes", None)
        html = render(data)
        assert "Brand Guide</div>" not in html

    def test_appears_before_per_url_cards_not_inside_the_first_one(self):
        # The actual bug being fixed: these used to be tucked into the
        # first row's own manual checklist instead of standing on their
        # own — assert the section shows up before "Automated Page Checks"
        # (where the per-URL cards start), not somewhere inside one.
        entries = [url_entry(url="https://a.com/"), url_entry(url="https://b.com/")]
        html = render(minimal_report(
            urls=entries, brand_guide_notes=["Writing Rule: No DIY content"],
        ))
        notes_pos = html.index("Writing Rule: No DIY content")
        cards_heading_pos = html.index("Automated Page Checks")
        assert notes_pos < cards_heading_pos

    def test_multiple_notes_all_rendered(self):
        html = render(minimal_report(brand_guide_notes=[
            "Tone should be friendly and professional",
            "Writing Rule: No DIY content",
            "Imaging: Stock photos okay",
        ]))
        assert "Tone should be friendly and professional" in html
        assert "Writing Rule: No DIY content" in html
        assert "Imaging: Stock photos okay" in html

    def test_xss_escaped(self):
        html = render(minimal_report(brand_guide_notes=["<script>alert(1)</script>"]))
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


class TestEmptyReport:
    def test_empty_urls_renders_without_error(self):
        html = render(minimal_report(urls=[]))
        assert "<!DOCTYPE html>" in html

    def test_missing_client_falls_back_gracefully(self):
        html = render({"month": "June 2025", "urls": []})
        assert "Client not specified" in html
