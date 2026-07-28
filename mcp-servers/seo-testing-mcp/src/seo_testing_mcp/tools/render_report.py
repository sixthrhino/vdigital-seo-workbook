"""
Convert QA agent JSON output to a styled HTML report.

Expected input dict shape:
    {"month": "...", "client": "...", "brand_guide_notes": ["...", ...], "urls": [
        {"url": "...", "verdict": "PASS|FAIL|PARTIAL",
         "opt_note": "...", "checks": [{"label": "...", "status": "pass|fail|warn", "detail": "..."}],
         "key_issues": "...", "recommended_fixes": "..."}
    ]}

brand_guide_notes is optional (omitted or empty when the batch had no
Brand Guide, or none of its guide-level reminders — voice/tone, writing
rules, imaging). Rendered once, at the top of the report, since these are
guide-level and don't vary by page — unlike manual_checklist entries on an
individual url, which do.
"""

import json
import html as h
from datetime import datetime


def _badge(status):
    cfg = {"pass": ("#1a7d3a", "PASS"), "fail": ("#c0392b", "FAIL"),
           "warn": ("#d68910", "WARN"), "info": ("#555", "INFO")}
    color, label = cfg.get(status, ("#888", status.upper()))
    return (f'<span style="background:{color};color:white;padding:2px 8px;'
            f'border-radius:3px;font-size:11px;font-weight:700;">{label}</span>')


def _row_style(status):
    styles = {
        "pass": "background:#eafaf1;color:#1a7d3a;border-left:4px solid #1a7d3a;",
        "fail": "background:#fdecea;color:#c0392b;border-left:4px solid #c0392b;",
        "warn": "background:#fef9e7;color:#b7770d;border-left:4px solid #e6ac00;",
    }
    return styles.get(status, "background:#f9f9f9;")


