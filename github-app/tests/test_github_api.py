import base64

import httpx

import pytest

from aletheore.pr_comment import COMMENT_MARKER
from scan_worker.github_api import (
    BranchNotOwnedByAletheoreError,
    create_check_run,
    create_pull_request,
    ensure_branch_at,
    ensure_docs_pull_request,
    fetch_default_branch_and_head_sha,
    fetch_default_branch_head_sha,
    fetch_file_content,
    fetch_pr_changed_files,
    fetch_pr_diff,
    fetch_recent_commits_for_path,
    find_open_pull_request,
    upsert_pr_comment,
    upsert_repo_file,
    _trim_patch_context,
)


def test_creates_comment_when_none_exists():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(201, json={"id": 1})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    upsert_pr_comment(client, "token", "octocat/hello-world", 42, f"{COMMENT_MARKER}\nbody")
    assert [method for method, _ in calls] == ["GET", "POST"]


def test_updates_existing_comment():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        if request.method == "GET":
            return httpx.Response(200, json=[{"id": 99, "body": f"{COMMENT_MARKER}\nold body"}])
        return httpx.Response(200, json={"id": 99})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    upsert_pr_comment(client, "token", "octocat/hello-world", 42, f"{COMMENT_MARKER}\nnew body")
    assert [method for method, _ in calls] == ["GET", "PATCH"]


def test_upsert_pr_comment_uses_custom_marker_when_given():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.content))
        if request.method == "GET":
            return httpx.Response(200, json=[{"id": 1, "body": f"{COMMENT_MARKER}\nold diff"}])
        return httpx.Response(201, json={"id": 2})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    upsert_pr_comment(
        client,
        "token",
        "octocat/hello-world",
        42,
        "<!-- aletheore-audit -->\nnew audit",
        marker="<!-- aletheore-audit -->",
    )
    assert [method for method, _ in calls] == ["GET", "POST"]


def test_create_check_run_posts_expected_payload():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(201, json={"id": 1})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    create_check_run(client, "token", "octocat/hello-world", "abc123", "failure", "New secret found")

    assert len(calls) == 1
    request = calls[0]
    assert request.method == "POST"
    assert request.url.path == "/repos/octocat/hello-world/check-runs"
    import json as _json

    body = _json.loads(request.content)
    assert body["head_sha"] == "abc123"
    assert body["status"] == "completed"
    assert body["conclusion"] == "failure"
    assert body["name"] == "Aletheore secrets check"


def test_create_check_run_uses_custom_name_when_given():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.content)
        return httpx.Response(201, json={"id": 1})

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.github.com",
    )
    create_check_run(
        client,
        "token",
        "octocat/hello-world",
        "abc123",
        "neutral",
        "summary text",
        name="Aletheore regression risk",
    )

    import json as _json

    payload = _json.loads(calls[0])
    assert payload["name"] == "Aletheore regression risk"
    assert payload["conclusion"] == "neutral"


