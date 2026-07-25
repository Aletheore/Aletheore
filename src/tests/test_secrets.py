import json
from pathlib import Path

from aletheore.secrets import find_secrets, load_secrets_baseline


def test_find_secrets_detects_aws_key(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "config.py").write_text('AWS_KEY = "AKIAABCDEFGHIJKLMNOP"\n')

    result = find_secrets(repo)

    assert result["scanned_files"] == 1
    assert len(result["findings"]) == 1
    finding = result["findings"][0]
    assert finding["path"] == "config.py"
    assert finding["line"] == 1
    assert finding["pattern"] == "aws_access_key_id"
    assert finding["likely_placeholder"] is False


def test_find_secrets_redacts_the_match(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "config.py").write_text('AWS_KEY = "AKIAABCDEFGHIJKLMNOP"\n')

    result = find_secrets(repo)

    preview = result["findings"][0]["match_preview"]
    assert "AKIAABCDEFGHIJKLMNOP" not in preview
    assert preview.startswith("AKIA")
    assert preview.endswith("MNOP")


def test_find_secrets_flags_test_fixture_paths_as_likely_placeholder(tmp_path):
    repo = tmp_path / "repo"
    (repo / "tests" / "fixtures").mkdir(parents=True)
    (repo / "tests" / "fixtures" / "sample.py").write_text(
        'STRIPE_KEY = "sk_test_00000000000000000000"\n'
    )

    result = find_secrets(repo)

    assert result["findings"][0]["likely_placeholder"] is True


def test_find_secrets_detects_github_token_and_private_key_header(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.env").write_text("TOKEN=ghp_" + "a" * 36 + "\n")
    (repo / "id_rsa").write_text("-----BEGIN RSA PRIVATE KEY-----\nMIIBogIBAAJ...\n")

    result = find_secrets(repo)

    patterns_found = {f["pattern"] for f in result["findings"]}
    assert "github_token" in patterns_found
    assert "private_key_header" in patterns_found


def test_find_secrets_ignores_ignored_dirs_and_binary_extensions(tmp_path):
    repo = tmp_path / "repo"
    (repo / "node_modules" / "pkg").mkdir(parents=True)
    (repo / "node_modules" / "pkg" / "secret.js").write_text('KEY = "AKIAABCDEFGHIJKLMNOP"\n')
    (repo / "logo.png").write_bytes(b"AKIAABCDEFGHIJKLMNOP" + b"\x89PNG")
    (repo / "clean.py").write_text("x = 1\n")

    result = find_secrets(repo)

    assert result["findings"] == []
    assert result["scanned_files"] == 1


def test_find_secrets_no_matches_in_ordinary_file(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("def add(a, b):\n    return a + b\n")

    result = find_secrets(repo)

    assert result["findings"] == []
    assert result["scanned_files"] == 1


def test_find_secrets_generic_credential_preview_previews_the_value_not_the_keyword(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    real_value = "totallyrealvalue1234567890tailend"
    (repo / "config.py").write_text(f'secret = "{real_value}"\n')

    result = find_secrets(repo)

    finding = result["findings"][0]
    assert finding["pattern"] == "generic_credential_assignment"
    # the full value must never appear in the preview
    assert real_value not in finding["match_preview"]
    # the preview must reflect the credential VALUE's own prefix (matching the
    # redaction scheme used by every other pattern), not the "secret"/"password"/
    # "api_key" keyword that happens to precede it in the source line - a keyword
    # prefix reveals nothing useful and previously meant the last chars shown were
    # an accidental mix of real value characters and a stray closing quote rather
    # than a clean, intentional preview of the value itself
    assert finding["match_preview"].startswith(real_value[:4])
    assert not finding["match_preview"].lower().startswith("secr")


def test_find_secrets_always_includes_accepted_key_defaulting_false(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "config.py").write_text('AWS_KEY = "AKIAABCDEFGHIJKLMNOP"\n')

    result = find_secrets(repo)

    assert result["findings"][0]["accepted"] is False


def test_find_secrets_marks_a_baselined_finding_as_accepted(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "config.py").write_text('AWS_KEY = "AKIAABCDEFGHIJKLMNOP"\n')
    preview = find_secrets(repo)["findings"][0]["match_preview"]

    baseline = [{"path": "config.py", "pattern": "aws_access_key_id", "match_preview": preview}]
    result = find_secrets(repo, baseline=baseline)

    assert result["findings"][0]["accepted"] is True


def test_find_secrets_baseline_does_not_accept_a_non_matching_finding(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "config.py").write_text('AWS_KEY = "AKIAABCDEFGHIJKLMNOP"\n')

    baseline = [{"path": "other.py", "pattern": "aws_access_key_id", "match_preview": "AKIA****...MNOP"}]
    result = find_secrets(repo, baseline=baseline)

    assert result["findings"][0]["accepted"] is False


def test_load_secrets_baseline_reads_a_valid_file(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    entry = {"path": "config.py", "pattern": "aws_access_key_id", "match_preview": "AKIA****...MNOP"}
    (repo / ".aletheore.json").write_text(json.dumps({"accepted_secrets": [entry]}))

    assert load_secrets_baseline(repo) == [entry]


def test_load_secrets_baseline_returns_empty_list_when_file_missing(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    assert load_secrets_baseline(repo) == []


def test_load_secrets_baseline_returns_empty_list_on_malformed_json(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".aletheore.json").write_text("{not valid json")

    assert load_secrets_baseline(repo) == []


def test_load_secrets_baseline_returns_empty_list_when_key_is_not_a_list(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".aletheore.json").write_text(json.dumps({"accepted_secrets": "not-a-list"}))

    assert load_secrets_baseline(repo) == []


def test_load_secrets_baseline_filters_out_non_dict_entries(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    entry = {"path": "config.py", "pattern": "aws_access_key_id", "match_preview": "AKIA****...MNOP"}
    (repo / ".aletheore.json").write_text(json.dumps({"accepted_secrets": [entry, "garbage", 5]}))

    assert load_secrets_baseline(repo) == [entry]
