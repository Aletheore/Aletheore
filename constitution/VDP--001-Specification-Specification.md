---
identifier: VDP--001
title: Specification Specification
status: Discussion
version: 0.9.0
format_version: "1.0"
authors:
  - Arihant Kaul
reviewers: []
created: "2026-07-13"
updated: "2026-07-13"
dependencies: []
supersedes: []
superseded_by: null
category: constitution
tags:
  - proposal-system
  - specification
  - governance-foundation
---

# Specification Specification

VDP--001 is the reserved bootstrap identifier for the Veridion Proposal System specification itself. No other reserved negative-form identifiers are defined.

This specification defines proposal-system rules. It does not define Veridion product behavior, repository discovery, scoring, engine architecture, CLI implementation, MCP implementation, or AI architecture.

## Abstract

This specification defines Version 1 of the Veridion Design Proposal format and processing expectations.

VDPs govern significant technical, architectural, security, operational, and governance decisions for Veridion. Routine repository changes do not automatically require a VDP.

The Markdown VDP document is the authoritative specification artifact. YAML front matter is authoritative only for metadata. Normative Requirements govern conformance obligations. Informative content supports interpretation but does not override normative requirements. Derived artifacts, summaries, embeddings, generated JSON, MCP responses, and model interpretations are non-authoritative.

## Motivation

Veridion needs a durable proposal system before substantive framework specifications can be authored.

The proposal system preserves rationale, evidence, trade-offs, validation, history, and lifecycle state. It must be readable without proprietary tooling while remaining suitable for future CLI tools, MCP servers, coding and review agents, CI validation, hosted platforms, IDE integrations, third-party extensions, and audit systems.

## Goals

- Define a canonical Markdown and YAML front matter format for VDPs.
- Define stable normative requirement identifiers.
- Define the Version 1 machine-processing boundary.
- Define evidence, reasoning, validation, and conformance expectations.
- Define lifecycle, amendment, supersession, exception, and provenance rules.
- Preserve compatibility with future automation without giving automation approval authority.

## Non Goals

- Define Veridion product architecture.
- Define repository discovery.
- Define scoring algorithms.
- Define evidence models beyond proposal-system classification rules.
- Define CLI, MCP, hosted, plugin, or agent implementation details.
- Define permanent governance or general acceptance authority.
- Define cryptographic signing, attestation, or trust roots.

## Terminology

- Authoritative VDP: The Markdown proposal file that governs the proposal.
- Metadata: YAML front matter fields validated by the metadata schema.
- Normative Requirement: A numbered requirement in the Normative Requirements section.
- Informative Content: Explanatory content that supports interpretation but does not override normative requirements.
- Processor: Software that reads, validates, indexes, transforms, or reports on VDPs.
- Extended Capability: Optional behavior such as CLI, MCP, agent, hosted, plugin, graph analysis, or IDE integration support.
- Working Revision: A proposal revision under active amendment.
- Authoritative Revision: The latest revision that has completed the required lifecycle transition.

## Background

The Veridion repository was bootstrapped with a proposal scaffold, schema, example, and process documentation. VDP--001 completes the proposal-system specification so future proposals can be authored against a stable foundation.

VDP--001 intentionally uses a reserved identifier because it specifies the proposal system itself before the standard namespace is populated.

## Problem Statement

Without an explicit proposal system, future specifications could diverge in metadata, requirement identity, lifecycle interpretation, evidence handling, and tool compatibility.

The project needs a specification format that remains human-readable, machine-processable, auditable, and resistant to accidental authority shifts from tools, generated artifacts, or AI-generated interpretations.

## Proposed Design

VDPs are Markdown documents with YAML front matter. The front matter is validated as metadata. The body contains canonical sections. Normative requirements use stable visible Markdown headings.

The Version 1 machine boundary is the visible Markdown requirement heading format. Processors may parse more structure, but they must preserve the authoritative Markdown artifact and avoid converting derived outputs into authority.

The system defines three conformance scopes:

- Document conformance applies to VDP files.
- Core processor conformance applies to parsing, metadata validation, section extraction, and diagnostics.
- Extended capability conformance applies only to claimed capabilities such as MCP, CLI, agents, plugins, hosted services, or graph analysis.

