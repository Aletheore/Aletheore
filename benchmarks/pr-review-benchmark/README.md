# Aletheore PR-Review Benchmark — Runbook

This benchmark measures Aletheore's grounding claim ("every finding traces back to evidence") against real competitors on real and reconstructed bugs, scored blind. This is a semi-automated, otherwise manual process; the runbook below describes every step.

See `METHODOLOGY.md` for runtime values (model versions, dates, provider versions) recorded at execution time.

See the full design spec in `docs/superpowers/specs/2026-07-26-aletheore-pr-review-benchmark-design.md`.

## Prerequisites

- Python 3.10+
- `git`, `gh` (GitHub CLI)
- Aletheore installed and available as `aletheore` CLI
- PR-Agent installed (`python -m pr_agent.cli`) — see PR-Agent setup section below
- DeepSeek API key (`DEEPSEEK_API_KEY` environment variable) — see model parity section below
- GitHub API token for accessing the scratch repo (typically `gh` auto-handles this)
- Access to run `aletheore audit` and `pr_agent` commands against test code

## Step 0: Prepare Test Cases (One-Time Setup)

The benchmark corpus lives in `benchmarks/pr-review-benchmark/cases/`, with one subdirectory per case. Cases must be authored before running the pipeline; see `docs/superpowers/specs/2026-07-26-aletheore-pr-review-benchmark-design.md` → "Test Corpus & Ground Truth" for the full procedure, and refer to `cases/001-flask-cli-key-quote/` as a worked example.

**Each case directory must contain:**
- `repo.txt` — initially two lines: `repo_url=<https://github.com/...>` and `base_commit=<commit-hash>`. After opening the real PR in Step 1, append two more lines: `pr_url=<the PR URL>` and `deepsource_run_id=<from DeepSource's API once configured>` (see Step 3 for how to populate these after running tools)
- `pr.diff` — the PR diff (for real bugs, this is the *inverse* of the fix, reintroducing the bug)
- `ground_truth.yaml` — structured metadata: `case_id`, `language`, `category` (one of `real_bug_fix`, `injected_bug`, `clean`), `bug_type`, `expected_file`, `expected_line`, `fix_reference` (URL to the fix or `null`), and `description`
- `ground_truth.md` — 2–4 sentence prose explanation for the published report

For case authoring details, see the task 10 brief in `.superpowers/sdd/2026-07-26-aletheore-pr-review-benchmark-implementation-plan/task-10-brief.md`.

## Step 1: Open Real PR on Scratch Repo

The benchmark runs against real PRs opened on a **scratch repo** that you control, letting the tools see a genuine PR context (CodeRabbit needs to review a real PR; DeepSource needs its own issue run).

