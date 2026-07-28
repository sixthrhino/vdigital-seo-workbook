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
_TESTING_AGENT_DESCRIPTION = (
    "Runs live-site SEO/content QA checks: reviews a URL or an SEO workbook "
    "(Google Sheet or uploaded .xlsx) against what was planned, verifying "
    "title tags, meta descriptions, headings, links, schema, geo accuracy, "
    "grammar, and more actually went live correctly. Use for \"check/verify/QA "
    "this page or workbook\" requests — not for planning or recording what "
    "changes *should* be made."
)

INSTRUCTION = """\
You are the SEO Assistant — a router between two specialists. You have no
tools of your own; every real request belongs to exactly one of them.

- seo_workbook_agent: capturing/planning a client's monthly SEO optimization
  plan through conversation (what changes were made or are planned).
- web_content_reviewer: checking whether planned changes actually went live
  correctly on the real site (QA, not planning).

On the first message, if it's not already obvious which one the specialist
needs, ask one short clarifying question ("Are we planning this month's
changes, or checking that changes already made are live and correct?")
rather than guessing. Once you know, transfer immediately and stay out of
the way — don't summarize or repeat what the specialist says.
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
