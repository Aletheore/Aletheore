from aletheore.wiki_mapping import MAX_SYMBOLS_PER_FILE, build_cluster_briefs


def make_evidence(functions_per_file: int = 2) -> dict:
    return {
        "repository": {
            "modules": [
                {
                    "path": "auth/login.py",
                    "language": "python",
                    "symbols": {
                        "functions": [
                            {"name": f"fn_{i}", "start_line": i, "end_line": i + 2}
                            for i in range(functions_per_file)
                        ],
                        "classes": [{"name": "LoginHandler", "start_line": 50, "end_line": 80}],
                    },
                },
                {"path": "auth/tokens.py", "language": "python", "symbols": {}},
            ]
        },
        "architecture": {
            "clusters": [{"id": 0, "modules": ["auth/login.py", "auth/tokens.py"], "internal_edges": 1}]
        },
    }


def test_build_cluster_briefs_returns_one_brief_per_cluster():
    briefs = build_cluster_briefs(make_evidence())
    assert len(briefs) == 1
    assert briefs[0]["cluster_id"] == 0


def test_build_cluster_briefs_includes_all_member_files():
    briefs = build_cluster_briefs(make_evidence())
    paths = {f["path"] for f in briefs[0]["files"]}
    assert paths == {"auth/login.py", "auth/tokens.py"}


def test_build_cluster_briefs_extracts_functions_and_classes():
    briefs = build_cluster_briefs(make_evidence())
    login_file = next(f for f in briefs[0]["files"] if f["path"] == "auth/login.py")
    names = {s["name"] for s in login_file["key_symbols"]}
    assert "fn_0" in names
    assert "LoginHandler" in names
    kinds = {s["name"]: s["kind"] for s in login_file["key_symbols"]}
    assert kinds["LoginHandler"] == "class"
    assert kinds["fn_0"] == "function"


def test_build_cluster_briefs_caps_symbols_per_file():
    briefs = build_cluster_briefs(make_evidence(functions_per_file=MAX_SYMBOLS_PER_FILE + 10))
    login_file = next(f for f in briefs[0]["files"] if f["path"] == "auth/login.py")
    assert len(login_file["key_symbols"]) == MAX_SYMBOLS_PER_FILE


def test_build_cluster_briefs_skips_files_missing_from_modules():
    evidence = make_evidence()
    evidence["architecture"]["clusters"][0]["modules"].append("ghost/deleted.py")
    briefs = build_cluster_briefs(evidence)
    paths = {f["path"] for f in briefs[0]["files"]}
    assert "ghost/deleted.py" not in paths


def test_build_cluster_briefs_includes_a_deterministic_fallback_name():
    briefs = build_cluster_briefs(make_evidence())
    assert briefs[0]["fallback_name"] == "auth"


def test_build_cluster_briefs_handles_empty_evidence():
    assert build_cluster_briefs({"repository": {"modules": []}, "architecture": {"clusters": []}}) == []