Capability-specific requirements apply only when that capability is claimed.

## Normative Requirements

### Document and Metadata Model

### VDP--001-REQ-001 — Authoritative document artifact

A VDP document MUST be a Markdown artifact that remains readable without proprietary tooling.

### VDP--001-REQ-002 — Metadata front matter

A VDP document MUST begin with YAML front matter containing the canonical metadata fields defined by the metadata schema.

### VDP--001-REQ-003 — Metadata authority boundary

YAML front matter MUST be authoritative only for metadata and MUST NOT override normative body requirements.

### VDP--001-REQ-004 — Required metadata fields

VDP metadata MUST include `identifier`, `title`, `status`, `version`, `format_version`, `authors`, `reviewers`, `created`, `updated`, `dependencies`, `supersedes`, `superseded_by`, `category`, and `tags`.

### VDP--001-REQ-005 — Metadata key spelling

Machine-readable metadata keys MUST use the exact snake_case names defined by the metadata schema.

### VDP--001-REQ-006 — Format version

Version 1 VDP documents MUST declare `format_version: "1.0"`.

### VDP--001-REQ-007 — Proposal version

The `version` field MUST identify the proposal document version and MUST be independent of `format_version`.

### VDP--001-REQ-008 — Semantic version syntax

The `version` field MUST use `MAJOR.MINOR.PATCH` numeric semantic version syntax without prerelease or build metadata.

### VDP--001-REQ-009 — Standard identifier syntax

Standard VDP identifiers MUST match `VDP-[0-9]{4}`.

### VDP--001-REQ-010 — Reserved meta identifier

`VDP--001` MUST be the only reserved negative-form identifier defined by this specification.

### VDP--001-REQ-011 — Constitution identifier

The Veridion Constitution, when authored, MUST use the standard identifier `VDP-0000`.

### VDP--001-REQ-012 — Metadata duplication

VDP bodies MUST NOT maintain a second editable metadata table that duplicates YAML front matter values.

### VDP--001-REQ-013 — Dependencies metadata

The `dependencies` field MUST contain only valid VDP identifiers.

### VDP--001-REQ-014 — Supersession metadata

The `supersedes` field MUST contain only valid VDP identifiers.

### VDP--001-REQ-015 — Replacement metadata

The `superseded_by` field MUST contain either a valid VDP identifier or null.

### VDP--001-REQ-016 — Status values

The `status` field MUST be one of Draft, Discussion, Accepted, Implemented, Stable, or Deprecated.

### VDP--001-REQ-017 — Date strings

The `created` and `updated` fields MUST use ISO calendar date strings.

### VDP--001-REQ-018 — Author list

The `authors` field MUST be a non-empty unique list of author names or handles.

### VDP--001-REQ-019 — Reviewer list

The `reviewers` field MUST be a unique list and MAY be empty until reviewers are known.

### VDP--001-REQ-020 — Canonical section preservation

VDP documents MUST retain all canonical body sections even when a conditional section is not applicable.

### Requirement Identity and Normative Language

### VDP--001-REQ-021 — Stable requirement headings

Every normative requirement MUST have a stable visible Markdown heading.

### VDP--001-REQ-022 — Requirement heading format

Normative requirement headings MUST match `### <VDP identifier>-REQ-<three-digit number> — <short title>`.

### VDP--001-REQ-023 — Canonical em dash

The separator between a requirement identifier and title MUST be an em dash.

### VDP--001-REQ-024 — Contiguous numbering

Normative requirement numbers within a VDP MUST be contiguous unless a later accepted revision explicitly retires an identifier.

### VDP--001-REQ-025 — Requirement immutability after Discussion

Requirement identifiers MUST become immutable once the VDP reaches Discussion.

### VDP--001-REQ-026 — Retired identifier reuse

Retired requirement identifiers MUST NOT be reused for different requirements.

### VDP--001-REQ-027 — Requirement title stability

Requirement titles SHOULD remain stable after Discussion unless an amendment records the reason for change.

### VDP--001-REQ-028 — Normative language standards

Uppercase MUST, MUST NOT, SHALL, SHALL NOT, SHOULD, SHOULD NOT, MAY, and OPTIONAL MUST be interpreted according to RFC 2119 and RFC 8174.

### VDP--001-REQ-029 — Uppercase normative terms

