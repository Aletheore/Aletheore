# Veridion Perspectives Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add six audience-specific "lenses" (Security, Investor/Technical-DD, Onboarding,
Engineering-Manager/Process, Documented-Policy-and-Governance-Gaps, Documentation Quality) that
re-synthesize evidence Parts II-VI already compute into a new `## Perspectives` report section.

**Architecture:** One small new deterministic detector (`detect_policy_docs`, marker-based,
identical pattern to `detect_build_tools`) feeds the two lenses that need it. The other four
lenses need zero new evidence — pure re-synthesis, same pattern Part IX (Roadmap) already
proved. All manual content lives in one new file, `manual/part-7-perspectives.md`.

**Tech Stack:** Python stdlib only. No new dependencies, no network calls anywhere in this
plan except the final, explicitly-gated reasoning-phase step.

## Global Constraints

- **No lens may ever assert or imply compliance, non-compliance, or certification status
  against any named regulation or standard** (GDPR, HIPAA, SOC 2, SOC 3, ISO 27001, ISO 42001,
  CCPA, CPRA, FERPA, DPDP, or any other named framework) — regardless of what `policy_docs`
  contains. Permitted: "this repo has no privacy policy file." Never permitted, at any
  confidence level: "this repo is/is not GDPR compliant."
- Every lens's "what evidence supports" claims must cite a specific earlier finding by exact
  name/file/field — no new claims, same rule Roadmap already established.
- Every lens must include a non-empty "what evidence doesn't cover" statement — not optional,
  not skippable even when a lens has substantial supporting evidence.
- `evidence.repository.policy_docs` is additive only — existing `repository` keys
  (`languages`, `frameworks`, `ai_usage`, `build_tools`, `monorepo`, `modules`,
  `dependency_graph`, `unparseable_files`) are unchanged.

---

## Task 1: `detect_policy_docs`

**Files:**
- Modify: `prototype/veridion/scanner/detect.py`
- Test: `prototype/tests/test_detect.py`

**Interfaces:**
- Consumes: nothing from other tasks — self-contained within `detect.py`.
- Produces: `detect_policy_docs(repo_path: Path) -> list[dict]` returning entries shaped
  `{"name": str, "evidence": str}`, identical shape to `build_tools`/`frameworks`/`ai_usage`
  entries. Task 2 calls this function by this exact name and return shape.

- [ ] **Step 1: Write the failing tests**

