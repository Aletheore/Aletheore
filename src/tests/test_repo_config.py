import json
from pathlib import Path

from aletheore.repo_config import DEFAULT_CONFIG, is_ignored, load_repo_config


def test_load_repo_config_returns_defaults_when_no_file(tmp_path: Path):
    assert load_repo_config(tmp_path) == DEFAULT_CONFIG


def test_load_repo_config_returns_defaults_on_malformed_json(tmp_path: Path):
    (tmp_path / ".aletheore.json").write_text("{not valid json")
    assert load_repo_config(tmp_path) == DEFAULT_CONFIG


def test_load_repo_config_returns_defaults_when_not_a_json_object(tmp_path: Path):
    (tmp_path / ".aletheore.json").write_text("[1, 2, 3]")
    assert load_repo_config(tmp_path) == DEFAULT_CONFIG


def test_load_repo_config_reads_ignored_paths(tmp_path: Path):
    (tmp_path / ".aletheore.json").write_text(json.dumps({"ignored_paths": ["vendor/**", "*.gen.go"]}))
    config = load_repo_config(tmp_path)
    assert config["ignored_paths"] == ["vendor/**", "*.gen.go"]


def test_load_repo_config_ignores_non_string_entries_in_ignored_paths(tmp_path: Path):
    (tmp_path / ".aletheore.json").write_text(json.dumps({"ignored_paths": ["vendor/**", 123, None]}))
    config = load_repo_config(tmp_path)
    assert config["ignored_paths"] == ["vendor/**"]


def test_load_repo_config_drops_unknown_disabled_checks(tmp_path: Path):
    (tmp_path / ".aletheore.json").write_text(
        json.dumps({"disabled_checks": ["licenses", "not_a_real_check"]})
    )
    config = load_repo_config(tmp_path)
    assert config["disabled_checks"] == ["licenses"]


def test_load_repo_config_reads_all_four_disableable_checks(tmp_path: Path):
    (tmp_path / ".aletheore.json").write_text(
        json.dumps(
            {"disabled_checks": ["vulnerabilities", "licenses", "endpoints", "secrets_history"]}
        )
    )
    config = load_repo_config(tmp_path)
    assert set(config["disabled_checks"]) == {
        "vulnerabilities",
        "licenses",
        "endpoints",
        "secrets_history",
    }


def test_load_repo_config_reads_valid_severity_threshold(tmp_path: Path):
    (tmp_path / ".aletheore.json").write_text(json.dumps({"severity_threshold": "high"}))
    config = load_repo_config(tmp_path)
    assert config["severity_threshold"] == "high"


def test_load_repo_config_rejects_invalid_severity_threshold(tmp_path: Path):
    (tmp_path / ".aletheore.json").write_text(json.dumps({"severity_threshold": "extremely-bad"}))
    config = load_repo_config(tmp_path)
    assert config["severity_threshold"] is None


def test_load_repo_config_still_reads_existing_keys(tmp_path: Path):
    (tmp_path / ".aletheore.json").write_text(
        json.dumps(
            {
                "layer_markers": {"domain": 0},
                "cluster_resolution": 1.5,
                "dead_code_entry_points": ["scripts/entry.py"],
                "accepted_secrets": [{"path": "a.py", "pattern": "x", "match_preview": "y"}],
            }
        )
    )
    config = load_repo_config(tmp_path)
    assert config["layer_markers"] == {"domain": 0}
    assert config["cluster_resolution"] == 1.5
    assert config["dead_code_entry_points"] == ["scripts/entry.py"]
    assert config["accepted_secrets"] == [{"path": "a.py", "pattern": "x", "match_preview": "y"}]


def test_is_ignored_no_patterns_matches_nothing():
    assert is_ignored("vendor/lib.js", []) is False


def test_is_ignored_exact_file_match():
    assert is_ignored("generated/schema.py", ["generated/schema.py"]) is True


def test_is_ignored_glob_match():
    # fnmatch's "*" crosses path separators, so a suffix pattern like this
    # matches at any depth, not just at the repo root - the more useful
    # default for "ignore every generated file named like this."
    assert is_ignored("src/foo.gen.go", ["*.gen.go"]) is True
    assert is_ignored("foo.gen.go", ["*.gen.go"]) is True


def test_is_ignored_directory_prefix_excludes_everything_under_it():
    assert is_ignored("vendor/pkg/lib.go", ["vendor"]) is True
    assert is_ignored("vendor/pkg/deep/nested/file.go", ["vendor"]) is True


def test_is_ignored_directory_glob_pattern():
    assert is_ignored("vendor/pkg/lib.go", ["vendor/**"]) is True
    assert is_ignored("other/pkg/lib.go", ["vendor/**"]) is False


def test_is_ignored_no_match_returns_false():
    assert is_ignored("src/main.py", ["vendor/**", "*.gen.go"]) is False