Uppercase RFC 2119 terms MUST be used only when normative intent is present.

### VDP--001-REQ-030 — Lowercase prose

Lowercase uses of requirement words MUST NOT be interpreted as RFC 2119 requirements.

### VDP--001-REQ-031 — SHOULD rationale

When a SHOULD-level requirement is not followed, the reason MUST be understood and documented by the claiming party.

### VDP--001-REQ-032 — Normative section authority

The Normative Requirements section MUST be the primary source of conformance obligations.

### VDP--001-REQ-033 — Informative section authority

Informative sections MUST NOT override Normative Requirements.

### VDP--001-REQ-034 — Source code authority

Source code MUST NOT be treated as automatically normative for a VDP unless the VDP explicitly incorporates that code by reference as normative.

### VDP--001-REQ-035 — Generated artifact authority

Generated artifacts MUST NOT be treated as authoritative unless the authoritative VDP explicitly grants that role.

### VDP--001-REQ-036 — Model interpretation authority

AI-generated interpretations MUST NOT be treated as authoritative merely because a model produced them.

### VDP--001-REQ-037 — Requirement body association

Processors MUST associate requirement body text with the nearest preceding canonical requirement heading.

### VDP--001-REQ-038 — Non-requirement headings

Processors MUST NOT treat non-matching headings as normative requirement identifiers.

### VDP--001-REQ-039 — Requirement reference form

References to normative requirements SHOULD use the full requirement identifier.

### VDP--001-REQ-040 — Requirement title brevity

Requirement heading titles SHOULD be short enough to support diagnostics, references, and review comments.

### Evidence, Reasoning, Validation, and Conformance

### VDP--001-REQ-041 — Fact identification

VDP authors MUST distinguish facts from assumptions and inferences when the distinction affects interpretation.

### VDP--001-REQ-042 — Assumption identification

Assumptions that materially affect a proposal MUST be explicitly identified.

### VDP--001-REQ-043 — Inference identification

Inferences that materially affect a proposal MUST identify the facts or assumptions on which they rely.

### VDP--001-REQ-044 — Evidence classification

Evidence cited by a VDP MUST be classifiable as direct evidence, indirect evidence, author assertion, external reference, or implementation evidence.

### VDP--001-REQ-045 — Evidence provenance

Evidence references SHOULD preserve enough provenance for later review.

### VDP--001-REQ-046 — Evidence limitations

Known limitations of material evidence SHOULD be documented.

### VDP--001-REQ-047 — AI output as evidence

AI-generated content MUST NOT be considered evidence merely because a model produced it.

### VDP--001-REQ-048 — External standard references

When external standards are invoked normatively, the VDP MUST list them in References.

### VDP--001-REQ-049 — Validation strategy

Every VDP MUST include a Validation Strategy section.

### VDP--001-REQ-050 — Validation traceability

Validation statements SHOULD be traceable to requirements, evidence, or explicit assumptions.

### VDP--001-REQ-051 — Conformance claims

Conformance claims MUST identify the conformance scope being claimed.

### VDP--001-REQ-052 — Document conformance

Document conformance MUST apply to VDP files and their required metadata, sections, and requirement structure.

### VDP--001-REQ-053 — Core processor conformance

Core processor conformance MUST apply to parsing, metadata validation, section extraction, requirement extraction, and diagnostics.

### VDP--001-REQ-054 — Extended capability conformance

Extended capability conformance MUST apply only to capabilities explicitly claimed by the processor or service.

### VDP--001-REQ-055 — Optional capability neutrality

A processor MUST NOT be considered non-conforming merely because it does not implement an optional capability it does not claim.

### VDP--001-REQ-056 — Conformance evidence

Conformance claims SHOULD link validation evidence.

### VDP--001-REQ-057 — Implementation claims

Implementation claims MUST identify the implemented VDP revision.

### VDP--001-REQ-058 — Implementation deviations

Known deviations from a claimed VDP revision MUST be documented.

### VDP--001-REQ-059 — Validation diagnostics

Validation diagnostics SHOULD identify the relevant file location, metadata field, section, or requirement identifier.

### VDP--001-REQ-060 — Partial validation

Partial validation results MUST identify the validation scope that was and was not evaluated.

