from aletheore.signature_diff import find_changed_signatures, find_regression_fence_violations


def _evidence(modules: list[dict]) -> dict:
    return {"repository": {"modules": modules}}


def test_find_changed_signatures_detects_a_changed_parameter_list():
    old = _evidence(
        [{"path": "billing.py", "symbols": {"functions": [{"name": "get_billing", "params": "(user_id)"}]}}]
    )
    new = _evidence(
        [
            {
                "path": "billing.py",
                "symbols": {"functions": [{"name": "get_billing", "params": "(user_id, include_history)"}]},
            }
        ]
    )

    changed = find_changed_signatures(old, new)

    assert changed == [
        {
            "file": "billing.py",
            "function": "get_billing",
            "old_params": "(user_id)",
            "new_params": "(user_id, include_history)",
        }
    ]


def test_find_changed_signatures_ignores_unchanged_functions():
    old = _evidence(
        [{"path": "billing.py", "symbols": {"functions": [{"name": "get_billing", "params": "(user_id)"}]}}]
    )
    new = _evidence(
        [{"path": "billing.py", "symbols": {"functions": [{"name": "get_billing", "params": "(user_id)"}]}}]
    )

    assert find_changed_signatures(old, new) == []


def test_find_changed_signatures_ignores_newly_added_functions():
    # A brand-new function isn't a "signature change" - there's no prior
    # signature to compare against, and it shows up in the diff comment's
    # own added-symbols section instead.
    old = _evidence([{"path": "billing.py", "symbols": {"functions": []}}])
    new = _evidence(
        [{"path": "billing.py", "symbols": {"functions": [{"name": "get_billing", "params": "(user_id)"}]}}]
    )

    assert find_changed_signatures(old, new) == []


def test_find_changed_signatures_ignores_removed_functions():
    old = _evidence(
        [{"path": "billing.py", "symbols": {"functions": [{"name": "get_billing", "params": "(user_id)"}]}}]
    )
    new = _evidence([{"path": "billing.py", "symbols": {"functions": []}}])

    assert find_changed_signatures(old, new) == []


def test_find_changed_signatures_ignores_a_different_file_with_the_same_function_name():
    old = _evidence(
        [{"path": "a.py", "symbols": {"functions": [{"name": "run", "params": "(x)"}]}}]
    )
    new = _evidence(
        [
            {"path": "a.py", "symbols": {"functions": [{"name": "run", "params": "(x)"}]}},
            {"path": "b.py", "symbols": {"functions": [{"name": "run", "params": "(x, y)"}]}},
        ]
    )

    assert find_changed_signatures(old, new) == []


def test_find_regression_fence_violations_flags_untouched_importers():
    old = _evidence(
        [
            {"path": "billing.py", "symbols": {"functions": [{"name": "get_billing", "params": "(user_id)"}]}},
            {"path": "auth/login.py", "symbols": {"functions": []}},
            {"path": "reports/export.py", "symbols": {"functions": []}},
        ]
    )
    new = _evidence(
        [
            {
                "path": "billing.py",
                "symbols": {"functions": [{"name": "get_billing", "params": "(user_id, include_history)"}]},
                "imported_by": ["auth/login.py", "reports/export.py"],
            },
            {"path": "auth/login.py", "symbols": {"functions": []}},
            {"path": "reports/export.py", "symbols": {"functions": []}},
        ]
    )

    violations = find_regression_fence_violations(old, new, changed_files=["billing.py", "auth/login.py"])

    assert violations == [
        {
            "file": "billing.py",
            "function": "get_billing",
            "old_params": "(user_id)",
            "new_params": "(user_id, include_history)",
            "untouched_callers": ["reports/export.py"],
        }
    ]


def test_find_regression_fence_violations_empty_when_all_importers_touched():
    old = _evidence(
        [{"path": "billing.py", "symbols": {"functions": [{"name": "get_billing", "params": "(user_id)"}]}}]
    )
    new = _evidence(
        [
            {
                "path": "billing.py",
                "symbols": {"functions": [{"name": "get_billing", "params": "(user_id, include_history)"}]},
                "imported_by": ["auth/login.py"],
            }
        ]
    )

    violations = find_regression_fence_violations(old, new, changed_files=["billing.py", "auth/login.py"])

    assert violations == []


