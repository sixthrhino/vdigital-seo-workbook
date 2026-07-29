from __future__ import annotations

from google.adk.agents import LlmAgent

from seo_testing_agent.agent import create_agent as create_testing_agent
from seo_workbook_agent.adk_agent import build_agent as build_workbook_agent
from seo_workbook_agent.config import get_agent_settings

# seo_testing_agent's create_agent() doesn't set a description (it was never
# meant to be used as anything but a standalone top-level agent) — ADK's
# transfer_to_agent routing relies on each sub-agent's description to know
# when to hand off, so one is set here instead of touching that package,
# which stays exactly as it works standalone.
#
# Deliberately avoids the word "workbook" — a real routing bug was traced to
# this description's old wording ("reviews ... an SEO workbook (Google Sheet
# or uploaded .xlsx)"), which is also stale now that Mode B reads a client's
# already-recorded plan (by client/month) rather than a Sheet or upload —
# see plan_session_source.py. Its overlap with seo_workbook_agent's own
# planning language caused plan-summary requests ("find the session and
# summarize it") to mis-route here instead.
_TESTING_AGENT_DESCRIPTION = (
    "Runs live-site SEO/content QA checks: verifies a URL, or a client's "
    "already-recorded monthly plan (by client name and month), actually "
    "went live correctly — title tags, meta descriptions, headings, links, "
    "schema, geo accuracy, grammar, and more. Use for \"check/verify/QA this "
    "page or plan against the live site\" requests. Never use this for "
    "recording, drafting, summarizing, or changing what should be done —"
    " even if the request names a specific client/month plan, that belongs "
    "to seo_workbook_agent unless the specialist explicitly wants it checked "
    "against what's actually live."
)

INSTRUCTION = """\
You are the SEO Assistant — a router between two specialists. You have no
tools of your own; every real request belongs to exactly one of them.

- seo_workbook_agent: everything about a client's recorded plan itself —
  capturing new optimizations through conversation, resuming/summarizing an
  existing plan, or IMPORTING a legacy workbook (a shared Google Sheet/
  spreadsheet link) into the system as plan history. Any request to
  "import," "load," or "add" a workbook/spreadsheet into the system is
  always this specialist — transfer immediately, never ask the
  planning-vs-checking clarifying question below for these, since importing
  historical data is neither "planning new changes" nor "checking what's
  live" and that question has no good answer for it.
- web_content_reviewer: verifying that a recorded plan (or a bare URL)
  actually went live correctly on the real site — QA against reality, not
  managing the plan's data.

On the first message, if it's a genuinely new/ongoing request and it's not
already obvious which one the specialist needs, ask one short clarifying
question ("Are we recording/reviewing this month's plan, or checking that
it's actually live and correct on the site?") rather than guessing. Once you
know, transfer immediately and stay out of the way — don't summarize or
repeat what the specialist says.
"""


def build_orchestrator() -> LlmAgent:
    workbook_agent = build_workbook_agent(get_agent_settings())
    testing_agent = create_testing_agent()
    testing_agent.description = _TESTING_AGENT_DESCRIPTION

    return LlmAgent(
        name="seo_assistant",
        model=get_agent_settings().agent_model,
        description="Routes SEO planning requests and live-site QA requests to the right specialist.",
        instruction=INSTRUCTION,
        sub_agents=[workbook_agent, testing_agent],
    )