Append to `prototype/tests/test_detect.py`:
```python
from veridion.scanner.detect import detect_policy_docs


def test_detect_policy_docs_finds_multiple_file_markers(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "LICENSE").write_text("MIT")
    (repo / "SECURITY.md").write_text("# Security Policy\n")
    (repo / "README.md").write_text("# My Project\n")

    result = detect_policy_docs(repo)

    names = {d["name"] for d in result}
    assert names == {"license", "security_policy", "readme"}
    license_entry = next(d for d in result if d["name"] == "license")
    assert license_entry["evidence"] == "LICENSE"


def test_detect_policy_docs_detects_directory_markers(tmp_path):
    repo = tmp_path / "repo"
    (repo / "docs" / "security").mkdir(parents=True)

    result = detect_policy_docs(repo)

    assert any(
        d["name"] == "security_policy" and d["evidence"] == "docs/security" for d in result
    )


def test_detect_policy_docs_empty_when_nothing_present(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    result = detect_policy_docs(repo)

    assert result == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd prototype && pytest tests/test_detect.py -v
```
Expected: FAIL (`detect_policy_docs` doesn't exist yet); all existing tests still PASS.

- [ ] **Step 3: Add the marker dict and `detect_policy_docs`**

In `prototype/veridion/scanner/detect.py`, add near `BUILD_TOOL_MARKERS`:
```python
POLICY_DOC_MARKERS = {
    "LICENSE": "license",
    "LICENSE.md": "license",
    "README.md": "readme",
    "SECURITY.md": "security_policy",
    "PRIVACY.md": "privacy_policy",
    "PRIVACY_POLICY.md": "privacy_policy",
    "CODE_OF_CONDUCT.md": "code_of_conduct",
    "CONTRIBUTING.md": "contributing_guide",
    "TERMS.md": "terms_of_service",
    "TERMS_OF_SERVICE.md": "terms_of_service",
    "GOVERNANCE.md": "governance_policy",
    "docs/security": "security_policy",
    "docs/privacy": "privacy_policy",
    "docs/compliance": "compliance_docs",
    "docs/governance": "governance_policy",
}
```

Add the function after `detect_build_tools`:
```python
def detect_policy_docs(repo_path: Path) -> list[dict]:
    docs = []
    for marker, category in POLICY_DOC_MARKERS.items():
        candidate = repo_path / marker
        if candidate.exists():
            docs.append({"name": category, "evidence": marker})
    return docs
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd prototype && pytest tests/test_detect.py -v
```
Expected: PASS — all tests, including the 3 new `detect_policy_docs` tests.

- [ ] **Step 5: Commit**

```bash
git add prototype/veridion/scanner/detect.py prototype/tests/test_detect.py
git commit -m "feat: add policy-document presence detection"
```

---

## Task 2: Wire `policy_docs` into `evidence.py`

**Files:**
- Modify: `prototype/veridion/evidence.py`
- Modify: `prototype/tests/test_evidence.py`

**Interfaces:**
- Consumes: `detect_policy_docs` (Task 1) by its exact name and return shape.
- Produces: `evidence["repository"]["policy_docs"]` — a new key inside the existing
  `repository` dict, placed after `ai_usage` and before `build_tools`.

- [ ] **Step 1: Write the failing test**

Append to `prototype/tests/test_evidence.py`:
```python
def test_scan_repository_includes_policy_docs_in_repository_block(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "LICENSE").write_text("MIT")
    (repo / "main.py").write_text("x = 1\n")

    with patch("veridion.evidence.check_dependency_vulnerabilities") as mock_check:
        mock_check.return_value = {"checked": True, "reason": None, "findings": []}
        evidence = scan_repository(repo)

    names = {d["name"] for d in evidence["repository"]["policy_docs"]}
    assert "license" in names
```

Check the top of `prototype/tests/test_evidence.py` for its existing `from unittest.mock
import patch` — reuse it.

- [ ] **Step 2: Run test to verify it fails**

```bash
cd prototype && pytest tests/test_evidence.py -v
```
Expected: FAIL (`evidence["repository"]["policy_docs"]` doesn't exist yet).

- [ ] **Step 3: Modify `evidence.py`**

Update the import:
```python
from veridion.scanner.detect import (
    detect_ai_usage,
    detect_build_tools,
    detect_frameworks,
    detect_languages,
    detect_monorepo,
    detect_policy_docs,
)
```

In `scan_repository`, add the call and wire it into the returned dict:
```python
    languages = detect_languages(repo_path)
    frameworks = detect_frameworks(repo_path)
    ai_usage = detect_ai_usage(repo_path)
    policy_docs = detect_policy_docs(repo_path)
    build_tools = detect_build_tools(repo_path)
```
and
```python
        "repository": {
            "languages": languages,
            "frameworks": frameworks,
            "ai_usage": ai_usage,
            "policy_docs": policy_docs,
            "build_tools": build_tools,
            "monorepo": monorepo,
            "modules": modules,
            "dependency_graph": dependency_graph,
            "unparseable_files": unparseable_files,
        },
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd prototype && pytest -v
```
Expected: PASS — full suite, no regressions in any earlier task.

- [ ] **Step 5: Commit**

```bash
git add prototype/veridion/evidence.py prototype/tests/test_evidence.py
git commit -m "feat: wire policy_docs into evidence.repository"
```

---

## Task 3: Perspectives manual content and Part I output-contract update

**Files:**
- Create: `prototype/manual/part-7-perspectives.md`
- Modify: `prototype/manual/part-1-operating-instructions.md`

**Interfaces:**
- Consumes: `evidence.repository.policy_docs` (Task 2) for two of the six lenses; the other
  four lenses consume evidence already produced by Parts II-VI (no new interface).
- Produces: nothing consumed by other tasks — read by the reasoning-phase agent only.

- [ ] **Step 1: Create the Perspectives manual file**

Create `prototype/manual/part-7-perspectives.md`:
```markdown
# Part VII — Perspectives

This section governs how to produce the `## Perspectives` section of the report, appearing
after `## Roadmap` and before `## Evidence Gaps`. Follow the mandatory verification rules in
Part I for everything below.

## What this section does

Six short, audience-specific readings of findings already stated earlier in this same report
(Repository Intelligence, Git Intelligence, Architecture Review, Security, and Roadmap). This
section introduces no new evidence and makes no new claims — it reframes what's already been
established for six audiences who would weight the same facts differently.

## Mandatory rules

1. **Every lens's "what evidence supports" claims must cite a specific earlier finding** by
   exact name, file, or field — the same no-new-claims rule Roadmap already follows. If a lens
   has nothing to cite, its "what evidence supports" subsection should say so plainly rather
   than reaching for something tenuous.
2. **Every lens must include a non-empty "what evidence doesn't cover" statement** — specific
   to that lens's core question, not a generic disclaimer copy-pasted across all six. This is
   mandatory even when a lens has substantial supporting evidence to cite.
3. **No lens may ever assert or imply compliance, non-compliance, or certification status
   against any named regulation or standard** — GDPR, HIPAA, SOC 2, SOC 3, ISO 27001, ISO
   42001, CCPA, CPRA, FERPA, DPDP, or any other named framework, regardless of confidence
   level and regardless of what `policy_docs` contains. This is a hard rule, not a confidence
   judgment call:
   - **Permitted**: "This repository has no file matching a privacy-policy naming convention
     (`repository.policy_docs` contains no `privacy_policy` entry)."
   - **Permitted**: "`SECURITY.md` exists and states: [quote the actual content]."
   - **Never permitted, at any confidence level**: "This repository is GDPR compliant."
     "This repository is not SOC 2 ready." "This meets ISO 27001 requirements." Do not soften
     these into a Low-confidence version either — the rule is not about confidence, it is
     that this report has no evidence capable of supporting a compliance verdict at all, so no
     such claim is ever made, regardless of how it's hedged.
4. **Produce all six lenses in the order listed below, every time.** Do not omit a lens
   because it has little to say — a lens with a short "what evidence supports" and a
   substantial "what evidence doesn't cover" is a complete, valid entry, not an incomplete one.

## The six lenses

### Security

**What this audience cares about**: attack surface and incident-response readiness — what
could go wrong, and who could actually respond if it did.

Draw "what evidence supports" from `evidence.security`'s secrets and dependency-vulnerability
findings, and from `evidence.git.ownership`'s concentration (a single point of
incident-response failure is itself relevant here, not only a financial fact). State "what
evidence doesn't cover": this report has no evidence of actual incident history, access
control configuration, or runtime security posture — only what is visible in the source tree
and its history.

### Investor / Technical Due Diligence

**What this audience cares about**: cost to inherit this codebase, and financial risk if key
people leave.

Draw from `evidence.git`'s ownership-concentration and commit-cadence findings,
`evidence.architecture`'s coupling findings (cost to change something safely), and
`evidence.repository.ai_usage` (provider/framework dependency as a switching-cost or
vendor-lock-in risk). State "what evidence doesn't cover": this report has no revenue,
market, customer, or valuation data of any kind — none of that exists in a source repository,
and no claim about investability or valuation is made anywhere in this report.

### Onboarding / New Contributor

**What this audience cares about**: where to start, and what is risky to touch on day one.

Draw from `evidence.architecture.clusters` (a structural map of the codebase's natural
groupings) and `evidence.repository`'s high-fan-in and god-module findings (places worth
extra care before changing). State "what evidence doesn't cover": this report has no
information about team norms, code review expectations, or who to ask questions of — only the
code's own structure.

