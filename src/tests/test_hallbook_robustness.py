"""Hallbook custom-skill robustness: errors without tracebacks, names, backups, offline sync, info, small CLI gaps."""

import json
import logging
import os

import httpx
import pytest
from typer.testing import CliRunner

from aletheore import skill_hub, skill_hub_sync
from aletheore.cli import app
from aletheore.mcp_server import build_server

from .test_mcp_server import tool_result_body

runner = CliRunner()
NO_TRACEBACK = "Traceback"
AWS_KEY = "AKIAQ3F7ZP2WLM9XD5RT"
GH_TOKEN = "ghp_aB3dE6gH9jK2mN5pQ8sT1vW4yZ7cF0hJ3lO6"


@pytest.fixture
def skills_dir(tmp_path, monkeypatch):
    directory = tmp_path / "home" / "custom_skills"
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: directory)
    monkeypatch.setattr(skill_hub_sync, "_personal_token", lambda: None)
    return directory


def _add(name, file):
    return runner.invoke(app, ["hallbook", "add", name, str(file)])


def _assert_clean_error(result, *words):
    assert result.exit_code == 1, result.output
    assert NO_TRACEBACK not in result.output
    assert result.output.strip().startswith("error:"), result.output
    assert len(result.output.strip().splitlines()) == 1
    for word in words:
        assert word in result.output


# --- 1. custom-skill commands never print a traceback ---------------------------------------------------------------


def test_add_missing_file(skills_dir, tmp_path):
    _assert_clean_error(_add("x", tmp_path / "nope.md"), "no such file")


def test_add_a_directory(skills_dir, tmp_path):
    _assert_clean_error(_add("x", tmp_path), "directory")


def test_add_binary_and_latin1_files(skills_dir, tmp_path):
    binary = tmp_path / "bin.md"
    binary.write_bytes(b"\xff\xfe\x00\x01")
    _assert_clean_error(_add("x", binary), "NUL")
    latin = tmp_path / "latin.md"
    latin.write_bytes("caf\xe9 au lait".encode("latin-1"))
    _assert_clean_error(_add("x", latin), "UTF-8")


def test_add_a_300_character_name(skills_dir, tmp_path):
    source = tmp_path / "s.md"
    source.write_text("steps", encoding="utf-8")
    _assert_clean_error(_add("a" * 300, source), "64 characters")


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="needs POSIX permissions and a non-root user")
def test_read_only_folder_for_add_and_remove(skills_dir, tmp_path):
    source = tmp_path / "s.md"
    source.write_text("steps", encoding="utf-8")
    assert _add("keep", source).exit_code == 0
    skills_dir.chmod(0o500)
    try:
        _assert_clean_error(_add("other", source), "permission denied")
        result = runner.invoke(app, ["hallbook", "remove", "keep"])
        _assert_clean_error(result, "permission denied")
    finally:
        skills_dir.chmod(0o700)
    assert (skills_dir / "keep.md").exists()


def test_custom_skills_path_blocked_by_a_file(skills_dir, tmp_path):
    skills_dir.parent.mkdir(parents=True)
    skills_dir.write_text("in the way", encoding="utf-8")
    source = tmp_path / "s.md"
    source.write_text("steps", encoding="utf-8")
    _assert_clean_error(_add("x", source), "custom skills folder")


# --- 2. names ---------------------------------------------------------------------------------------------------


def test_name_cap_is_64_and_the_message_states_the_rules():
    assert skill_hub.sanitize_skill_name("a" * 64) == "a" * 64
    with pytest.raises(ValueError, match=r"letters, digits, - and _, up to 64 characters"):
        skill_hub.sanitize_skill_name("a" * 65)
    with pytest.raises(ValueError, match=r"letters, digits, - and _, up to 64 characters"):
        skill_hub.sanitize_skill_name("bad name")