### Tooling, CLI, MCP, Agents, Extensions, and Scale

### VDP--001-REQ-061 — Automation boundary

Automation MAY parse, validate, index, compare, report, and prepare changes to VDP artifacts.

### VDP--001-REQ-062 — Approval authority boundary

Automation MUST NOT independently approve lifecycle transitions.

### VDP--001-REQ-063 — Untrusted input model

Processors MUST treat VDP content as untrusted input.

### VDP--001-REQ-064 — Safe YAML parsing

Processors that parse YAML MUST use safe parsing behavior that does not execute arbitrary code.

### VDP--001-REQ-065 — Prompt instruction isolation

Agentic systems MUST NOT treat VDP content as instructions that override system, developer, maintainer, or user instructions.

### VDP--001-REQ-066 — Prompt injection resistance

Agentic systems processing VDPs SHOULD apply prompt-injection-resistant handling for quoted, embedded, or generated content.

### VDP--001-REQ-067 — Safe write behavior

Tools that prepare changes SHOULD present inspectable diffs before writing or committing changes unless a user explicitly grants a narrower automated write mode.

### VDP--001-REQ-068 — CLI compatibility

CLI tools that claim VDP support MUST preserve authoritative Markdown content when reading or writing VDPs.

### VDP--001-REQ-069 — CLI diagnostics

CLI tools that claim validation support SHOULD emit structured diagnostics suitable for humans and automation.

### VDP--001-REQ-070 — MCP compatibility

MCP servers that expose VDP data MUST preserve the distinction between authoritative source and derived responses.

### VDP--001-REQ-071 — Agent compatibility

Agents that review or edit VDPs MUST preserve requirement identifiers unless explicitly performing an approved amendment.

### VDP--001-REQ-072 — Hosted platform compatibility

Hosted platforms that render VDPs MUST make the authoritative Markdown source discoverable.

### VDP--001-REQ-073 — IDE integration compatibility

IDE integrations that claim VDP support SHOULD provide diagnostics without requiring proprietary document conversion.

### VDP--001-REQ-074 — Extension preservation

Processors SHOULD preserve unknown non-authoritative extension data when performing round-trip edits.

### VDP--001-REQ-075 — Capability separation

Processors MUST distinguish core processing from optional extended capabilities.

### VDP--001-REQ-076 — Large corpus behavior

Processors SHOULD support incremental processing for large proposal corpora.

### VDP--001-REQ-077 — Scalable indexing

Indexing systems SHOULD use stable identifiers to support efficient updates.

### VDP--001-REQ-078 — Structured diagnostics

Diagnostics SHOULD include machine-readable codes when a diagnostic-code registry exists.

### VDP--001-REQ-079 — Diagnostic-code deferral

Until a diagnostic-code registry exists, processors MAY use local diagnostic codes if they avoid claiming registry authority.

### VDP--001-REQ-080 — Graph analysis capability

Graph analysis requirements apply only to processors that claim graph analysis capability.

### Versioning, Amendments, Supersession, and History

### VDP--001-REQ-081 — Version and status independence

Proposal document version and lifecycle status MUST be treated as separate dimensions.

### VDP--001-REQ-082 — Version change recording

Material proposal changes SHOULD update the proposal document version.

### VDP--001-REQ-083 — Accepted amendment path

Accepted VDPs MAY receive explicit reviewed amendments.

### VDP--001-REQ-084 — Normative amendment status

Normative amendments to Accepted VDPs MUST re-enter Discussion before becoming authoritative.

### VDP--001-REQ-085 — Working revision authority

While a normative amendment is under Discussion, the working revision MUST NOT replace the latest previously Accepted revision as authoritative.

### VDP--001-REQ-086 — Amendment acceptance

An amended revision MUST become authoritative only after completing the applicable acceptance process.

### VDP--001-REQ-087 — Review records

Review records for amendments SHOULD be retained in inspectable repository artifacts or linked systems.

### VDP--001-REQ-088 — Supersession discoverability

Superseded proposals MUST remain discoverable.

### VDP--001-REQ-089 — Deprecation discoverability

Deprecated proposals MUST remain discoverable.

### VDP--001-REQ-090 — Withdrawal convention

