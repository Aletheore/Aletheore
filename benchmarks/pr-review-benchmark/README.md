# Aletheore PR-Review Benchmark — Runbook

This benchmark measures Aletheore's grounding claim ("every finding traces back to evidence") against real competitors on real and reconstructed bugs, scored blind. This is a **named, 3-way comparison**: Aletheore (hosted Flash Review) vs. Qodo/PR-Agent vs. DeepSource. This is a semi-automated, otherwise manual process; the runbook below describes every step.

CodeRabbit was dropped from this comparison entirely: its ToS bans publishing benchmark results without consent, and its Free-plan rate limits made a fair, repeatable comparison impractical. Since there's no CodeRabbit output to anonymize for legal reasons, this comparison runs **named, not blind-labeled** — every tool's output is scored under its real name. (Blind manual scoring of the ground-truth *judgment* — recall/false-positives/actionability — is still a good practice on its own merits, but tool identity no longer needs to be hidden from the scorer.)

Aletheore's real comparable feature is its hosted GitHub App's **Flash Review** (`deepseek-v4-flash`, hardcoded server-side), which posts findings as a PR comment from `aletheore[bot]` — not the CLI's whole-repo `aletheore audit` command, which is a different, non-comparable feature.

See `METHODOLOGY.md` for runtime values (model versions, dates, provider versions) recorded at execution time.

See the full design spec in `docs/superpowers/specs/2026-07-26-aletheore-pr-review-benchmark-design.md`.

## Prerequisites

- Python 3.10+
- `git`, `gh` (GitHub CLI)
- Aletheore's GitHub App installed (paid plan) on the scratch repo, so Flash Review runs automatically on each PR
- DeepSource's GitHub App installed and configured on the scratch repo
- PR-Agent installed (`python -m pr_agent.cli`) — see PR-Agent setup section below
- DeepSeek API key (`DEEPSEEK_API_KEY` environment variable) — see model parity section below
- GitHub API token for accessing the scratch repo (typically `gh` auto-handles this)

## Step 0: Prepare Test Cases (One-Time Setup)

The benchmark corpus lives in `benchmarks/pr-review-benchmark/cases/`, with one subdirectory per case. Cases must be authored before running the pipeline; see `docs/superpowers/specs/2026-07-26-aletheore-pr-review-benchmark-design.md` → "Test Corpus & Ground Truth" for the full procedure, and refer to `cases/001-flask-cli-key-quote/` as a worked example.

**Each case directory must contain:**
- `repo.txt` — initially two lines: `repo_url=<https://github.com/...>` and `base_commit=<commit-hash>`. After opening the real PR in Step 1, append a third line: `pr_url=<the PR URL>` (see Step 3 for how to populate this after running tools)
- `pr.diff` — the PR diff (for real bugs, this is the *inverse* of the fix, reintroducing the bug)
- `ground_truth.yaml` — structured metadata: `case_id`, `language`, `category` (one of `real_bug_fix`, `injected_bug`, `clean`), `bug_type`, `expected_file`, `expected_line`, `fix_reference` (URL to the fix or `null`), and `description`
- `ground_truth.md` — 2–4 sentence prose explanation for the published report

For case authoring details, see the task 10 brief in `.superpowers/sdd/2026-07-26-aletheore-pr-review-benchmark-implementation-plan/task-10-brief.md`.

## Step 1: Open Real PR on Scratch Repo