@pytest.mark.parametrize("name", ["CON", "con", "Prn", "AUX", "nul", "COM1", "com9", "LPT1", "lpt9"])
def test_windows_device_names_are_rejected(name):
    with pytest.raises(ValueError, match="reserved"):
        skill_hub.sanitize_skill_name(name)
    assert skill_hub.sanitize_skill_name("com10") == "com10"


def test_a_name_differing_only_by_case_is_rejected(skills_dir):
    skill_hub.add_custom_skill("My-Review", "steps")
    with pytest.raises(skill_hub.CustomSkillError, match="capital letters"):
        skill_hub.add_custom_skill("my-review", "other")
    skill_hub.add_custom_skill("My-Review", "updated")  # the same name is a normal update
    assert skill_hub.read_custom_skill_file("My-Review") == "updated"


def test_a_preexisting_long_named_skill_can_still_be_removed(skills_dir):
    name = "l" * 90  # over the 64 cap; kept short so the path stays under the Windows limit
    skills_dir.mkdir(parents=True)
    (skills_dir / f"{name}.md").write_text("legacy", encoding="utf-8")
    assert skill_hub.read_custom_skill_file(name) == "legacy"
    assert skill_hub.remove_custom_skill(name) is True
    assert not (skills_dir / f"{name}.md").exists()
    backups = list(skills_dir.glob("*.bak"))
    assert len(backups) == 1 and len(backups[0].name.encode()) < 200


def test_backup_names_never_exceed_200_bytes_for_any_name(skills_dir):
    for kind in ("bak", "deleted", "server", "synced"):
        assert len(skill_hub.unique_backup_path("z" * 251, kind).name.encode()) <= 200


# --- 3. offline sync: one line, no traceback, no token -----------------------------------------------------------


@pytest.fixture
def logged_in(skills_dir, monkeypatch):
    monkeypatch.setattr(skill_hub_sync, "_personal_token", lambda: "super-secret-token-123")


def _offline(monkeypatch):
    def fail(url, **kwargs):
        raise httpx.ConnectError("connection refused", request=httpx.Request("PUT", url))

    for verb in ("put", "delete"):
        monkeypatch.setattr(skill_hub_sync.httpx, verb, fail)


