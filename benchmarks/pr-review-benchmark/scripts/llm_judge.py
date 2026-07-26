"""Independent LLM scoring pass: builds a rubric prompt from
anonymized findings and ground truth, and parses the model's
structured response into scoring_template.py's score shape. The
judge model must be a different provider/family than whichever
model powers the tools under test in a given run (see
METHODOLOGY.md)."""
import json


def build_judge_prompt(ground_truth: dict, anonymized_findings: dict) -> str:
    return (
        "You are scoring anonymized code-review tool outputs against a known "
        "ground truth. Tools are labeled Tool A/B/C/D; you do not know their real "
        "identities.\n\n"
        f"Ground truth:\n{json.dumps(ground_truth, indent=2)}\n\n"
        f"Anonymized findings:\n{json.dumps(anonymized_findings, indent=2)}\n\n"
        "For each tool label, score:\n"
        '- recall: "hit", "partial", or "miss" against the ground truth issue\n'
        "- false_positives: list of findings that are not the ground truth issue and "
        "are not legitimate secondary issues\n"
        "- actionability: 1-5, is the finding specific enough to act on\n\n"
        "Respond with ONLY a JSON object: "
        '{"Tool A": {"recall": ..., "false_positives": [...], "actionability": ...}, ...}'
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
