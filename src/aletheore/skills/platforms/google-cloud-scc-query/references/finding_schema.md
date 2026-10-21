# Security Command Center Finding Schema Reference

This reference describes the common fields returned in the JSON payload of a
Google Cloud Security Command Center finding.

## Resource Name Patterns

Finding resource names follow either a 4-segment global pattern or a 5-segment
regional pattern when Data Residency (DRZ) is enabled:

### Global Resource Names (4 segments)

-   Organization-scoped:
    `organizations/{org_id}/sources/{source_id}/findings/{finding_id}`
-   Folder-scoped:
    `folders/{folder_id}/sources/{source_id}/findings/{finding_id}`
-   Project-scoped:
    `projects/{project_id}/sources/{source_id}/findings/{finding_id}`

### Regional Resource Names (5 segments)

-   Organization-scoped:
    `organizations/{org_id}/sources/{source_id}/locations/{location}/findings/{finding_id}`
-   Folder-scoped:
    `folders/{folder_id}/sources/{source_id}/locations/{location}/findings/{finding_id}`
-   Project-scoped:
    `projects/{project_id}/sources/{source_id}/locations/{location}/findings/{finding_id}`

Supported locations include `global`, `us`, `eu`, and `me-central2`.

## Sample JSON Payload

```json
{
  "name": "organizations/{org_id}/sources/{source_id}/locations/{location}/findings/{finding_id}",
  "parent": "organizations/{org_id}",
  "parentDisplayName": "Cloud Armor",
  "resourceName": "//compute.googleapis.com/projects/{project_id}/zones/{zone}/instances/{instance_name}",
  "findingClass": "TOXIC_COMBINATION",
  "category": "TOXIC_COMBINATION_PUBLIC_VM_WITH_EXCESSIVE_PERMISSIONS",
  "state": "ACTIVE",
  "severity": "CRITICAL",
  "eventTime": "2026-06-16T17:41:31Z",
  "createTime": "2026-06-16T17:41:31Z",
  "attackExposure": {
    "score": 0.85,
    "attackExposureResult": "organizations/{org_id}/simulations/{sim_id}/attackExposureResults/{result_id}"
  },
  "description": "Publicly accessible instance with exploitable software vulnerability and the ability to assume service accounts"
}
```

## Field Explanations

*   `name`: The unique identifier for the finding (either 4-segment global or
    5-segment regional format).
*   `parent`: The organization, folder, or project under which this finding is
    grouped.
*   `parentDisplayName`: The display name of the detector or source provider
    that emitted the finding (e.g., `"Cloud Armor"`, `"Vulnerability
    Assessment"`, `"Sensitive Data Protection"`, `"Event Threat Detection"`).
*   `findingClass`: The high-level classification of the finding. Valid enum
    values from `google/cloud/securitycenter/v2/finding.proto` are:
    -   `THREAT`: Unwanted or malicious activity.
    -   `VULNERABILITY`: Potential software weaknesses (CVEs, CVSS scores,
        packages).
    -   `MISCONFIGURATION`: Weaknesses in resource/asset configuration.
    -   `OBSERVATION`: Informational security observations.
    -   `SCC_ERROR`: Errors preventing SCC functionality.
    -   `POSTURE_VIOLATION`: Security posture drift or compliance violations.
    -   `TOXIC_COMBINATION`: Multiple security issues creating a severe attack
        path.
    -   `SENSITIVE_DATA_RISK`: Risks to assets containing sensitive data.
    -   `CHOKEPOINT`: Attack path simulation convergence resources.
    -   `EXTERNAL_EXPOSURE`: Public internet access exposures.
    -   `SECRET`: Exposed plaintext credentials, keys, or tokens.
*   `state`: The current status of the finding (`ACTIVE` or `MUTED`).
*   `severity`: Finding severity level (`CRITICAL`, `HIGH`, `MEDIUM`, `LOW`).
*   `description`: Contains more details or explanation about the finding.
*   `attackExposure`: Holds information about the computed exposure risk and
    simulation result identifiers.
*   `vulnerability`: Holds authentic CVE details (`cve.id`, `cve.cvssv3`,
    `cve.exploitationActivity`, `cve.observedInTheWild`, `cve.zeroDay`,
    `cve.upstreamFixAvailable`), package details (`offendingPackage`,
    `fixedPackage`), and `securityBulletin`.