def test_fetch_pr_diff_concatenates_real_patches():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/repos/octocat/hello-world/compare/aaa...bbb":
            # image.png's omitted patch: the reconstruction fallback fetches
            # its content to try rebuilding a diff, and a real binary file's
            # response correctly has no usable content - see
            # test_fetch_pr_diff_records_omitted_files_when_reconstruction_also_fails
            # for this behavior tested directly.
            return httpx.Response(200, json={"content": "", "encoding": "none", "size": 1})
        return httpx.Response(
            200,
            json={
                "files": [
                    {
                        "filename": "app.py",
                        "patch": "@@ -1,2 +1,3 @@\n def hello():\n+    print('hi')\n     pass",
                    },
                    {"filename": "image.png", "patch": None},
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    diff_text = fetch_pr_diff(client, "fake-token", "octocat/hello-world", "aaa", "bbb")

    assert "app.py" in diff_text
    assert "print('hi')" in diff_text
    assert "image.png" not in diff_text
    assert diff_text.patches == (("app.py", "@@ -1,2 +1,3 @@\n def hello():\n+    print('hi')\n     pass"),)


def test_fetch_pr_diff_returns_empty_string_when_no_files_changed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"files": []})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    diff_text = fetch_pr_diff(client, "fake-token", "octocat/hello-world", "aaa", "bbb")

    assert diff_text == ""


def test_fetch_pr_diff_reconstructs_a_patch_github_omitted_for_a_large_text_file():
    # Real gap, confirmed live against benchmarks/pr-review-benchmark's own
    # case 007: GitHub's compare API returns patch=None for a large changed
    # text file (a minified/vendored bundle is exactly this shape), and
    # without this fallback the file - and whatever real bug it contains -
    # was silently invisible to Flash Review with no signal anything was
    # lost.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/octocat/hello-world/compare/aaa...bbb":
            return httpx.Response(
                200, json={"files": [{"filename": "lib/bundle.js", "patch": None}]}
            )
        assert request.url.path == "/repos/octocat/hello-world/contents/lib/bundle.js"
        ref = request.url.params["ref"]
        content = b"function f() {\n  return isPlainObject(v) || isArray(v);\n}\n"
        if ref == "bbb":
            content = b"function f() {\n  return isPlainObject(v);\n}\n"
        return httpx.Response(
            200,
            json={"content": base64.b64encode(content).decode(), "encoding": "base64", "size": len(content)},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    diff_text = fetch_pr_diff(client, "fake-token", "octocat/hello-world", "aaa", "bbb")

    assert "lib/bundle.js" in diff_text
    assert "isPlainObject(v);" in diff_text
    assert diff_text.omitted_files == ()
    assert len(diff_text.patches) == 1
    assert diff_text.patches[0][0] == "lib/bundle.js"


def test_fetch_pr_diff_records_omitted_files_when_reconstruction_also_fails():
    # A genuinely binary file: GitHub omits the patch, and the contents
    # fetch's own base64/encoding check correctly refuses to treat it as
    # text - reconstruction must fail closed here, not fabricate a diff.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/octocat/hello-world/compare/aaa...bbb":
            return httpx.Response(200, json={"files": [{"filename": "image.png", "patch": None}]})
        return httpx.Response(200, json={"content": "", "encoding": "none", "size": 12345})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    diff_text = fetch_pr_diff(client, "fake-token", "octocat/hello-world", "aaa", "bbb")

    assert diff_text == ""
    assert diff_text.patches == ()
    assert diff_text.omitted_files == ("image.png",)


def test_fetch_pr_diff_does_not_reconstruct_when_base_and_head_content_are_identical():
    # GitHub's own patch omission isn't always hiding a real change (e.g. a
    # pure mode/permission change with no content diff) - reconstructing an
    # empty diff would be pure noise in the prompt, so this must still land
    # in omitted_files rather than fabricate a no-op patch.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/octocat/hello-world/compare/aaa...bbb":
            return httpx.Response(200, json={"files": [{"filename": "script.sh", "patch": None}]})
        content = base64.b64encode(b"#!/bin/sh\necho hi\n").decode()
        return httpx.Response(200, json={"content": content, "encoding": "base64", "size": 18})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    diff_text = fetch_pr_diff(client, "fake-token", "octocat/hello-world", "aaa", "bbb")

    assert diff_text == ""
    assert diff_text.omitted_files == ("script.sh",)


def test_fetch_pr_diff_reconstructs_a_newly_added_file_github_omitted_a_patch_for():
    # base_ref genuinely has no such file (a brand-new large file) -
    # fetch_file_content correctly returns None for the base fetch (404),
    # and the whole head content is the real diff, not a reason to give up.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/octocat/hello-world/compare/aaa...bbb":
            return httpx.Response(200, json={"files": [{"filename": "new_big.py", "patch": None}]})
        ref = request.url.params["ref"]
        if ref == "aaa":
            return httpx.Response(404, json={"message": "Not Found"})
        content = b"def f():\n    return 1\n"
        return httpx.Response(
            200,
            json={"content": base64.b64encode(content).decode(), "encoding": "base64", "size": len(content)},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    diff_text = fetch_pr_diff(client, "fake-token", "octocat/hello-world", "aaa", "bbb")

    assert "new_big.py" in diff_text
    assert "def f():" in diff_text
    assert diff_text.omitted_files == ()


def test_trim_patch_context_shrinks_wide_context_to_one_line():
    """Real corpus measurement (25 real cases, real gpt-5.6-luna calls):
    trimming GitHub's default 3-line context to 1 held recall at parity
    and cut real cost ~5.7% - see DIFF_PROMPT_CONTEXT_LINES's own comment
    for the full real numbers this constant is grounded in."""
    patch = (
        "@@ -10,7 +10,8 @@ def foo():\n"
        " line8\n"
        " line9\n"
        " line10\n"
        "-old_line\n"
        "+new_line_a\n"
        "+new_line_b\n"
        " line13\n"
        " line14\n"
        " line15"
    )

    trimmed = _trim_patch_context(patch, context_lines=1)

    assert trimmed == (
        "@@ -12,3 +12,4 @@ def foo():\n"
        " line10\n"
        "-old_line\n"
        "+new_line_a\n"
        "+new_line_b\n"
        " line13"
    )


def test_trim_patch_context_splits_hunk_when_changes_are_far_apart():
    """Two change clusters with more untouched context between them than
    the trimmed window (1 line each side, so >2 lines of gap) become two
    real, separately-numbered hunks - matching real `git diff -U1`
    semantics, not one hunk with a stale middle section."""
    patch = (
        "@@ -1,10 +1,10 @@\n"
        " line1\n"
        "-line2\n"
        "+line2_new\n"
        " line3\n"
        " line4\n"
        " line5\n"
        " line6\n"
        " line7\n"
        "-line8\n"
        "+line8_new\n"
        " line9\n"
        " line10"
    )

    trimmed = _trim_patch_context(patch, context_lines=1)

    assert trimmed == (
        "@@ -1,3 +1,3 @@\n"
        " line1\n"
        "-line2\n"
        "+line2_new\n"
        " line3\n"
        "@@ -7,3 +7,3 @@\n"
        " line7\n"
        "-line8\n"
        "+line8_new\n"
        " line9"
    )


def test_trim_patch_context_preserves_every_change_line():
    """Whatever the context window does, no +/- line's content is ever
    lost - only surrounding unchanged lines are ever dropped."""
    patch = (
        "@@ -1,10 +1,10 @@\n"
        " a\n"
        " b\n"
        " c\n"
        "-removed_one\n"
        "+added_one\n"
        " d\n"
        " e\n"
        " f\n"
        "-removed_two\n"
        "+added_two\n"
        " g"
    )

    trimmed = _trim_patch_context(patch, context_lines=1)

    assert "-removed_one" in trimmed
    assert "+added_one" in trimmed
    assert "-removed_two" in trimmed
    assert "+added_two" in trimmed


def test_trim_patch_context_handles_pure_addition():
    """A zero-count old-side range ('-5,0', a pure insertion after old
    line 5) needs different header math than a real, non-empty range -
    difflib itself reports an empty range's position as 0, not 1, so the
    same -1 offset that converts a normal range would shift this one by
    one and silently misreport where the insertion really happened."""
    patch = "@@ -5,0 +6,2 @@ def foo():\n+new_line_1\n+new_line_2"

    trimmed = _trim_patch_context(patch, context_lines=1)

    assert trimmed == patch
    assert not any(line.startswith("-") for line in trimmed.splitlines()[1:])


def test_trim_patch_context_handles_pure_removal():
    """Symmetric case to the pure-addition test above, on the new side's
    zero-count range instead of the old side's."""
    patch = "@@ -5,2 +6,0 @@ def foo():\n-old_line_1\n-old_line_2"

    trimmed = _trim_patch_context(patch, context_lines=1)

    assert trimmed == patch


def test_fetch_pr_diff_packs_smallest_patches_first_under_a_total_byte_budget():
    # Real gap: fetch_pr_diff had no total size cap at all before this -
    # every file's patch got concatenated unconditionally. Packs smallest
    # patches first (matching order_changed_files_by_diff_size's rationale
    # in flash_review.py, see #473): a single huge low-value file
    # shouldn't be able to consume the whole budget and starve several
    # genuinely important small files around it - the same shape of miss
    # #473 fixed for evidence context. Demotes whatever doesn't fit
    # instead of leaving the budget uncapped.
    import scan_worker.github_api as github_api_module

    original_budget = github_api_module.MAX_DIFF_TOTAL_BYTES
    github_api_module.MAX_DIFF_TOTAL_BYTES = 100
    try:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "files": [
                        {"filename": "big.py", "patch": "@@ -1,1 +1,1 @@\n-" + "x" * 90 + "\n+" + "y" * 90},
                        {"filename": "small.py", "patch": "@@ -1,1 +1,1 @@\n-a\n+b"},
                        {"filename": "medium.py", "patch": "@@ -1,1 +1,1 @@\n-" + "z" * 40 + "\n+" + "w" * 40},
                    ]
                },
            )

        client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
        diff_text = fetch_pr_diff(client, "fake-token", "octocat/hello-world", "aaa", "bbb")

        # big.py (~183 bytes) alone exceeds the 100-byte budget, so it must
        # lose out to the two smaller files even though it's listed first
        # in GitHub's own order - proves the sort is smallest-first, not
        # just "whatever fits" in arbitrary order.
        included_names = {name for name, _ in diff_text.patches}
        assert "small.py" in included_names
        assert "big.py" in diff_text.budget_omitted_files
    finally:
        github_api_module.MAX_DIFF_TOTAL_BYTES = original_budget


def test_fetch_pr_diff_keeps_original_file_order_among_included_files():
    # The size-desc pass only decides which files make the cut - files
    # that DO fit should still render in GitHub's own diff order, not
    # size order, so the prompt reads like a normal diff.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "files": [
                    {"filename": "a.py", "patch": "@@ -1,1 +1,1 @@\n-1\n+2"},
                    {"filename": "b.py", "patch": "@@ -1,1 +1,1 @@\n-" + "x" * 20 + "\n+" + "y" * 20},
                    {"filename": "c.py", "patch": "@@ -1,1 +1,1 @@\n-3\n+4"},
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    diff_text = fetch_pr_diff(client, "fake-token", "octocat/hello-world", "aaa", "bbb")

    assert [name for name, _ in diff_text.patches] == ["a.py", "b.py", "c.py"]
    assert diff_text.budget_omitted_files == ()


def test_fetch_pr_diff_trims_prompt_text_but_keeps_original_patches_for_grounding():
    original_patch = (
        "@@ -1,7 +1,8 @@ def foo():\n"
        " line1\n"
        " line2\n"
        " line3\n"
        "-old_line\n"
        "+new_line\n"
        " line5\n"
        " line6\n"
        " line7"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"files": [{"filename": "app.py", "patch": original_patch}]}
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    diff_text = fetch_pr_diff(client, "fake-token", "octocat/hello-world", "aaa", "bbb")

    # Grounding must still validate against GitHub's own real patch,
    # untouched - only the prompt copy shrinks.
    assert diff_text.patches == (("app.py", original_patch),)
    # The prompt copy dropped the wide context (line1, line2, line6,
    # line7) but kept every real change and its immediate neighbor.
    assert "new_line" in diff_text
    assert "old_line" in diff_text
    assert "line3" in diff_text
    assert "line5" in diff_text
    # line1/line2 and line6/line7 sit outside the trimmed 1-line window -
    # dropped from the prompt copy, unlike the untouched original patch.
    assert "line1" not in diff_text
    assert "line7" not in diff_text
    assert "line1" in original_patch
    assert diff_text.count("\n") < original_patch.count("\n")


def test_fetch_pr_changed_files_returns_filenames():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/octocat/hello-world/compare/aaa...bbb"
        return httpx.Response(
            200,
            json={"files": [{"filename": "app.py", "patch": "..."}, {"filename": "lib.py", "patch": "..."}]},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    result = fetch_pr_changed_files(client, "tok", "octocat/hello-world", "aaa", "bbb")

    assert result == ["app.py", "lib.py"]


def test_fetch_file_content_decodes_base64():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/octocat/hello-world/contents/app.py"
        assert request.url.params["ref"] == "bbb"
        content = base64.b64encode(b"print('hello')\n").decode()
        return httpx.Response(200, json={"content": content, "encoding": "base64", "size": 16})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    result = fetch_file_content(client, "tok", "octocat/hello-world", "app.py", "bbb")

    assert result == "print('hello')\n"


def test_fetch_file_content_returns_none_for_404():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    result = fetch_file_content(client, "tok", "octocat/hello-world", "deleted.py", "bbb")

    assert result is None


def test_fetch_file_content_returns_none_for_binary():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"content": "", "encoding": "none", "size": 12345})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    result = fetch_file_content(client, "tok", "octocat/hello-world", "image.png", "bbb")

    assert result is None


