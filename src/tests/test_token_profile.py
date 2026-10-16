import json

import pytest

from aletheore.token_profile import (
    NON_CORE_TOOLS, ProfileError, apply_deny, build_deny_list, parse_audit, remove_deny, run_audit,
)

SEEN = ["Bash", "Read", "Edit", "Write", "Task", "ToolSearch", "WebFetch", "WebSearch", "CronCreate", "Monitor",
        "mcp__aletheore__aletheore_scan", "BrandNewTool"]


def test_core_denies_only_seen_non_core_tools():
    deny = build_deny_list(SEEN, "core")
    assert deny == ["CronCreate", "Monitor", "WebFetch", "WebSearch"]
    for kept in ("Bash", "Read", "Edit", "Write", "Task", "ToolSearch", "mcp__aletheore__aletheore_scan", "BrandNewTool"):
        assert kept not in deny


def test_core_plus_web_keeps_the_web_tools_and_off_denies_nothing():
    assert build_deny_list(SEEN, "core+web") == ["CronCreate", "Monitor"]
    assert build_deny_list(SEEN, "off") == []


def test_without_a_seen_list_every_known_non_core_tool_is_denied_and_tool_search_never():
    deny = build_deny_list(None, "core")
    assert set(deny) == set(NON_CORE_TOOLS) and "ToolSearch" not in deny


def test_unknown_profile_is_rejected():
    with pytest.raises(ProfileError):
        build_deny_list(SEEN, "turbo")


def test_apply_merges_into_existing_settings_and_remove_restores_them(tmp_path):
    d = tmp_path / ".claude"
    d.mkdir()
    original = {"model": "sonnet", "permissions": {"deny": ["Bash(rm -rf:*)"], "allow": ["Read"]}}
    (d / "settings.json").write_text(json.dumps(original))
    added = apply_deny(tmp_path, ["Monitor", "CronCreate"])
    now = json.loads((d / "settings.json").read_text())
    assert added == ["Monitor", "CronCreate"] and now["model"] == "sonnet"
    assert now["permissions"]["deny"] == ["Bash(rm -rf:*)", "Monitor", "CronCreate"]
    assert remove_deny(tmp_path) == ["CronCreate", "Monitor"]
    assert json.loads((d / "settings.json").read_text()) == original
    assert not (d / "aletheore-tokens.json").exists()


def test_remove_deletes_a_settings_file_that_apply_created_and_leaves_user_entries_alone(tmp_path):
    apply_deny(tmp_path, ["Monitor"])
    assert remove_deny(tmp_path) == ["Monitor"]
    assert not (tmp_path / ".claude" / "settings.json").exists()
    d = tmp_path / ".claude"
    (d / "settings.json").write_text(json.dumps({"permissions": {"deny": ["Monitor"]}}))
    assert apply_deny(tmp_path, ["Monitor"]) == []  # the user already denied it
    assert remove_deny(tmp_path) == []
    assert json.loads((d / "settings.json").read_text())["permissions"]["deny"] == ["Monitor"]


def test_apply_refuses_unreadable_settings_without_changing_anything(tmp_path):
    d = tmp_path / ".claude"
    d.mkdir()
    (d / "settings.json").write_text("{not json")
    with pytest.raises(ProfileError):
        apply_deny(tmp_path, ["Monitor"])
    assert (d / "settings.json").read_text() == "{not json"
    assert not (d / "aletheore-tokens.json").exists()


def test_remove_without_a_previous_apply_does_nothing(tmp_path):
    assert remove_deny(tmp_path) == []


def stream(tools=("Bash", "Read"), usage=None, cost=0.005):
    usage = usage if usage is not None else {"input_tokens": 2, "cache_creation_input_tokens": 24000, "cache_read_input_tokens": 0}
    return "\n".join(json.dumps(e) for e in [
        {"type": "system", "subtype": "init", "tools": list(tools)},
        {"type": "result", "usage": usage, "total_cost_usd": cost},
    ])


def test_parse_audit_reads_tools_and_input_side_tokens():
    a = parse_audit(stream())
    assert a.tools == ["Bash", "Read"] and a.input_side_tokens == 24002 and a.cost_usd == 0.005


def test_parse_audit_fails_clearly_on_an_empty_or_failed_request():
    for bad in ("", "not json", stream(usage={}), stream(tools=())):
        with pytest.raises(ProfileError):
            parse_audit(bad)


def test_run_audit_builds_the_command_and_passes_deny_only_on_the_command_line(tmp_path):
    seen = []

    def fake(cmd, cwd):
        seen.append((cmd, cwd))
        return stream()

    run_audit(tmp_path, runner=fake)
    run_audit(tmp_path, deny=["Monitor"], runner=fake)
    assert "--settings" not in seen[0][0] and "--no-session-persistence" in seen[0][0]
    cmd = seen[1][0]
    assert json.loads(cmd[cmd.index("--settings") + 1]) == {"permissions": {"deny": ["Monitor"]}}
    assert seen[0][1] == tmp_path and not (tmp_path / ".claude").exists()