### Engineering Manager / Process

**What this audience cares about**: team practice health — whether work is flowing smoothly,
not whether any individual represents a financial risk.

Draw from `evidence.git.commit_cadence`'s trend, unmerged or stale-branch findings (work
started but not landed — a process bottleneck signal), and ownership distribution reframed as
a team-practice question ("is contribution concentrated in a way that could bottleneck
review or continuity") rather than the Investor lens's financial framing of the same numbers.
State "what evidence doesn't cover": this report has no visibility into actual review
turnaround time, meeting cadence, or process outside what is reconstructable from commit and
branch timestamps.

### Documented Policy & Governance Gaps

**What this audience cares about**: what this repository's own paper trail documents, and
where it is silent.

Draw "what evidence supports" from `evidence.repository.policy_docs` directly: for each
detected entry, read that file's actual content (you have file access to this repository) and
quote the relevant part, citing the file by name. For each common policy area with a
corresponding marker category (`license`, `security_policy`, `privacy_policy`,
`code_of_conduct`, `contributing_guide`, `governance_policy`) that has no detected entry in
`policy_docs`, state plainly that no such file was found in this repository. State "what
evidence doesn't cover" explicitly and completely, every time: whether any documented policy
is actually followed in practice, whether the organization holds any certification, and
compliance status with any named regulation — none of that is answerable from a source
repository, and per the mandatory rules above, no claim about it is ever made here.

### Documentation Quality

**What this audience cares about**: whether the code that matters most is explained anywhere.

Draw from `evidence.repository.policy_docs`'s `readme` and `contributing_guide` entries (cite
the file if found; state plainly if not found) and `evidence.repository`'s high-fan-in and
god-module findings, reframed as "these are the modules most worth documenting, given how
many other files depend on them." State "what evidence doesn't cover": this report has no
visibility into inline comments, docstrings, or the accuracy of any existing documentation —
only whether top-level documentation files exist.

## What this section does not produce

No compliance verdicts of any kind, for any named regulation or standard. No numeric scores.
No ranking of the six lenses against each other. No claims not already stated earlier in the
report.
```

- [ ] **Step 2: Update Part I's output contract**

In `prototype/manual/part-1-operating-instructions.md`, replace the output contract list:
```markdown
1. **Summary** — 3-5 sentences, no unsupported claims, citing the highest-confidence findings.
2. **Repository Intelligence** — findings from `evidence.repository`, per Part II below.
3. **Git Intelligence** — findings from `evidence.git`, per Part III below.
4. **Architecture Review** — findings from `evidence.architecture`, per Part IV below.
5. **Security** — findings from `evidence.security`, per Part V below.
6. **Roadmap** — prioritized findings from the sections above, per Part IX below.
7. **Evidence Gaps** — an explicit list of what `evidence.json` could not tell you
   (unparseable files, unavailable git data, anything you were tempted to claim but couldn't
   support).
