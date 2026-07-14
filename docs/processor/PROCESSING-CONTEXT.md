---
title: Processing Context
purpose: Provide informative guidance for VDP-0003 Processing Context semantics.
status: Draft
owner: Arihant Kaul
related_documents:
  - ../../constitution/VDP-0003-Processing-Context-and-Capability-Model.md
  - ../../constitution/VDP-0002-Core-Processor-Model.md
last_updated: "2026-07-14"
---

# Processing Context

This document is informative. VDP-0003 is authoritative.

## Purpose

Processing Context is the immutable semantic input to a Veridion Processing Session. It is constructed only after pre-session capability negotiation completes sufficiently for the requested operation.

```text
Discovered Repository Result
  + Processor Descriptor
  + Processing Request
  -> Negotiation Result
  -> Processing Context
  -> freeze
  -> VDP-0002 Processing Session
```

Negotiation is pre-session orchestration. A concrete command or API may hide that boundary from a user, but the semantic boundary remains.

## Context Contents

Processing Context may include:

- Discovered Repository Result;
- Processing Request;
- Processor Descriptor;
- Negotiation Result;
- accepted specification set;
- declared configuration;
- declared policies;
- exact requested profile definition;
- extensions;
- supported versions;
- capability selection;
- mode;
- declared external inputs.

## Context Freeze

Context is frozen before conditional processing states produce normative or conformance conclusions. If repository state or environment conditions change after the freeze, the session continues against the frozen Context or terminates with diagnostics.

## Context Identity

Every frozen Context has a stable context identity or equivalent reproducible provenance record. That identity or record is sufficient to identify the discovered repository snapshot, accepted specifications and revisions, Processing Request, Processor Descriptor, Negotiation Result, selected capabilities, exact profile definition, policies, configuration, extensions, declared external inputs, relevant captured environment facts, mode, and lifecycle sources.

Processing Results reference the exact Context identity or include an equivalent reproducible Context record. If exact reconstruction is impossible, the Result discloses the limitation and does not claim full reproducibility.

## Environment Boundary

Execution Environment is separate from Context. Filesystem access, network availability, sandbox, memory, CPU, operating system, interactive state, clock, and process limits are environment conditions. They affect execution ability, but they do not become semantic inputs unless explicitly captured into Context or reported as limitations.

Implementation support, environment availability, policy permission, dependency satisfaction, and version compatibility are separate dimensions. A capability is not implementation-unsupported merely because the network is offline, policy blocks it, a dependency is unavailable, or a sandbox is restrictive.

## Non-Implementation Boundary

This document does not define a Context schema, JSON shape, manifest integration, CLI flags, MCP resources, HTTP payloads, LSP messages, validator API, or executable behavior.