Until a formal disposition field or Withdrawn status exists, retained withdrawn proposals MUST use the visible notice `**Disposition: Withdrawn**`.

### VDP--001-REQ-091 — Withdrawn Draft activity

A withdrawn Draft MUST NOT be treated as active solely because metadata status remains Draft.

### VDP--001-REQ-092 — Historical integrity

Proposal history MUST NOT be rewritten to obscure prior authoritative revisions.

### VDP--001-REQ-093 — Replacement linkage

Direct replacements SHOULD populate `superseded_by` on the replaced proposal.

### VDP--001-REQ-094 — Supersession rationale

Supersession SHOULD document the reason for replacement.

### VDP--001-REQ-095 — Deprecated rationale

Deprecation MUST document the reason the proposal is no longer preferred.

### VDP--001-REQ-096 — Mirror behavior

Mirrors of VDP repositories SHOULD preserve identifiers, versions, statuses, and source links.

### VDP--001-REQ-097 — Fork behavior

Forks SHOULD avoid claiming upstream authority unless they preserve provenance and authority boundaries.

### VDP--001-REQ-098 — Conflicting authority handling

When authority conflicts are detected, processors SHOULD report the conflict rather than silently selecting an authority.

### VDP--001-REQ-099 — Auditability

Lifecycle changes SHOULD be auditable through repository history or linked records.

### VDP--001-REQ-100 — Emergency action records

Emergency action affecting a VDP-controlled area MUST be recorded and reviewed retrospectively.

### Exceptions, Implementation Evidence, Lifecycle, and Provenance

### VDP--001-REQ-101 — Exception record separation

Exceptions and deviations MUST be explicit records separate from the normative VDP.

### VDP--001-REQ-102 — Exception rationale

Exception records MUST document rationale.

### VDP--001-REQ-103 — Exception scope

Exception records MUST identify scope.

### VDP--001-REQ-104 — Exception duration

Exception records SHOULD identify duration or review conditions.

### VDP--001-REQ-105 — Deviation evidence

Deviation records SHOULD link supporting evidence.

### VDP--001-REQ-106 — Implemented status prerequisite

A VDP MUST NOT be marked Implemented unless it is already Accepted.

### VDP--001-REQ-107 — Implementation evidence

Implemented status MUST link implementation evidence.

### VDP--001-REQ-108 — Validation evidence

Implemented status MUST link validation evidence.

### VDP--001-REQ-109 — Deviation documentation

Known deviations from the accepted design MUST be documented before marking a VDP Implemented.

### VDP--001-REQ-110 — Code presence insufficiency

Implementation status MUST NOT be inferred merely from code presence.

### VDP--001-REQ-111 — Stable status prerequisite

A VDP MUST NOT be marked Stable unless it is already Implemented.

### VDP--001-REQ-112 — Stable validation completion

Stable status MUST require completed validation.

### VDP--001-REQ-113 — Critical deviation closure

Stable status MUST NOT be used while unresolved critical deviations remain.

### VDP--001-REQ-114 — Stable compatibility accuracy

Stable status MUST require compatibility statements to reflect actual implementation.

### VDP--001-REQ-115 — Stable migration accuracy

Stable status MUST require migration statements to reflect actual implementation.

### VDP--001-REQ-116 — Operational evidence

Stable status SHOULD include operational or usage evidence where applicable.

### VDP--001-REQ-117 — Lifecycle transition legality

Lifecycle transitions MUST follow the legal transitions defined by the lifecycle documentation unless a future governance specification changes them.

### VDP--001-REQ-118 — Acceptance authority deferral

General acceptance authority MUST remain deferred to a future governance specification.

### VDP--001-REQ-119 — Provenance preservation

Processors SHOULD preserve source provenance when producing derived artifacts.

### VDP--001-REQ-120 — Repository record preference

Lifecycle authority records SHOULD be inspectable repository artifacts or durable linked records.

### Validation, Security, Performance, and Compatibility

### VDP--001-REQ-121 — Multi-dimensional validation

VDP validation SHOULD distinguish metadata validation, section validation, requirement validation, lifecycle validation, link validation, and capability validation.

### VDP--001-REQ-122 — Metadata schema validation

Core processors MUST validate metadata against the VDP metadata schema.

### VDP--001-REQ-123 — Section extraction