def test_fetch_recent_commits_for_path_returns_shaped_commits():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/octocat/hello-world/commits"
        assert dict(request.url.params) == {
            "path": "controllers/user.controller.ts",
            "per_page": "1",
        }
        return httpx.Response(
            200,
            json=[
                {
                    "sha": "abc123def456",
                    "commit": {
                        "author": {
                            "name": "Ada Lovelace",
                            "date": "2026-07-23T10:00:00Z",
                        },
                        "message": "fix: guard against null user id\n\nlonger body here",
                    },
                }
            ],
        )

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.github.com",
    )
    commits = fetch_recent_commits_for_path(
        client,
        "token",
        "octocat/hello-world",
        "controllers/user.controller.ts",
    )

    assert commits == [
        {
            "sha": "abc123def456",
            "author": "Ada Lovelace",
            "date": "2026-07-23T10:00:00Z",
            "subject": "fix: guard against null user id",
        }
    ]


def test_fetch_recent_commits_for_path_respects_limit():
    def handler(request: httpx.Request) -> httpx.Response:
        assert dict(request.url.params)["per_page"] == "3"
        return httpx.Response(200, json=[])

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.github.com",
    )
    fetch_recent_commits_for_path(client, "token", "octocat/hello-world", "app.py", limit=3)


def test_fetch_recent_commits_for_path_returns_empty_list_for_404():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.github.com",
    )
    commits = fetch_recent_commits_for_path(
        client,
        "token",
        "octocat/hello-world",
        "deleted_file.py",
    )

    assert commits == []


