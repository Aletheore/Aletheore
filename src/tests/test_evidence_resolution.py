import json
import subprocess

from aletheore.evidence_resolution import (
    attach_dependency_evidence,
    attach_risk_evidence,
    empty_resolution,
    find_symbol_at_location,
    merge_resolution,
    normalize_resolution,
    resolve_endpoint,
    resolve_owner,
    resolve_recent_commit,
)


def make_evidence():
    return {
        "repository": {
            "modules": [
                {
                    "path": "app/routes.py",
                    "imports": ["app/services/users.py", "os"],
                    "imported_by": [],
                    "symbols": {
                        "functions": [{"name": "list_users", "start_line": 10, "end_line": 20}],
                        "classes": [],
                    },
                }
            ],
            "api_endpoints": {
                "checked": True,
                "endpoints": [
                    {
                        "method": "GET",
                        "path": "/v1/users",
                        "framework": "fastapi",
                        "file": "app/routes.py",
                        "line": 12,
                        "handler": "list_users",
                        "unresolved": False,
                    }
                ],
            },
        },
        "security": {
            "secrets": {
                "findings": [
                    {
                        "path": "app/routes.py",
                        "line": 15,
                        "pattern": "generic_secret",
                        "likely_placeholder": False,
                    }
                ]
            },
            "dependency_vulnerabilities": {
                "findings": [
                    {
                        "package": "os",
                        "severity": "HIGH",
                        "advisory_id": "GHSA-test",
                    }
                ]
            },
            "dependency_licenses": {
                "findings": [
                    {
                        "package": "app-services",
                        "license": "UNKNOWN",
                        "severity": "medium",
                    }
                ]
            },
        },
        "architecture": {
            "layer_violations": {
                "violations": [
                    {
                        "from": "app/routes.py",
                        "to": "app/services/users.py",
                        "reason": "route depends on lower layer incorrectly",
                    }
                ]
            }
        },
    }


def test_empty_resolution_exposes_missing_evidence():
    result = empty_resolution("endpoint")

    assert result["kind"] == "endpoint"
    assert result["file"] is None
    assert result["line"] is None
    assert result["symbol"] is None
    assert result["owner"] is None
    assert result["commit"] is None
    assert result["dependency"] is None
    assert result["risk"] == []
    assert result["confidence"] == "unavailable"
    assert result["owner_status"] == "unavailable"
    assert result["commit_status"] == "unavailable"
    assert result["dependency_status"] == "unavailable"
    assert result["risk_status"] == "unavailable"


def test_normalize_resolution_preserves_source_location():
    result = normalize_resolution(
        kind="symbol",
        file="app/routes.py",
        line=10,
        end_line=20,
        symbol="list_users",
        evidence_path="repository.modules[0].symbols.functions[0]",
        confidence="exact",
    )

    assert result["file"] == "app/routes.py"
    assert result["line"] == 10
    assert result["end_line"] == 20
    assert result["symbol"] == "list_users"
    assert result["confidence"] == "exact"


def test_merge_resolution_does_not_replace_exact_with_unavailable():
    exact = normalize_resolution(kind="endpoint", file="app/routes.py", line=12, confidence="exact")
    unavailable = empty_resolution("endpoint")

    result = merge_resolution(exact, unavailable)

    assert result["file"] == "app/routes.py"
    assert result["line"] == 12
    assert result["confidence"] == "exact"


def test_resolve_endpoint_returns_file_line_and_handler_symbol():
    result = resolve_endpoint(make_evidence(), "get", "/v1/users")

    assert result["kind"] == "endpoint"
    assert result["file"] == "app/routes.py"
    assert result["line"] == 12
    assert result["symbol"] == "list_users"
    assert result["confidence"] == "exact"
    assert result["evidence_path"] == "repository.api_endpoints.endpoints[0]"


def test_resolve_endpoint_is_unavailable_for_unknown_endpoint():
    result = resolve_endpoint(make_evidence(), "POST", "/missing")

    assert result["kind"] == "endpoint"
    assert result["file"] is None
    assert result["line"] is None
    assert result["confidence"] == "unavailable"
    assert result["evidence_status"] == "unavailable"


def test_resolve_owner_uses_last_matching_codeowners_rule(tmp_path):
    repo = tmp_path
    (repo / ".github").mkdir()
    (repo / ".github" / "CODEOWNERS").write_text(
        "* @global\napp/ @app-team\napp/routes.py @api-team @second-owner\n"
    )

    result = resolve_owner(repo, "app/routes.py")

    assert result["owner"] == ["@api-team", "@second-owner"]
    assert result["owner_status"] == "available"
    assert result["confidence"] == "inferred"


