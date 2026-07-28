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
