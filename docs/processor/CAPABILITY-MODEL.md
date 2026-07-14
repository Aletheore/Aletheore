---
title: Capability Model
purpose: Provide informative guidance for VDP-0003 capability and profile semantics.
status: Draft
owner: Arihant Kaul
related_documents:
  - ../../constitution/VDP-0003-Processing-Context-and-Capability-Model.md
  - ../../constitution/VDP-0002-Core-Processor-Model.md
last_updated: "2026-07-14"
---

# Capability Model

This document is informative. VDP-0003 is authoritative.

## Purpose

Capabilities describe what a Processor can do within a Processing Context. Profiles compose existing capabilities for common processing purposes without introducing new behavior.

## Capability Concepts

A capability is a declared behavior unit. Capabilities may have versions, lifecycle status, dependencies, limitations, and negotiation outcomes.

Illustrative capability areas include validation, migration, semantic model, documentation, repository graph, governance, dependency graph, and extension processing.

## Lifecycle

Capability lifecycle states are:

- Experimental;
- Draft;
- Stable;
- Deprecated;
- Removed.

Capability lifecycle is independent of Processor version.

## Negotiation

Processors advertise capabilities. Clients request capabilities or profiles. Negotiation classifies requested behavior as supported, unsupported, partially supported, or deprecated.

No transport protocol is defined here.

## Profiles

Profiles compose existing capabilities. Examples include Validation, Migration, Documentation, Governance, Semantic Analysis, and Repository Analysis. A profile does not create behavior outside the capabilities it composes.

## Non-Implementation Boundary

This document does not define a capability registry, profile registry, manifest schema, diagnostics format, extension wire protocol, CLI, MCP, HTTP, LSP, JSON, hosted API, validator interface, or executable behavior.
