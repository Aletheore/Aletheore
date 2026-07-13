---
title: Specification Process
purpose: Describe the Veridion Design Proposal process without defining framework behavior.
status: Placeholder
owner: TODO
related_documents:
  - ../templates/VDP_TEMPLATE.md
  - ../schemas/vdp.schema.json
  - proposal-lifecycle.md
last_updated: TODO
---

# Specification Process

## Purpose

The Veridion Proposal System provides a reusable structure for future Veridion Design Proposals.

This document describes proposal infrastructure only. Governance authority is out of scope.

## Canonical Metadata

YAML front matter is the canonical metadata source for VDP Markdown files.

The canonical metadata keys are:

- `identifier`
- `title`
- `status`
- `version`
- `authors`
- `reviewers`
- `created`
- `updated`
- `dependencies`
- `supersedes`
- `superseded_by`
- `category`
- `tags`

Metadata must not be duplicated manually in the Markdown body as editable values.

Generated metadata tables may be added by future tooling, but generated output is not implemented here.

## Metadata Validation

Extracted YAML front matter must validate against [VDP metadata schema](../schemas/vdp.schema.json).

The schema validates metadata only. Markdown body validation is outside the JSON Schema.

## Identifier Rules

Standard VDP identifiers use `VDP-0000` format.

`VDP--001` is the reserved bootstrap identifier for the proposal-system specification itself.

No additional reserved identifiers are defined.

## Body Sections

VDP bodies must use [VDP template](../templates/VDP_TEMPLATE.md).

Sections must not be silently deleted from the template.

## Required Sections

- Abstract
- Motivation
- Goals
- Non Goals
- Terminology
- Problem Statement
- Proposed Design
- Normative Requirements
- Informative Notes
- Validation Strategy
- Security Considerations
- Performance Considerations
- Compatibility
- Migration
- Extensibility
- Alternatives Considered
- Open Questions
- Future Work
- References

## Conditional Sections

- Background
- Architecture
- Interfaces
- Algorithms
- Evidence Requirements
- Reasoning Requirements
- Scoring Considerations
- Appendices

Conditional sections must still appear in the document. A contributor may use `Not applicable.` with a short rationale.

## TODO Rules

Draft proposals may contain TODO markers.

Discussion proposals may contain placeholders only when unresolved sections are clearly marked.

Accepted proposals must not contain unresolved TODO markers in normative sections.

## Normative and Informative Content

Normative requirements belong in the Normative Requirements section.

Explanatory material belongs in Informative Notes or other non-normative sections.

Casual prose should avoid uppercase normative terms unless a requirement is intended.

## RFC 2119 and RFC 8174 Terminology

The key words MUST, MUST NOT, SHALL, SHALL NOT, SHOULD, SHOULD NOT, MAY, and OPTIONAL are to be interpreted according to RFC 2119 and RFC 8174 only when written in uppercase.

SHOULD requires an understood and documented reason when not followed.

This document does not define framework-specific meanings for RFC terminology.

## Dependencies and Supersession

Dependencies are listed in `dependencies` as VDP identifiers.

Superseded proposals are listed in `supersedes` as VDP identifiers.

A direct replacement is listed in `superseded_by` as a VDP identifier, or `null` when no direct replacement exists.

## Lifecycle

Proposal stage responsibilities and transition gates are described in [proposal lifecycle](proposal-lifecycle.md).

Acceptance authority is intentionally deferred to a future governance specification.

## References

- RFC 2119: Key words for use in RFCs to Indicate Requirement Levels
- RFC 8174: Ambiguity of Uppercase vs Lowercase in RFC 2119 Key Words
