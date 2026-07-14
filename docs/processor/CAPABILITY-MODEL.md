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

Capabilities describe what a Processor can do. Negotiation compares a Processor Descriptor, Processing Request, capability definitions, dependencies, policies, environment availability, and versions before Processing Context is constructed. Profiles compose existing capabilities for common processing purposes without introducing new behavior.

## Capability Concepts

A capability is a declared behavior unit. Capabilities may have identifiers, source namespaces, versions, lifecycle status, lifecycle authority sources, dependencies, limitations, implementation support, runtime availability, and negotiation outcomes.

Illustrative capability areas include validation, migration, semantic model, documentation, repository graph, governance, dependency graph, and extension processing.

## Identifier Namespaces

Capability identifiers identify a source namespace or authority class. Semantic categories include core capability, organization-qualified capability, extension-qualified capability, and local experimental capability. Local identifiers are not globally standardized, and namespace ownership is not inferred from repository hosting or popularity.

## Lifecycle

Capability lifecycle states describe maturity, not implementation support:

- Experimental;
- Draft;
- Stable;
- Deprecated;
- Removed.

Capability lifecycle is independent of Processor version. Authoritative lifecycle status derives from Accepted specifications, accepted capability records, valid extension declarations under an accepted extension model, or other explicitly authorized artifacts. Processor-declared lifecycle claims are implementation-declared and non-authoritative unless backed by an authoritative source.

## Negotiation

Processors expose a Processor Descriptor. Clients provide a Processing Request. Negotiation produces a Negotiation Result before Context construction.

Negotiation does not collapse status dimensions:

| Dimension | Meaning | Example states |
| --- | --- | --- |
| Support | Whether the implementation claims behavior exists. | supported, partially_supported, unsupported |
| Availability | Whether behavior can execute for this request. | available, blocked_by_policy, unavailable_in_environment, dependency_unsatisfied, version_incompatible, deferred |
| Lifecycle | Maturity of capability or profile. | Experimental, Draft, Stable, Deprecated, Removed |
| Dependency | Required dependency closure state. | satisfied, partially_satisfied, unsatisfied, unknown |

No transport protocol is defined here.

## Dependencies

Required dependency closure is resolved before a capability or profile is selected as fully supported. Closure accounts for identifiers, version constraints, transitive dependencies, lifecycle compatibility, support, availability, policy restrictions, and extension requirements. Optional dependencies remain distinct and may reduce functionality without being reported as satisfied required closure.

## Profiles

Profiles compose existing capabilities. Examples include Validation, Migration, Documentation, Governance, Semantic Analysis, and Repository Analysis. A profile does not create behavior outside the capabilities it composes.

A profile has identity, version, source or authority, composed capability identifiers and version constraints, required and optional capabilities, dependency closure, lifecycle status when applicable, limitations, and compatibility expectations. Two profiles with the same display name but different identifiers, versions, sources, or composition are distinct profiles and are not silently merged.

## Non-Implementation Boundary

This document does not define a capability registry, profile registry, manifest schema, diagnostics format, extension wire protocol, CLI, MCP, HTTP, LSP, JSON, hosted API, validator interface, or executable behavior.