def test_resolve_owner_returns_unavailable_without_codeowners(tmp_path):
    result = resolve_owner(tmp_path, "app/routes.py")

    assert result["owner"] is None
    assert result["owner_status"] == "unavailable"


def test_resolve_owner_matches_unanchored_directory_pattern_at_any_depth(tmp_path):
    # CODEOWNERS follows gitignore anchoring rules, and GitHub's own docs
    # give this exact example: "apps/" (no leading slash) "owns any file
    # in an apps directory anywhere in your repository" - not just an
    # "apps" directory at the repo root.
    repo = tmp_path
    (repo / ".github").mkdir()
    (repo / ".github" / "CODEOWNERS").write_text("apps/ @octocat\n")

    result = resolve_owner(repo, "src/apps/main.py")

    assert result["owner"] == ["@octocat"]


def test_resolve_owner_leading_slash_directory_pattern_stays_anchored_to_root(tmp_path):
    # The counterpart to the unanchored case above (GitHub's own docs,
    # same page): "/docs/" (leading slash) "owns any file in the `/docs`
    # directory in the root of your repository and any of its
    # subdirectories" - explicitly NOT any docs/ directory at any depth.
    repo = tmp_path
    (repo / ".github").mkdir()
    (repo / ".github" / "CODEOWNERS").write_text("/docs/ @doctocat\n")

    nested = resolve_owner(repo, "src/docs/readme.md")
    root = resolve_owner(repo, "docs/readme.md")

    assert nested["owner"] is None
    assert root["owner"] == ["@doctocat"]


def test_resolve_owner_unanchored_directory_pattern_does_not_match_a_bare_file(tmp_path):
    # Flash Review finding: an earlier fix's `file_path == dir_name` clause
    # made "apps/" (a directory-only pattern per its trailing slash) match
    # a plain FILE literally named "apps" - no such directory involved at
    # all. A trailing slash in CODEOWNERS means "directory", never "a
    # regular file at this exact path".
    repo = tmp_path
    (repo / ".github").mkdir()
    (repo / ".github" / "CODEOWNERS").write_text("apps/ @octocat\n")

    result = resolve_owner(repo, "apps")

    assert result["owner"] is None


def test_resolve_owner_glob_star_does_not_cross_a_path_separator(tmp_path):
    # GitHub's own CODEOWNERS docs give this exact example: "docs/*"
    # matches "docs/getting-started.md" but explicitly NOT a further
    # nested file like "docs/build-app/troubleshooting.md" - a single
    # "*" matches within one path segment only, gitignore-style.
    repo = tmp_path
    (repo / ".github").mkdir()
    (repo / ".github" / "CODEOWNERS").write_text("docs/* @doctocat\n")

    direct_child = resolve_owner(repo, "docs/getting-started.md")
    nested = resolve_owner(repo, "docs/build-app/troubleshooting.md")

    assert direct_child["owner"] == ["@doctocat"]
    assert nested["owner"] is None


def test_resolve_owner_bracket_syntax_is_treated_as_a_literal_filename(tmp_path):
    # GitHub's own CODEOWNERS docs list this as an explicit deviation from
    # gitignore syntax: "[ ]" character-range/class syntax is not
    # supported. A pattern "[Dd]ocs" names a literal file called "[Dd]ocs",
    # not "Docs" or "docs" - but fnmatch.fnmatch doesn't know that and
    # treats "[Dd]" as a character class regardless, matching files GitHub
    # itself would never attribute to this pattern.
    repo = tmp_path
    (repo / ".github").mkdir()
    (repo / ".github" / "CODEOWNERS").write_text("[Dd]ocs @doctocat\n")

    expanded = resolve_owner(repo, "Docs")
    literal = resolve_owner(repo, "[Dd]ocs")

    assert expanded["owner"] is None
    assert literal["owner"] == ["@doctocat"]


