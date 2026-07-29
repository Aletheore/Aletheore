from aletheore.signature_diff import (
    find_changed_signatures,
    find_regression_fence_violations,
    is_backward_compatible_change,
)


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
    # A genuinely breaking change: the new argument is required, so every
    # existing call site really does need updating. (An additive
    # `include_history=False` would be backward compatible and correctly
    # produces nothing - see the additive tests above.)
    (base / "billing.py").write_text("def get_billing(user_id):\n    return {}\n")
    (head / "billing.py").write_text("def get_billing(user_id, tenant):\n    return {}\n")

    old, new = _scan(base), _scan(head)

    assert find_changed_signatures(old, new) == [
        {
            "file": "billing.py",
            "function": "get_billing",
            "old_params": "(user_id)",
            "new_params": "(user_id, tenant)",
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
            "new_params": "(user_id, tenant)",
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


def test_is_backward_compatible_change_accepts_the_real_pr_97_signature():
    # The first real PR Regression Fencing ran on flagged this: a
    # keyword-only argument with a default was added, no caller could
    # break, and it still posted a Check Run naming a file that merely
    # imports the module.
    old = "( parsed: dict | None, evidence: dict, fetch_line_count: Callable[[str], int | None] | None = None, )"
    new = (
        "( parsed: dict | None, evidence: dict, "
        'fetch_line_count: Callable[[str], int | None] | None = None, *, context: str = "output", )'
    )

    assert is_backward_compatible_change(old, new) is True


def test_is_backward_compatible_change_accepts_added_defaults_and_variadics():
    assert is_backward_compatible_change("(a)", "(a, b=1)") is True
    assert is_backward_compatible_change("(a)", "(a, *args)") is True
    assert is_backward_compatible_change("(a)", "(a, **kwargs)") is True
    assert is_backward_compatible_change("(a)", "(a, b?: number)") is True
    # A default value containing a comma must not be split into two params.
    assert is_backward_compatible_change("(a)", "(a, b=(1, 2))") is True


def test_is_backward_compatible_change_rejects_anything_a_caller_can_trip_on():
    assert is_backward_compatible_change("(a)", "(a, b)") is False        # new required arg
    assert is_backward_compatible_change("(a, b)", "(a)") is False        # removed arg
    assert is_backward_compatible_change("(a)", "(b)") is False           # renamed
    assert is_backward_compatible_change("(a, b)", "(b, a)") is False     # reordered
    # Go has no default arguments, so an addition really does break callers
    # and must stay flagged.
    assert is_backward_compatible_change("(a int)", "(a int, b string)") is False


def test_is_backward_compatible_change_is_false_when_params_are_unknown():
    assert is_backward_compatible_change(None, "(a)") is False
    assert is_backward_compatible_change("(a)", None) is False


def test_find_changed_signatures_ignores_a_purely_additive_change():
    old = _evidence(
        [{"path": "billing.py", "symbols": {"functions": [{"name": "get_billing", "params": "(user_id)"}]}}]
    )
    new = _evidence(
        [
            {
                "path": "billing.py",
                "symbols": {"functions": [{"name": "get_billing", "params": "(user_id, verbose=False)"}]},
            }
        ]
    )

    assert find_changed_signatures(old, new) == []


def test_find_regression_fence_violations_stays_silent_for_an_additive_change():
    old = _evidence(
        [{"path": "billing.py", "symbols": {"functions": [{"name": "get_billing", "params": "(user_id)"}]}}]
    )
    new = _evidence(
        [
            {
                "path": "billing.py",
                "symbols": {"functions": [{"name": "get_billing", "params": "(user_id, verbose=False)"}]},
                "imported_by": ["reports/export.py"],
            }
        ]
    )

    assert find_regression_fence_violations(old, new, changed_files=["billing.py"]) == []