def test_offline_add_prints_one_line_and_no_traceback(logged_in, monkeypatch, tmp_path, caplog):
    _offline(monkeypatch)
    source = tmp_path / "s.md"
    source.write_text("steps", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        result = _add("offline-skill", source)
    assert result.exit_code == 0
    lines = result.output.strip().splitlines()
    assert lines[0].startswith("Added custom skill")
    assert lines[1] == (
        "Saved locally. Could not reach your Aletheore account (offline?); "
        "it will sync the next time you run aletheore login."
    )
    assert len(lines) == 2
    assert "super-secret-token-123" not in result.output
    assert not [r for r in caplog.records if r.exc_info]


def test_offline_remove_says_the_account_copy_still_exists_and_leaves_a_tombstone(logged_in, monkeypatch, tmp_path):
    source = tmp_path / "s.md"
    source.write_text("steps", encoding="utf-8")
    assert _add("gone", source).exit_code == 0
    _offline(monkeypatch)
    result = runner.invoke(app, ["hallbook", "remove", "gone"])
    assert "still exists" in result.output and "aletheore login" in result.output
    assert NO_TRACEBACK not in result.output and "super-secret-token-123" not in result.output
    assert "gone" in skill_hub_sync._load_pending_deletes()


def test_401_push_and_delete_say_to_log_in_again(logged_in, monkeypatch):
    def unauthorized(url, **kwargs):
        return httpx.Response(401, request=httpx.Request("PUT", url), json={"detail": "revoked"})

    monkeypatch.setattr(skill_hub_sync.httpx, "put", unauthorized)
    monkeypatch.setattr(skill_hub_sync.httpx, "delete", unauthorized)
    assert "aletheore login" in skill_hub_sync.push_note(skill_hub_sync.push_custom_skill_best_effort("a-skill", "x"))
    skill_hub.write_custom_skill_file("a-skill", "x")
    outcome = skill_hub_sync.delete_custom_skill_best_effort("a-skill")
    note = skill_hub_sync.delete_note(outcome)
    assert "still exists" in note and "aletheore login" in note
    assert "a-skill" in skill_hub_sync._load_pending_deletes()


def test_connect_timeout_is_short(logged_in, monkeypatch):
    seen = {}

    def capture(url, **kwargs):
        seen["timeout"] = kwargs["timeout"]
        return httpx.Response(204, request=httpx.Request("DELETE", url))

    monkeypatch.setattr(skill_hub_sync.httpx, "delete", capture)
    skill_hub_sync.delete_custom_skill_best_effort("a-skill")
    assert seen["timeout"].connect == 2.0 and seen["timeout"].read == 5.0


def test_a_network_error_logs_its_traceback_only_at_debug_level(logged_in, monkeypatch, caplog):
    _offline(monkeypatch)
    with caplog.at_level(logging.WARNING):
        skill_hub_sync.push_custom_skill_best_effort("a-skill", "x")
    assert caplog.records == []
    with caplog.at_level(logging.DEBUG):
        skill_hub_sync.push_custom_skill_best_effort("a-skill", "x")
    assert any(r.exc_info for r in caplog.records)


# --- 4. confidence ------------------------------------------------------------------------------------------------


def test_a_custom_skill_named_after_the_task_does_not_downgrade_a_bundled_high_answer(skills_dir):
    before = skill_hub.find_skill("git commit message")
    assert before["confidence"] == "high"
    skill_hub.add_custom_skill("git-commit-message", "my own notes")
    after = skill_hub.find_skill("git commit message")
    assert after["confidence"] == "high"
    assert after["top"]["skill_id"] == before["top"]["skill_id"]
    assert "custom/git-commit-message" in [alt["skill_id"] for alt in after["alternatives"]]


# --- 5. compiled python files ------------------------------------------------------------------------------------


@pytest.fixture
def bundled_root(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    folder = root / "tools" / "scripted"
    (folder / "scripts" / "__pycache__").mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        "---\nskill_id: tools/scripted\ntitle: Scripted\ndescription: D\nsource_url: https://example.com/s\nlicense: MIT\n---\nbody"
    , encoding="utf-8", newline="\n")  # the frontmatter parser wants LF, which Windows would otherwise rewrite
    (folder / "scripts" / "run.py").write_text("print(1)", encoding="utf-8")
    (folder / "scripts" / "__pycache__" / "run.cpython-312.pyc").write_bytes(b"\x00\x01")
    (folder / "scripts" / "old.pyo").write_bytes(b"\x00")
    monkeypatch.setattr(skill_hub, "bundled_skills_dir", lambda: root)
    return root


def test_compiled_python_is_not_listed_or_readable(bundled_root):
    files, total = skill_hub.list_skill_files("tools/scripted")
    assert files == ["scripts/run.py"] and total == 1
    for path in ("scripts/__pycache__/run.cpython-312.pyc", "scripts/old.pyo"):
        with pytest.raises(skill_hub.SkillFileError, match="compiled"):
            skill_hub.read_skill_file("tools/scripted", path)
    assert skill_hub.read_skill_file("tools/scripted", "scripts/run.py") == "print(1)"


# --- 6. hallbook info ----------------------------------------------------------------------------------------------


def test_skill_info_reads_the_holder_from_notice():
    info = skill_hub.skill_info("debugging/systematic-debugging")
    assert info["license"] == "MIT" and "Jesse Vincent" in info["copyright"]
    assert info["source_url"].startswith("https://github.com/") and info["requires"] == ["subagents"]
    assert info["files_total"] == len(skill_hub.list_skill_files("debugging/systematic-debugging")[0])
    assert info["scripts"] >= 1


def test_copyright_falls_back_to_not_recorded_when_notice_is_absent(bundled_root):
    assert skill_hub.skill_info("tools/scripted")["copyright"] == "not recorded"
    assert skill_hub.get_skill("tools/scripted").copyright == "not recorded"


def test_notice_table_is_parsed_once():
    skill_hub._notice_table.cache_clear()
    skill_hub.skill_copyright("debugging/systematic-debugging")
    skill_hub.skill_copyright("code-review/security-focused")
    assert skill_hub._notice_table.cache_info().misses == 1


def test_resolve_skill_id_exact_bare_ambiguous_and_unknown():
    assert skill_hub.resolve_skill_id("debugging/systematic-debugging") == ("debugging/systematic-debugging", [])
    assert skill_hub.resolve_skill_id("systematic-debugging") == ("debugging/systematic-debugging", [])
    ids, suggestions = skill_hub.resolve_skill_id("systematic-debuging")
    assert ids is None and "debugging/systematic-debugging" in suggestions and len(suggestions) <= 5
    by_name = {}
    for meta in skill_hub.list_bundled_skills():
        by_name.setdefault(meta.skill_id.split("/", 1)[1], []).append(meta.skill_id)
    duplicates = next((n for n, found in by_name.items() if len(found) > 1), None)
    if duplicates:
        resolved, options = skill_hub.resolve_skill_id(duplicates)
        assert resolved is None and set(options) <= set(by_name[duplicates])


def test_info_command_for_a_real_skill():
    result = runner.invoke(app, ["hallbook", "info", "debugging/systematic-debugging"])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0] == "Systematic Debugging" and lines[1] == "debugging/systematic-debugging"
    for label in ("Shelf", "Category", "License", "Copyright", "Source", "Needs", "Files"):
        assert any(line.startswith(label) for line in lines), label
    assert "\x1b" not in result.output
    assert lines[-1] == "Full licence text: aletheore hallbook info debugging/systematic-debugging --licence"


