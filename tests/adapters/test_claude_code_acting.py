"""Which MCP tools a test run is never offered (D6 final): those whose own name has a verb that writes or acts, and
those whose name can't be told; tools named for reading are kept."""

from __future__ import annotations

import pytest

from agentlint.adapters.claude_code.acting import ToolVerdict, acting_tools, verdict, verdicts, words

CALENDAR, WIKI = "mcp__plugin_calendar_calendar-tools__", "mcp__plugin_agents-hub_wiki__"


@pytest.mark.parametrize("name", [
    CALENDAR + "calendar_book_beta", WIKI + "wiki_upsert", WIKI + "wiki_delete", CALENDAR + "calendar_accept",
    CALENDAR + "calendar_cancel_or_decline", CALENDAR + "calendar_renew_series", CALENDAR + "calendar_resolve_conflicts",
    CALENDAR + "calendar_update_or_reschedule_meetings", "mcp__p_mltool__kill_training_job",
    "mcp__p_mltool__recurring_flow_toggle", "mcp__p_mltool__revert_and_ban",
    "mcp__p_api__autoscaling_api_service-disable_shadow_mode-thrift", "mcp__p_exp__autoscaling_start_experiment",
    "mcp__p_ccs__ccs_abort_rollout", "mcp__p_powertools__add_diff_comment", "mcp__p_notebook__execute_notebook_cell",
    "mcp__srv__sendMessage", "mcp__srv__post", "mcp__srv__publish_page", "mcp__srv__merge_pull_request",
])
def test_a_tool_whose_name_acts_is_removed(name: str) -> None:
    found = verdict(name)
    assert found is not None and found.removed and found.why.startswith("acts: ")


@pytest.mark.parametrize("name", [
    CALENDAR + "calendar_list_beta", CALENDAR + "calendar_employee_search", WIKI + "wiki_read",
    "mcp__p_analytics__knowledge_load", "mcp__p_exp__autoscaling_status", "mcp__p_figma__figma_get_screenshot",
    "mcp__p_hub__search_files", "mcp__srv__getUser", "mcp__srv__describe_table", "mcp__srv__lookup", "mcp__srv__view",
    "mcp__srv__fetch_url", "mcp__srv__query", "mcp__srv__show_info", "mcp__srv__find", "mcp__p_ccs__ccs_get_rollout_status",
])
def test_a_tool_named_for_reading_is_kept(name: str) -> None:
    found = verdict(name)
    assert found is not None and not found.removed and found.why.startswith("reads: ")


@pytest.mark.parametrize("name", ["mcp__x__frobnicate", CALENDAR + "employee_reports", "mcp__p_capacity__job_size_check_tool",
                                  "mcp__p_acme__google_docs", "mcp__p_assistant__assistant_agent_tool"])
def test_a_tool_whose_name_cant_be_told_is_removed(name: str) -> None:
    found = verdict(name)
    assert found is not None and found.removed and found.why.startswith("can't tell")


def test_an_acting_verb_wins_over_a_reading_one_and_inflections_count() -> None:
    assert verdict("mcp__s__get_and_delete_item").removed
    assert verdict("mcp__s__list_then_update").removed
    assert verdict(CALENDAR + "calendar_scheduling_ai").why == "acts: schedule"
    assert verdict("mcp__s__stopping_jobs").why == "acts: stop"
    assert verdict("mcp__s__booked_rooms").why == "acts: book"


def test_only_the_tools_own_name_counts_and_other_tools_are_not_classified() -> None:
    assert not verdict("mcp__delete-server__list_items").removed  # the server's name is not the tool's verb
    assert verdict("WebFetch") is None and verdict("Bash") is None and verdict("mcp__onlyserver") is None
    assert words("calendar_book-betaVersion2") == ["calendar", "book", "beta", "version2"]


def test_the_list_is_sorted_and_each_tool_once() -> None:
    names = [WIKI + "wiki_upsert", CALENDAR + "calendar_book_beta", WIKI + "wiki_upsert", WIKI + "wiki_read", "Bash"]
    assert acting_tools(names) == (WIKI + "wiki_upsert", CALENDAR + "calendar_book_beta")
    assert [v.name for v in verdicts(names)] == [WIKI + "wiki_read", WIKI + "wiki_upsert", CALENDAR + "calendar_book_beta"]


@pytest.mark.parametrize(("tool", "why"), [("change_status", "acts: change"), ("store_info", "acts: store"),
                                           ("record_status", "acts: record"), ("drain_load", "acts: drain"),
                                           ("run_query", "acts: run"), ("log_event_status", "acts: log"),
                                           ("review_info", "acts: review"), ("runs_search", "acts: run"), ("flush_status", "acts: flush")])
def test_a_read_word_used_as_a_noun_does_not_keep_a_tool_whose_verb_acts(tool: str, why: str) -> None:
    """Verbs that are often nouns count as acting: a read word after them (status, info, load, query) is the thing
    acted on, not the verb."""
    assert verdict("mcp__srv__" + tool) == ToolVerdict("mcp__srv__" + tool, True, why)


def test_a_verdict_is_cores_type_and_the_adapter_says_when_it_runs_inside_a_session() -> None:
    from agentlint.adapters.claude_code.adapter import ClaudeCodeAdapter
    from agentlint.harness import ToolVerdict

    assert verdict("mcp__mail__send_message") == ToolVerdict("mcp__mail__send_message", True, "acts: send")
    adapter = ClaudeCodeAdapter()
    assert adapter.nested({}) is None and adapter.nested({"CLAUDECODE": ""}) is None
    said = adapter.nested({"CLAUDECODE": "1", "HOME": "/x"})
    assert said is not None and said.endswith("start agentlint ui from a plain terminal")