def test_codeowners_multiple_wildcard_segments_does_not_blow_up(tmp_path):
    # Real bug found via audit (backward-audit sweep re-verifying #686/
    # #687): the naive recursive "**" branch in _glob_segments_match
    # forks into len(path_segments)+1 calls with no memoization, and a
    # pattern with several "**" segments multiplies that branching at
    # every level - exponential in the number of "**" segments. A
    # CODEOWNERS file lives inside the scanned repo itself - untrusted
    # input by design, the same threat model repo_config.py's
    # ignored_paths already has (see its own
    # test_is_ignored_multiple_wildcard_segments_does_not_blow_up) - so a
    # crafted CODEOWNERS pattern is a real denial-of-service vector, not
    # a theoretical one. Confirmed directly: this exact shape took ~65s
    # pre-fix. Bounded here to well under a second as the regression
    # guard.
    import time

    repo = tmp_path
    (repo / ".github").mkdir()
    pattern = "/".join(["**"] * 10) + "/nomatch"
    (repo / ".github" / "CODEOWNERS").write_text(f"{pattern} @doctocat\n")
    path = "/".join(f"seg{i}" for i in range(25)) + "/file.py"

    start = time.monotonic()
    result = resolve_owner(repo, path)
    elapsed = time.monotonic() - start

    assert result["owner"] is None
    assert elapsed < 1.0


def test_resolve_recent_commit_returns_file_commit(tmp_path):
    repo = tmp_path
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Alice"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "alice@example.com"], cwd=repo, check=True)
    (repo / "app.py").write_text("print('hello')\n")
    subprocess.run(["git", "add", "app.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add app"], cwd=repo, check=True, capture_output=True)

    result = resolve_recent_commit(repo, "app.py")

    assert result["commit"]["subject"] == "add app"
    assert result["commit"]["author"] == "Alice"
    assert result["commit_status"] == "available"
    assert result["confidence"] == "weak"


def test_resolve_recent_commit_returns_unavailable_for_non_git_repo(tmp_path):
    result = resolve_recent_commit(tmp_path, "app.py")

    assert result["commit"] is None
    assert result["commit_status"] == "unavailable"