def test_info_accepts_a_bare_name_and_both_spellings_of_the_licence_flag():
    bare = runner.invoke(app, ["hallbook", "info", "systematic-debugging"])
    assert bare.exit_code == 0 and "debugging/systematic-debugging" in bare.output
    for flag in ("--licence", "--license"):
        text = runner.invoke(app, ["hallbook", "info", "systematic-debugging", flag])
        assert text.exit_code == 0 and "Permission is hereby granted" in text.output


def test_info_unknown_skill_suggests_ids_and_exits_1():
    result = runner.invoke(app, ["hallbook", "info", "systematic-debuging"])
    assert result.exit_code == 1 and result.output.startswith("error:")
    assert "Did you mean: debugging/systematic-debugging" in result.output


def test_info_without_a_licence_file_says_so_and_shows_the_spdx_id(bundled_root):
    result = runner.invoke(app, ["hallbook", "info", "tools/scripted", "--licence"])
    assert result.exit_code == 0 and "ships no licence file" in result.output and "MIT" in result.output


def test_info_for_a_custom_skill(skills_dir):
    skill_hub.add_custom_skill("mine", "steps")
    result = runner.invoke(app, ["hallbook", "info", "custom/mine"])
    assert result.exit_code == 0 and "custom skill" in result.output and "not vetted" in result.output
    assert "no licence information" in result.output


def test_info_without_an_argument_is_a_catalogue_summary_computed_from_the_index():
    result = runner.invoke(app, ["hallbook", "info"])
    assert result.exit_code == 0, result.output
    summary = skill_hub.catalogue_summary()
    assert f"{summary['total']} skills" in result.output
    for licence, count in summary["licenses"]:
        assert any(line.split() == [licence, str(count)] for line in result.output.splitlines())
    assert sum(count for _, count in summary["licenses"]) == summary["total"]
    for shelf in summary["shelves"]:
        assert shelf["title"] in result.output
    assert len(summary["shelves"]) == 6
    assert str(skill_hub.notice_path()) in result.output


