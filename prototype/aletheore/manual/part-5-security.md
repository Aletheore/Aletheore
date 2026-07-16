# Part V — Security

This section governs how to read `evidence.security`. Follow the mandatory verification
rules in Part I for everything below.

## What's in `evidence.security`

- `secrets.scanned_files`: how many files were scanned for secret patterns.
- `secrets.findings`: each with `path`, `line`, `pattern` (which rule matched),
  `match_preview` (redacted — never the real value), and `likely_placeholder` (a heuristic
  based on the file path containing "test"/"example"/"fixture"/"mock").
- `dependency_vulnerabilities.checked`: whether the OSV.dev check actually ran.
- `dependency_vulnerabilities.reason`: why it didn't run or failed, when `checked` is `false`.
- `dependency_vulnerabilities.findings`: each with `ecosystem`, `package`,
  `installed_version`, `advisory_id`, `summary`, and `severity` (a list of CVSS entries —
  may be empty even for a real finding, since not every advisory has a computed CVSS score).

## Mandatory rules

- **Never state a secret's real value.** Cite only `match_preview`. If asked to reveal more,
  refuse — `evidence.toon` itself never contains the real value, so there is nothing more to
  reveal.
- **Never claim "no vulnerabilities" when `dependency_vulnerabilities.checked` is `false`.**
  State plainly that the check did not run, and cite `reason`.
- **Treat `likely_placeholder: true` as a hint to weigh, not an automatic dismissal.** A real
  secret could coincidentally live at a path that matches a test-naming convention. Note the
  flag, don't silently drop the finding.

## What counts as noteworthy

- **A secret finding with `likely_placeholder: false`** is high severity by default — name
  the exact `path` and `line`, and state the `pattern` matched.
- **A secret finding with `likely_placeholder: true`** is still worth reporting, at lower
  confidence — say plainly that the path suggests (but does not guarantee) a placeholder
  value.
- **A dependency-vulnerability finding on a package that is actually imported somewhere**,
  per `repository.dependency_graph` or any module's `imports` list, outranks a finding on a
  package that only appears in the manifest with no confirmed import in the scanned modules —
  this cross-reference is now possible because the module graph already exists. State
  explicitly which case applies; if you can't confirm either way from evidence, say so.
- **Severity**: `severity` entries carry a raw CVSS vector string (e.g.
  `CVSS:3.1/AV:N/AC:L/.../A:H`), not a plain label. Do not translate this into "HIGH"/"LOW"
  yourself unless you can point to the specific vector components driving that judgment —
  otherwise, quote the vector and the advisory `summary` as the evidence, and let the human
  reader assess severity.

## What this section does not produce

Do not attempt OWASP/CWE/MITRE ATT&CK framework mapping, container/Kubernetes/cloud/IaC
findings, or authentication/authorization review (RBAC, JWT, OAuth, OIDC, SAML). Those are
not covered by any evidence this scanner produces. Do not scan or claim anything about git
history for secrets — only the current working tree was checked.
