"""A second, whole-PR generation call added to per-file review.

Per-file review reads each changed file on its own, so it is blind to problems that only show when the whole PR is
read together (an interaction between two files, a change in one place that breaks a caller in another). One call
over the whole diff sees those. On held-out PRs its findings, merged into the per-file ones, raised recall; the call
costs about as much as one more per-file call. It must never break a review: any failure of the extra call leaves
the per-file findings exactly as they were, and it only ever runs on the per-file path.
"""
import json
from unittest.mock import MagicMock, patch

from scan_worker.flash_review import review_diff

_PER_FILE_MARKER = "ONE file from a larger"

_PATCH_A = "@@ -1,2 +1,2 @@\n+x = compute(1)\n+y = 2"
_PATCH_B = "@@ -1,2 +1,2 @@\n+z = helper(3)\n+w = 4"
_DIFF_PATCHES = (("a.py", _PATCH_A), ("b.py", _PATCH_B))
_DIFF = "\n\n".join(f"--- {name} ---\n{patch}" for name, patch in _DIFF_PATCHES)


def _yaml(issues: list[dict]) -> str:
    return json.dumps({"review": {"key_issues_to_review": issues}})


def _issue(file: str, line: int, text: str) -> dict:
    return {
        "relevant_file": file, "start_line": line, "end_line": line,
        "issue_header": text, "issue_content": text,
    }


def _adapter(per_file_issues: dict[str, list[dict]], whole_response):
    """Per-file prompts carry the per-file marker; the whole-PR prompt does not. Answering by prompt keeps the test
    independent of the order the worker threads happen to run in."""
    adapter = MagicMock()
    adapter.is_available.return_value = True

    def complete(system_prompt, user_prompt, cwd="."):
        if _PER_FILE_MARKER in user_prompt:
            filename = "a.py" if "--- a.py ---" in user_prompt.split("The rest of this pull request")[0] else "b.py"
            return _yaml(per_file_issues.get(filename, []))
        if isinstance(whole_response, Exception):
            raise whole_response
        return whole_response

    adapter.simple_completion.side_effect = complete
    return adapter


def _review(adapter, **kwargs):
    return review_diff(
        _DIFF, adapter=adapter, pr_title="t", diff_patches=_DIFF_PATCHES, per_file_completeness=True,
        cache_lookup=lambda diff: None, **kwargs,
    )


def _whole_calls(adapter):
    return [c for c in adapter.simple_completion.call_args_list if _PER_FILE_MARKER not in c.args[1]]


def test_whole_diff_pass_adds_findings_only_the_whole_pr_call_sees():
    adapter = _adapter(
        {"a.py": [_issue("a.py", 1, "compute() result is unused")]},
        _yaml([_issue("b.py", 1, "helper() was changed in a.py but this caller still passes the old arguments")]),
    )

    findings = _review(adapter, whole_diff_pass=True)

    assert {(f["file"], f["line"]) for f in findings} == {("a.py", 1), ("b.py", 1)}
    assert len(_whole_calls(adapter)) == 1


def test_no_extra_call_unless_asked_for():
    adapter = _adapter({"a.py": [_issue("a.py", 1, "compute() result is unused")]}, _yaml([]))

    findings = _review(adapter)

    assert [(f["file"], f["line"]) for f in findings] == [("a.py", 1)]
    assert _whole_calls(adapter) == []


def test_a_failed_whole_pr_call_leaves_the_per_file_findings_untouched():
    adapter = _adapter({"a.py": [_issue("a.py", 1, "compute() result is unused")]}, RuntimeError("provider down"))

    findings = _review(adapter, whole_diff_pass=True)

    assert [(f["file"], f["line"]) for f in findings] == [("a.py", 1)]


def test_an_unparseable_whole_pr_answer_leaves_the_per_file_findings_untouched():
    adapter = _adapter({"a.py": [_issue("a.py", 1, "compute() result is unused")]}, "not yaml, just prose")

    findings = _review(adapter, whole_diff_pass=True)

    assert [(f["file"], f["line"]) for f in findings] == [("a.py", 1)]


def test_the_same_finding_from_both_calls_is_reported_once():
    same = _issue("a.py", 1, "compute() result is unused")
    adapter = _adapter({"a.py": [same]}, _yaml([same, _issue("a.py", 2, "y is never read")]))

    findings = _review(adapter, whole_diff_pass=True)

    assert sorted((f["file"], f["line"]) for f in findings) == [("a.py", 1), ("a.py", 2)]


def test_a_different_problem_on_the_same_line_is_kept():
    adapter = _adapter(
        {"a.py": [_issue("a.py", 1, "compute() result is unused")]},
        _yaml([_issue("a.py", 1, "compute(1) is called with the wrong argument type")]),
    )

    findings = _review(adapter, whole_diff_pass=True)

    assert len(findings) == 2


def test_the_flag_is_ignored_without_per_file_review():
    adapter = MagicMock()
    adapter.is_available.return_value = True
    adapter.simple_completion.return_value = _yaml([_issue("a.py", 1, "compute() result is unused")])

    findings = review_diff(
        _DIFF, adapter=adapter, pr_title="t", diff_patches=_DIFF_PATCHES, per_file_completeness=False,
        cache_lookup=lambda diff: None, whole_diff_pass=True,
    )

    assert len(findings) == 1
    assert adapter.simple_completion.call_count == 1, "the single-call path already reads the whole PR once"


@patch("scan_worker.flash_review.flash_review_generation_adapter")
def test_the_extra_call_is_billed_through_the_callers_usage_callback(mock_adapter_factory):
    on_usage = MagicMock()
    mock_adapter_factory.return_value = _adapter({}, _yaml([]))

    review_diff(
        _DIFF, on_usage=on_usage, pr_title="t", diff_patches=_DIFF_PATCHES, per_file_completeness=True,
        cache_lookup=lambda diff: None, whole_diff_pass=True,
    )

    assert mock_adapter_factory.call_args.kwargs["on_usage"] is on_usage
