"""Independent LLM scoring pass: builds a rubric prompt from each
tool's findings and ground truth, and parses the model's structured
response into scoring_template.py's score shape. The judge model must
be a different provider/family than whichever model powers the tools
under test in a given run (see METHODOLOGY.md).

This is a named comparison (see README.md) -- CodeRabbit was dropped
and there's no ToS-driven reason left to hide tool identity from the
judge, so findings_by_tool is keyed by each tool's real name
(aletheore, pr_agent, deepsource), not an anonymized Tool A/B/C/D
label."""
import json


def build_judge_prompt(ground_truth: dict, findings_by_tool: dict) -> str:
    return (
        "You are scoring code-review tool outputs against a known "
        "ground truth. Each tool is identified by its real name.\n\n"
        f"Ground truth:\n{json.dumps(ground_truth, indent=2)}\n\n"
        f"Findings by tool:\n{json.dumps(findings_by_tool, indent=2)}\n\n"
        "For each tool name, score:\n"
        '- recall: "hit", "partial", or "miss" against the ground truth issue\n'
        "- false_positives: list of findings that are not the ground truth issue and "
        "are not legitimate secondary issues\n"
        "- actionability: 1-5, is the finding specific enough to act on\n\n"
        "Respond with ONLY a JSON object: "
        '{"aletheore": {"recall": ..., "false_positives": [...], "actionability": ...}, ...}'
    )


def parse_judge_response(response_text: str) -> dict:
    start = response_text.find("{")
    end = response_text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("judge response did not contain a JSON object")
    parsed = json.loads(response_text[start:end + 1])
    for label, score in parsed.items():
        if score.get("recall") not in {"hit", "partial", "miss"}:
            raise ValueError(
                f"{label}: recall must be hit/partial/miss, got {score.get('recall')!r}"
            )
    return parsed


def call_judge_model(client, prompt: str, model: str) -> str:
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content
