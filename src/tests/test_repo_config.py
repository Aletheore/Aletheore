import json
from pathlib import Path

from aletheore.repo_config import DEFAULT_CONFIG, is_ignored, load_repo_config, parse_repo_config


def test_load_repo_config_returns_defaults_when_no_file(tmp_path: Path):
    assert load_repo_config(tmp_path) == DEFAULT_CONFIG


def test_parse_repo_config_returns_defaults_for_none():
    # None is parse_repo_config's "no .aletheore.json at this ref" case -
    # a caller with no local checkout to read a Path from (see
    # scan_worker.jobs._run_flash_review) fetches the file via GitHub's
    # Contents API instead, which reports a missing file as a 404 rather
    # than an OSError load_repo_config's Path-based check catches.
    assert parse_repo_config(None) == DEFAULT_CONFIG


def test_parse_repo_config_matches_load_repo_config_for_the_same_content(tmp_path: Path):
    raw_text = json.dumps({"ignored_paths": ["vendor/**"], "severity_threshold": "high"})
    (tmp_path / ".aletheore.json").write_text(raw_text)

    assert parse_repo_config(raw_text) == load_repo_config(tmp_path)


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


def test_load_repo_config_drops_a_non_int_layer_marker_rank(tmp_path: Path):
    # Real bug found via audit: an unvalidated string rank flowed straight
    # into architecture.detect_layer_violations's `from_rank < to_rank`
    # comparison. "2" < "10" is False under lexicographic string
    # comparison, so a real inner-to-outer violation went unreported with
    # no error - and mixing a string-ranked custom marker with any
    # built-in int-ranked marker raised a TypeError that crashed the
    # whole scan. cluster_resolution is already type-checked for exactly
    # this reason; layer_markers must be too.
    (tmp_path / ".aletheore.json").write_text(
        json.dumps({"layer_markers": {"domain": "2", "web": 10}})
    )
    config = load_repo_config(tmp_path)
    assert config["layer_markers"] == {"web": 10}


def test_load_repo_config_drops_a_bool_layer_marker_rank(tmp_path: Path):
    # bool is a subclass of int in Python - True/False must not slip
    # through as a rank of 1/0.
    (tmp_path / ".aletheore.json").write_text(
        json.dumps({"layer_markers": {"domain": True, "web": 10}})
    )
    config = load_repo_config(tmp_path)
    assert config["layer_markers"] == {"web": 10}


def test_is_ignored_no_patterns_matches_nothing():
    assert is_ignored("vendor/lib.js", []) is False


def test_is_ignored_exact_file_match():
    assert is_ignored("generated/schema.py", ["generated/schema.py"]) is True


def test_is_ignored_glob_match():
    # "*.gen.go" has no "/", so it's an unanchored (gitignore-style) bare
    # pattern - tried against every starting depth, so a suffix pattern
    # like this matches at any depth, not just at the repo root - the more
    # useful default for "ignore every generated file named like this."
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


def test_is_ignored_unanchored_bare_name_matches_a_nested_directory():
    # Real bug found via audit: this module's own design spec calls these
    # "gitignore-style glob patterns," and this function's own docstring
    # gives "vendor" (no slash) as an example of something that excludes
    # "everything under vendor/" - but a bare, unanchored pattern like
    # "vendor" was previously always anchored to the repo root regardless,
    # so a nested vendor/ directory (a real, common repo shape - a
    # monorepo package vendoring its own dependencies) was never excluded
    # at all, contrary to both the docstring and real gitignore semantics.
    assert is_ignored("packages/some-lib/vendor/pkg/lib.go", ["vendor"]) is True


def test_is_ignored_anchored_pattern_still_only_matches_at_repo_root():
    # The counterpart to the unanchored case above: a pattern with a
    # leading slash IS anchored under gitignore rules and must not start
    # matching at every depth just because the unanchored case now does.
    assert is_ignored("packages/some-lib/vendor/pkg/lib.go", ["/vendor"]) is False
    assert is_ignored("vendor/pkg/lib.go", ["/vendor"]) is True


def test_is_ignored_directory_glob_still_excludes_everything_beneath_a_wildcard_match():
    # is_ignored's own documented contract is broader than raw gitignore
    # matching: ANY matched prefix (wildcard-matched segments included)
    # excludes the whole subtree beneath it, not just that one segment -
    # "if it matches any parent-directory prefix of the path" per this
    # function's own docstring. Confirmed this was already the pre-fix
    # behavior too (a plain fnmatch.fnmatch("docs/sub/x.md", "docs/*") is
    # True, since fnmatch's own "*" already crosses "/"), so this is
    # preserved intentionally, not a new regression from the segment-based
    # rewrite above.
    assert is_ignored("docs/x.md", ["docs/*"]) is True
    assert is_ignored("docs/sub/x.md", ["docs/*"]) is True


def test_is_ignored_multiple_wildcard_segments_does_not_blow_up():
    # Real bug found via Flash Review on this same PR: the naive recursive
    # "**" branch in _segments_match forks into
    # len(candidate_segments)+1 calls with no memoization, and a pattern
    # with several "**" segments multiplies that branching at every level -
    # exponential in the number of "**" segments. ignored_paths comes from
    # the scanned repo's own .aletheore.json, untrusted input by design (
    # that's the entire threat model this module exists under), so a
    # crafted config is a real denial-of-service vector, not a theoretical
    # one - confirmed directly: this exact shape took ~60s pre-fix.
    # Bounded here to well under a second as the regression guard.
    import time

    pattern = "/".join(["**"] * 10) + "/nomatch"
    path = "/".join(f"seg{i}" for i in range(25))

    start = time.monotonic()
    result = is_ignored(path, [pattern])
    elapsed = time.monotonic() - start

    assert result is False
    assert elapsed < 1.0
