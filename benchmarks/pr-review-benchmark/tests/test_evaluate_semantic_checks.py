from scripts.evaluate_semantic_checks import git_diff_to_review_format


def test_git_diff_to_review_format_survives_a_removed_line_shaped_like_a_real_diff_header():
    # A removed source line reading "-- old value ---" renders, once
    # diffed, as the raw line "--- old value ---" - indistinguishable from
    # a real "--- a/path" header line without a positional guard. Real
    # headers (index/---/+++) only ever appear between "diff --git" and
    # that file's first "@@" hunk, never inside a hunk body -
    # seen_first_hunk scopes the filter so a hunk-body line matching that
    # same shape is kept instead of silently dropped, which is exactly
    # what happened before this fix: the removed line's content vanished
    # from the converted diff entirely, indistinguishable from it never
    # having existed.
    raw_diff = (
        "diff --git a/caller.py b/caller.py\n"
        "index abc123..def456 100644\n"
        "--- a/caller.py\n"
        "+++ b/caller.py\n"
        "@@ -1,3 +1,2 @@\n"
        "-shared_call()\n"
        "--- old value ---\n"
        "+shared_call()\n"
    )

    result = git_diff_to_review_format(raw_diff)

    assert "--- caller.py ---" in result
    assert "--- old value ---" in result
    assert "-shared_call()" in result
    assert "+shared_call()" in result


def test_git_diff_to_review_format_still_strips_real_header_lines():
    # Regression guard the other way: seen_first_hunk must not swallow the
    # real index/---/+++ header lines that legitimately precede the first
    # hunk - only content that appears after it should ever be kept.
    raw_diff = (
        "diff --git a/caller.py b/caller.py\n"
        "index abc123..def456 100644\n"
        "--- a/caller.py\n"
        "+++ b/caller.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-old()\n"
        "+new()\n"
    )

    result = git_diff_to_review_format(raw_diff)

    assert "index abc123..def456 100644" not in result
    assert "--- a/caller.py" not in result
    assert "+++ b/caller.py" not in result
    assert "-old()" in result
    assert "+new()" in result
