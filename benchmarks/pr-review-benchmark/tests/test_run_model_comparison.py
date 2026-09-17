import subprocess

from scripts.run_model_comparison import (
    _diff_patches_from_diff,
    _production_diff_text,
)


def test_diff_patches_from_diff_strips_git_headers_to_the_pure_hunk_body():
    # Real bug this covers: passing the git-header-included raw section
    # (diff --git/index/--- a/+++ b/) into review_diff()/
    # find_semantic_regressions() meant _FILE_MARKER_RE (r"^--- (.+) ---$")
    # never matched anything, so every deterministic semantic check
    # silently returned zero findings on every case this script ever ran.
    raw_diff = (
        "diff --git a/src/foo.py b/src/foo.py\n"
        "index abc123..def456 100644\n"
        "--- a/src/foo.py\n"
        "+++ b/src/foo.py\n"
        "@@ -1,3 +1,4 @@\n"
        " import os\n"
        "+import sys\n"
        " \n"
        " def foo():\n"
    )
    patches = _diff_patches_from_diff(raw_diff)
    assert patches == (
        ("src/foo.py", "@@ -1,3 +1,4 @@\n import os\n+import sys\n \n def foo():"),
    )


def test_diff_patches_from_diff_handles_multiple_hunks_in_one_file():
    raw_diff = (
        "diff --git a/src/foo.py b/src/foo.py\n"
        "index abc123..def456 100644\n"
        "--- a/src/foo.py\n"
        "+++ b/src/foo.py\n"
        "@@ -1,3 +1,4 @@\n"
        " import os\n"
        "+import sys\n"
        " \n"
        " def foo():\n"
        "@@ -20,3 +21,4 @@ def bar():\n"
        "     return 1\n"
        "+    # trailing comment\n"
    )
    patches = _diff_patches_from_diff(raw_diff)
    assert len(patches) == 1
    file_path, body = patches[0]
    assert file_path == "src/foo.py"
    assert body.count("@@ -") == 2
    assert "import sys" in body
    assert "trailing comment" in body


def test_diff_patches_from_diff_handles_multiple_files():
    raw_diff = (
        "diff --git a/src/foo.py b/src/foo.py\n"
        "index abc123..def456 100644\n"
        "--- a/src/foo.py\n"
        "+++ b/src/foo.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-old\n"
        "+new\n"
        "diff --git a/src/baz.py b/src/baz.py\n"
        "index 111..222 100644\n"
        "--- a/src/baz.py\n"
        "+++ b/src/baz.py\n"
        "@@ -5,2 +5,3 @@ def baz():\n"
        "     pass\n"
        "+    return None\n"
    )
    patches = _diff_patches_from_diff(raw_diff)
    assert [f for f, _ in patches] == ["src/foo.py", "src/baz.py"]


def test_diff_patches_from_diff_gives_a_binary_file_an_empty_patch():
    # A binary file's "diff --git" section has no "@@" hunk at all - must
    # not crash, and must not swallow the file entirely (production's real
    # PR-files API also lists binary files with no patch body).
    raw_diff = (
        "diff --git a/src/binary.png b/src/binary.png\n"
        "new file mode 100644\n"
        "index 0000000..1234567\n"
        "Binary files /dev/null and b/src/binary.png differ\n"
    )
    patches = _diff_patches_from_diff(raw_diff)
    assert patches == (("src/binary.png", ""),)


def test_production_diff_text_matches_the_real_github_api_shape():
    # Real shape github_api.py's fetch_pr_diff builds: "--- {file} ---" per
    # file, each followed by its pure hunk body, joined with a blank line -
    # the exact format _FILE_MARKER_RE and find_semantic_regressions expect.
    patches = (
        ("src/foo.py", "@@ -1,1 +1,1 @@\n-old\n+new"),
        ("src/baz.py", "@@ -5,2 +5,3 @@ def baz():\n     pass\n+    return None"),
    )
    assert _production_diff_text(patches) == (
        "--- src/foo.py ---\n@@ -1,1 +1,1 @@\n-old\n+new\n\n"
        "--- src/baz.py ---\n@@ -5,2 +5,3 @@ def baz():\n     pass\n+    return None"
    )


def test_production_diff_text_round_trip_matches_file_marker_regex():
    import re

    file_marker_re = re.compile(r"^--- (.+) ---$")
    raw_diff = (
        "diff --git a/src/foo.py b/src/foo.py\n"
        "index abc123..def456 100644\n"
        "--- a/src/foo.py\n"
        "+++ b/src/foo.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-old\n"
        "+new\n"
    )
    patches = _diff_patches_from_diff(raw_diff)
    diff_text = _production_diff_text(patches)
    matched_files = [
        m.group(1) for line in diff_text.splitlines() if (m := file_marker_re.match(line))
    ]
    assert matched_files == ["src/foo.py"]
    # And the git header lines must be gone - only the real content remains.
    assert "diff --git" not in diff_text
    assert "index abc123" not in diff_text
    assert "+++ b/src/foo.py" not in diff_text


def test_diff_patches_from_diff_matches_a_real_git_diff_output():
    # End-to-end against an actual `git diff` invocation, not a hand-typed
    # fixture - real git header formatting (mode changes, index lines with
    # varying hash lengths) can differ subtly from what a synthetic string
    # assumes.
    import tempfile
    from pathlib import Path

    def _run(*args, cwd):
        return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout

    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
        (repo / "a.py").write_text("x = 1\ny = 2\n")
        subprocess.run(["git", "add", "a.py"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True)
        (repo / "a.py").write_text("x = 1\ny = 3\n")
        raw_diff = _run("git", "diff", cwd=repo)

    patches = _diff_patches_from_diff(raw_diff)
    assert len(patches) == 1
    file_path, body = patches[0]
    assert file_path == "a.py"
    assert body.startswith("@@")
    assert "diff --git" not in body
    assert "+++ b/a.py" not in body
    assert "-y = 2" in body
    assert "+y = 3" in body