The benchmark runs against real PRs opened on a **scratch repo** that you control, letting the tools see a genuine PR context (Aletheore's Flash Review and DeepSource are both hosted GitHub Apps that react to a real PR event).

**Scratch repo:** `https://github.com/ArihantK15/proctor-browser` (user controls; Aletheore's GitHub App — paid plan — and DeepSource's GitHub App are both already installed on it)

For each case in the corpus:

1. Check out the case's base commit locally:
   ```bash
   git clone <case repo_url> /tmp/scratch
   cd /tmp/scratch
   git checkout <base_commit>
   ```

2. Apply the case's PR diff:
   ```bash
   git apply <path-to-benchmarks/pr-review-benchmark/cases/<case-id>/pr.diff>
   ```

3. Create a feature branch and push to the scratch repo:
   ```bash
   git checkout -b case-<case-id>
   git add .
   git commit -m "test case: <case-id>"
   git push -u origin case-<case-id>
   ```

4. Open a PR on the scratch repo via `gh`:
   ```bash
   gh pr create --repo ArihantK15/proctor-browser --base main --head ArihantK15:case-<case-id> \
     --title "Test Case: <case-id>" \
     --body "Benchmark case from pr-review-benchmark; see ground_truth.md for the real issue."
   ```

   Note the PR URL from the output; you'll need it for the Aletheore, PR-Agent, and DeepSource steps below.

5. Wait for Aletheore's Flash Review and DeepSource to post their reviews (both GitHub Apps do this automatically; may take a minute or two).

## Step 2: Set Up Models — Model Parity (DeepSeek)

The benchmark aims for **model parity** between Aletheore and PR-Agent: both use the same underlying LLM so the comparison isolates grounding architecture, not which LLM is smarter. This run uses **DeepSeek** as the shared backend — specifically `deepseek-v4-flash`.

### Environment Variables

Set once in your shell, or add to `.env` and `source` it:

```bash
export DEEPSEEK_API_KEY="<your-deepseek-api-key>"
```

### Aletheore Configuration

**Nothing to configure.** Aletheore's comparable feature in this benchmark is the hosted GitHub App's Flash Review, not the CLI's `audit` command — it runs automatically the moment a PR is opened on a repo with the app installed, and its model is hardcoded server-side to `deepseek-v4-flash` (see `github-app/scan_worker/model_tiers.py`). There is no BYOK model config for it as of 2026-07-26. Model parity with PR-Agent is achieved by pointing PR-Agent at the same model, below.

### PR-Agent Configuration

`scripts/adapters.py`'s `pr_agent_adapter()` already invokes PR-Agent with `--config.model=deepseek/deepseek-v4-flash` for parity with Aletheore's Flash Review. PR-Agent still needs the DeepSeek key available as its OpenAI-compatible credential:

```bash
export OPENAI_API_KEY="$DEEPSEEK_API_KEY"
export OPENAI_API_BASE="https://api.deepseek.com/v1"
```

Refer to PR-Agent's official documentation if `--config.model` needs a different key format for your installed version.

## Step 3: Run the Case Pipeline

For each case, run `scripts/run_case.py`. This orchestrates:
1. Clone the repo at `base_commit`
2. Apply the case's `pr.diff`
3. Invoke each adapter (Aletheore, PR-Agent, DeepSource)
4. Normalize raw output to the common schema
5. Run the automated grounding check (file/line verification)
6. Store results in `results/raw/`, `results/grounding/`

### Collecting Tool Outputs

The adapters in `scripts/adapters.py` accept injected callables so you can fetch each tool's real output without hardcoding API logic in the adapter itself. Populate these as follows:

#### PR-Agent

Runs locally via `subprocess.run()`, but its `review` command posts its output as a PR comment rather than printing it to stdout — `pr_agent_adapter()` takes a `fetch_review` callable to retrieve that comment after invoking the CLI. See the wiring example below.

#### Aletheore

Aletheore's Flash Review posts as a plain PR comment (not a per-line review comment) from `aletheore[bot]`, containing inline `file:line` citations in its prose. Fetch it via the **issue comments** endpoint (PRs are issues in the GitHub API) and let `aletheore_adapter()` filter to the bot's own comments:

```python
import re
import subprocess
import json

def fetch_issue_comments(pr_url):
    match = re.match(r"https://github.com/(.+)/(.+)/pull/(\d+)", pr_url)
    owner, repo, number = match.groups()
    result = subprocess.run(
        ["gh", "api", f"repos/{owner}/{repo}/issues/{number}/comments"],
        capture_output=True, text=True, check=True
    )
    return json.loads(result.stdout)
```

#### DeepSource

1. **DeepSource's GitHub App is already installed** on the scratch repo and will post reviews to each PR automatically.

2. **Fetch DeepSource's PR comments**: unlike Aletheore's, DeepSource posts ordinary GitHub PR *review* comments (path/line/body attached to a specific diff line) — fetch via the **PR review comments** endpoint and let `deepsource_adapter()` filter to the bot's own comments:

   ```python
   def fetch_review_comments(pr_url):
       match = re.match(r"https://github.com/(.+)/(.+)/pull/(\d+)", pr_url)
       owner, repo, number = match.groups()
       result = subprocess.run(
           ["gh", "api", f"repos/{owner}/{repo}/pulls/{number}/comments"],
           capture_output=True, text=True, check=True
       )
       return json.loads(result.stdout)
   ```

3. **Note on output storage**: All intermediate results (raw tool outputs, grounding checks, manual scores, LLM judge scores) are **working state, not published artifacts**. The entire `benchmarks/pr-review-benchmark/results/` directory is `.gitignore`d and stays local to your machine.

   The published deliverables are only:
   - `benchmarks/pr-review-benchmark/REPORT.md` — the final scored summary
   - `benchmarks/pr-review-benchmark/METHODOLOGY.md` — runtime metadata
   - `benchmarks/pr-review-benchmark/cases/` — the test corpus (contains no tool outputs, only ground truth)

   Since every tool in this comparison is named (no ToS-restricted vendor), the local-only convention for `results/` is now purely to avoid publishing noisy intermediate/raw data — not a legal requirement.

### Running the Pipeline

Create a script (or run interactively) that:

```python
import json
import re
import subprocess
from pathlib import Path
from scripts.run_case import run_case
from scripts.adapters import aletheore_adapter, pr_agent_adapter, deepsource_adapter
from scripts.normalize import normalize_aletheore, normalize_pr_agent, normalize_deepsource

def _pr_number_and_repo(pr_url):
    match = re.match(r"https://github.com/(.+)/(.+)/pull/(\d+)", pr_url)
    return match.group(1), match.group(2), match.group(3)

def fetch_issue_comments(pr_url):
    # Aletheore's Flash Review posts a plain PR/issue comment (see README's
    # "Aletheore" subsection above for why this differs from DeepSource's).
    owner, repo, number = _pr_number_and_repo(pr_url)
    result = subprocess.run(
        ["gh", "api", f"repos/{owner}/{repo}/issues/{number}/comments"],
        capture_output=True, text=True, check=True
    )
    return json.loads(result.stdout)

def fetch_review_comments(pr_url):
    # DeepSource posts ordinary GitHub PR *review* comments (path/line/body).
    owner, repo, number = _pr_number_and_repo(pr_url)
    result = subprocess.run(
        ["gh", "api", f"repos/{owner}/{repo}/pulls/{number}/comments"],
        capture_output=True, text=True, check=True
    )
    return json.loads(result.stdout)

def fetch_pr_agent_review(pr_url):
    # PR-Agent's `review` command also posts its output as a PR comment
    # rather than printing JSON to stdout -- see scripts/normalize.py's
    # normalize_pr_agent for the real comment shape.
    owner, repo, number = _pr_number_and_repo(pr_url)
    result = subprocess.run(
        ["gh", "api", f"repos/{owner}/{repo}/issues/{number}/comments"],
        capture_output=True, text=True, check=True
    )
    comments = json.loads(result.stdout)
    pr_agent_comment = next(c for c in comments if "PR Reviewer Guide" in c.get("body", ""))
    changed_files_result = subprocess.run(
        ["gh", "api", f"repos/{owner}/{repo}/pulls/{number}/files", "--jq", ".[].filename"],
        capture_output=True, text=True, check=True
    )
    changed_files = changed_files_result.stdout.splitlines()
    return {"comment_body": pr_agent_comment["body"], "changed_files": changed_files}

# Run one case
case_dir = Path("benchmarks/pr-review-benchmark/cases/001-flask-cli-key-quote")
workdir = Path("/tmp/pr-review-benchmark/work")
results_dir = Path("benchmarks/pr-review-benchmark/results")

adapters = {
    "aletheore": lambda checkout_dir, case: aletheore_adapter(checkout_dir, case, fetch_pr_comments=fetch_issue_comments),
    "pr_agent": lambda checkout_dir, case: pr_agent_adapter(checkout_dir, case, fetch_review=fetch_pr_agent_review),
    "deepsource": lambda checkout_dir, case: deepsource_adapter(checkout_dir, case, fetch_pr_comments=fetch_review_comments),
}

normalizers = {
    "aletheore": normalize_aletheore,
    "pr_agent": normalize_pr_agent,
    "deepsource": normalize_deepsource,
}

result = run_case(case_dir, workdir, results_dir, adapters, normalizers)
print(f"Case {result['case_id']} processed; results in {results_dir}/raw/{result['case_id']}/")
```

**Output structure after running all cases:**
```
benchmarks/pr-review-benchmark/results/
  raw/<case-id>/              # (all local/uncommitted working state)
    aletheore.json            # tool's raw output (bot-filtered PR comments)
    pr_agent.json
    deepsource.json
  grounding/<case-id>/        # (all local/uncommitted working state)
    aletheore.json            # grounding check: {total_findings, verified, unverified, grounding_rate}
    pr_agent.json
    deepsource.json
```

## Step 4: Manual Scoring

This is a **named** comparison, not a blind-labeled one: since CodeRabbit is out of scope entirely, there's no ToS-driven reason to hide tool identity, so findings are scored under each tool's real name (`aletheore`, `pr_agent`, `deepsource`). Read the findings in `results/raw/<case-id>/` (normalized via `results/grounding/<case-id>/` for the automated file/line check) alongside the ground truth in `cases/<case-id>/ground_truth.yaml` and `ground_truth.md`, and score each tool independently.

For each case, create `results/scored/<case-id>.yaml`:

```yaml
case_id: 001-flask-cli-key-quote
scores:
  aletheore:
    recall: "hit"  # or "partial" / "miss"
    false_positives: []  # list of findings that are NOT the ground-truth issue
    actionability: 5  # 1–5 scale
  pr_agent:
    recall: "partial"
    false_positives:
      - "suggestion about quote handling in a different context, not the _validate_key bug"
    actionability: 3
  deepsource:
    recall: "miss"
    false_positives: []
    actionability: null
```

**Scoring definitions:**
- **recall**: Did the tool find the ground-truth issue?
  - `"hit"` — yes, the finding directly points to the buggy line/function
  - `"partial"` — yes, but the finding is vague or requires reading between the lines
  - `"miss"` — no, the tool didn't flag anything about the bug
- **false_positives**: List any findings that are NOT the ground-truth issue and are NOT legitimate secondary issues (e.g., related but distinct bugs). Include the finding message for clarity.
- **actionability**: On a 1–5 scale, is the finding specific enough to act on?
  - 1 = generic advice ("improve error handling")
  - 5 = actionable ("fix the missing double quote on line 798")
  - `null` if no finding was made (no actionability to score)

### Template Generation

To generate blank scorecards for all cases:

```python
from pathlib import Path
from scripts.scoring_template import write_blank_scorecard

results_dir = Path("benchmarks/pr-review-benchmark/results")
TOOL_NAMES = ["aletheore", "pr_agent", "deepsource"]
for raw_dir in sorted((results_dir / "raw").glob("*")):
    case_id = raw_dir.name
    out_path = results_dir / "scored" / f"{case_id}.yaml"
    write_blank_scorecard(case_id, TOOL_NAMES, out_path)
    print(f"Created {out_path}")
```

## Step 5: LLM Judge — Claude Subagent

The benchmark includes a second, independent scoring pass by an LLM judge to measure human/LLM agreement and catch reviewer fatigue. The judge must be a **different provider/family** than the models under test (Aletheore and PR-Agent both use DeepSeek in this run, so the judge is Claude, a different provider). It is given real tool names, not anonymized labels — this is a named comparison (see Step 4).

**Important: The judge is NOT an API call.** Instead, this is a **Claude subagent dispatch** from a Claude Code session (your CLI agent acts as the controller):

1. **Build the judge prompt** for a case using `scripts/llm_judge.py`:
   ```python
   from scripts.llm_judge import build_judge_prompt
   import json
   import yaml
   from pathlib import Path
   
   case_id = "001-flask-cli-key-quote"
   ground_truth = yaml.safe_load((Path("benchmarks/pr-review-benchmark/cases") / case_id / "ground_truth.yaml").read_text())
   
   raw_dir = Path("benchmarks/pr-review-benchmark/results/raw") / case_id
   findings_by_tool = {}
   for tool_file in sorted(raw_dir.glob("*.json")):
       findings_by_tool[tool_file.stem] = json.loads(tool_file.read_text())
   
   prompt = build_judge_prompt(ground_truth, findings_by_tool)
   ```

2. **Dispatch a fresh Claude subagent** with this prompt:
   - In your Claude Code CLI session, use the Agent tool to spawn a `"general-purpose"` or similar fresh agent
   - Pass the full `prompt` from step 1 as the agent's task
   - The subagent has no information except the prompt; it does not see this runbook or the case structure

3. **Capture the subagent's response** and parse it:
   ```python
   from scripts.llm_judge import parse_judge_response
   
   # Assume you received the judge's response text
   judge_response = """... the subagent's JSON response ..."""
   scores = parse_judge_response(judge_response)
   
   # Save to results/llm_judged/<case_id>.json
   import json
   out_path = Path("benchmarks/pr-review-benchmark/results/llm_judged") / f"{case_id}.json"
   out_path.parent.mkdir(parents=True, exist_ok=True)
   out_path.write_text(json.dumps(scores, indent=2))
   ```

**Note**: The design originally included an optional `scripts/llm_judge.py` → `call_judge_model()` function that uses an OpenAI-style client. This is **not used in the real run** (it's tested code but doesn't execute here); the blind subagent dispatch above is the actual procedure for the benchmark.

## Step 6: Aggregate Scores & Build Report

After scoring all cases (both manual and LLM), aggregate the results and render the final report.

### Aggregate Scores

```python
from scripts.aggregate import load_manual_scores, load_llm_scores, load_case_label_maps, build_scorecard
from pathlib import Path
import json

results_dir = Path("benchmarks/pr-review-benchmark/results")

manual_scores = load_manual_scores(results_dir)
llm_scores = load_llm_scores(results_dir)
# No results/sealed/ directory exists in this named (non-anonymized) run, so
# this returns {} and build_scorecard falls back to using each scored.yaml's
# keys directly as the real tool name (see scripts/aggregate.py's
# `label_to_tool.get(label, label)` fallback).
case_label_maps = load_case_label_maps(results_dir)

scorecard = build_scorecard(manual_scores, llm_scores, case_label_maps)

# Save scorecard
scorecard_path = results_dir / "scorecard.json"
scorecard_path.write_text(json.dumps(scorecard, indent=2))
print(f"Scorecard saved to {scorecard_path}")
```

### Load Grounding Results & Merge

```python
from scripts.build_report import merge_grounding_into_scorecard, render_report_markdown
import json
from pathlib import Path

results_dir = Path("benchmarks/pr-review-benchmark/results")

# Load the scorecard built in the previous step
scorecard = json.loads((results_dir / "scorecard.json").read_text())

# Load grounding results (automated check of file/line citations)
grounding_by_case_and_tool = {}
for grounding_file in sorted((results_dir / "grounding").glob("*/*.json")):
    case_id = grounding_file.parent.name
    tool = grounding_file.stem
    grounding_data = json.loads(grounding_file.read_text())
    grounding_by_case_and_tool.setdefault(case_id, {})[tool] = grounding_data

# Merge grounding into scorecard
final_scorecard = merge_grounding_into_scorecard(scorecard, grounding_by_case_and_tool)

# Render markdown report
report_md = render_report_markdown(final_scorecard)
print(report_md)

# Save to REPORT.md
report_path = Path("benchmarks/pr-review-benchmark/REPORT.md")
report_path.write_text(report_md)
```

### Final Report Output

The report includes:
- A summary table: recall (hit/partial/miss), false-positive count, avg actionability, grounding rate per tool
- Human/LLM judge agreement rates (per rubric dimension)
- Known limitations (reproduced from the design spec)
- Methodology (dates, model versions, provider info — to be filled into `METHODOLOGY.md` at execution time)

## Step 7: Fill in METHODOLOGY.md

Before publishing, record runtime values in `benchmarks/pr-review-benchmark/METHODOLOGY.md`:

```markdown
- **Run date:** YYYY-MM-DD at HH:MM UTC
- **Aletheore version/model:** git commit hash (github-app deployment), `deepseek-v4-flash` (Flash Review, hardcoded server-side)
- **PR-Agent version/model:** pip freeze output, `deepseek/deepseek-v4-flash`
- **DeepSource plan/version:** as displayed in Settings (e.g., "Team plan, v2026-07-26")
- **LLM judge model:** Claude (subagent dispatch, no separate API key)
- **Corpus:** 25 cases — 15 real bug-fix reconstructions, 6 injected bugs, 4 clean PRs
```

## Troubleshooting

### Aletheore's Flash Review hasn't posted a comment

Confirm the Aletheore GitHub App is installed on the scratch repo under the account/org that's actually paid (installations are per-account — see `github-app/app_server/dashboard.py`'s `_uninitialized_repos_for_installation` for how the dashboard resolves this). Flash Review only runs for paid installations; check the app's own logs (`docker compose logs scan_worker` on the production host) if it's installed but silent.