```
with:
```markdown
1. **Summary** — 3-5 sentences, no unsupported claims, citing the highest-confidence findings.
2. **Repository Intelligence** — findings from `evidence.repository`, per Part II below.
3. **Git Intelligence** — findings from `evidence.git`, per Part III below.
4. **Architecture Review** — findings from `evidence.architecture`, per Part IV below.
5. **Security** — findings from `evidence.security`, per Part V below.
6. **Roadmap** — prioritized findings from the sections above, per Part IX below.
7. **Perspectives** — six audience-specific readings of the findings above, per Part VII
   below.
8. **Evidence Gaps** — an explicit list of what `evidence.json` could not tell you
   (unparseable files, unavailable git data, anything you were tempted to claim but couldn't
   support).
```

- [ ] **Step 3: Commit**

```bash
git add prototype/manual/part-7-perspectives.md prototype/manual/part-1-operating-instructions.md
git commit -m "docs: add Perspectives manual (six lenses), update Part I output contract"
```

---

## Task 4: Live verification (not automated)

No further code changes — confirms the wired-up detector and manual content behave correctly
against real repositories.

- [ ] **Step 1: Reinstall the prototype**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion/prototype
pip install -e ".[dev]"
```

- [ ] **Step 2: Confirm `policy_docs` detection against Veridion's own real repo root**

```bash
python3 -c "
from pathlib import Path
from veridion.scanner.detect import detect_policy_docs
result = detect_policy_docs(Path('/Users/arihantkaul/Documents/GitHub/Veridion'))
for d in result:
    print(d)
"
```
Veridion's own repo root already has `SECURITY.md`, `CODE_OF_CONDUCT.md`, `GOVERNANCE.md`,
`CONTRIBUTING.md`, and `LICENSE` (from the original VDP constitution bootstrap) — confirm all
five are detected with the correct category names. This is a real, non-synthetic check, not
just the synthetic fixtures from Task 1 — this is Success Criterion 1 from the design spec.

- [ ] **Step 3: Full reasoning-phase run (requires explicit go-ahead, same as every previous part)**

Only after checking with the user first — this is a live `claude` call against Procta's
private source:
```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
veridion audit /Users/arihantkaul/proctored-browser
```

- [ ] **Step 4: Check the report against all five success criteria**

Confirm, reading `.veridion/audit-report.md` directly:
1. A `## Perspectives` section exists, positioned after `## Roadmap` and before `##
   Evidence Gaps`.
2. All six lenses are present, in the order specified in the manual.
3. Every "what evidence supports" claim in each lens traces to a finding already stated
   earlier in the same report — spot-check at least one claim per lens against its cited
   source section.
4. Every lens includes a non-empty "what evidence doesn't cover" statement.
5. Search the entire Perspectives section for compliance-verdict language — confirm there is
   none. Specifically check for any sentence asserting or implying pass/fail, compliant/
   non-compliant, or "meets requirements" against GDPR, HIPAA, SOC 2, SOC 3, ISO 27001, ISO
   42001, CCPA, CPRA, FERPA, or DPDP. This must be checked explicitly by reading the text, not
   assumed absent because the manual forbids it.

- [ ] **Step 5: Record the outcome**

If all five criteria pass, Perspectives is done — report back with whatever real findings
surfaced (real policy-doc gaps, real coupling/cadence numbers as they read through each lens)
and any surprises. If any criterion fails, that's the next debugging task, not a new plan.

---

## Self-Review Notes

**Spec coverage:** the policy-document marker list and evidence shape (Task 1), the
`evidence.repository.policy_docs` placement (Task 2), the output-contract position and the
fixed three-part lens template for all six lenses including the hard compliance-verdict
constraint with concrete permitted/forbidden examples (Task 3), and all five numbered success
criteria (Task 4, mapped 1:1) are each covered by a specific task.

**Placeholder scan:** no TBD/TODO; the manual content in Task 3 is complete, final prose for
all six lenses, not a description of what they should eventually contain.

**Type consistency:** `detect_policy_docs` (Task 1) → consumed by `evidence.py` (Task 2) with
the exact same `{"name", "evidence"}` entry shape used by `frameworks`/`build_tools`/
`ai_usage` throughout the codebase — no new shape introduced. No naming collisions (unlike
Part V's `check_vulnerabilities`) since `detect_policy_docs` doesn't share a name with any
parameter or existing import.