def test_find_regression_fence_violations_empty_when_no_signature_changed():
    old = _evidence(
        [{"path": "billing.py", "symbols": {"functions": [{"name": "get_billing", "params": "(user_id)"}]}}]
    )
    new = _evidence(
        [
            {
                "path": "billing.py",
                "symbols": {"functions": [{"name": "get_billing", "params": "(user_id)"}]},
                "imported_by": ["auth/login.py"],
            }
        ]
    )

    assert find_regression_fence_violations(old, new, changed_files=["billing.py"]) == []


def test_find_regression_fence_violations_handles_missing_imported_by_key():
    old = _evidence(
        [{"path": "billing.py", "symbols": {"functions": [{"name": "get_billing", "params": "(user_id)"}]}}]
    )
    new = _evidence(
        [{"path": "billing.py", "symbols": {"functions": [{"name": "get_billing", "params": "(user_id, x)"}]}}]
    )

    assert find_regression_fence_violations(old, new, changed_files=["billing.py"]) == []


def _scan(root) -> dict:
    from pathlib import Path

    from aletheore.scanner.graph import build_module_graph

    modules, dependency_graph, unparseable = build_module_graph(Path(root))
    return {
        "repository": {
            "modules": modules,
            "dependency_graph": dependency_graph,
            "unparseable_files": unparseable,
        }
    }


def test_regression_fence_end_to_end_against_the_real_scanner(tmp_path):
    """Drives the whole path the Check Run actually uses - real tree-sitter
    parsing, real params capture, real imported_by resolution - rather than
    hand-written evidence dicts.

    Regression Fencing posts a Check Run that a repo can require in branch
    protection, so a false positive here blocks merges. Every other test in
    this file feeds it evidence shaped by hand, which cannot catch a
    mismatch between what the scanner really emits and what this module
    expects.
    """
    base = tmp_path / "base"
    head = tmp_path / "head"
    for root in (base, head):
        root.mkdir()
        (root / "report.py").write_text(
            "from billing import get_billing\n\ndef make_report(uid):\n    return get_billing(uid)\n"
        )
        (root / "admin.py").write_text(
            "from billing import get_billing\n\ndef admin_view(uid):\n    return get_billing(uid)\n"
        )
    (base / "billing.py").write_text("def get_billing(user_id):\n    return {}\n")
    (head / "billing.py").write_text(
        "def get_billing(user_id, include_history=False):\n    return {}\n"
    )

    old, new = _scan(base), _scan(head)

    assert find_changed_signatures(old, new) == [
        {
            "file": "billing.py",
            "function": "get_billing",
            "old_params": "(user_id)",
            "new_params": "(user_id, include_history=False)",
        }
    ]

    # Only report.py was updated alongside the signature change, so admin.py
    # is the one genuinely-stale importer.
    violations = find_regression_fence_violations(old, new, ["billing.py", "report.py"])
    assert violations == [
        {
            "file": "billing.py",
            "function": "get_billing",
            "old_params": "(user_id)",
            "new_params": "(user_id, include_history=False)",
            "untouched_callers": ["admin.py"],
        }
    ]

    # Updating every importer in the same PR must not block the merge.
    assert (
        find_regression_fence_violations(old, new, ["billing.py", "report.py", "admin.py"]) == []
    )


def test_regression_fence_stays_silent_when_only_a_body_changes(tmp_path):
    # The most likely false-positive source for a merge-blocking check:
    # ordinary edits that don't touch any signature must produce nothing.
    base = tmp_path / "base"
    head = tmp_path / "head"
    for root in (base, head):
        root.mkdir()
        (root / "report.py").write_text("from billing import get_billing\n")
    (base / "billing.py").write_text("def get_billing(user_id):\n    return 1\n")
    (head / "billing.py").write_text("def get_billing(user_id):\n    return 2\n")

    old, new = _scan(base), _scan(head)

    assert find_changed_signatures(old, new) == []
    assert find_regression_fence_violations(old, new, ["billing.py"]) == []
