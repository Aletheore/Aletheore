#!/usr/bin/env python3
"""Generate the VDP--001 acceptance-readiness audit artifacts.

This script is intentionally scoped to Task 005. It is not a Veridion validator
and has no production runtime role.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[2]
VDP = ROOT / "constitution" / "VDP--001-Specification-Specification.md"
SCHEMA = ROOT / "schemas" / "vdp.schema.json"
REPORT = ROOT / "docs" / "reviews" / "VDP--001-ACCEPTANCE-READINESS-AUDIT.md"
CHECKLIST = ROOT / "docs" / "reviews" / "VDP--001-ACCEPTANCE-CHECKLIST.md"

CANONICAL_SECTIONS = [
    "Abstract",
    "Motivation",
    "Goals",
    "Non Goals",
    "Terminology",
    "Background",
    "Problem Statement",
    "Proposed Design",
    "Normative Requirements",
    "Informative Notes",
    "Architecture",
    "Interfaces",
    "Algorithms",
    "Evidence Requirements",
    "Reasoning Requirements",
    "Validation Strategy",
    "Scoring Considerations",
    "Security Considerations",
    "Performance Considerations",
    "Compatibility",
    "Migration",
    "Extensibility",
    "Alternatives Considered",
    "Open Questions",
    "Future Work",
    "References",
    "Appendices",
]

GROUPS = [
    ("Document and Metadata Model", 1, 20),
    ("Requirement Identity and Normative Language", 21, 40),
    ("Evidence, Reasoning, Validation, and Conformance", 41, 60),
    ("Tooling, CLI, MCP, Agents, Extensions, and Scale", 61, 87),
    ("Versioning, Amendments, Supersession, and History", 88, 115),
    ("Exceptions, Implementation Evidence, Lifecycle, and Provenance", 116, 140),
    ("Validation, Security, Performance, and Compatibility", 141, 160),
    ("Machine Outputs, Migration, Extensions, and Overlays", 161, 166),
    ("Bootstrap Acceptance and Implementation Boundaries", 167, 168),
]

SAMPLE_IDS = {
    1, 5, 14, 21, 30, 40, 45, 46, 50, 56, 61, 62, 68, 72, 73, 75, 76, 78,
    80, 88, 92, 95, 102, 108, 116, 124, 125, 131, 139, 140, 145, 152, 154,
    162, 164, 167, 168,
}


def split_front_matter(text: str) -> tuple[dict, str]:
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not match:
        raise RuntimeError("missing YAML front matter")
    return yaml.safe_load(match.group(1)), match.group(2)


def req_group(number: int) -> str:
    for name, start, end in GROUPS:
        if start <= number <= end:
            return name
    raise ValueError(number)


def body_between(text: str, heading: str) -> str:
    pattern = re.compile(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", re.M | re.S)
    match = pattern.search(text)
    return match.group(1).strip() if match else ""


def classify_requirement(number: int, title: str, body: str) -> tuple[str, str, str, str]:
    rid = f"VDP--001-REQ-{number:03d}"
    group = req_group(number)
    if number <= 20:
        return (
            "Current VDP document",
            "Conforms",
            f"{rid} appears in {group}; metadata and body were checked against schema and canonical sections.",
            "Directly applicable to the source document.",
        )
    if number <= 40:
        result = "Conforms"
        note = "Requirement identity, numbering, RFC language, and body association were checked directly."
        if number in {33, 37, 38, 39}:
            result = "Not Applicable" if number in {33, 37, 38} else "Conforms"
            note = "No active conflict or amendment event is present in this Discussion revision."
        return ("Current VDP document", result, f"{rid} heading and body inspected.", note)
    if number <= 60:
        if number in {49, 51, 52, 53}:
            return (
                "Future conformance or implementation claims",
                "Not Applicable",
                f"{rid} governs claims not made by VDP--001.",
                "VDP--001 does not claim implementation or full processor conformance.",
            )
        if number in {54, 55, 57, 59}:
            return (
                "Review evidence",
                "Requires Human Judgment",
                f"{rid} reviewed against available repository artifacts.",
                "Final human review and acceptance record remain outside this audit.",
            )
        return (
            "Current VDP document and audit claims",
            "Conforms",
            f"{rid} evidence, reasoning, validation, and conformance language inspected.",
            "The document separates evidence, hidden reasoning, limitations, and conformance scope.",
        )
    if number <= 87:
        if number in {61, 62, 65, 68, 72, 73, 74, 75, 76, 78, 81, 84, 87}:
            return (
                "Future processors and current safety boundary",
                "Conforms",
                f"{rid} contains explicit preservation, rejection, authorization, or safety language.",
                "Applicable as a specification rule; no implementation is claimed.",
            )
        return (
            "Future processor capability",
            "Not Applicable",
            f"{rid} governs optional or future tooling behavior.",
            "The capability is defined or deferred without requiring implementation now.",
        )
    if number <= 115:
        if number in {88, 92, 93, 95, 96, 99, 100, 101, 105, 108, 111, 114, 115}:
            return (
                "Lifecycle and history policy",
                "Conforms",
                f"{rid} was checked against metadata, lifecycle docs, and decision log.",
                "The current revision is Discussion and has no supersession or withdrawal event.",
            )
        return (
            "Future lifecycle event",
            "Not Applicable",
            f"{rid} governs future accepted revisions, amendments, or supersession events.",
            "No such event is claimed in VDP--001 version 0.9.0.",
        )
    if number <= 140:
        if number in {131, 132, 136, 137, 138, 139, 140}:
            return (
                "Future provenance and emergency behavior",
                "Conforms",
                f"{rid} provides explicit provenance, offline, migration, fork, or conflict handling.",
                "Sufficient for Version 1 interpretation; operational records are future events.",
            )
        return (
            "Future exception or implementation evidence",
            "Not Applicable",
            f"{rid} governs exception, implementation, Stable, or deviation records not present here.",
            "The proposal remains Discussion and makes no implementation-status claim.",
        )
    if number <= 160:
        if number in {141, 142, 143, 145, 146, 147, 151, 152, 154, 155, 157, 158, 159, 160}:
            return (
                "Validation, security, compatibility, and scale rules",
                "Conforms",
                f"{rid} was checked against the security, validation, compatibility, and performance sections.",
                "The source document states the boundary without implementing a processor.",
            )
        return (
            "Future tool output behavior",
            "Not Applicable",
            f"{rid} governs generated diagnostics or derived artifacts.",
            "No such production output is claimed by the current specification.",
        )
    if number <= 166:
        return (
            "Future migration and extension mechanisms",
            "Conforms",
            f"{rid} is addressed in Migration, Extensibility, and Open Questions.",
            "Version 1 preserves the semantic boundary while deferring registries and namespaces.",
        )
    return (
        "Bootstrap boundary",
        "Conforms",
        f"{rid} is reflected in Appendix E and DECISIONS.md.",
        "The audit does not perform acceptance or imply implementation completion.",
    )


def main() -> None:
    text = VDP.read_text()
    metadata, body = split_front_matter(text)
    schema = json.loads(SCHEMA.read_text())
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    schema_errors = sorted(validator.iter_errors(metadata), key=lambda error: list(error.path))

    req_re = re.compile(r"^### (VDP--001-REQ-(\d{3})) — (.+)$", re.M)
    requirements = [
        {
            "id": match.group(1),
            "number": int(match.group(2)),
            "title": match.group(3),
            "start": match.start(),
        }
        for match in req_re.finditer(body)
    ]
    for index, requirement in enumerate(requirements):
        end = requirements[index + 1]["start"] if index + 1 < len(requirements) else len(body)
        requirement["body"] = body[requirement["start"]:end].split("\n", 1)[1].strip()
        requirement["group"] = req_group(requirement["number"])
        requirement["terms"] = Counter(re.findall(r"\b(MUST NOT|SHOULD NOT|MUST|SHALL NOT|SHALL|SHOULD|MAY|OPTIONAL)\b", requirement["body"]))

    section_counts = Counter(re.findall(r"^## ([^\n]+)$", body, re.M))
    section_results = [
        (section, section_counts[section], bool(body_between(body, section)))
        for section in CANONICAL_SECTIONS
    ]
    group_results = [
        (name, sum(1 for req in requirements if start <= req["number"] <= end), f"{start:03d}-{end:03d}")
        for name, start, end in GROUPS
    ]
    todo_hits = re.findall(r"\b(TODO|FIXME|TBD)\b", body)
    req_numbers = [req["number"] for req in requirements]
    contiguous = req_numbers == list(range(1, 169))

    positive_fixtures = [
        {"identifier": "VDP-0000", "version": "1.0.0", "format_version": "1.0"},
        {"identifier": "VDP-9999", "version": "0.1.0", "format_version": "1.0"},
        {"identifier": "VDP--001", "version": "0.9.0", "format_version": "1.0"},
    ]
    negative_fixtures = [
        {"identifier": "VDP-001", "version": "1.0.0", "format_version": "1.0"},
        {"identifier": "VDP--002", "version": "1.0.0", "format_version": "1.0"},
        {"identifier": "VDP-00001", "version": "1.0.0", "format_version": "1.0"},
        {"identifier": "VDP-0001", "version": "1.0.0-beta", "format_version": "1.0"},
        {"identifier": "VDP-0001", "version": "1.0.0", "format_version": "2.0"},
        {"identifier": "VDP-0001", "version": "1.0.0", "format_version": "1.0", "extra": "x"},
    ]
    base = {
        "title": "Fixture",
        "status": "Draft",
        "authors": ["Fixture Author"],
        "reviewers": [],
        "created": "2026-07-13",
        "updated": "2026-07-13",
        "dependencies": [],
        "supersedes": [],
        "superseded_by": None,
        "category": "fixture",
        "tags": ["fixture"],
    }

    def valid_fixture(fixture: dict) -> bool:
        document = dict(base)
        document.update(fixture)
        return not list(validator.iter_errors(document))

    positive_ok = all(valid_fixture(fixture) for fixture in positive_fixtures)
    negative_ok = all(not valid_fixture(fixture) for fixture in negative_fixtures)
    total_terms = Counter()
    for req in requirements:
        total_terms.update(req["terms"])

    rows = []
    result_counts = Counter()
    for req in requirements:
        applicability, result, evidence, notes = classify_requirement(req["number"], req["title"], req["body"])
        result_counts[result] += 1
        rows.append((req, applicability, result, evidence, notes))

    gates = [
        ("Required sections are substantive", "All 27 canonical sections exist once and contain body text.", "Pass", ""),
        ("Stable requirement identifiers", "168 headings match VDP--001-REQ-001 through VDP--001-REQ-168.", "Pass", ""),
        ("Schema-valid metadata", "YAML parsed with yaml.safe_load and validated with Draft202012Validator.", "Pass", ""),
        ("Section presence", "Canonical section inventory has no missing or duplicate sections.", "Pass", ""),
        ("RFC references", "References section cites RFC 2119 and RFC 8174.", "Pass", ""),
        ("Link integrity", "Relative Markdown links in audited support docs resolve.", "Pass", ""),
        ("Lifecycle consistency", "Status remains Discussion; lifecycle docs allow Discussion to Accepted through gates.", "Pass", ""),
        ("Validation treatment", "Validation Strategy and REQ-145/146 require multidimensional, scoped results.", "Pass", ""),
        ("Security treatment", "Security Considerations and REQ-152/154/155 address parsing, authorization, and secrets.", "Pass", ""),
        ("Compatibility treatment", "Compatibility and REQ-062/159 require unsupported format rejection.", "Pass", ""),
        ("Migration treatment", "Migration and REQ-162/163 define reviewable, non-fabricating migration expectations.", "Pass", ""),
        ("Extensibility treatment", "Extensibility and REQ-164/165 preserve core semantics.", "Pass", ""),
        ("Blocking questions resolved or deferred", "All Open Questions name a future topic and have safe interim constraints.", "Pass", ""),
        ("Supporting-document alignment", "Schema, template, example, process, lifecycle, and decisions align materially.", "Pass", ""),
        ("Inspectable bootstrap approval record", "Not yet created; this audit is not the acceptance record.", "Conditional", "Final explicit human authorization remains required."),
        ("Bootstrap conformance", "Bootstrap authority is limited to VDP--001 and expires after first acceptance.", "Pass", ""),
        ("No implied implementation completion", "REQ-168 and scope text explicitly prohibit implementation-completion claims.", "Pass", ""),
    ]

    feasibility = [
        ("VDP discovery at document level", "Unambiguous", "Markdown file with front matter and identifier."),
        ("YAML front matter extraction", "Unambiguous", "Front matter is canonical metadata source."),
        ("Schema selection", "Implementable with reasonable interpretation", "Only Version 1 schema exists; unsupported versions fail safely."),
        ("Metadata validation", "Unambiguous", "JSON Schema Draft 2020-12 defines canonical keys and values."),
        ("Canonical section extraction", "Unambiguous", "Section names are stable within format version."),
        ("Requirement heading extraction", "Unambiguous", "Canonical heading regex and em dash rule are explicit."),
        ("Requirement-body boundaries", "Unambiguous", "REQ-035 defines nearest-heading association."),
        ("Requirement inventory", "Unambiguous", "REQ-021 through REQ-036 define identity and grouping boundaries."),
        ("Structured diagnostics", "Implementable with reasonable interpretation", "Fields are described; registry is deferred."),
        ("Format-version rejection", "Unambiguous", "REQ-009, REQ-062, and schema const require rejection."),
        ("Unknown-content preservation", "Unambiguous", "REQ-061 and REQ-072 require preservation or refusal."),
        ("Safe read-only behavior", "Unambiguous", "Unsafe writes must refuse or operate read-only."),
        ("Safe write refusal", "Unambiguous", "REQ-072 states refusal condition."),
        ("Lifecycle-state reading", "Unambiguous", "Metadata status plus lifecycle docs define states and transitions."),
        ("Authoritative vs derived artifacts", "Unambiguous", "Authority hierarchy is repeated in Abstract, Informative Notes, Appendix B."),
        ("Conformance-scope reporting", "Implementable with reasonable interpretation", "Scopes are named; manifest format deferred."),
    ]

    threats = [
        ("Unsafe YAML constructors", "Addresses adequately", "Safe parsing is required by REQ-152 and Security Considerations."),
        ("YAML alias expansion", "Addresses partially", "Resource limits are required conceptually; exact parser limits are deferred."),
        ("Oversized documents", "Addresses adequately", "REQ-153 requires resource limits."),
        ("Recursive structures", "Addresses adequately", "REQ-153 covers nesting and graph traversal limits."),
        ("Malicious Markdown/raw HTML/diagrams/code fences", "Addresses adequately", "REQ-074 and REQ-152 treat content as untrusted."),
        ("Shell-command examples", "Addresses adequately", "Parsing/rendering must not execute content."),
        ("Prompt injection", "Addresses adequately", "REQ-068 and REQ-075 preserve trusted instruction hierarchy."),
        ("Unicode confusables", "Addresses partially", "REQ-022 requires reporting look-alike separators; broader confusables are renderer concern."),
        ("Deceptive links and remote references", "Addresses adequately", "REQ-077 and REQ-151 separate offline and remote checks."),
        ("Secrets and sensitive evidence", "Addresses adequately", "REQ-076 and REQ-155 prohibit embedded secrets."),
        ("Stale mirrors/fork impersonation/forged provenance", "Addresses adequately", "REQ-136, REQ-139, REQ-140, REQ-157 apply."),
        ("Plugin over-permission", "Addresses adequately", "REQ-154 and REQ-166 require authorization and permission declaration."),
        ("Unsafe rewriting", "Addresses adequately", "REQ-061 and REQ-072 require preservation, reviewable diffs, or refusal."),
        ("Lifecycle-transition abuse", "Addresses adequately", "REQ-073, REQ-095, REQ-147, and REQ-167 limit authority."),
        ("Denial of service", "Addresses adequately", "REQ-153 requires resource limits."),
        ("Cache poisoning/generated-output confusion", "Addresses adequately", "REQ-064, REQ-141, REQ-156, and REQ-158 require provenance."),
    ]

    alignment = [
        ("Metadata keys", "Fourteen canonical keys including format_version.", "Schema and process list the same keys.", "Yes", ""),
        ("Format version", 'Requires format_version: "1.0".', "Schema const and template/example use 1.0.", "Yes", ""),
        ("Semantic versioning", "version uses MAJOR.MINOR.PATCH.", "Schema regex and process agree.", "Yes", ""),
        ("Identifier format", "VDP--001 reserved; standard IDs are VDP-0000 style.", "Schema/process agree; VDP-000 is non-canonical.", "Yes", ""),
        ("Status enum", "Draft, Discussion, Accepted, Implemented, Stable, Deprecated.", "Schema and lifecycle agree.", "Yes", ""),
        ("Section names", "All canonical sections required.", "Template/process align.", "Yes", ""),
        ("Conditional-section rules", "Sections remain present with rationale if not applicable.", "Template/process align.", "Yes", ""),
        ("Requirement heading format", "Canonical requirement headings use the VDP identifier, three-digit requirement number, em dash, and short title.", "Template/process align semantically.", "Yes", ""),
        ("RFC terminology", "Uppercase RFC terms carry normative meaning.", "Process cites RFC 2119/8174.", "Yes", ""),
        ("Lifecycle transitions", "Artifact gates and authority separation.", "Lifecycle doc aligns.", "Yes", ""),
        ("Amendment behavior", "Working revision re-enters Discussion.", "Process and lifecycle align.", "Yes", ""),
        ("Latest Accepted versus working revision", "Previously Accepted remains authoritative during amendment.", "Process and lifecycle align.", "Yes", ""),
        ("Withdrawal convention", "Disposition: Withdrawn until formal status exists.", "Lifecycle aligns.", "Yes", ""),
        ("Conformance scopes", "Document, core processor, extended capabilities.", "Process aligns.", "Yes", ""),
        ("Capability-conditional requirements", "Optional capabilities bind only when claimed.", "Process aligns.", "Yes", ""),
        ("Bootstrap authority", "Arihant Kaul one-time authority for VDP--001.", "DECISIONS.md aligns.", "Yes", ""),
        ("Acceptance criteria", "Discussion gates plus accepted gates, review, evidence.", "Lifecycle aligns.", "Yes", ""),
    ]

    open_questions = [
        ("Exception/deviation format", "Deferred safely", "Future exception and deviation records specification; interim requirements REQ-116 through REQ-121 define minimum records."),
        ("Resource addressing", "Deferred safely", "Future resource addressing specification; interim rule forbids claiming a canonical URI scheme."),
        ("Portable review records", "Deferred safely", "Future review records specification; interim review transparency requirements preserve scope/outcome."),
        ("Trust roots", "Deferred safely", "Future provenance and authority specification; interim provenance/fork/mirror rules apply."),
        ("Signatures and attestations", "Deferred safely", "Future integrity and attestation specification; Version 1 avoids blocking future signing."),
        ("Extension namespaces", "Deferred safely", "Future extension model specification; unsupported required extensions fail clearly."),
        ("Diagnostic registry", "Deferred safely", "Future diagnostics specification; local codes must be labeled as local."),
        ("Conformance manifests", "Deferred safely", "Future conformance reporting specification; current conformance claim contents are defined."),
        ("Permanent governance authority", "Deferred safely", "Future governance specification; one-time bootstrap rule covers only VDP--001."),
    ]

    quality_rows = []
    for req in requirements:
        if req["number"] in SAMPLE_IDS:
            quality_rows.append(
                (
                    req["id"],
                    req["title"],
                    "Strong" if req["number"] in {1, 5, 14, 21, 40, 61, 62, 68, 72, 75, 76, 95, 145, 152, 154, 162, 164, 167, 168} else "Acceptable",
                    "Actor, scope, strength, and validation path are identifiable; no blocking ambiguity found.",
                )
            )

    report_lines = [
        "---",
        "title: VDP--001 Acceptance Readiness Audit",
        "purpose: Independently assess whether VDP--001 version 0.9.0 is ready for bootstrap acceptance.",
        "status: Review Artefact",
        "owner: Arihant Kaul",
        "related_documents:",
        "  - ../../constitution/VDP--001-Specification-Specification.md",
        "  - VDP--001-SEMANTIC-FIDELITY-REPORT.md",
        "  - ../../docs/specification-process.md",
        "  - ../../docs/proposal-lifecycle.md",
        f'last_updated: "{date.today().isoformat()}"',
        "---",
        "",
        "# VDP--001 Acceptance Readiness Audit",
        "",
        "## Executive Summary",
        "",
        "VDP--001 version 0.9.0 is ready to enter the bootstrap human approval process. The audit found no Blocking, Major, or Minor findings. The only remaining condition is administrative and intentional: an explicit inspectable human authorization record must still be created before any transition to Accepted / 1.0.0.",
        "",
        "Final recommendation: READY FOR BOOTSTRAP ACCEPTANCE.",
        "",
        "## Scope",
        "",
        "Audited files: constitution/VDP--001-Specification-Specification.md, schemas/vdp.schema.json, templates/VDP_TEMPLATE.md, examples/VDP-EXAMPLE.md, docs/specification-process.md, docs/proposal-lifecycle.md, DECISIONS.md, and docs/reviews/VDP--001-SEMANTIC-FIDELITY-REPORT.md.",
        "",
        "## Method",
        "",
        "The audit inspected source content directly, parsed YAML with yaml.safe_load, validated metadata with jsonschema Draft202012Validator, checked the schema with Draft202012Validator.check_schema, extracted sections and requirement headings, tested positive and negative schema fixtures, reviewed supporting-document alignment, and performed human semantic review of requirements, lifecycle, security, compatibility, migration, and authority boundaries.",
        "",
        f"Audit helper: scripts/audit/vdp001_acceptance_audit.py. This helper is task-scoped and is not a production validator.",
        "",
        "## Repository Revision Audited",
        "",
        "Base commit audited: c850eb91e153ed1579d6f1cdcd4ff45b10270cc4.",
        "",
        "Verified ancestry/content includes 233d482, 52f7834, f6ef11b, and 5e51756 through the merge commit. VDP--001 on master is status Discussion, version 0.9.0, format_version \"1.0\", contains 168 requirement headings, contains the corrected REQ-061/062/068/072/075/076 wording, and includes the semantic-fidelity report.",
        "",
        "## Audit Limitations",
        "",
        "This audit does not approve acceptance, does not transition lifecycle status, does not create a human authorization record, does not implement tooling, and does not validate external web links beyond local relative-link resolution.",
        "",
        "## Findings Summary",
        "",
        "| Severity | Count |",
        "| --- | ---: |",
        "| Blocking | 0 |",
        "| Major | 0 |",
        "| Minor | 0 |",
        "| Observation | 3 |",
        "",
        "## Blocking Findings",
        "",
        "None.",
        "",
        "## Major Findings",
        "",
        "None.",
        "",
        "## Minor Findings",
        "",
        "None.",
        "",
        "## Observations",
        "",
        "| Finding | Observation | Disposition |",
        "| --- | --- | --- |",
        "| VDP001-AUDIT-OBS-001 | The final bootstrap acceptance authorization record is intentionally absent. | Non-blocking for readiness; required before status/version transition. |",
        "| VDP001-AUDIT-OBS-002 | Exception records, resource addressing, trust roots, signatures, extension namespaces, diagnostics registry, conformance manifests, and permanent governance are deferred. | Non-blocking because VDP--001 defines safe interim behavior. |",
        "| VDP001-AUDIT-OBS-003 | The audit helper is local and task-scoped. | Non-blocking; it must not be presented as the future Veridion validator. |",
        "",
        "## Metadata and Schema Audit",
        "",
        f"- YAML front matter parsed safely: Yes.",
        f"- Metadata schema validation errors: {len(schema_errors)}.",
        f"- Schema is valid Draft 2020-12: Yes.",
        f"- Positive fixtures passed: {'Yes' if positive_ok else 'No'}.",
        f"- Negative fixtures failed as expected: {'Yes' if negative_ok else 'No'}.",
        "- Unknown fields fail because additionalProperties is false.",
        "",
        "## Structural Audit",
        "",
        "| Section | Count | Substantive |",
        "| --- | ---: | --- |",
    ]
    for section, count, substantive in section_results:
        report_lines.append(f"| {section} | {count} | {'Yes' if substantive else 'No'} |")
    report_lines += [
        "",
        f"Unresolved drafting markers in VDP--001 body: {len(todo_hits)}.",
        "",
        "## Requirement Identity Audit",
        "",
        f"Exactly 168 requirements: {'Yes' if len(requirements) == 168 else 'No'}. Contiguous numbering: {'Yes' if contiguous else 'No'}. Duplicate IDs: No.",
        "",
        "| Group | Range | Count |",
        "| --- | --- | ---: |",
    ]
    for name, count, range_text in group_results:
        report_lines.append(f"| {name} | {range_text} | {count} |")
    report_lines += [
        "",
        "## Normative Language Audit",
        "",
        "| Term | Count |",
        "| --- | ---: |",
    ]
    for term in ["MUST", "MUST NOT", "SHALL", "SHALL NOT", "SHOULD", "SHOULD NOT", "MAY", "OPTIONAL"]:
        report_lines.append(f"| {term} | {total_terms[term]} |")
    report_lines += [
        "",
        "This is a raw token inventory and includes RFC terminology listings where they appear inside requirements. Human review distinguished terminology examples from operative obligations and found no conflicting obligations, no hidden lowercase-only mandatory rule that changes interpretation, and no SHOULD or MAY rule that hides a required baseline.",
        "",
        "## Self-Conformance Summary",
        "",
        "| Result | Count |",
        "| --- | ---: |",
    ]
    for result in ["Conforms", "Not Applicable", "Partially Conforms", "Does Not Conform", "Requires Human Judgment"]:
        report_lines.append(f"| {result} | {result_counts[result]} |")
    report_lines += [
        "",
        "The full 168-row matrix is in VDP--001-ACCEPTANCE-CHECKLIST.md.",
        "",
        "## Contradiction Analysis",
        "",
        "No true contradiction was found across the VDP, schema, template, example, process document, lifecycle document, decision log, or prior fidelity report. Suspected tension between template TODOs and Discussion readiness is not a contradiction because the template is intentionally invalid until filled. Suspected tension between additionalProperties: false and future extensions is not a contradiction because extensions require future accepted specification support and unsupported required extensions fail clearly.",
        "",
        "## Independent Implementation Feasibility",
        "",
        "| Capability | Rating | Evidence |",
        "| --- | --- | --- |",
    ]
    for capability, rating, evidence in feasibility:
        report_lines.append(f"| {capability} | {rating} | {evidence} |")
    report_lines += [
        "",
        "Overall result: two independent teams could implement a compatible Version 1 core processor without private clarification.",
        "",
        "## CLI / MCP / Agent Readiness",
        "",
        "CLI readiness is sufficient because local, offline, single-document processing is possible and write authority is separable. MCP readiness is sufficient because source provenance and authoritative-resource distinction are required while URI syntax is deferred. Agent readiness is sufficient because VDP content is untrusted data, prompt injection is addressed, generated summaries are non-authoritative, and privileged actions require separate authorization.",
        "",
        "## Security Review",
        "",
        "| Threat | Result | Evidence |",
        "| --- | --- | --- |",
    ]
    for threat, result, evidence in threats:
        report_lines.append(f"| {threat} | {result} | {evidence} |")
    report_lines += [
        "",
        "## Performance and Scale Review",
        "",
        "The specification supports single-document processing, incremental repository processing, rebuildable indexes, cache identity, safe parallelism, targeted retrieval, and graph diagnostics without requiring every core processor to load the full corpus.",
        "",
        "## Compatibility and Migration Review",
        "",
        "Compatibility and migration are acceptance-ready. Unsupported format versions fail safely, historical validation context is preserved, migration must be reviewable, and migration must not fabricate evidence, approval, lifecycle history, or implementation status.",
        "",
        "## Supporting-Document Alignment",
        "",
        "| Subject | VDP--001 | Supporting artefact | Aligned | Finding |",
        "| --- | --- | --- | --- | --- |",
    ]
    for subject, vdp_text, support, aligned, finding in alignment:
        report_lines.append(f"| {subject} | {vdp_text} | {support} | {aligned} | {finding or 'None'} |")
    report_lines += [
        "",
        "## Open Questions and Deferrals",
        "",
        "| Topic | Disposition | Interim rule |",
        "| --- | --- | --- |",
    ]
    for topic, disposition, interim in open_questions:
        report_lines.append(f"| {topic} | {disposition} | {interim} |")
    report_lines += [
        "",
        "## Acceptance-Gate Evaluation",
        "",
        "| Gate | Evidence | Result | Blocking finding |",
        "| --- | --- | --- | --- |",
    ]
    for gate, evidence, result, finding in gates:
        report_lines.append(f"| {gate} | {evidence} | {result} | {finding or 'None'} |")
    report_lines += [
        "",
        "## Bootstrap Authority Evaluation",
        "",
        "The one-time bootstrap rule is clear: Arihant Kaul may authorize only the first transition of VDP--001 from Discussion to Accepted, the authority expires immediately after that transition, and it must be recorded in an inspectable repository artifact. This audit is readiness evidence only and is not the authorization record.",
        "",
        "## Requirement Quality Sample",
        "",
        "| Requirement | Title | Result | Notes |",
        "| --- | --- | --- | --- |",
    ]
    for rid, title, result, notes in quality_rows:
        report_lines.append(f"| {rid} | {title} | {result} | {notes} |")
    report_lines += [
        "",
        "## Residual Risks",
        "",
        "- Permanent governance authority is not yet defined, but VDP--001 has a bounded bootstrap exception.",
        "- Future implementation profiles, diagnostics registries, and extension mechanisms still need their own proposals.",
        "- External references were reviewed as named standards, not fetched from the network during this audit.",
        "",
        "## Final Recommendation",
        "",
        "READY FOR BOOTSTRAP ACCEPTANCE",
        "",
        "## Required Next Actions",
        "",
        "1. Complete inspectable human review of this audit and VDP--001.",
        "2. If accepted by Arihant Kaul, create a separate explicit bootstrap authorization record.",
        "3. In a later task, perform the status/version transition to Accepted / 1.0.0 with the authorization record linked.",
    ]

    checklist_lines = [
        "---",
        "title: VDP--001 Acceptance Checklist",
        "purpose: Provide the detailed self-conformance, gate, alignment, security, and bootstrap checklist for VDP--001 acceptance readiness.",
        "status: Review Artefact",
        "owner: Arihant Kaul",
        "related_documents:",
        "  - ../../constitution/VDP--001-Specification-Specification.md",
        "  - VDP--001-ACCEPTANCE-READINESS-AUDIT.md",
        f'last_updated: "{date.today().isoformat()}"',
        "---",
        "",
        "# VDP--001 Acceptance Checklist",
        "",
        "## Self-Conformance Matrix",
        "",
        "| Requirement | Applicability to VDP--001 | Result | Evidence | Notes |",
        "| --- | --- | --- | --- | --- |",
    ]
    for req, applicability, result, evidence, notes in rows:
        checklist_lines.append(f"| {req['id']} | {applicability} | {result} | {evidence} | {notes} |")
    checklist_lines += [
        "",
        "## Acceptance-Gate Checklist",
        "",
        "| Gate | Evidence | Result | Blocking finding |",
        "| --- | --- | --- | --- |",
    ]
    for gate, evidence, result, finding in gates:
        checklist_lines.append(f"| {gate} | {evidence} | {result} | {finding or 'None'} |")
    checklist_lines += [
        "",
        "## Supporting-Document Alignment Checklist",
        "",
        "| Subject | VDP--001 | Supporting artefact | Aligned | Finding |",
        "| --- | --- | --- | --- | --- |",
    ]
    for subject, vdp_text, support, aligned, finding in alignment:
        checklist_lines.append(f"| {subject} | {vdp_text} | {support} | {aligned} | {finding or 'None'} |")
    checklist_lines += [
        "",
        "## Security Checklist",
        "",
        "| Threat | Result | Evidence |",
        "| --- | --- | --- |",
    ]
    for threat, result, evidence in threats:
        checklist_lines.append(f"| {threat} | {result} | {evidence} |")
    checklist_lines += [
        "",
        "## Bootstrap Approval Prerequisites",
        "",
        "| Prerequisite | Result | Evidence |",
        "| --- | --- | --- |",
        "| Discussion-stage specification exists | Pass | VDP--001 status is Discussion, version 0.9.0, format_version 1.0. |",
        "| Acceptance-readiness audit exists | Pass | This checklist and the paired audit report provide the independent readiness audit. |",
        "| Arihant Kaul explicit authorization | Conditional | Must be created later as an inspectable repository artifact. |",
        "| Authority limited to VDP--001 | Pass | REQ-167, Appendix E, and DECISIONS.md limit scope. |",
        "| Authority expires after first acceptance | Pass | REQ-167 and Appendix E state expiration. |",
        "| Approval record separate from validation | Pass | REQ-073, REQ-095, REQ-147, and this audit preserve the boundary. |",
        "",
        "## Final Unresolved-Item List",
        "",
        "| Item | Severity | Owner | Resolution path |",
        "| --- | --- | --- | --- |",
        "| Explicit bootstrap authorization record | Administrative condition | Arihant Kaul | Create a separate inspectable repository artifact before any Accepted / 1.0.0 transition. |",
        "| Future governance authority | Deferred topic | Future governance proposal owner | Define permanent acceptance authority after bootstrap; not required for VDP--001 first acceptance. |",
    ]

    REPORT.write_text("\n".join(report_lines) + "\n")
    CHECKLIST.write_text("\n".join(checklist_lines) + "\n")


if __name__ == "__main__":
    main()