### DeepSource isn't connected

Ensure the scratch repo (`ArihantK15/proctor-browser`) is connected to DeepSource and has run an analysis. Verify in DeepSource's UI that it's scanning the repo.

### PR-Agent command not found

Ensure PR-Agent is installed:
```bash
pip install pr-agent
python -m pr_agent.cli --help
```

If it's not, install it and configure `.pr_agent.toml` per the PR-Agent setup section above.

## Reproducibility & Auditing

To verify results later:

1. Re-run the pipeline against the same cases (files in `benchmarks/pr-review-benchmark/cases/`) — git log records the exact case authors and dates
2. Check raw tool outputs in `results/raw/` to audit individual findings (scored under real tool names — see Step 4)
3. Verify grounding results in `results/grounding/` to confirm file/line citations

All inputs (cases, raw outputs, human scores, LLM scores) are stored locally in `results/`. This directory is gitignored to keep noisy intermediate data out of the published deliverables (`REPORT.md`, `METHODOLOGY.md`, `cases/`), not because of any tool's ToS.

## References

- **Design spec:** `docs/superpowers/specs/2026-07-26-aletheore-pr-review-benchmark-design.md`
- **Case authoring:** `.superpowers/sdd/2026-07-26-aletheore-pr-review-benchmark-implementation-plan/task-10-brief.md`
- **Script internals:** `benchmarks/pr-review-benchmark/scripts/*.py` (each file is documented)
- **Aletheore cite verification:** `src/aletheore/citation_verifier.py`
- **PR-Agent docs:** https://github.com/Codium-ai/pr-agent#readme
- **DeepSource API docs:** https://docs.deepsource.com/