def render(data: dict) -> str:
    month = data.get("month") or ""
    client = data.get("client") or ""
    urls = data.get("urls") or []
    brand_guide_notes = data.get("brand_guide_notes") or []

    fails = sum(1 for u in urls for c in u.get("checks", []) if c.get("status") == "fail")
    warns = sum(1 for u in urls for c in u.get("checks", []) if c.get("status") == "warn")

    if fails:
        sc, sb = "#c0392b", "#fdecea"
        st = f"{fails} FAIL(s), {warns} warning(s) — needs attention before closing."
    elif warns:
        sc, sb = "#d68910", "#fef9e7"
        st = f"{warns} warning(s) — review before closing."
    else:
        sc, sb = "#1a7d3a", "#eafaf1"
        st = "All automated checks passed."

    brand_guide_section = ""
    if brand_guide_notes:
        items = "".join(
            f'<li style="margin:5px 0;font-size:12px;color:#444;">☐ {h.escape(str(n))}</li>'
            for n in brand_guide_notes
        )
        brand_guide_section = (
            '<div style="margin-bottom:20px;border:1px solid #e0e0e0;border-radius:6px;overflow:hidden;">'
            '<div style="background:#231F20;color:white;padding:10px 14px;font-size:12px;font-weight:600;">Brand Guide</div>'
            '<div style="padding:12px 14px;background:#f5f5f5;">'
            '<div style="font-size:11px;font-weight:700;color:#888;margin-bottom:6px;text-transform:uppercase;letter-spacing:0.5px;">'
            'Applies to every page in this batch — voice/tone, writing rules, imaging</div>'
            f'<ul style="margin:0;padding-left:18px;">{items}</ul></div></div>'
        )

    cards = ""
    for entry in urls:
        url = entry.get("url", "")
        opt_note = entry.get("opt_note", "").strip()
        key_issues = entry.get("key_issues", "").strip()
        rec_fixes = entry.get("recommended_fixes", "").strip()

        rows = "".join(
            f'<tr style="{_row_style(c.get("status",""))}">'
            f'<td style="padding:7px 12px;font-weight:600;border-bottom:1px solid #eee;width:160px;">{h.escape(str(c.get("label","")))}</td>'
            f'<td style="padding:7px 12px;border-bottom:1px solid #eee;width:60px;">{_badge(c.get("status",""))}</td>'
            f'<td style="padding:7px 12px;border-bottom:1px solid #eee;word-break:break-word;">{h.escape(str(c.get("detail","")))}</td>'
            "</tr>"
            for c in entry.get("checks", [])
        )

        manual_checklist = entry.get("manual_checklist") or []

        extras = ""
        if opt_note:
            extras += (
                '<div style="margin:0;padding:8px 14px;background:#f0f4ff;border-top:1px solid #e0e0e0;">'
                '<div style="font-size:11px;font-weight:700;color:#888;margin-bottom:2px;text-transform:uppercase;letter-spacing:0.5px;">Opt Note</div>'
                f'<div style="font-size:12px;color:#444;font-style:italic;">{h.escape(opt_note)}</div></div>'
            )
        if key_issues or rec_fixes:
            extras += '<div style="margin:0;padding:10px 14px;background:#fffbe6;border-top:1px solid #e0e0e0;">'
            if key_issues:
                extras += (
                    '<div style="font-size:11px;font-weight:700;color:#888;margin-bottom:4px;text-transform:uppercase;">Key Issues</div>'
                    f'<div style="font-size:12px;color:#231F20;margin-bottom:8px;white-space:pre-wrap;">{h.escape(key_issues)}</div>'
                )
            if rec_fixes:
                extras += (
                    '<div style="font-size:11px;font-weight:700;color:#888;margin-bottom:4px;text-transform:uppercase;">Recommended Fixes</div>'
                    f'<div style="font-size:12px;color:#231F20;white-space:pre-wrap;">{h.escape(rec_fixes)}</div>'
                )
            extras += "</div>"
        if manual_checklist:
            items = "".join(
                f'<li style="margin:4px 0;font-size:12px;color:#444;">☐ {h.escape(str(q))}</li>'
                for q in manual_checklist
            )
            extras += (
                '<div style="margin:0;padding:10px 14px;background:#f5f5f5;border-top:1px solid #e0e0e0;">'
                '<div style="font-size:11px;font-weight:700;color:#888;margin-bottom:6px;text-transform:uppercase;letter-spacing:0.5px;">Manual Verification Checklist</div>'
                f'<ul style="margin:0;padding-left:18px;">{items}</ul></div>'
            )

        cards += (
            '<div style="margin-bottom:20px;border:1px solid #e0e0e0;border-radius:6px;overflow:hidden;">'
            f'<div style="background:#231F20;color:white;padding:10px 14px;font-size:12px;font-weight:600;word-break:break-all;">{h.escape(url)}</div>'
            '<table style="width:100%;border-collapse:collapse;font-size:12px;">'
            '<thead><tr style="background:#f5f5f5;">'
            '<th style="padding:7px 12px;text-align:left;width:160px;border-bottom:1px solid #ddd;">Check</th>'
            '<th style="padding:7px 12px;text-align:left;width:60px;border-bottom:1px solid #ddd;">Status</th>'
            '<th style="padding:7px 12px;text-align:left;border-bottom:1px solid #ddd;">Detail</th>'
            f'</tr></thead><tbody>{rows}</tbody></table>{extras}</div>'
        )

    run_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>VDS Opts QA — {h.escape(client)} {h.escape(month)}</title>
<style>
  body {{ font-family: 'Helvetica Neue', Arial, sans-serif; color: #231F20;
          max-width: 960px; margin: 0 auto; padding: 28px 20px; }}
  @media print {{ body {{ padding: 12px; }} }}
</style>
</head>
<body>
  <div style="background:#E21B23;padding:18px 24px;border-radius:8px;margin-bottom:24px;">
    <h1 style="color:white;margin:0;font-size:18px;">VDS On-Page Opts QA Report</h1>
    <p style="color:rgba(255,255,255,0.85);margin:4px 0 0;font-size:12px;">
      {h.escape(client) or 'Client not specified'} &nbsp;|&nbsp;
      {h.escape(month) or 'Month not specified'} &nbsp;|&nbsp;
      Run: {run_time}
    </p>
  </div>
  <div style="background:{sb};color:{sc};padding:12px 16px;border-radius:6px;
              font-weight:600;margin-bottom:20px;font-size:13px;">{st}</div>
  {brand_guide_section}
  <h2 style="color:#231F20;margin-top:28px;font-size:16px;">Automated Page Checks</h2>
  {cards}
</body>
</html>"""


