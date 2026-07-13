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