Core processors MUST be able to extract canonical section headings.

### VDP--001-REQ-124 — Requirement extraction

Core processors MUST be able to extract canonical requirement headings.

### VDP--001-REQ-125 — Requirement numbering validation

Core processors SHOULD validate requirement numbering for duplicates, gaps, and ordering.

### VDP--001-REQ-126 — Link validation

Processors that claim link validation MUST report unresolved relative links.

### VDP--001-REQ-127 — Security model

Processors MUST treat VDP parsing as processing untrusted text.

### VDP--001-REQ-128 — External reference safety

Processors SHOULD avoid fetching external references during validation unless explicitly configured to do so.

### VDP--001-REQ-129 — Resource exhaustion

Processors SHOULD guard against resource exhaustion from very large or adversarial documents.

### VDP--001-REQ-130 — YAML feature restrictions

Processors SHOULD reject or ignore YAML features not needed for the metadata model when those features increase security risk.

### VDP--001-REQ-131 — Compatibility with CommonMark

VDP Markdown SHOULD remain compatible with CommonMark where practical.

### VDP--001-REQ-132 — YAML compatibility

VDP front matter SHOULD remain compatible with YAML 1.2 where practical.

### VDP--001-REQ-133 — Backward compatibility

Future format changes SHOULD preserve readability of Version 1 VDPs.

### VDP--001-REQ-134 — Forward compatibility

Version 1 processors MUST reject unsupported `format_version` values rather than silently interpreting them as Version 1.

### VDP--001-REQ-135 — Performance on large corpora

Processors SHOULD avoid requiring whole-repository recomputation when incremental processing is sufficient.

### VDP--001-REQ-136 — Stable anchors

Requirement identifiers SHOULD be usable as stable anchors for diagnostics and traceability.

### VDP--001-REQ-137 — Human readability

VDP files MUST remain reviewable in plain text.

### VDP--001-REQ-138 — Proprietary tool independence

Conformance MUST NOT require proprietary tooling.

### VDP--001-REQ-139 — Compatibility diagnostics

Compatibility issues SHOULD be reported as diagnostics rather than silently ignored.

### VDP--001-REQ-140 — Migration documentation

Migration impact MUST be addressed or explicitly marked not applicable with rationale.

### Machine Outputs, Migration, Extensions, and Overlays

### VDP--001-REQ-141 — Derived artifact labeling

Derived artifacts SHOULD identify themselves as derived from an authoritative VDP.

### VDP--001-REQ-142 — Generated JSON authority

Generated JSON representations MUST NOT replace the authoritative Markdown VDP unless a future specification grants that authority.

### VDP--001-REQ-143 — Embedding authority

Embeddings MUST NOT be treated as authoritative specification content.

### VDP--001-REQ-144 — Summary authority

Summaries MUST NOT override the authoritative VDP.

### VDP--001-REQ-145 — MCP response authority

MCP responses derived from VDPs MUST NOT be treated as authoritative unless they point back to the authoritative source.

### VDP--001-REQ-146 — Model interpretation labeling

Model-generated interpretations SHOULD be labeled as interpretations.

### VDP--001-REQ-147 — Migration from scaffold

Existing scaffold VDPs migrated to Version 1 MUST add canonical YAML metadata, `format_version`, and visible requirement headings before claiming conformance.

### VDP--001-REQ-148 — Extension namespacing

Extension namespacing is deferred and extensions MUST NOT claim canonical namespace authority until a future specification defines it.

### VDP--001-REQ-149 — Overlay status

Overlays MAY annotate VDPs but MUST NOT modify authoritative content without an explicit repository change.

### VDP--001-REQ-150 — Overlay provenance

Overlays SHOULD identify their provenance.

### VDP--001-REQ-151 — Overlay conflict reporting

Overlay conflicts SHOULD be reported rather than silently resolved.

### VDP--001-REQ-152 — Portable review records

Portable review-record format is deferred and tools MUST NOT assume one canonical format exists.

### VDP--001-REQ-153 — URI addressing

URI and resource addressing format is deferred and tools MUST NOT assume one canonical addressing scheme exists.

### VDP--001-REQ-154 — Trust roots

Trust root and conflicting-authority rules are deferred and tools MUST report unresolved authority conflicts when detected.

