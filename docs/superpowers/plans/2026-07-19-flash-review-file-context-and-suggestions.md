# Flash Review File Context & Suggestions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Flash Review the full content of each changed file (not just the diff hunk) so it can judge whether a change is actually correct in context, and let it optionally suggest a fix - while keeping the same evidence/citation discipline, the same shared spend cap, and closing every exploit surface a bigger, LLM-facing context window opens up.

**Architecture:** Additive only. `fetch_pr_diff` (existing, tested) is untouched. A new function fetches the list of changed filenames plus (capped) full file content at the head SHA via GitHub's Contents API. `flash_review.py`'s prompt and output schema gain one new optional field (`suggestion`). Nothing about the existing debounce, spend cap, or webhook wiring changes structurally - this plan explicitly confirms why.

**Tech Stack:** Python, httpx, DeepSeek `deepseek-v4-flash` (existing adapter, unchanged model/pricing).

## Global Constraints

- No exploit surface may grow. Every new file-fetching or LLM-facing path in this plan must be bounded (file count, file size, total bytes) - no unbounded loop over attacker-controlled PR contents.
- Never use GitHub's native ` ```suggestion ` code-fence syntax for the LLM's suggested fix. That syntax makes GitHub render a one-click "Commit suggestion" button that applies the text directly to the PR with no further review. Use a plain fenced code block (e.g. ` ```python `) so a human must read and manually apply it. This is a hard rule, not a style preference - it is the difference between "a suggestion" and "unreviewed LLM output with one-click write access."
- The suggestion field is display-only text. Nothing in this plan or its implementation may execute, apply, or auto-commit a suggestion. If a future task ever proposes that, it needs its own separate design/threat-model discussion - out of scope here.
- Do not change `fetch_pr_diff`'s existing signature or behavior - it's used by real, already-shipped, already-tested code. Add new functions alongside it.

---

### Task 1: Add a bounded, capped file-context fetcher to `github_api.py`

**Files:**
- Modify: `github-app/scan_worker/github_api.py`
- Test: `github-app/tests/test_github_api.py` (existing file - match its `httpx.MockTransport`-based patterns for the other functions in this file before writing new tests)

**Interfaces:**
- Produces: `fetch_pr_changed_files(client: httpx.Client, token: str, repo_full_name: str, base_ref: str, head_ref: str) -> list[str]` - returns just the list of changed filenames from the same compare API `fetch_pr_diff` already uses (one extra parse of the same response shape, not a new API call inside `fetch_pr_diff` itself - keep this as its own function so `fetch_pr_diff` remains untouched).
- Produces: `fetch_file_content(client: httpx.Client, token: str, repo_full_name: str, path: str, ref: str) -> str | None` - fetches one file's full content at `ref` via `GET /repos/{repo}/contents/{path}?ref={ref}`, base64-decodes it, and returns the text. Returns `None` (not an exception) if the file is missing, is a directory, or is not valid UTF-8 text (binary file) - all of these are expected, not error conditions, since a diff can touch binary files or files later deleted on head.

**Constants (define at module level in `github_api.py`):**
```python
MAX_CONTEXT_FILES = 15
MAX_CONTEXT_FILE_BYTES = 40_000
MAX_CONTEXT_TOTAL_BYTES = 200_000
```

These three caps exist specifically to bound cost and GitHub API rate-limit exposure against an attacker-controlled PR (e.g. a PR that touches hundreds of files, or one huge generated file) - see Task 3's usage of them for where they're enforced. Do not remove or raise them without discussing the cost/rate-limit tradeoff first.

- [ ] **Step 1: Write the failing tests**

```python
def test_fetch_pr_changed_files_returns_filenames():
    def handler(request):
        assert request.url.path == "/repos/octocat/hello-world/compare/aaa...bbb"
        return httpx.Response(
            200,
            json={"files": [{"filename": "app.py", "patch": "..."}, {"filename": "lib.py", "patch": "..."}]},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    result = fetch_pr_changed_files(client, "tok", "octocat/hello-world", "aaa", "bbb")

    assert result == ["app.py", "lib.py"]


def test_fetch_file_content_decodes_base64():
    import base64

    def handler(request):
        assert request.url.path == "/repos/octocat/hello-world/contents/app.py"
        assert request.url.params["ref"] == "bbb"
        content = base64.b64encode(b"print('hello')\n").decode()
        return httpx.Response(200, json={"content": content, "encoding": "base64", "size": 16})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    result = fetch_file_content(client, "tok", "octocat/hello-world", "app.py", "bbb")

    assert result == "print('hello')\n"


def test_fetch_file_content_returns_none_for_404():
    def handler(request):
        return httpx.Response(404, json={"message": "Not Found"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    result = fetch_file_content(client, "tok", "octocat/hello-world", "deleted.py", "bbb")

    assert result is None


def test_fetch_file_content_returns_none_for_binary():
    def handler(request):
        return httpx.Response(200, json={"content": "", "encoding": "none", "size": 12345})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    result = fetch_file_content(client, "tok", "octocat/hello-world", "image.png", "bbb")

    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd github-app && python -m pytest tests/test_github_api.py -k "changed_files or file_content" -v`
Expected: FAIL (functions don't exist yet)

- [ ] **Step 3: Write the implementation**

Add to `github-app/scan_worker/github_api.py`:

```python
import base64

MAX_CONTEXT_FILES = 15
MAX_CONTEXT_FILE_BYTES = 40_000
MAX_CONTEXT_TOTAL_BYTES = 200_000


def fetch_pr_changed_files(
    client: httpx.Client,
    token: str,
    repo_full_name: str,
    base_ref: str,
    head_ref: str,
) -> list[str]:
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }
    response = client.get(
        f"/repos/{repo_full_name}/compare/{base_ref}...{head_ref}",
        headers=headers,
    )
    response.raise_for_status()
    return [file["filename"] for file in response.json().get("files", [])]


def fetch_file_content(
    client: httpx.Client,
    token: str,
    repo_full_name: str,
    path: str,
    ref: str,
) -> str | None:
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }
    response = client.get(
        f"/repos/{repo_full_name}/contents/{path}",
        headers=headers,
        params={"ref": ref},
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    data = response.json()
    if data.get("encoding") != "base64" or not data.get("content"):
        return None
    try:
        return base64.b64decode(data["content"]).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd github-app && python -m pytest tests/test_github_api.py -k "changed_files or file_content" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add github-app/scan_worker/github_api.py github-app/tests/test_github_api.py
git commit -m "feat: add bounded per-file content fetching for Flash review context"
```

---

### Task 2: Add a capped context-gathering helper in `flash_review.py`

**Files:**
- Modify: `github-app/scan_worker/flash_review.py`
- Test: `github-app/tests/test_flash_review.py`

**Interfaces:**
- Consumes: `fetch_pr_changed_files`, `fetch_file_content`, `MAX_CONTEXT_FILES`, `MAX_CONTEXT_FILE_BYTES`, `MAX_CONTEXT_TOTAL_BYTES` from `scan_worker.github_api` (Task 1).
- Produces: `gather_file_context(client, token, repo_full_name, changed_files, head_ref) -> str` - builds a bounded text block of full file contents to append to the LLM prompt, enforcing all three caps. Never raises - any per-file fetch failure is skipped silently (matches `fetch_file_content`'s `None`-on-failure contract).

**Why this is a separate function from `review_diff`:** keeps `review_diff`'s existing, already-tested control flow (parse diff -> call LLM -> parse findings) unchanged; this is pure context-assembly, easy to unit test in isolation without mocking the LLM call.

- [ ] **Step 1: Write the failing tests**

```python
def test_gather_file_context_stops_at_max_files(monkeypatch):
    from scan_worker import flash_review

    monkeypatch.setattr(flash_review, "MAX_CONTEXT_FILES", 2)
    fetched = []

    def fake_fetch(client, token, repo, path, ref):
        fetched.append(path)
        return "x" * 10

    monkeypatch.setattr(flash_review, "fetch_file_content", fake_fetch)

    flash_review.gather_file_context(None, "tok", "o/r", ["a.py", "b.py", "c.py", "d.py"], "sha")

    assert fetched == ["a.py", "b.py"]


def test_gather_file_context_skips_oversized_files(monkeypatch):
    from scan_worker import flash_review

    monkeypatch.setattr(flash_review, "MAX_CONTEXT_FILE_BYTES", 5)

    def fake_fetch(client, token, repo, path, ref):
        return "way too long for the cap"

    monkeypatch.setattr(flash_review, "fetch_file_content", fake_fetch)

    result = flash_review.gather_file_context(None, "tok", "o/r", ["a.py"], "sha")

    assert "a.py" not in result


def test_gather_file_context_stops_at_total_byte_budget(monkeypatch):
    from scan_worker import flash_review

    monkeypatch.setattr(flash_review, "MAX_CONTEXT_FILES", 10)
    monkeypatch.setattr(flash_review, "MAX_CONTEXT_FILE_BYTES", 1000)
    monkeypatch.setattr(flash_review, "MAX_CONTEXT_TOTAL_BYTES", 15)

    def fake_fetch(client, token, repo, path, ref):
        return "0123456789"  # 10 bytes each

    monkeypatch.setattr(flash_review, "fetch_file_content", fake_fetch)

    result = flash_review.gather_file_context(None, "tok", "o/r", ["a.py", "b.py", "c.py"], "sha")

    # only the first file (10 bytes) fits under a 15-byte total budget
    assert result.count("0123456789") == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd github-app && python -m pytest tests/test_flash_review.py -k gather_file_context -v`
Expected: FAIL (function doesn't exist)

- [ ] **Step 3: Write the implementation**

Add to `github-app/scan_worker/flash_review.py`:

```python
from scan_worker.github_api import (
    MAX_CONTEXT_FILE_BYTES,
    MAX_CONTEXT_FILES,
    MAX_CONTEXT_TOTAL_BYTES,
    fetch_file_content,
)


def gather_file_context(client, token: str, repo_full_name: str, changed_files: list[str], head_ref: str) -> str:
    parts = []
    total_bytes = 0
    for path in changed_files[:MAX_CONTEXT_FILES]:
        content = fetch_file_content(client, token, repo_full_name, path, head_ref)
        if content is None:
            continue
        encoded_len = len(content.encode("utf-8"))
        if encoded_len > MAX_CONTEXT_FILE_BYTES:
            continue
        if total_bytes + encoded_len > MAX_CONTEXT_TOTAL_BYTES:
            break
        parts.append(f"--- full content: {path} ---\n{content}")
        total_bytes += encoded_len
    return "\n\n".join(parts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd github-app && python -m pytest tests/test_flash_review.py -k gather_file_context -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add github-app/scan_worker/flash_review.py github-app/tests/test_flash_review.py
git commit -m "feat: add bounded file-context gathering for Flash review"
```

---

### Task 3: Wire file context and the optional `suggestion` field into `review_diff`

**Files:**
- Modify: `github-app/scan_worker/flash_review.py`
- Test: `github-app/tests/test_flash_review.py`

**Context:** `review_diff(diff_text, on_usage=None)` currently takes only diff text. It needs a new optional parameter for the gathered file context, and the system prompt + output-parsing loop need to accept an optional `suggestion` string per finding, same validation rigor as the existing required fields (parse failure or wrong types => drop that finding, exactly like today's handling of missing `file`/`line`/`issue`).

**Hard rule (repeat from Global Constraints):** the prompt must explicitly instruct the model to return suggestions as **plain text**, never as a ` ```suggestion ` GitHub code-fence block - if the model outputs a suggestion, this code renders it as a plain fenced code block itself, not by passing through arbitrary markdown from the model. Do not let the model's raw text decide the fence type.

- [ ] **Step 1: Write the failing tests**

```python
@patch("scan_worker.flash_review.OpenAICompatibleAdapter")
def test_review_diff_includes_file_context_in_prompt(mock_adapter_class):
    mock_adapter = MagicMock()
    mock_adapter.simple_completion.return_value = "[]"
    mock_adapter_class.return_value = mock_adapter

    review_diff("some diff", file_context="--- full content: a.py ---\nprint(1)")

    call_args = mock_adapter.simple_completion.call_args
    assert "print(1)" in call_args.args[1] or "print(1)" in call_args.kwargs.get("user_prompt", "")


@patch("scan_worker.flash_review.OpenAICompatibleAdapter")
def test_review_diff_parses_optional_suggestion_field(mock_adapter_class):
    mock_adapter = MagicMock()
    mock_adapter.simple_completion.return_value = (
        '[{"file": "a.py", "line": 3, "issue": "off-by-one", '
        '"suggestion": "for i in range(n):"}]'
    )
    mock_adapter_class.return_value = mock_adapter

    findings = review_diff("some diff")

    assert findings == [
        {"file": "a.py", "line": 3, "issue": "off-by-one", "suggestion": "for i in range(n):"}
    ]


@patch("scan_worker.flash_review.OpenAICompatibleAdapter")
def test_review_diff_suggestion_field_is_optional(mock_adapter_class):
    mock_adapter = MagicMock()
    mock_adapter.simple_completion.return_value = (
        '[{"file": "a.py", "line": 3, "issue": "off-by-one"}]'
    )
    mock_adapter_class.return_value = mock_adapter

    findings = review_diff("some diff")

    assert findings == [{"file": "a.py", "line": 3, "issue": "off-by-one"}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd github-app && python -m pytest tests/test_flash_review.py -k "file_context or suggestion" -v`
Expected: FAIL

- [ ] **Step 3: Update the implementation**

Replace the `FLASH_REVIEW_SYSTEM_PROMPT` and `review_diff` in `github-app/scan_worker/flash_review.py`:

```python
FLASH_REVIEW_SYSTEM_PROMPT = """You are reviewing a code diff for potential issues. You may also be
given the full current content of the changed files for context. You must respond with ONLY a
JSON array of findings, no other text, no markdown code fences, no explanation outside the
array. Each finding must be an object with these fields: "file" (the exact file path shown in
the diff), "line" (the exact line number from the diff, as an integer), "issue" (a concrete,
specific, checkable description of an actual problem at that exact line - never a style
opinion, never "consider refactoring", never a vague concern that isn't tied to something you
can point at), and optionally "suggestion" (a short plain-text code fix for that exact issue,
with no markdown formatting or code fences of your own - if you have no concrete fix, omit this
field entirely rather than restating the issue). Only report a finding if you can name a
specific, real issue at a specific line. If you find nothing worth flagging, respond with
exactly: []"""


def review_diff(
    diff_text: str,
    file_context: str = "",
    on_usage: Callable[[int, int], None] | None = None,
) -> list[dict]:
    if not diff_text.strip():
        return []

    adapter = OpenAICompatibleAdapter(
        name="DeepSeek",
        base_url="https://api.deepseek.com",
        api_key_env_var="DEEPSEEK_API_KEY",
        model="deepseek-v4-flash",
        on_usage=on_usage,
    )
    user_prompt = diff_text if not file_context else f"{diff_text}\n\n{file_context}"
    raw_output = adapter.simple_completion(FLASH_REVIEW_SYSTEM_PROMPT, user_prompt, cwd=".")

    try:
        findings = json.loads(raw_output)
    except json.JSONDecodeError:
        return []

    if not isinstance(findings, list):
        return []

    valid: list[dict] = []
    for finding in findings:
        if not (
            isinstance(finding, dict)
            and isinstance(finding.get("file"), str)
            and finding.get("file")
            and isinstance(finding.get("line"), int)
            and isinstance(finding.get("issue"), str)
            and finding.get("issue")
        ):
            continue
        result = {"file": finding["file"], "line": finding["line"], "issue": finding["issue"]}
        suggestion = finding.get("suggestion")
        if isinstance(suggestion, str) and suggestion.strip():
            result["suggestion"] = suggestion.strip()
        valid.append(result)
    return valid
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd github-app && python -m pytest tests/test_flash_review.py -v`
Expected: PASS (all tests, including the pre-existing ones from before this task)

- [ ] **Step 5: Commit**

```bash
git add github-app/scan_worker/flash_review.py github-app/tests/test_flash_review.py
git commit -m "feat: add optional suggestion field and file-context prompt to Flash review"
```

---

### Task 4: Wire context-gathering into `run_flash_review_job` and render suggestions safely

**Files:**
- Modify: `github-app/scan_worker/jobs.py`
- Test: `github-app/tests/test_jobs.py`

**Context:** `run_flash_review_job` (in `github-app/scan_worker/jobs.py`) currently calls `fetch_pr_diff` then `review_diff(diff_text, on_usage=_on_usage)`. It needs to also call `fetch_pr_changed_files` + `gather_file_context` and pass the result through, and the comment-rendering loop needs to render `suggestion` as a plain fenced code block - **never** a ` ```suggestion ` fence.

**Why no change is needed to the spend cap or debounce logic:** `installation_spend_lock`, `get_llm_spend_this_month`, `monthly_cap_for_installation`, and `check_and_reserve_flash_review_attempt` all wrap the *entire* review call already (verified in the current code) - they don't care how large the prompt is, only how much the real API call actually cost afterward via `on_usage`/`record_llm_spend`, which already scales correctly with real token counts. The three new caps in Task 1/2 exist specifically so that "how large the prompt is" itself stays bounded - they are the actual cost/rate-limit control for this feature, the spend cap is the backstop behind them. Do not add a second, redundant cap here.

- [ ] **Step 1: Write the failing test**

Add to `github-app/tests/test_jobs.py`, near the existing `test_flash_review_job_posts_findings_and_updates_state`:

```python
def test_flash_review_job_renders_suggestion_as_plain_fence_not_github_suggestion_syntax(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "pro"}
    )
    monkeypatch.setattr(
        "scan_worker.jobs.check_and_reserve_flash_review_attempt", lambda *a, **k: True
    )
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.get_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_diff", lambda *a, **k: "--- app.py ---\n+bug")
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_changed_files", lambda *a, **k: ["app.py"])
    monkeypatch.setattr("scan_worker.jobs.gather_file_context", lambda *a, **k: "")
    monkeypatch.setattr(
        "scan_worker.jobs.review_diff",
        lambda diff_text, file_context="", on_usage=None: [
            {"file": "app.py", "line": 1, "issue": "unclosed handle", "suggestion": "f.close()"}
        ],
    )
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.set_last_reviewed_sha", lambda *a, **k: None)
    posted = {}
    monkeypatch.setattr(
        "scan_worker.jobs.upsert_pr_comment",
        lambda client, token, repo_full_name, pr_number, body, **kwargs: posted.update(body=body),
    )
    from scan_worker.jobs import run_flash_review_job

    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    assert "f.close()" in posted["body"]
    assert "```suggestion" not in posted["body"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd github-app && python -m pytest tests/test_jobs.py -k plain_fence -v`
Expected: FAIL (`fetch_pr_changed_files`/`gather_file_context` not imported/used yet in `jobs.py`)

- [ ] **Step 3: Update `run_flash_review_job`**

In `github-app/scan_worker/jobs.py`:

1. Add to the existing `from scan_worker.github_api import create_check_run, fetch_pr_diff, upsert_pr_comment` line: also import `fetch_pr_changed_files`.
2. Add to the existing `from scan_worker.flash_review import review_diff` line: also import `gather_file_context`.
3. Inside `run_flash_review_job`, right after the existing `diff_text = fetch_pr_diff(...)` line, add:

```python
        changed_files = fetch_pr_changed_files(client, token, repo_full_name, diff_base, head_sha)
        file_context = gather_file_context(client, token, repo_full_name, changed_files, head_sha)
```

4. Change the `review_diff(diff_text, on_usage=_on_usage)` call to:

```python
        findings = review_diff(diff_text, file_context=file_context, on_usage=_on_usage)
```

5. Update the comment-rendering loop (the `if findings:` block) to render suggestions as a plain fence:

```python
        if findings:
            lines = [f"{FLASH_REVIEW_MARKER}\n### Aletheore Flash review\n"]
            for finding in findings:
                lines.append(f"- `{finding['file']}:{finding['line']}` — {finding['issue']}")
                suggestion = finding.get("suggestion")
                if suggestion:
                    lines.append(f"  ```\n  {suggestion}\n  ```")
            body = "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd github-app && python -m pytest tests/test_jobs.py -k plain_fence -v`
Expected: PASS

- [ ] **Step 5: Run the full github-app suite**

Run: `cd github-app && python -m pytest -q`
Expected: all pass, no regressions in the existing Flash review / job tests.

- [ ] **Step 6: Commit**

```bash
git add github-app/scan_worker/jobs.py github-app/tests/test_jobs.py
git commit -m "feat: wire bounded file context and safe suggestion rendering into Flash review job"
```

---

### Task 5: Final review pass

- [ ] **Step 1: Re-read the three new constants and confirm they're still in place and not accidentally loosened**

`MAX_CONTEXT_FILES = 15`, `MAX_CONTEXT_FILE_BYTES = 40_000`, `MAX_CONTEXT_TOTAL_BYTES = 200_000` in `github-app/scan_worker/github_api.py`.

- [ ] **Step 2: Grep for the forbidden GitHub suggestion syntax**

Run: ``grep -rn '```suggestion' github-app/scan_worker/``
Expected: zero matches - confirms nothing anywhere in the implementation ever emits GitHub's auto-apply suggestion fence.

- [ ] **Step 3: Run the full suite one more time**

Run: `cd github-app && python -m pytest -q`
Expected: all pass.

- [ ] **Step 4: Do not deploy without explicit approval**

This plan does not include a production deploy step. Once implemented and tested, stop and report back - deploying (migrations, if any - this plan adds none - and container rebuild/restart on KVM4) requires separate explicit sign-off, per this project's established deploy protocol.