def test_resolve_recent_commit_attributes_a_line_to_its_own_last_editor_not_the_file(tmp_path):
    # Real bug found via audit: a prior version accepted `line` but
    # silently discarded it (`del line`), always answering with the whole
    # file's most recent commit - so a line nobody has touched since it
    # was first written still got attributed to whoever most recently
    # edited some OTHER, unrelated line in the same file.
    repo = tmp_path
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Alice"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "alice@example.com"], cwd=repo, check=True)
    (repo / "app.py").write_text("def foo():\n    return 1\n\ndef bar():\n    return 2\n")
    subprocess.run(["git", "add", "app.py"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Alice adds foo and bar"], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(["git", "config", "user.name", "Bob"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "bob@example.com"], cwd=repo, check=True)
    (repo / "app.py").write_text("def foo():\n    return 100\n\ndef bar():\n    return 2\n")
    subprocess.run(["git", "add", "app.py"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Bob edits foo only"], cwd=repo, check=True, capture_output=True
    )

    bar_line = resolve_recent_commit(repo, "app.py", line=5)
    foo_line = resolve_recent_commit(repo, "app.py", line=2)
    whole_file = resolve_recent_commit(repo, "app.py")

    assert bar_line["commit"]["author"] == "Alice"
    assert bar_line["commit"]["subject"] == "Alice adds foo and bar"
    assert foo_line["commit"]["author"] == "Bob"
    # Whole-file fallback (no line given) is unchanged: the most recent
    # commit touching the file at all, regardless of which line.
    assert whole_file["commit"]["author"] == "Bob"


def test_resolve_recent_commit_falls_back_to_whole_file_when_the_line_is_out_of_range(tmp_path):
    # A line number past the file's real current length (stale evidence,
    # or evidence describing a symbol at a slightly different line than
    # the file's exact current line count) makes `git blame -L` fail -
    # this must degrade to the same whole-file answer `line=None` gets,
    # not silently report the commit as unavailable.
    repo = tmp_path
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Alice"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "alice@example.com"], cwd=repo, check=True)
    (repo / "app.py").write_text("print('hello')\n")
    subprocess.run(["git", "add", "app.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add app"], cwd=repo, check=True, capture_output=True)

    result = resolve_recent_commit(repo, "app.py", line=999)

    assert result["commit"]["subject"] == "add app"
    assert result["commit_status"] == "available"


def test_attach_dependency_evidence_uses_module_imports():
    resolution = resolve_endpoint(make_evidence(), "GET", "/v1/users")

    result = attach_dependency_evidence(make_evidence(), resolution)

    assert result["dependency"] == ["app/services/users.py", "os"]
    assert result["dependency_status"] == "available"


def test_attach_risk_evidence_attaches_matching_findings():
    resolution = resolve_endpoint(make_evidence(), "GET", "/v1/users")

    result = attach_risk_evidence(make_evidence(), resolution)

    categories = {risk["category"] for risk in result["risk"]}
    assert "secret" in categories
    assert "architecture" in categories
    assert "vulnerability" in categories
    assert result["risk_status"] == "available"


def test_attach_risk_evidence_matches_a_finding_whose_package_name_differs_from_the_import_name():
    # Real bug found via audit: dependencies holds import-time names
    # (module["imports"]) but a finding's own "package" is the real
    # registry name - these diverge for a real, common set of packages
    # (PyYAML vs yaml, beautifulsoup4 vs bs4, Pillow vs PIL). A bare
    # `package in dependencies` equality check silently dropped every
    # real vulnerability/license match whenever they differed.
    evidence = {
        "security": {
            "dependency_vulnerabilities": {
                "findings": [
                    {"package": "PyYAML", "severity": "high", "advisory_id": "CVE-2024-XXXX"}
                ]
            },
            "dependency_licenses": {
                "findings": [
                    {"package": "beautifulsoup4", "license": "MIT", "severity": "low"}
                ]
            },
        }
    }
    resolution = normalize_resolution(
        kind="symbol", file="app.py", dependency=["yaml", "bs4"], confidence="exact"
    )

    result = attach_risk_evidence(evidence, resolution)

    categories = {risk["category"] for risk in result["risk"]}
    assert categories == {"vulnerability", "license"}


def test_normalize_resolution_sets_suggestion_and_status():
    result = normalize_resolution(kind="owner", suggestion="add a null check here", confidence="inferred")
    assert result["suggestion"] == "add a null check here"
    assert result["suggestion_status"] == "available"

    empty = empty_resolution("owner")
    assert empty["suggestion"] is None
    assert empty["suggestion_status"] == "unavailable"


def test_merge_resolution_carries_suggestion_from_attachment():
    base = empty_resolution("endpoint")
    attachment = normalize_resolution(kind="suggestion", suggestion="fix by X", confidence="inferred")
    merged = merge_resolution(base, attachment)
    assert merged["suggestion"] == "fix by X"
    assert merged["suggestion_status"] == "available"


def test_resolution_is_json_serializable():
    result = attach_risk_evidence(
        make_evidence(),
        attach_dependency_evidence(make_evidence(), resolve_endpoint(make_evidence(), "GET", "/v1/users")),
    )

    json.dumps(result)


def _evidence_with_symbols(path: str, functions=(), classes=()) -> dict:
    return {
        "repository": {
            "modules": [
                {
                    "path": path,
                    "symbols": {"functions": list(functions), "classes": list(classes)},
                }
            ]
        }
    }


def test_find_symbol_at_location_returns_the_containing_function():
    evidence = _evidence_with_symbols(
        "app.py",
        functions=[{"name": "handle_request", "start_line": 10, "end_line": 30}],
    )
    assert find_symbol_at_location(evidence, "app.py", 15) == "handle_request"


def test_find_symbol_at_location_picks_the_innermost_of_nested_candidates():
    # A method's range is a strict subset of its containing class's - the
    # narrower match should win, since it's the more useful attribution
    # for a line-level finding.
    evidence = _evidence_with_symbols(
        "app.py",
        functions=[{"name": "handle_request", "start_line": 12, "end_line": 20}],
        classes=[{"name": "RequestHandler", "start_line": 1, "end_line": 40}],
    )
    assert find_symbol_at_location(evidence, "app.py", 15) == "handle_request"


def test_find_symbol_at_location_returns_none_outside_any_symbol():
    evidence = _evidence_with_symbols(
        "app.py",
        functions=[{"name": "handle_request", "start_line": 10, "end_line": 30}],
    )
    assert find_symbol_at_location(evidence, "app.py", 5) is None


def test_find_symbol_at_location_returns_none_for_a_file_not_in_evidence():
    evidence = _evidence_with_symbols(
        "app.py", functions=[{"name": "handle_request", "start_line": 10, "end_line": 30}]
    )
    assert find_symbol_at_location(evidence, "other.py", 15) is None


def test_find_symbol_at_location_returns_none_when_evidence_is_none():
    assert find_symbol_at_location(None, "app.py", 15) is None


def test_find_symbol_at_location_returns_none_when_evidence_has_no_modules():
    assert find_symbol_at_location({"repository": {}}, "app.py", 15) is None


def test_find_symbol_at_location_skips_a_symbol_entry_missing_line_bounds():
    evidence = _evidence_with_symbols(
        "app.py",
        functions=[
            {"name": "malformed_entry", "start_line": None, "end_line": None},
            {"name": "handle_request", "start_line": 10, "end_line": 30},
        ],
    )
    assert find_symbol_at_location(evidence, "app.py", 15) == "handle_request"