def test_fetch_recent_commits_for_path_returns_empty_list_when_no_commits():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.github.com",
    )
    commits = fetch_recent_commits_for_path(client, "token", "octocat/hello-world", "app.py")

    assert commits == []


def test_fetch_default_branch_and_head_sha_returns_name_and_sha():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path.endswith("/repos/octocat/hello-world"):
            return httpx.Response(200, json={"default_branch": "trunk"})
        return httpx.Response(200, json={"sha": "abc123"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    assert fetch_default_branch_and_head_sha(client, "token", "octocat/hello-world") == ("trunk", "abc123")
    # One /repos/{repo} call, not two - this replaced two separately-called
    # functions that each fetched it for the same default_branch value.
    assert len(calls) == 2
    assert calls[0].endswith("/repos/octocat/hello-world")
    assert calls[1].endswith("/repos/octocat/hello-world/commits/trunk")


def test_fetch_default_branch_head_sha_returns_none_for_empty_repo_409():
    # Found live: a real installation's fresh, genuinely-empty repo (no
    # commits pushed yet) sent run_initial_scan_job an unhandled
    # HTTPStatusError, firing an ops alert for what's actually a normal,
    # expected state - GitHub's commits endpoint 409s specifically for a
    # repo with no commits at all, distinct from a 404 (missing repo/ref).
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/repos/octocat/hello-world"):
            return httpx.Response(200, json={"default_branch": "main"})
        return httpx.Response(409, json={"message": "Git Repository is empty."})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    assert fetch_default_branch_head_sha(client, "token", "octocat/hello-world") is None


def test_ensure_branch_at_creates_ref_when_missing():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        if request.method == "GET":
            return httpx.Response(404)
        return httpx.Response(201, json={"ref": "refs/heads/aletheore/docs-update"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    ensure_branch_at(client, "token", "octocat/hello-world", "aletheore/docs-update", "abc123", "aletheore[bot]")
    assert [method for method, _ in calls] == ["GET", "POST"]


def test_ensure_branch_at_force_updates_existing_ref_owned_by_us():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        if request.method == "GET" and str(request.url).endswith("/git/ref/heads/aletheore/docs-update"):
            return httpx.Response(200, json={"object": {"sha": "old-sha"}})
        if request.method == "GET" and str(request.url).endswith("/commits/old-sha"):
            return httpx.Response(200, json={"committer": {"login": "aletheore[bot]"}})
        assert request.method == "PATCH"
        import json as _json
        assert _json.loads(request.content) == {"sha": "new-sha", "force": True}
        return httpx.Response(200, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    ensure_branch_at(client, "token", "octocat/hello-world", "aletheore/docs-update", "new-sha", "aletheore[bot]")
    assert [method for method, _ in calls] == ["GET", "GET", "PATCH"]


def test_ensure_branch_at_refuses_to_force_push_when_not_owned_by_us():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and str(request.url).endswith("/git/ref/heads/aletheore/docs-update"):
            return httpx.Response(200, json={"object": {"sha": "old-sha"}})
        if request.method == "GET" and str(request.url).endswith("/commits/old-sha"):
            return httpx.Response(200, json={"committer": {"login": "some-contributor"}})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    with pytest.raises(BranchNotOwnedByAletheoreError):
        ensure_branch_at(client, "token", "octocat/hello-world", "aletheore/docs-update", "new-sha", "aletheore[bot]")


def test_upsert_repo_file_creates_when_no_existing_file():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        if request.method == "GET":
            return httpx.Response(404)
        import json as _json
        body = _json.loads(request.content)
        assert "sha" not in body
        assert body["branch"] == "aletheore/docs-update"
        return httpx.Response(201, json={"content": {"sha": "new"}})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    upsert_repo_file(
        client, "token", "octocat/hello-world", ".aletheore/docs/API.md",
        "aletheore/docs-update", "# Docs", "docs: update API reference",
    )
    assert [method for method, _ in calls] == ["GET", "PUT"]


def test_upsert_repo_file_includes_sha_when_file_exists():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"sha": "existing-sha"})
        import json as _json
        assert _json.loads(request.content)["sha"] == "existing-sha"
        return httpx.Response(200, json={"content": {"sha": "updated"}})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    upsert_repo_file(
        client, "token", "octocat/hello-world", ".aletheore/docs/API.md",
        "aletheore/docs-update", "# Docs v2", "docs: update API reference",
    )


def test_find_open_pull_request_returns_number_when_one_exists():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["head"] == "octocat:aletheore/docs-update"
        assert request.url.params["state"] == "open"
        return httpx.Response(200, json=[{"number": 7}])

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    assert find_open_pull_request(client, "token", "octocat/hello-world", "aletheore/docs-update") == 7


def test_find_open_pull_request_returns_none_when_no_open_pr():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    assert find_open_pull_request(client, "token", "octocat/hello-world", "aletheore/docs-update") is None


def test_create_pull_request_returns_new_pr_number():
    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json
        assert _json.loads(request.content) == {
            "title": "docs: update API reference",
            "head": "aletheore/docs-update",
            "base": "main",
            "body": "body",
        }
        return httpx.Response(201, json={"number": 42})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    number = create_pull_request(
        client, "token", "octocat/hello-world", "aletheore/docs-update", "main",
        "docs: update API reference", "body",
    )
    assert number == 42


def test_ensure_docs_pull_request_reuses_existing_open_pr():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(200, json=[{"number": 7}])

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    number = ensure_docs_pull_request(
        client, "token", "octocat/hello-world", "aletheore/docs-update", "main", "title", "body",
    )
    assert number == 7
    assert calls == ["GET"]


def test_ensure_docs_pull_request_creates_new_pr_when_none_open():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(201, json={"number": 9})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    number = ensure_docs_pull_request(
        client, "token", "octocat/hello-world", "aletheore/docs-update", "main", "title", "body",
    )
    assert number == 9