def test_info_wraps_long_values_to_the_width_without_cutting_the_url(monkeypatch):
    monkeypatch.setattr("aletheore.cli._plain_width", lambda: 50)
    result = runner.invoke(app, ["hallbook", "info", "debugging/systematic-debugging"])
    info = skill_hub.skill_info("debugging/systematic-debugging")
    assert info["source_url"] in result.output
    assert all(len(line) <= 50 for line in result.output.splitlines() if "https://" not in line)


@pytest.mark.asyncio
async def test_mcp_get_skill_has_copyright_source_and_license(tmp_path):
    server = build_server(tmp_path)
    body = tool_result_body(await server.call_tool("aletheore_get_skill", {"skill_id": "debugging/systematic-debugging"}))["result"]
    assert "Jesse Vincent" in body["copyright"] and body["license"] == "MIT" and body["source_url"].startswith("https://")


def test_browser_detail_view_has_a_copyright_line():
    from aletheore import skills_browser

    skill = skill_hub.get_skill("debugging/systematic-debugging")
    labels = dict(skills_browser.detail_lines(skill))
    assert "Jesse Vincent" in labels["Copyright"]


# --- 7. atomic writes and permissions are covered in test_skill_hub.py -------------------------------------------


# --- 8. content checks -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("text, word", [("", "empty"), ("  \n\t ", "empty"), ("a\0b", "NUL"), ("x" * (1024 * 1024 + 1), "1 MB")])
def test_unusable_content_is_rejected(skills_dir, text, word):
    with pytest.raises(skill_hub.CustomSkillError, match=word):
        skill_hub.add_custom_skill("x", text)
    assert skill_hub.list_custom_skill_names() == []


def test_cli_rejects_a_large_file(skills_dir, tmp_path):
    big = tmp_path / "big.md"
    big.write_text("x" * (6 * 1024 * 1024), encoding="utf-8")
    _assert_clean_error(_add("x", big), "1 MB")


def test_secret_looking_content_warns_but_is_saved(skills_dir, tmp_path):
    source = tmp_path / "s.md"
    source.write_text(f"use {AWS_KEY} and {GH_TOKEN}\n-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA7\n", encoding="utf-8")
    result = _add("secretive", source)
    assert result.exit_code == 0
    assert "WARNING" in result.output and "plain" in result.output and "credential" in result.output
    assert AWS_KEY not in result.output and GH_TOKEN not in result.output
    assert skill_hub.read_custom_skill_file("secretive") is not None


def test_plain_content_has_no_warning(skills_dir, tmp_path):
    source = tmp_path / "s.md"
    source.write_text("# Steps\n1. Do the thing\n", encoding="utf-8")
    assert "WARNING" not in _add("calm", source).output


@pytest.mark.asyncio
async def test_mcp_add_applies_the_same_checks(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path / "custom_skills")
    monkeypatch.setattr(skill_hub_sync, "_personal_token", lambda: None)
    server = build_server(tmp_path)
    empty = await server.call_tool("aletheore_add_custom_skill", {"name": "x", "content": "  "})
    assert "empty" in empty.content[0].text
    secret = await server.call_tool("aletheore_add_custom_skill", {"name": "y", "content": f"key {AWS_KEY}"})
    assert "Saved custom skill" in secret.content[0].text and "WARNING" in secret.content[0].text


# --- 9. smaller gaps ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("query", [[""], ["  "], ["a"]])
def test_search_with_too_little_to_search_says_so(query):
    result = runner.invoke(app, ["hallbook", "search", *query])
    assert result.exit_code == 1 and "at least 2 characters" in result.output
    assert "No skills matched" not in result.output


