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

Processing Context is the immutable semantic input to a Veridion Processing Session. It lets processors explain what repository state, specifications, configuration, policies, profile, capabilities, versions, extensions, mode, and declared external inputs shaped a result.

## Context Contents

Processing Context may include:

- Discovered Repository Result;
- accepted specification set;
- declared configuration;
- declared policies;
- requested profile;
- extensions;
- supported versions;
- capability selection;
- mode;
- declared external inputs.

## Context Freeze

Context is frozen before conditional processing states produce normative or conformance conclusions. If repository state or environment conditions change after the freeze, the session continues against the frozen Context or terminates with diagnostics.

## Environment Boundary

Execution Environment is separate from Context. Filesystem access, network availability, sandbox, memory, CPU, operating system, interactive state, clock, and process limits are environment conditions. They affect execution ability, but they do not become semantic inputs unless explicitly captured into Context or reported as limitations.

## Non-Implementation Boundary

This document does not define a Context schema, JSON shape, manifest integration, CLI flags, MCP resources, HTTP payloads, LSP messages, validator API, or executable behavior.
