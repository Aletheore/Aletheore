---
title: Decisions
purpose: Record concise architectural decisions for Veridion repository infrastructure.
status: Placeholder
owner: TODO
related_documents:
  - docs/specification-process.md
  - docs/proposal-lifecycle.md
  - schemas/vdp.schema.json
last_updated: TODO
---

# Decisions

## 2026-07-13 — Canonical VDP metadata and validation

**Decision**

Veridion Design Proposals use YAML front matter as canonical metadata. Extracted metadata is validated with `schemas/vdp.schema.json`, uses snake_case field names, and supports the reserved `VDP--001` identifier for the proposal-system specification.

**Rationale**

A single metadata source avoids divergence between Markdown bodies and machine-readable validation. JSON Schema provides a direct validation path while keeping proposal body content outside schema scope.

**Alternatives considered**

Handwritten Markdown metadata tables and duplicated JSON metadata were rejected because they create multiple editable sources of truth.

**Consequences**

VDP bodies must not repeat editable metadata values. Standard VDP identifiers remain strict, `VDP--001` is the only reserved negative-form identifier, and body validation remains outside the JSON Schema.

## 2026-07-13 — Proposal format version

**Decision**

VDP metadata requires `format_version: "1.0"` for Version 1 documents. The format version is separate from the proposal document `version`.

**Rationale**

Separating format and document versions lets the proposal system evolve without overloading proposal revision numbers.

**Alternatives considered**

Using only the proposal document version was rejected because it would not identify the syntax and processing rules used by the document.

**Consequences**

Version 1 processors interpret `format_version: "1.0"` strictly. Other format versions are invalid until a future specification defines them.

## 2026-07-13 — Bootstrap acceptance authority

**Decision**

Arihant Kaul may authorize the first transition of VDP--001 from Discussion to Accepted.

**Rationale**

The one-time authority avoids a circular dependency on future governance before the proposal system specification can be accepted.

**Alternatives considered**

Waiting for a general governance specification was rejected because VDP--001 is needed to establish the specification system that later governance proposals will use.

**Consequences**

The authority applies only to VDP--001, expires immediately after its first Accepted transition, must be recorded in an inspectable repository artifact, and does not establish acceptance authority for later VDPs.