### VDP--001-REQ-155 — Signatures

Signature and attestation requirements are deferred and MUST NOT be required for Version 1 document conformance.

### VDP--001-REQ-156 — Conformance manifest

Conformance-manifest format is deferred and processors MUST NOT require a manifest for core document conformance.

### VDP--001-REQ-157 — Diagnostic registry

Diagnostic-code registry format is deferred and local codes MUST NOT claim registry status.

### VDP--001-REQ-158 — Extension preservation on migration

Migration tools SHOULD preserve unknown extension content when it can be preserved without changing authoritative meaning.

### Bootstrap Acceptance and Implementation Boundaries

### VDP--001-REQ-159 — One-time bootstrap authority

Arihant Kaul, as initial repository owner and project steward, MAY authorize the first transition of VDP--001 from Discussion to Accepted.

### VDP--001-REQ-160 — Bootstrap authority scope

The bootstrap authority MUST apply only to VDP--001.

### VDP--001-REQ-161 — Bootstrap authority expiry

The bootstrap authority MUST expire immediately after the first Accepted transition of VDP--001.

### VDP--001-REQ-162 — Bootstrap authority record

Use of the bootstrap authority MUST be recorded in an inspectable repository artifact.

### VDP--001-REQ-163 — No general governance authority

The bootstrap authority MUST NOT establish general acceptance authority for later VDPs.

### VDP--001-REQ-164 — Product behavior exclusion

VDP--001 MUST NOT define Veridion product behavior.

### VDP--001-REQ-165 — Implementation exclusion

VDP--001 MUST NOT require implementation of CLI, MCP, parser, validator, hosted, plugin, agent, graph, or CI systems.

### VDP--001-REQ-166 — Capability-conditional obligations

Capability-specific requirements MUST apply only when the relevant capability is claimed.

### VDP--001-REQ-167 — Approval tooling exclusion

Tools MUST NOT convert validation success into lifecycle approval.

### VDP--001-REQ-168 — Future governance deferral

Permanent governance and acceptance authority MUST be defined by a future governance specification.

## Informative Notes

The proposal system is intended to support long-lived engineering review without making tools, generated outputs, or model interpretations authoritative.

The Version 1 machine boundary intentionally relies on visible Markdown headings so humans and tools can share one source.

## Architecture

The proposal system has four conceptual layers:

1. Authoritative Markdown VDP files.
2. YAML front matter metadata validated by JSON Schema.
3. Human-readable body sections and visible normative requirement headings.
4. Optional processors, indexes, diagnostics, renderers, agents, and integrations that derive non-authoritative outputs.

## Interfaces

The primary interface is a Markdown file with YAML front matter.

The metadata validation interface is the JSON object extracted from YAML front matter and validated against `schemas/vdp.schema.json`.

Future CLI, MCP, hosted, IDE, plugin, and agent interfaces may consume this format but are not defined by this VDP.

## Algorithms

Version 1 does not define a required parser algorithm.

A conforming core processor can operate using this abstract sequence:

1. Read the Markdown file as untrusted text.
2. Extract YAML front matter.
3. Parse YAML safely.
4. Validate metadata against the schema.
5. Extract canonical section headings.
6. Extract canonical requirement headings.
7. Emit diagnostics for validation failures.

## Evidence Requirements

Evidence cited by proposals should retain provenance and distinguish direct evidence, indirect evidence, author assertion, external reference, and implementation evidence.

Implementation and Stable claims require linked evidence as defined by the normative requirements.

## Reasoning Requirements

Proposals should distinguish facts, assumptions, and inferences when the distinction materially affects interpretation.

Reasoning obligations are especially important for SHOULD-level deviations, lifecycle changes, exceptions, and authority conflicts.

## Validation Strategy

VDP--001 can be validated by checking:

- YAML front matter parsing.
- Metadata schema conformance.
- Canonical section presence.
- Requirement heading count, numbering, uniqueness, and format.
- Absence of unresolved placeholders in normative requirements.
- Supporting document consistency.
- Relative link resolution.

## Scoring Considerations

Not applicable. This specification defines proposal-system rules and does not define Veridion scoring.

## Security Considerations

VDP content is untrusted input.

Processors should use safe YAML parsing, avoid executing content, guard against resource exhaustion, isolate agent instructions, and avoid treating AI-generated output as evidence or authority.