**Scratch repo:** `https://github.com/ArihantK15/proctor-browser` (user controls; CodeRabbit's GitHub App is already installed on it)

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

   Note the PR URL from the output; you'll need it for CodeRabbit, PR-Agent, and Aletheore steps below.

5. Wait for CodeRabbit to post its review (GitHub App does this automatically; may take a minute or two).

## Step 2: Set Up Models — Model Parity (DeepSeek)

The benchmark aims for **model parity** between Aletheore and PR-Agent: both use the same underlying LLM so the comparison isolates grounding architecture, not which LLM is smarter. This run uses **DeepSeek** as the shared backend.

### Environment Variables

Set once in your shell, or add to `.env` and `source` it:

```bash
export DEEPSEEK_API_KEY="<your-deepseek-api-key>"
```

### Aletheore Configuration

Aletheore supports OpenAI-compatible endpoints via the `OpenAICompatibleAdapter` class. However, **DeepSeek is not yet registered** in `src/aletheore/cli.py`'s `KNOWN_ADAPTERS` list (which currently includes `openai`, `mistral`, `grok`, `ollama`, and `gemini`).

**Prerequisite: Add DeepSeek to KNOWN_ADAPTERS**

Before running the benchmark, add a new adapter entry to `src/aletheore/cli.py:50-91`. Add this after the existing adapter entries:

```python
OpenAICompatibleAdapter(
    name="deepseek",
    base_url="https://api.deepseek.com/v1",
    api_key_env_var="DEEPSEEK_API_KEY",
    model="deepseek-v4-pro",
)
```

After this change, run Aletheore's audit against DeepSeek:

```bash
aletheore audit <checkout_dir> --agent deepseek
```

**Configuration:**
- Set the environment variable: `export DEEPSEEK_API_KEY=<your-key>`
- The model used is `deepseek-v4-pro` (as of 2026-07-26; see `github-app/scan_worker/model_tiers.py:19` for the canonical model name used in this codebase)
- The base URL is `https://api.deepseek.com/v1` (DeepSeek's OpenAI-compatible endpoint)

### PR-Agent Configuration

PR-Agent reads configuration from `.pr_agent.toml` (or environment variables). To configure it to use DeepSeek:

1. Create or edit `.pr_agent.toml` in your working directory or PR-Agent's config directory:

```toml
[config]
model = "openai"  # or the specific model key for DeepSeek
openai_key = "sk-..."  # or leave empty and use env var
openai_org_id = ""
openai_base_url = "https://api.deepseek.com/v1"  # DeepSeek's OpenAI-compatible endpoint
```

2. Or set environment variables:
```bash
export OPENAI_API_KEY="$DEEPSEEK_API_KEY"
export OPENAI_API_BASE="https://api.deepseek.com/v1"
```

3. Run PR-Agent:
```bash
python -m pr_agent.cli --pr_url <pr_url> review
```

Refer to PR-Agent's official documentation for the exact config keys. The adapter pattern (OpenAI-compatible) means most LLM endpoints can be swapped by changing the base URL and API key.

## Step 3: Run the Case Pipeline

For each case, run `scripts/run_case.py`. This orchestrates:
1. Clone the repo at `base_commit`
2. Apply the case's `pr.diff`
3. Invoke each adapter (Aletheore, PR-Agent, DeepSource, CodeRabbit)
4. Normalize raw output to the common schema
5. Run the automated grounding check (file/line verification)
6. Store results in `results/raw/`, `results/grounding/`

### Collecting Tool Outputs

The adapters in `scripts/adapters.py` accept injected callables for external tools (DeepSource, CodeRabbit) so you can fetch their real output without hardcoding API logic. Populate these as follows:

#### Aletheore & PR-Agent

Both run locally and write to stdout/files; the adapters invoke them directly via `subprocess.run()`.

#### DeepSource

1. **Set up DeepSource on the scratch repo** (one-time):
   - Go to `https://deepsource.com` and connect the scratch repo (`ArihantK15/proctor-browser`)
   - Configure the repo's analysis settings

2. **Fetch DeepSource issues** for each case's PR:
   - DeepSource posts issues to the PR; fetch them via its API
   - Write a `fetch_issues` callable that:
     - Takes `deepsource_run_id` (from the case's `ground_truth.yaml` or fetch via PR API)
     - Calls DeepSource's issues API (likely `GET /api/issues?run_id=...`)
     - Returns raw JSON in the shape `{"issues": [{"title": ..., "location": {"path": ..., "position": {"begin": {"line": ...}}}, "severity": ...}, ...]}`
   - The adapter calls this callable; see `scripts/adapters.py` → `deepsource_adapter()`

   **Example stub** (fill in with real DeepSource API calls):
   ```python
   def fetch_issues(deepsource_run_id):
       # Pseudo-code; see DeepSource API docs
       response = requests.get(
           f"https://api.deepsource.io/v1/issues",
           params={"run_id": deepsource_run_id},
           headers={"Authorization": f"Bearer {os.getenv('DEEPSOURCE_API_KEY')}"}
       )
       return response.json()
   ```

#### CodeRabbit

1. **CodeRabbit's GitHub App is already installed** on the scratch repo and will post reviews to each PR automatically.

2. **Fetch CodeRabbit's PR comments**:
   - Use the GitHub REST API to list PR comments
   - Write a `fetch_pr_comments` callable that:
     - Takes `pr_url` (e.g., `https://github.com/ArihantK15/proctor-browser/pull/123`)
     - Calls `gh api repos/ArihantK15/proctor-browser/pulls/<number>/comments` or equivalent
     - Returns raw JSON as a list of comment objects: `[{"path": ..., "line": ..., "body": ...}, ...]`

   **Example using `gh`**:
   ```python
   def fetch_pr_comments(pr_url):
       # Parse PR URL to extract owner/repo/number
       import re
       match = re.match(r"https://github.com/(.+)/(.+)/pull/(\d+)", pr_url)
       if not match:
           raise ValueError(f"Invalid PR URL: {pr_url}")
       owner, repo, number = match.groups()
       
       # Fetch comments via gh
       import subprocess
       result = subprocess.run(
           ["gh", "api", f"repos/{owner}/{repo}/pulls/{number}/comments"],
           capture_output=True, text=True, check=True
       )
       return json.loads(result.stdout)
   ```

3. **Note on output storage**: All intermediate results (raw tool outputs, grounding checks, anonymized findings, sealed mappings, manual scores, LLM judge scores) are **working state, not published artifacts**. The entire `benchmarks/pr-review-benchmark/results/` directory is `.gitignore`d and stays local to your machine.
   
   The published deliverables are only:
   - `benchmarks/pr-review-benchmark/REPORT.md` — the final scored summary
   - `benchmarks/pr-review-benchmark/METHODOLOGY.md` — runtime metadata
   - `benchmarks/pr-review-benchmark/cases/` — the test corpus (contains no CodeRabbit or other tool outputs, only ground truth)
   
   Per CodeRabbit's ToS (§4.2), CodeRabbit's raw output is not published; only its anonymized scored summary is included in the headline scorecard. The same applies to all intermediate results: they're kept local to prevent accidental publication of raw tool output (which may contain vendor-identifying information even if anonymized).

### Running the Pipeline

Create a script (or run interactively) that:

```python
import json
from pathlib import Path
from scripts.run_case import run_case
from scripts.adapters import aletheore_adapter, pr_agent_adapter, deepsource_adapter, coderabbit_adapter
from scripts.normalize import normalize_aletheore, normalize_pr_agent, normalize_deepsource, normalize_coderabbit
from scripts.anonymize import write_anonymized_case
import random

# Inject your callables here
def fetch_deepsource_issues(deepsource_run_id):
    # Implement per the DeepSource API section above
    ...

def fetch_coderabbit_comments(pr_url):
    # Implement per the CodeRabbit API section above
    ...

# Run one case
case_dir = Path("benchmarks/pr-review-benchmark/cases/001-flask-cli-key-quote")
workdir = Path("/tmp/pr-review-benchmark/work")
results_dir = Path("benchmarks/pr-review-benchmark/results")

adapters = {
    "aletheore": lambda checkout_dir, case: aletheore_adapter(checkout_dir, case),
    "pr_agent": lambda checkout_dir, case: pr_agent_adapter(checkout_dir, case),
    "deepsource": lambda checkout_dir, case: deepsource_adapter(checkout_dir, case, fetch_deepsource_issues),
    "coderabbit": lambda checkout_dir, case: coderabbit_adapter(checkout_dir, case, fetch_coderabbit_comments),
}

normalizers = {
    "aletheore": normalize_aletheore,
    "pr_agent": normalize_pr_agent,
    "deepsource": normalize_deepsource,
    "coderabbit": normalize_coderabbit,
}

result = run_case(case_dir, workdir, results_dir, adapters, normalizers)
print(f"Case {result['case_id']} processed; results in {results_dir}/raw/{result['case_id']}/")

# Anonymize: relabel tools to Tool A/B/C/D
rng = random.Random(42)  # seed for reproducibility
anon = write_anonymized_case(result["case_id"], result["findings_by_tool"], results_dir, rng)
print(f"Anonymized outputs in {anon['anon_dir']}/")
```

**Output structure after running all cases:**
```
benchmarks/pr-review-benchmark/results/
  raw/<case-id>/
    aletheore.json          # tool's raw output
    pr_agent.json
    deepsource.json
    coderabbit.json         # (not committed to repo)
  grounding/<case-id>/
    aletheore.json          # grounding check: {total_findings, verified, unverified, grounding_rate}
    pr_agent.json
    ...
  anon/<case-id>/
    tool_a.json             # anonymized findings
    tool_b.json
    tool_c.json
    tool_d.json
  sealed/<case-id>.json     # mapping: {"Tool A": "aletheore", "Tool B": "pr_agent", ...}
```

## Step 4: Manual Blind Scoring

Before scoring, **read the anonymized findings** in `results/anon/<case-id>/` and the ground truth in `cases/<case-id>/ground_truth.yaml` and `ground_truth.md`. Score each tool (A/B/C/D) independently and blind to its real identity.

For each case, create `results/scored/<case-id>.yaml`:

```yaml
case_id: 001-flask-cli-key-quote
scores:
  Tool A:
    recall: "hit"  # or "partial" / "miss"
    false_positives: []  # list of findings that are NOT the ground-truth issue
    actionability: 5  # 1–5 scale
  Tool B:
    recall: "partial"
    false_positives:
      - "suggestion about quote handling in a different context, not the _validate_key bug"
    actionability: 3
  Tool C:
    recall: "miss"
    false_positives: []
    actionability: null
  Tool D:
    recall: "hit"
    false_positives: []
    actionability: 4
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

To generate blank scorecards for all cases (without peeking at the sealed mapping until scoring is done):

```python
from pathlib import Path
from scripts.scoring_template import write_blank_scorecard

results_dir = Path("benchmarks/pr-review-benchmark/results")
for anon_dir in sorted((results_dir / "anon").glob("*")):
    case_id = anon_dir.name
    # Extract tool labels from anonymized findings, without opening the sealed mapping
    labels = sorted([f.stem.replace("_", " ").title() for f in anon_dir.glob("*.json")])
    
    out_path = results_dir / "scored" / f"{case_id}.yaml"
    write_blank_scorecard(case_id, labels, out_path)
    print(f"Created {out_path}")
```

## Step 5: LLM Judge — Blind Claude Subagent

The benchmark includes a second, independent scoring pass by an LLM judge to measure human/LLM agreement and catch reviewer fatigue. The judge is **blind** to tool identity and must be a **different provider/family** than the models under test (Aletheore and PR-Agent both use DeepSeek in this run, so the judge is Claude, a different provider).

**Important: The judge is NOT an API call.** Instead, this is a **blind Claude subagent dispatch** from a Claude Code session (your CLI agent acts as the controller):

1. **Build the judge prompt** for a case using `scripts/llm_judge.py`:
   ```python
   from scripts.llm_judge import build_judge_prompt
   import json
   import yaml
   from pathlib import Path
   
   case_id = "001-flask-cli-key-quote"
   ground_truth = yaml.safe_load((Path("benchmarks/pr-review-benchmark/cases") / case_id / "ground_truth.yaml").read_text())
   
   anon_dir = Path("benchmarks/pr-review-benchmark/results/anon") / case_id
   anonymized_findings = {}
   for tool_file in sorted(anon_dir.glob("*.json")):
       tool_label = tool_file.stem.replace("_", " ").title()
       anonymized_findings[tool_label] = json.loads(tool_file.read_text())
   
   prompt = build_judge_prompt(ground_truth, anonymized_findings)
   ```

2. **Dispatch a fresh Claude subagent** with this prompt (no context, no memory of which tool is which):
   - In your Claude Code CLI session, use the Agent tool to spawn a `"general-purpose"` or similar fresh agent
   - Pass the full `prompt` from step 1 as the agent's task
   - The subagent has no information except the prompt; it does not see this runbook, the case structure, or the tool identities

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
- **Aletheore version/model:** git commit hash, DeepSeek model variant
- **PR-Agent version/model:** pip freeze output, DeepSeek model variant
- **DeepSource plan/version:** as displayed in Settings (e.g., "Team plan, v2026-07-26")
- **CodeRabbit plan/version:** as displayed in your account (anonymized, e.g., "Professional plan, v2026-07-26")
- **LLM judge model:** Claude (subagent dispatch, blind, no separate API key)
- **Corpus:** 25 cases — 15 real bug-fix reconstructions, 6 injected bugs, 4 clean PRs
```

## Troubleshooting

### "aletheore audit --agent deepseek" fails with "requested adapter 'deepseek' is not available"

This means the DeepSeek adapter entry hasn't been added to `KNOWN_ADAPTERS` yet. See the "Prerequisite: Add DeepSeek to KNOWN_ADAPTERS" section in Step 2 above — you need to edit `src/aletheore/cli.py:50-91` and add the new `OpenAICompatibleAdapter` entry before running the audit.

### DeepSource isn't connected

Ensure the scratch repo (`ArihantK15/proctor-browser`) is connected to DeepSource and has run an analysis. Verify in DeepSource's UI that it's scanning the repo. Then fetch its issue run ID from the PR or DeepSource API to populate the case's `deepsource_run_id`.

### CodeRabbit hasn't reviewed the PR yet

CodeRabbit's GitHub App needs to be installed on the scratch repo and active. Verify in the repo's GitHub settings (Settings → Applications & Authorizations) that CodeRabbit's app is installed and authorized. If it is, wait a minute; GitHub App reviews can take a moment to post.

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
2. Use the sealed label-to-tool mappings in `results/sealed/<case-id>.json` to de-anonymize scoring
3. Check raw tool outputs in `results/raw/` to audit individual findings
4. Verify grounding results in `results/grounding/` to confirm file/line citations

All inputs (cases, raw outputs, human scores, LLM scores) are stored; only CodeRabbit's raw output is excluded per its ToS.

## References

- **Design spec:** `docs/superpowers/specs/2026-07-26-aletheore-pr-review-benchmark-design.md`
- **Case authoring:** `.superpowers/sdd/2026-07-26-aletheore-pr-review-benchmark-implementation-plan/task-10-brief.md`
- **Script internals:** `benchmarks/pr-review-benchmark/scripts/*.py` (each file is documented)
- **Aletheore cite verification:** `src/aletheore/citation_verifier.py`
- **PR-Agent docs:** https://github.com/Codium-ai/pr-agent#readme
- **DeepSource API docs:** https://docs.deepsource.com/
