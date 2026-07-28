from seo_assistant.orchestrator_agent import build_orchestrator


def test_build_orchestrator_has_expected_identity():
    orchestrator = build_orchestrator()
    assert orchestrator.name == "seo_assistant"
    assert orchestrator.description


def test_build_orchestrator_has_both_specialists_as_sub_agents():
    orchestrator = build_orchestrator()
    names = {agent.name for agent in orchestrator.sub_agents}
    assert names == {"seo_workbook_agent", "web_content_reviewer"}


def test_every_sub_agent_has_a_description_for_transfer_routing():
    # ADK's transfer_to_agent routing relies on each sub-agent's description
    # to decide who to hand off to — a blank one would make that specialist
    # effectively unreachable.
    orchestrator = build_orchestrator()
    for agent in orchestrator.sub_agents:
        assert agent.description.strip(), f"{agent.name} has no description"


def test_sub_agents_have_parent_agent_set_to_the_orchestrator():
    orchestrator = build_orchestrator()
    for agent in orchestrator.sub_agents:
        assert agent.parent_agent is orchestrator


def test_testing_agent_description_is_distinguishable_from_workbook_agent():
    # Rough guard against the two descriptions being too similar to route
    # between reliably — not a precise check, just a sanity floor.
    orchestrator = build_orchestrator()
    descriptions = {agent.name: agent.description for agent in orchestrator.sub_agents}
    assert "QA" in descriptions["web_content_reviewer"] or "check" in descriptions["web_content_reviewer"].lower()
    assert "plan" in descriptions["seo_workbook_agent"].lower()