## Performance Considerations

The format should support large proposal corpora through stable identifiers, incremental processing, and structured diagnostics.

The specification does not require a processor to implement optional large-corpus indexes unless it claims that capability.

## Compatibility

VDP Version 1 aims to remain compatible with plain text review, CommonMark-style Markdown, YAML 1.2 front matter, JSON Schema Draft 2020-12 metadata validation, and Git-based repository history.

Processors must reject unsupported format versions rather than silently treating them as Version 1.

## Migration

Existing scaffold proposal documents migrate to Version 1 by adding canonical YAML metadata, adding `format_version: "1.0"`, preserving canonical sections, and using visible normative requirement headings.

No code migration is required by this specification.

## Extensibility

The system supports future CLI, MCP, agent, hosted, plugin, IDE, graph, audit, signature, review-record, and diagnostic-code specifications.

Extension namespacing, URI addressing, portable review records, trust roots, signatures, diagnostic registries, and conformance manifests are deferred.

## Alternatives Considered

- Markdown metadata tables: rejected because they create duplicate editable metadata.
- JSON-only proposals: rejected because they reduce human readability.
- Proprietary document formats: rejected because they reduce reviewability and portability.
- Invisible requirement identifiers: rejected because visible headings provide a shared human and machine boundary.
- Tool-enforced approval authority: rejected because validation and approval are separate responsibilities.

## Open Questions

- Exception/deviation format: deferred to a future exception and deviation records specification.
- URI/resource addressing: deferred to a future resource addressing specification.
- Portable review records: deferred to a future review records specification.
- Trust roots and conflicting authority: deferred to a future provenance and authority specification.
- Signatures and attestations: deferred to a future integrity and attestation specification.
- Extension namespacing: deferred to a future extension model specification.
- Diagnostic-code registry: deferred to a future diagnostics specification.
- Conformance-manifest format: deferred to a future conformance reporting specification.
- Permanent governance and acceptance authority: deferred to a future governance specification.

## Future Work

- Author the Veridion Constitution as `VDP-0000`.
- Define permanent governance and acceptance authority.
- Define exception and deviation record formats.
- Define diagnostic-code registry mechanics.
- Define extension namespacing.
- Define portable review records.
- Define trust, signature, and attestation mechanisms.
- Define conformance manifests.

## References

- RFC 2119: Key words for use in RFCs to Indicate Requirement Levels.
- RFC 8174: Ambiguity of Uppercase vs Lowercase in RFC 2119 Key Words.
- Semantic Versioning 2.0.0.
- JSON Schema Draft 2020-12.
- CommonMark.
- YAML 1.2.
- Git documentation.
- Rust RFC process.
- Python Enhancement Proposal process.
- Kubernetes Enhancement Proposal process.

## Appendices

### Appendix A — Normative Authority Hierarchy

1. Normative Requirements in the authoritative Markdown VDP.
2. Other normative language in the authoritative Markdown VDP.
3. YAML front matter for metadata only.
4. Informative text for interpretation only.
5. Derived artifacts, summaries, embeddings, generated JSON, MCP responses, and model interpretations as non-authoritative outputs.

### Appendix B — Conformance Scopes

Document conformance applies to VDP files.

Core processor conformance applies to parsing, metadata validation, section extraction, requirement extraction, and diagnostics.

Extended capability conformance applies only to claimed capabilities.

### Appendix C — Acceptance Criteria for VDP--001

VDP--001 is ready for acceptance review when metadata validates, all canonical sections exist, exactly 168 normative requirements are present, supporting artifacts are consistent, and the one-time bootstrap acceptance record can be inspected.

### Appendix D — Deferred Ecosystem Formats

Deferred ecosystem formats include exception/deviation records, URI/resource addressing, portable review records, trust roots, signatures and attestations, extension namespacing, diagnostic-code registries, and conformance manifests.

### Appendix E — Bootstrap Acceptance Authority

The initial repository owner, Arihant Kaul, may authorize the first transition of VDP--001 from Discussion to Accepted.

This authorization applies only to VDP--001.

It expires immediately after that transition.

It must be recorded in an inspectable repository artifact.

It does not establish governance authority for any later VDP.
