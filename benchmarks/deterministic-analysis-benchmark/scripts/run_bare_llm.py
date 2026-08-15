"""Runs the three bare-LLM tests (hotspots, ownership, dead-code) against a
given model - a single simple_completion() call per test, no tools, no
multi-turn refinement. This is what produced data/model_outputs.md.

Usage: python run_bare_llm.py <input_dir> <model-name>
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

import os
for line in open(Path(__file__).resolve().parents[3] / ".env"):
    line = line.strip()
    if line.startswith("OPENAI_API_KEY="):
        os.environ["OPENAI_API_KEY"] = line.split("=", 1)[1]

from aletheore.adapters.openai_compatible import OpenAICompatibleAdapter

PROMPTS = {
    "hotspots_input.txt": (
        "Below is raw `git log --name-only --pretty=format:\"COMMIT %H\"` output "
        "from a real repository - every commit hash followed by the list of files "
        "it touched. Count, across ALL commits in this log, how many times each "
        "file path appears (i.e. how many commits touched it), and report the top "
        "10 files by that count, most-touched first, with exact counts."
    ),
    "ownership_input.txt": (
        "Below is raw `git log --pretty=format:\"%an|%ae\"` output from a real "
        "repository - one author name|email pair per commit. Count, across ALL "
        "lines, how many commits belong to each unique author (dedupe by email), "
        "and report the top 8 authors by commit count, most commits first, with "
        "exact counts and exact percentages of the total."
    ),
    "deadcode_input.txt": (
        "Below is every Python file in a real repository, each followed by its "
        "own import/from-import lines exactly as written in the source. Using "
        "ONLY this data, determine which files in this list are never imported "
        "by any other file in the list (i.e. no other file's import statements "
        "resolve to them) - these are candidate dead/unreachable modules. List "
        "every such file path."
    ),
}

SYSTEM_PROMPT = "You are a careful data analyst. Compute exact results from the data given; do not estimate, round, or guess about data not shown."


def main():
    in_dir = Path(sys.argv[1])
    model = sys.argv[2]

    adapter = OpenAICompatibleAdapter(
        name="OpenAI", base_url="https://api.openai.com/v1",
        api_key_env_var="OPENAI_API_KEY", model=model, extra_body=None,
    )

    for filename, instruction in PROMPTS.items():
        data = (in_dir / filename).read_text()
        prompt = f"{instruction}\n\n{data}"
        output = adapter.simple_completion(SYSTEM_PROMPT, prompt, cwd=".")
        print(f"\n{'=' * 20} {filename} ({model}) {'=' * 20}")
        print(output)


if __name__ == "__main__":
    main()
