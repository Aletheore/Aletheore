from pathlib import Path

from veridion.secrets import find_secrets


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
