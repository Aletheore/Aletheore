# Threat Model Perspective Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a seventh Perspectives lens, Threat Model, to the AIR audit report's `Perspectives` section — organizing evidence already cited elsewhere in the report (API endpoints, secrets, dependency vulnerabilities, ownership concentration, and optionally infrastructure/environment-variable evidence if AIR expansion phase 2 has shipped by then) through STRIDE threat-modeling categories, rather than free prose.

**Architecture:** This is a documentation-only change to `aletheore/manual/part-7-perspectives.md` (and one cross-reference in `part-1-operating-instructions.md`) — no Python code, no new evidence detection, no pytest coverage, since nothing in the codebase validates Perspectives lens count or names (`REQUIRED_SECTIONS` only checks that the top-level `Perspectives` section exists). Business Model was considered and explicitly dropped — it would duplicate the existing Investor/Technical-Due-Diligence lens's "no revenue/market/customer data is derivable from source" framing with no genuinely separate evidence angle.

**Tech Stack:** Markdown only.

## Global Constraints

- No new evidence blocks, no new code, no new tests — this plan touches only manual/prompt content.
- The existing Perspectives rules (every claim cites a specific earlier finding, every lens has a non-empty "what evidence doesn't cover," no compliance/certification claims ever, all lenses produced every time) apply to the new lens exactly as they apply to the existing six — restated, not weakened.
- Verification is a real audit run against a real repo, not a unit test — this section's correctness is about what the LLM actually writes, which pytest cannot check.

---

### Task 1: Update lens-count references from six to seven

**Files:**
- Modify: `aletheore/manual/part-1-operating-instructions.md:49`
- Modify: `aletheore/manual/part-7-perspectives.md:12,36,40,119`

**Interfaces:** none — text-only edits.

- [ ] **Step 1: Update `part-1-operating-instructions.md`**

Change line 49 from:

```markdown
7. **Perspectives** — six audience-specific readings of the findings above, per Part VII
```

to:

```markdown
7. **Perspectives** — seven audience-specific readings of the findings above, per Part VII
```

- [ ] **Step 2: Update `part-7-perspectives.md`'s four references**

Line 12, change:

```markdown
established for six audiences who would weight the same facts differently.
```

to:

```markdown
established for seven audiences who would weight the same facts differently.
```

Line 36, change:

```markdown
4. **Produce all six lenses in the order listed below, every time.** Do not omit a lens
```

to:

```markdown
4. **Produce all seven lenses in the order listed below, every time.** Do not omit a lens
```

Line 40, change:

```markdown
## The six lenses
```

to:

```markdown
## The seven lenses
```

Line 119, change:

```markdown
No ranking of the six lenses against each other. No claims not already stated earlier in the
```

to:

```markdown
No ranking of the seven lenses against each other. No claims not already stated earlier in the
```

- [ ] **Step 3: Verify no stale "six" references remain**

Run: `cd prototype && grep -rn "six lenses\|six audience" aletheore/manual/*.md`
Expected: no output (empty).

- [ ] **Step 4: Commit**

```bash
cd prototype
git add aletheore/manual/part-1-operating-instructions.md aletheore/manual/part-7-perspectives.md
git commit -m "docs: update Perspectives lens count from six to seven"
```

---

### Task 2: Add the Threat Model lens

**Files:**
- Modify: `aletheore/manual/part-7-perspectives.md` (insert new lens section, after "Security" and before "Investor / Technical Due Diligence")

**Interfaces:** none — text-only edit.

- [ ] **Step 1: Insert the new lens**

In `aletheore/manual/part-7-perspectives.md`, insert this new `###` section immediately after the existing "Security" lens's paragraph (which ends with `...only what is visible in the source tree and its history.`) and before the `### Investor / Technical Due Diligence` heading:

```markdown
### Threat Model

**What this audience cares about**: where the real entry points are, what trust boundaries
exist, and which categories of threat already have concrete evidence behind them — organized
by STRIDE (spoofing, tampering, repudiation, information disclosure, denial of service,
elevation of privilege), not a generic checklist independent of this report's own findings.

Organize by STRIDE category, citing only what is already established elsewhere in this same
report — this lens introduces no new evidence, only a different organizing structure over
facts already stated:

- **Entry points**: draw from `evidence.repository.api_endpoints` — list unauthenticated vs.
  any-auth endpoints as the literal external attack surface already mapped earlier.
- **Spoofing / tampering**: draw from `evidence.security.secrets` findings (a weak or leaked
  credential undermines any identity claim built on it) and, if present,
  `evidence.repository.environment_variables.declared` — cite the *names* of secret-shaped
  configuration the application depends on, never a value, since AIR never surfaces one.
- **Information disclosure**: draw from `evidence.security.dependency_vulnerabilities`
  findings whose summary or advisory text specifically describes a disclosure risk — not
  every vulnerability is disclosure-shaped; only cite the ones that are.
- **Denial of service**: draw from `evidence.security.dependency_vulnerabilities` findings
  whose summary specifically describes a DoS risk. If `evidence.repository.infrastructure` is
  present, note which `docker_compose_services` entries look internet-facing only if that
  exposure is itself evidenced (e.g., a cited port mapping) — never infer exposure that isn't
  actually stated in evidence.
- **Elevation of privilege / repudiation**: draw from `evidence.git.ownership` concentration —
  the same bus-factor fact the Security lens already cites, reframed here as "who could
  actually reconstruct what happened after a privilege-escalation incident."

**What evidence doesn't cover**: this report has no runtime network topology beyond what a
config file declares, no penetration-test results, no verification that any authentication or
authorization code is *correctly* implemented (only whether such code exists, per evidence),
no attacker capability or motivation modeling, and no likelihood or probability estimate for
any threat category. Never assert that a specific vulnerability is exploitable in this
codebase without a cited advisory or CVSS detail backing that specific claim.
```

- [ ] **Step 2: Verify the file still parses as valid markdown and the section landed in the right place**

Run: `cd prototype && grep -n "^### " aletheore/manual/part-7-perspectives.md`
Expected output, in this exact order:
```
### Security
### Threat Model
### Investor / Technical Due Diligence
### Onboarding / New Contributor
### Engineering Manager / Process
### Documented Policy & Governance Gaps
### Documentation Quality
```

- [ ] **Step 3: Commit**

```bash
cd prototype
git add aletheore/manual/part-7-perspectives.md
git commit -m "feat: add Threat Model lens to Perspectives (STRIDE-organized)"
```

---

### Task 3: Real end-to-end verification via an actual audit run

**Files:** none modified — verification only.

- [ ] **Step 1: Run a real audit against this repo**

Using the same DeepSeek V4 Pro adapter setup already proven working this session:

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion/prototype
export DEEPSEEK_API_KEY=$(ssh -o BatchMode=yes -o ConnectTimeout=8 root@187.127.169.89 "cd /root/aletheore/github-app && docker compose exec -T scan-worker printenv DEEPSEEK_API_KEY")
python3 - <<'PYEOF'
import sys
sys.path.insert(0, ".")
from aletheore.adapters.openai_compatible import OpenAICompatibleAdapter
from aletheore.report import run_reasoning_phase

adapter = OpenAICompatibleAdapter(
    name="DeepSeek",
    base_url="https://api.deepseek.com",
    api_key_env_var="DEEPSEEK_API_KEY",
    model="deepseek-v4-pro",
    supports_tool_choice=False,
)
report_path = run_reasoning_phase(adapter, "/Users/arihantkaul/Documents/GitHub/Veridion", "aletheore/manual")
print(f"Report written to: {report_path}")
PYEOF
```

- [ ] **Step 2: Confirm the Threat Model section actually appears with real citations**

Run: `grep -A 20 "^## Threat Model" /Users/arihantkaul/Documents/GitHub/Veridion/.aletheore/audit-report.md`

Expected: a real section with content organized by (or at minimum referencing) spoofing/tampering/information-disclosure/denial-of-service/elevation-of-privilege categories, citing real fields from this repo's own evidence (e.g., a real finding from `security.secrets`, `security.dependency_vulnerabilities`, or `git.ownership` — whatever this repo's actual current evidence contains at scan time), and a non-empty "what evidence doesn't cover" statement specific to threat modeling, not a copy-pasted generic disclaimer.

- [ ] **Step 3: Confirm no compliance-claim violation slipped in**

Run: `grep -iE "GDPR|HIPAA|SOC ?2|SOC ?3|ISO 27001|ISO 42001|CCPA|CPRA|FERPA|DPDP|compliant|compliance" /Users/arihantkaul/Documents/GitHub/Veridion/.aletheore/audit-report.md`

Expected: either no output, or any matches are *negative* statements ("no evidence of X compliance," matching the existing Documented Policy & Governance Gaps lens's own established pattern) — never an assertion that the repository *is* or *is not* compliant with anything.

- [ ] **Step 4: No commit needed — this task is verification-only**

If Steps 1-3 all pass, the new lens is confirmed working in a real, live audit, not just present in the manual file's text.