def test_unknown_category_is_an_error_with_the_valid_ones(monkeypatch):
    for command in (["hallbook", "list", "--category", "nope"], ["hallbook", "search", "test", "--category", "nope"]):
        result = runner.invoke(app, command)
        assert result.exit_code == 1 and "unknown category 'nope'" in result.output and "debugging" in result.output
    wrong_case = runner.invoke(app, ["hallbook", "list", "--category", "Debugging"])
    assert wrong_case.exit_code == 1 and "Did you mean 'debugging'" in wrong_case.output
    assert runner.invoke(app, ["hallbook", "list", "--category", "custom"]).exit_code == 0


def test_add_help_documents_both_arguments():
    out = runner.invoke(app, ["hallbook", "add", "--help"]).output
    assert "letters, digits" in out and "64" in out and "markdown" in out and "frontmatter" in out


def test_suggest_help_works_through_the_launcher(monkeypatch, capsys):
    from aletheore import _launcher

    monkeypatch.setattr("sys.argv", ["aletheore", "hallbook", "suggest", "--help"])
    with pytest.raises(SystemExit) as exit_info:
        _launcher.main()
    assert exit_info.value.code == 0
    assert "--mode" in capsys.readouterr().out


def test_suggest_with_a_bogus_mode_prints_nothing_to_stdout_and_exits_zero(monkeypatch, capsys):
    from aletheore import hallbook_hook

    monkeypatch.setattr("sys.stdin", __import__("io").TextIOWrapper(__import__("io").BytesIO(b'{"prompt": "debug the failing test in my checkout flow please"}')))
    assert hallbook_hook.main(["--mode", "bogus"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "" and "unknown mode" in captured.err and len(captured.err.strip().splitlines()) == 1


def test_hook_command_rejects_a_bogus_mode_with_exit_2():
    result = runner.invoke(app, ["hallbook", "hook", "--mode", "bogus"])
    assert result.exit_code == 2 and "hint, inject" in result.output


def test_long_queries_are_capped():
    long_query = "debug flaky test " * 400
    assert len(skill_hub.cap_query(long_query)) <= skill_hub.MAX_QUERY_CHARS
    result = runner.invoke(app, ["hallbook", "search", long_query])
    assert result.exit_code == 0 and "cut to its first" in result.output


def test_get_with_the_wrong_case_is_a_plain_not_found_with_a_suggestion(caplog):
    with caplog.at_level(logging.WARNING):
        result = runner.invoke(app, ["hallbook", "get", "DEBUGGING/Systematic-Debugging"])
    assert result.exit_code == 1
    assert "skill not found" in result.output and "Did you mean: debugging/systematic-debugging" in result.output
    assert caplog.records == []
    assert skill_hub.get_bundled_skill("DEBUGGING/Systematic-Debugging") is None


def test_hook_hints_include_the_cli_fallback():
    hint = skill_hub.hook_suggestion("git commit message conventions for my team repository")
    assert hint is not None and "aletheore hallbook get" in hint


def test_crlf_text_is_stored_with_unix_line_endings_and_round_trips(skills_dir, tmp_path):
    source = tmp_path / "crlf.md"
    source.write_bytes(b"# Steps\r\n1. one\r\n")
    assert _add("crlf", source).exit_code == 0
    assert (skills_dir / "crlf.md").read_bytes() == b"# Steps\n1. one\n"
    assert skill_hub.read_custom_skill_file("crlf") == "# Steps\n1. one\n"


def test_non_ascii_text_round_trips_as_utf8(skills_dir):
    skill_hub.add_custom_skill("accents", "caf\u00e9 \u2014 na\u00efve")
    assert (skills_dir / "accents.md").read_bytes().decode("utf-8") == "caf\u00e9 \u2014 na\u00efve"


def test_a_backup_that_cannot_be_deleted_is_skipped_not_fatal(skills_dir, monkeypatch):
    for number in range(8):
        skill_hub.write_backup("locked", "bak", f"v{number}")

    def locked(self, *args, **kwargs):
        raise PermissionError("file in use")  # what Windows says for a file another process has open

    monkeypatch.setattr(type(skills_dir), "unlink", locked)
    skill_hub.prune_backups("locked")  # must not raise
