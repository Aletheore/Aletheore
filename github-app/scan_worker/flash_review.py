import json
from collections.abc import Callable

from aletheore.adapters.openai_compatible import OpenAICompatibleAdapter

FLASH_REVIEW_SYSTEM_PROMPT = """You are reviewing a code diff for potential issues. You must
respond with ONLY a JSON array of findings, no other text, no markdown code fences, no
explanation outside the array. Each finding must be an object with exactly these fields:
"file" (the exact file path shown in the diff), "line" (the exact line number from the diff,
as an integer), and "issue" (a concrete, specific, checkable description of an actual problem
at that exact line - never a style opinion, never "consider refactoring", never a vague
concern that isn't tied to something you can point at). Only report a finding if you can name
a specific, real issue at a specific line. If you find nothing worth flagging, respond with
exactly: []"""


def review_diff(diff_text: str, on_usage: Callable[[int, int], None] | None = None) -> list[dict]:
    if not diff_text.strip():
        return []

    adapter = OpenAICompatibleAdapter(
        name="DeepSeek",
        base_url="https://api.deepseek.com",
        api_key_env_var="DEEPSEEK_API_KEY",
        model="deepseek-v4-flash",
        on_usage=on_usage,
    )
    raw_output = adapter.simple_completion(FLASH_REVIEW_SYSTEM_PROMPT, diff_text, cwd=".")

    try:
        findings = json.loads(raw_output)
    except json.JSONDecodeError:
        return []

    if not isinstance(findings, list):
        return []

    valid: list[dict] = []
    for finding in findings:
        if (
            isinstance(finding, dict)
            and isinstance(finding.get("file"), str)
            and finding.get("file")
            and isinstance(finding.get("line"), int)
            and isinstance(finding.get("issue"), str)
            and finding.get("issue")
        ):
            valid.append(
                {"file": finding["file"], "line": finding["line"], "issue": finding["issue"]}
            )
    return valid
