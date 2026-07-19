import json
from collections.abc import Callable

from aletheore.adapters.openai_compatible import OpenAICompatibleAdapter
from scan_worker.github_api import (
    MAX_CONTEXT_FILE_BYTES,
    MAX_CONTEXT_FILES,
    MAX_CONTEXT_TOTAL_BYTES,
    fetch_file_content,
)

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


def gather_file_context(
    client,
    token: str,
    repo_full_name: str,
    changed_files: list[str],
    head_ref: str,
) -> str:
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
