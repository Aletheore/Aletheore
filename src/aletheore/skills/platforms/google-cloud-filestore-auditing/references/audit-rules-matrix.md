# Filestore Audit Rules & Scoring Matrix

This reference specifies the grading rubrics, severity classification matrix,
metric calculations, and table schemas used to evaluate Filestore fleet health.

--------------------------------------------------------------------------------

## Table of Contents

| Section | Line hints |
| :--- | :--- |
| [1. Severity Classification Matrix](#1-severity-classification-matrix) | Lines 22-70 |
| [2. Overall Health Posture & Grading Algorithm](#2-overall-health-posture--grading-algorithm) | Lines 73-106 |
| [3. Fleet Metrics Calculation Formulas](#3-fleet-metrics-calculation-formulas) | Lines 109-120 |
| [4. Standard Report Schemas](#4-standard-report-schemas) | Lines 123-162 |
| • [4.1 Executive Posture Scorecard](#41-executive-posture-scorecard) | Lines 125-137 |
| • [4.2 Priority Findings & Remediation Matrix](#42-priority-findings--remediation-matrix) | Lines 139-151 |
| • [4.3 Instance Inventory & Compliance Status Table](#43-instance-inventory--compliance-status-table) | Lines 153-162 |

--------------------------------------------------------------------------------

## 1. Severity Classification Matrix

Every finding identified during the audit must be categorized into one of four
deterministic severity levels:

| Severity       | Emoji Badge    | Criteria /         | Remediation SLA      |
:                :                : Triggers           :                      :
| :------------- | :------------- | :----------------- | :------------------- |
| **`CRITICAL`** | 🚨 **CRITICAL** | • `0.0.0.0/0` or   | Immediate (within 24 |
:                :                : `\:\:/0` in        : hours)               :
:                :                : `ipRanges` with    :                      :
:                :                : `accessMode\:      :                      :
:                :                : READ_WRITE`<br>•   :                      :
:                :                : `NO_ROOT_SQUASH`   :                      :
:                :                : paired with open   :                      :
:                :                : network exposure   :                      :
| **`HIGH`**     | ⚠️ **HIGH**    | • Zero backups on  | Urgent (within 72    |
:                :                : active file share  : hours)               :
:                :                : (`backup_count ==  :                      :
:                :                : 0`)<br>•           :                      :
:                :                : `NO_ROOT_SQUASH`   :                      :
:                :                : on internal VPC    :                      :
:                :                : subnets<br>•       :                      :
:                :                : `0.0.0.0/0` with   :                      :
:                :                : `accessMode\:      :                      :
:                :                : READ_ONLY`<br>•    :                      :
:                :                : Multi-zone tier    :                      :
:                :                : (`REGIONAL` /      :                      :
:                :                : `ENTERPRISE`) with :                      :
:                :                : `satisfiesPzs\:    :                      :
:                :                : false`             :                      :
| **`MEDIUM`**   | ℹ️ **MEDIUM**  | • Stale backup     | Planned (next        |
:                :                : exceeding SLA      : maintenance cycle)   :
:                :                : threshold (> 7     :                      :
:                :                : days)<br>• Default :                      :
:                :                : open VPC export    :                      :
:                :                : (no explicit       :                      :
:                :                : `nfsExportOptions` :                      :
:                :                : defined)<br>•      :                      :
:                :                : Zonal instance     :                      :
:                :                : with               :                      :
:                :                : `satisfiesPzi\:    :                      :
:                :                : false`             :                      :
| **`LOW`**      | 🟢 **LOW**      | • Informational    | Advisory             |
:                :                : notices,           :                      :
:                :                : non-blocking       :                      :
:                :                : configuration      :                      :
:                :                : advice             :                      :

--------------------------------------------------------------------------------

## 2. Overall Health Posture & Grading Algorithm

The audit engine evaluates the aggregate findings across all instances in the
project and assigns a single **Overall Health Posture Grade**:

$$\text{Total Critical} = \sum \text{Findings with Severity }
\mathbf{CRITICAL}$$ $$\text{Total High} = \sum \text{Findings with Severity }
\mathbf{HIGH}$$ $$\text{Total Medium} = \sum \text{Findings with Severity }
\mathbf{MEDIUM}$$

### Posture Grade Determination

1.  **Grade F (🔴 CRITICAL RISK)**:
    -   *Condition*: $\text{Total Critical} \ge 1$
    -   *Description*: Severe security exposures detected (such as
        world-accessible NFS shares or root escalation risks). Requires
        immediate remediation.
2.  **Grade C (🟠 ELEVATED RISK)**:
    -   *Condition*: $\text{Total Critical} = 0 \text{ AND } \text{Total High}
        \ge 1$
    -   *Description*: High-risk operational vulnerabilities present
        (unprotected instances without backups or cross-zone physical separation
        failures).
3.  **Grade B (🟡 MODERATE)**:
    -   *Condition*: $\text{Total Critical} = 0 \text{ AND } \text{Total High} =
        0 \text{ AND } \text{Total Medium} \ge 1$
    -   *Description*: Moderate configuration gaps (stale backups exceeding SLA,
        default VPC export rules, or missing PZI isolation).
4.  **Grade A (🟢 HEALTHY)**:
    -   *Condition*: $\text{Total Critical} = 0 \text{ AND } \text{Total High} =
        0 \text{ AND } \text{Total Medium} = 0$
    -   *Description*: All instances fully compliant with backup SLA, security
        export rules, and zone isolation standards.

--------------------------------------------------------------------------------

## 3. Fleet Metrics Calculation Formulas

-   **Total Instances Audited**: $N_{\text{total}}$ (Count of all discovered
    instances in the project).
-   **Unprotected Instances**: $N_{\text{unprotected}}$ (Count of instances
    where `backup_count == 0`).
-   **Backup Protection Rate (%)**: $$\text{Backup Protection Rate} =
    \begin{cases} 100.0\% & \text{if } N_{\text{total}} = 0 \\ \left(
    \frac{N_{\text{total}} - N_{\text{unprotected}}}{N_{\text{total}}} \right)
    \times 100 & \text{if } N_{\text{total}} > 0 \end{cases}$$
-   **PZI Compliance Count**: Number of instances with `satisfiesPzi == true`.

--------------------------------------------------------------------------------

## 4. Standard Report Schemas

### 4.1 Executive Posture Scorecard

```markdown
## Executive Posture Scorecard: `{project_id}`

| Metric | Status | Details |
| :--- | :--- | :--- |
| **Overall Health Posture** | **`{status_badge}`** | `{posture_grade}` |
| **Instances Audited** | `{total_instances}` | Total Filestore instances evaluated |
| **Backup Protection Rate** | **`{backup_coverage_pct}%`** | `{protected}/{total_instances}` instances have active backups |
| **Critical & High Security Findings** | `{crit_count} Critical, {high_count} High` | Open exports, root squash, or missing backups |
| **PZI Isolation Compliance** | `{pzi_count}/{total_instances}` | Physical Zone Isolation adherence |
```

### 4.2 Priority Findings & Remediation Matrix

Sorted strictly in descending order of severity (`CRITICAL` $\to$ `HIGH` $\to$
`MEDIUM` $\to$ `LOW`):

```markdown
## Priority Findings & Remediation Matrix

| Severity | Instance ID | Category | Finding Description | Remediation Plan |
| :--- | :--- | :--- | :--- | :--- |
| 🚨 **CRITICAL** | `[instance]` | Security | [Clear description of vulnerability] | [Specific remediation action] |
| ⚠️ **HIGH** | `[instance]` | Disaster Recovery | [Clear description of backup gap] | [Attributed gcloud command or MCP action] |
```

### 4.3 Instance Inventory & Compliance Status Table

```markdown
## Filestore Instance Inventory & Compliance Status

| Instance ID | Location | Tier | Capacity | Reserved CIDR | Write IOPS | Throughput | PZI | PZS | Backups | Latest Backup |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `[name]` | `[zone/region]` | `[tier]` | `[capacity] GiB` | `[cidr]` | `[iops]` | `[mb_s] MB/s` | ✅ Yes / ❌ No | ✅ Yes / ❌ No / N/A | 🔴 0 / ✅ `[count]` | `[date]` |
```
