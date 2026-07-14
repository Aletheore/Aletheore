---
identifier: VDP-0003
title: Processing Context and Capability Model
status: Draft
version: 0.1.0
format_version: "1.0"
authors:
  - Arihant Kaul
reviewers: []
created: "2026-07-14"
updated: "2026-07-14"
dependencies:
  - VDP--001
  - VDP-0000
  - VDP-0001
  - VDP-0002
supersedes: []
superseded_by: null
category: processor
tags:
  - processing-context
  - capabilities
  - profiles
  - execution-environment
---

# Processing Context and Capability Model

## Abstract

VDP-0003 defines the immutable Processing Context and capability system used by Veridion Processors. It specifies what semantic inputs are captured before a Processing Session, how Execution Environment is separated from Context, how capabilities are declared, requested, negotiated, selected, composed into profiles, versioned through lifecycle states, and reflected in abstract Processing Results.

This specification does not define the Processor itself. VDP-0002 defines the Core Processor Model.

## Motivation

VDP-0002 establishes that Processor behavior is determined by equivalent context, profile, capability, configuration, policy, and declared inputs. VDP-0003 gives those concepts stable meaning and defines the pre-session negotiation boundary so concrete implementations can claim the same processing boundary without inventing incompatible capability models.

Without a shared Context and Capability model, Processors could silently rely on environment state, overclaim support, treat profiles as new behavior, confuse capability versions with Processor versions, or return results whose capability basis cannot be reconstructed.

## Goals

- Define immutable Processing Context.
- Define Execution Environment as separate from Processing Context.
- Define Capability, Capability Dependency, Capability Negotiation, Capability Lifecycle, and Processing Profile.
- Define abstract Processing Result Contract obligations without serialization.
- Define capability advertisement, request, selection, and result status semantics.
- Define profile composition without adding new behavior.
- Preserve VDP-0002 boundaries around discovery, catastrophic interruption, derived outputs, and deterministic processing.
- Address capability spoofing, profile escalation, unknown capabilities, conflicting declarations, version mismatches, and malicious extensions.

## Non Goals

- Define the Processor lifecycle or Processor authority model.
- Define diagnostics formats.
- Define manifest schema.
- Define CLI, MCP, HTTP, LSP, JSON, hosted API, or validator interfaces.
- Define extension wire protocol.
- Define repository graph serialization.
- Define implementation code, package layout, runtime behavior, or executable validation.
- Define concrete capability identifiers beyond illustrative examples.
- Define a complete profile registry.

## Terminology

- Processor Descriptor: A pre-session derived declaration of a Processor implementation's claimed support boundary.
- Processing Request: The caller's pre-session request for capabilities, profile, mode, policies, versions, and declared inputs.
- Negotiation Result: The pre-session result of comparing Processor Descriptor, Processing Request, capability definitions, dependencies, policies, environment availability, and supported versions.
- Processing Context: The immutable semantic input to a Processing Session, constructed only after negotiation completes sufficiently for the requested operation.
- Execution Environment: Runtime conditions under which a Processor executes.
- Capability: A declared unit of Processor behavior that can be advertised, requested, selected, negotiated, and reported.
- Capability Dependency: A relationship where one capability requires another capability.
- Capability Negotiation: The abstract pre-session process of comparing requested capabilities with Processor Descriptor support, definitions, dependencies, policy constraints, environment availability, and versions.
- Capability Lifecycle: The maturity status of a capability independent of Processor version.
- Processing Profile: A named composition of existing capabilities for a requested processing purpose.
- Requested Profile: The profile selected for a Processing Session.
- Capability Selection: The set of capabilities selected for a Processing Session after negotiation.
- Support Status: The semantic dimension describing whether an implementation can perform capability behavior.
- Availability Status: The semantic dimension describing whether supported behavior is executable in the current request, policy, dependency, version, and environment conditions.
- Dependency State: The semantic dimension describing whether required capability dependencies are satisfied, partially satisfied, unsatisfied, or unknown.
- Processing Result Contract: The abstract obligations a Processing Result must satisfy when reporting Context, capabilities, profile, lifecycle, and limitations.
- Mode: A declared processing mode that constrains how selected capabilities are used, without creating new capability behavior.

## Background

VDP-0001 defines repository discovery and produces a Discovered Repository Result. VDP-0002 defines the Processor and requires the Processor to consume that result, construct immutable Context, distinguish Context from Execution Environment, run mandatory and conditional lifecycle states, and keep outputs derived.

VDP-0003 defines the Context and capability model used by that Processor.

## Problem Statement

Processors need a common way to describe the semantic inputs and capability boundary for each Processing Session. The model must be immutable after Context construction, independent of runtime environment variation, explicit about unsupported or partial support, and precise enough for deterministic processing without defining concrete transport or serialization.

## Proposed Design

The abstract ordering is: Discovered Repository Result, Processor Descriptor, Processing Request, Capability Negotiation, Negotiation Result, Processing Context construction, Context freeze, VDP-0002 Processing Session, then Processing Result. Negotiation is pre-session orchestration. A concrete implementation may expose negotiation and processing through one command or API call, but the semantic boundaries remain distinct.

Processing Context is the immutable semantic bundle used by a Processor after negotiation. It contains or references the Processing Request, Processor Descriptor, Negotiation Result, selected capabilities, exact profile definition, declared policies and configuration, declared external inputs, Discovered Repository Result or equivalent discovered repository representation, accepted specifications and revisions, extensions and versions, mode, capability lifecycle sources, and relevant captured environment facts.

Execution Environment is separate. Filesystem access, network availability, sandbox, memory, interactive state, operating system, processor limits, clock, and process state may affect whether a Processor can execute, but they do not become authoritative input unless explicitly captured into Context or reported as limitations.

Capabilities describe everything a Processor can do. Negotiation separates implementation support, runtime availability, lifecycle maturity, and dependency state. Profiles compose capabilities for common purposes such as Validation, Migration, Documentation, Governance, Semantic Analysis, and Repository Analysis. Profiles never introduce behavior that is not already present through capabilities.

## Normative Requirements

### Processing Context

### VDP-0003-REQ-001 — Context definition

Processing Context MUST be the immutable semantic input to a Processing Session.

### VDP-0003-REQ-002 — Context immutability

Processing Context MUST NOT mutate during a Processing Session after it is frozen.

### VDP-0003-REQ-003 — Discovered repository inclusion

Processing Context MUST include or reference a VDP-0001-conformant Discovered Repository Result or logically equivalent discovered repository representation.

### VDP-0003-REQ-004 — Accepted specification inclusion

Processing Context MUST identify accepted specification identifiers, versions, and revisions used by the Processing Session.

### VDP-0003-REQ-005 — Configuration inclusion

Processing Context MUST include declared configuration that affects Processor behavior.

### VDP-0003-REQ-006 — Policy inclusion

Processing Context MUST include declared policies that affect capability selection, processing mode, validation scope, or result interpretation.

### VDP-0003-REQ-007 — Requested profile inclusion

Processing Context MUST include or reference the exact requested profile definition when a profile is requested.

### VDP-0003-REQ-008 — Extension inclusion

Processing Context MUST identify extensions used by the Processing Session when extensions affect behavior.

### VDP-0003-REQ-009 — Supported version inclusion

Processing Context MUST identify supported specification, capability, profile, and extension versions from the Processor Descriptor and Negotiation Result when those versions affect processing.

### VDP-0003-REQ-010 — Capability selection inclusion

Processing Context MUST include the selected capability identifiers, versions, lifecycle sources, and negotiation outcomes for the Processing Session.

### VDP-0003-REQ-011 — Mode inclusion

Processing Context MUST identify the declared processing mode when mode affects behavior.

### VDP-0003-REQ-012 — Declared external inputs

Processing Context MUST identify declared external inputs used by the session.

### VDP-0003-REQ-013 — No undeclared semantic inputs

A Processor MUST NOT use undeclared semantic inputs to change normative conclusions.

### VDP-0003-REQ-014 — Context freeze point

Processing Context MUST be frozen before conditional processing states produce normative or conformance conclusions.

### VDP-0003-REQ-015 — Context reconstructability

Processing Context MUST have a stable context identity or equivalent reproducible provenance record sufficient to reconstruct or identify the frozen semantic inputs.

### Execution Environment

### VDP-0003-REQ-016 — Environment definition

Execution Environment MUST mean mutable runtime conditions under which a Processor executes.

### VDP-0003-REQ-017 — Environment separation

Execution Environment MUST remain separate from Processing Context unless an environment-dependent fact is explicitly captured as declared input.

### VDP-0003-REQ-018 — Environment examples

Filesystem access, network availability, sandbox, memory, CPU, operating system, interactive state, clock, and process limits MUST be treated as Execution Environment conditions unless captured into Context.

### VDP-0003-REQ-019 — Behavior depends on Context

Normative Processor behavior MUST depend on Processing Context rather than directly on mutable Execution Environment.

### VDP-0003-REQ-020 — Environment limitations

Execution Environment limits that affect processing MUST be reported as availability limitations, partial support, failure, or interruption without being conflated with implementation support.

### VDP-0003-REQ-021 — Performance adaptation

Processors MAY adapt performance behavior to Execution Environment constraints when semantic equivalence is preserved.

### VDP-0003-REQ-022 — Environment non-authority

Execution Environment availability MUST NOT by itself become authoritative input.

### VDP-0003-REQ-023 — Environment capture

Environment-dependent facts that affect results MUST be captured in Context as declared inputs or disclosed in the Processing Result Contract as limitations.

### Capability Model

### VDP-0003-REQ-024 — Capability definition

A Capability MUST be a declared unit of Processor behavior.

### VDP-0003-REQ-025 — Capability expression

Everything a Processor claims it can do MUST be expressed as one or more capabilities.

### VDP-0003-REQ-026 — Capability examples

Capabilities MAY include validation, migration, semantic model, documentation, repository graph, governance, dependency graph, and extension processing.

### VDP-0003-REQ-027 — No implementation definition

A Capability MUST NOT require a specific implementation language, runtime, protocol, command, API, or storage format.

### VDP-0003-REQ-028 — Capability identifier stability

Capability identifiers MUST identify their source namespace or authority class and SHOULD remain stable within their lifecycle and version.

### VDP-0003-REQ-029 — Capability versioning

Capabilities MUST have versions or version references when compatibility depends on capability behavior.

### VDP-0003-REQ-030 — Capability scope

A Capability MUST define its behavioral scope without expanding Processor authority.

### VDP-0003-REQ-031 — Capability limitation disclosure

Processors MUST disclose material limitations for advertised, requested, partially available, or selected capabilities.

### VDP-0003-REQ-032 — Capability non-authority

Advertising or selecting a Capability MUST NOT make Processor output authoritative.

### Capability Lifecycle

### VDP-0003-REQ-033 — Lifecycle independence

Capability Lifecycle MUST be independent of Processor version and distinct from implementation support status.

### VDP-0003-REQ-034 — Experimental lifecycle

Experimental capabilities MUST be reported as experimental when advertised or selected and when the lifecycle source is authoritative or implementation-declared.

### VDP-0003-REQ-035 — Draft lifecycle

Draft capabilities MUST be reported as draft when advertised or selected and when the lifecycle source is authoritative or implementation-declared.

### VDP-0003-REQ-036 — Stable lifecycle

Stable capabilities MUST be reported as stable when advertised or selected and when the lifecycle source is authoritative or implementation-declared.

### VDP-0003-REQ-037 — Deprecated lifecycle

Deprecated capabilities MUST be reported as lifecycle-deprecated when advertised, requested, selected, or used.

### VDP-0003-REQ-038 — Removed lifecycle

Removed capabilities MUST NOT be selected for new Processing Sessions unless an accepted compatibility rule explicitly permits legacy handling.

### VDP-0003-REQ-039 — Lifecycle transition visibility

Capability lifecycle transitions SHOULD be visible in authoritative lifecycle sources or reviewable records.

### VDP-0003-REQ-040 — Lifecycle and compatibility

Capability lifecycle status MUST be reported separately from support status, availability status, and dependency state.

### Capability Dependencies

### VDP-0003-REQ-041 — Dependency declaration

Capabilities MAY declare required or optional dependencies on other capabilities.

### VDP-0003-REQ-042 — Acyclic dependencies

Capability dependency graphs MUST be acyclic, and cycles MUST prevent fully supported selection for affected capabilities.

### VDP-0003-REQ-043 — Unknown dependency handling

Unknown capability dependencies MUST NOT crash conforming Processors.

### VDP-0003-REQ-044 — Unknown dependency reporting

Unknown required capability dependencies MUST be reported as unsupported, partially supported, deferred, or dependency-unsatisfied when they affect requested processing.

### VDP-0003-REQ-045 — Dependency selection

A Capability MUST NOT be selected as fully supported until the complete required dependency closure is resolved as supported, available, compatible, policy-permitted, lifecycle-compatible, and acyclic.

### VDP-0003-REQ-046 — Dependency version mismatch

Capability dependency version mismatches MUST be reported as version-incompatible when they affect behavior.

### VDP-0003-REQ-047 — Optional dependency

Optional capability dependencies MAY reduce functionality but MUST remain distinct from required dependencies and MUST be disclosed when they affect the Processing Result Contract.

### Capability Negotiation

### VDP-0003-REQ-048 — Capability advertisement

Processors MUST provide or expose a Processor Descriptor identifying claimed support boundary when capability negotiation is performed.

### VDP-0003-REQ-049 — Capability request

Clients MAY provide a Processing Request for capabilities, profiles, modes, policies, versions, required capabilities, optional capabilities, and declared inputs without implying that the Processor supports them.

### VDP-0003-REQ-050 — Negotiation result statuses

Capability negotiation MUST separately report support status, availability status, lifecycle status, and dependency state.

### VDP-0003-REQ-051 — Supported result

Supported MUST mean the implementation claims the requested capability behavior exists for the requested capability version.

### VDP-0003-REQ-052 — Unsupported result

Unsupported MUST mean the implementation does not claim the requested capability behavior as requested.

### VDP-0003-REQ-053 — Partially supported result

Partially supported MUST mean the implementation claims some but not all requested capability behavior and must disclose limitations.

### VDP-0003-REQ-054 — Availability status

Availability status MUST explain whether supported or partially supported behavior is available, blocked by policy, unavailable in the environment, dependency-unsatisfied, version-incompatible, or deferred for the current request.

### VDP-0003-REQ-055 — No transport protocol

Capability negotiation MUST NOT require a specific transport protocol.

### VDP-0003-REQ-056 — Negotiation evidence

Negotiation outcomes MUST be reflected in Processing Context and SHOULD be reflected in the Processing Result Contract or retained session evidence.

### Processing Profiles

### VDP-0003-REQ-057 — Profile definition

A Processing Profile MUST be an identified and versioned composition of existing capabilities.

### VDP-0003-REQ-058 — Profile non-behavior

A Processing Profile MUST NOT introduce behavior that is not provided by composed capabilities.

### VDP-0003-REQ-059 — Profile examples

Profiles MAY include Validation, Migration, Documentation, Governance, Semantic Analysis, and Repository Analysis.

### VDP-0003-REQ-060 — Profile capability list

A Processing Profile MUST identify its stable profile identifier, profile version, source or authority, composed capability identifiers, version constraints, required capabilities, and optional capabilities.

### VDP-0003-REQ-061 — Profile dependency closure

A Processing Profile MUST identify or preserve required capability dependency closure for requested processing.

### VDP-0003-REQ-062 — Profile selection

Requested Profile selection MUST be captured in Processing Context with profile identifier, version, source, exact definition, and provenance.

### VDP-0003-REQ-063 — Profile limitation disclosure

If a Processor cannot support or make available all required capabilities in a requested profile, it MUST report unsupported, partially supported, dependency-unsatisfied, version-incompatible, policy-blocked, or environment-unavailable status as applicable.

### VDP-0003-REQ-064 — Profile escalation prevention

A Processing Profile MUST NOT escalate authority, bypass lifecycle rules, or make Processor outputs authoritative.

### Modes and Policies

### VDP-0003-REQ-065 — Mode declaration

Mode MUST be declared in Processing Context when it affects selected capability behavior.

### VDP-0003-REQ-066 — Mode constraint

Mode MAY constrain capability behavior but MUST NOT introduce new behavior outside selected capabilities.

### VDP-0003-REQ-067 — Policy declaration

Policies that affect capability selection, scope, limitations, or result interpretation MUST be declared in Processing Context.

### VDP-0003-REQ-068 — Policy non-authority

Policies MAY affect processing only within authority explicitly granted by an Accepted specification or valid governance record, and MUST NOT override the Constitution, override Accepted VDPs generally, redefine capability semantics, create new authority, lower required conformance, bypass lifecycle rules, or convert derived output into authority.

### Processing Result Contract

### VDP-0003-REQ-069 — Result contract definition

Processing Result Contract MUST define abstract result obligations without defining serialization.

### VDP-0003-REQ-070 — Context reference

A Processing Result Contract MUST reference the exact Context identity or include an equivalent reproducible Context record.

### VDP-0003-REQ-071 — Capability reporting

A Processing Result Contract MUST report Processor Descriptor, Processing Request, Negotiation Result, selected capabilities, rejected capabilities, support status, availability status, lifecycle status, dependency state, lifecycle authority source, and capability limitations when applicable.

### VDP-0003-REQ-072 — Profile reporting

A Processing Result Contract MUST report profile identifier, version, source, exact definition or reproducible reference, limitations, and conflicts when applicable.

### VDP-0003-REQ-073 — Environment limitation reporting

A Processing Result Contract MUST report Execution Environment limitations, policy restrictions, version incompatibilities, extension influence, and reproducibility limitations that affected processing.

### VDP-0003-REQ-074 — Lifecycle reporting

A Processing Result Contract MUST report whether capability and profile lifecycle statuses are authoritative or implementation-declared when lifecycle affects interpretation.

### VDP-0003-REQ-075 — Derived result boundary

Processing Result Contract content MUST remain derived and MUST NOT create authority.

### VDP-0003-REQ-076 — No serialization

This specification MUST NOT define JSON, CLI output, HTTP payloads, MCP resources, LSP messages, or repository graph serialization.

### Determinism and Compatibility

### VDP-0003-REQ-077 — Context equivalence

Equivalent Processing Contexts MUST produce equivalent capability selection and profile interpretation for conforming Processors with equivalent Processor Descriptors, Processing Requests, Negotiation Results, policies, configuration, declared inputs, and environment availability outcomes.

### VDP-0003-REQ-078 — Capability difference reporting

Processors with different Processor Descriptors or capability sets MUST report capability differences and MUST NOT claim full equivalence.

### VDP-0003-REQ-079 — Shared subset equivalence

Processors with different capability sets SHOULD preserve equivalent conclusions for the shared capability subset where applicable.

### VDP-0003-REQ-080 — Unknown future capability

Unknown future capabilities and unknown namespaces MUST be preserved when possible, reported when relevant, and never silently reinterpreted as older capabilities.

### VDP-0003-REQ-081 — Version mismatch

Version mismatches among specifications, capabilities, profiles, dependencies, extensions, policies, and configuration MUST be reported when they affect processing.

### Extensions

### VDP-0003-REQ-082 — Extension capability declaration

Extensions that provide or alter capability behavior MUST declare the affected extension-qualified capabilities and capability namespaces.

### VDP-0003-REQ-083 — Extension context capture

Extensions used during processing MUST be captured in Processing Context with identity, version, and capability influence when they affect results.

### VDP-0003-REQ-084 — Extension non-authority

Extensions MUST NOT override accepted specifications, governance records, Processor authority boundaries, capability namespace rules, or authoritative capability lifecycle rules.

### VDP-0003-REQ-085 — Malicious extension handling

Malicious or conflicting extensions MUST produce unsupported, partially supported, unavailable, failed, or security-relevant results rather than silent capability expansion.

### Security

### VDP-0003-REQ-086 — Capability spoofing

Processors MUST detect or report capability declarations that conflict with selected behavior, supported versions, authoritative lifecycle sources, namespaces, or observed limitations when such conflicts are visible.

### VDP-0003-REQ-087 — Profile escalation

Profile requests MUST NOT escalate authority, silently merge distinct profiles, or select capabilities outside the negotiated capability boundary.

### VDP-0003-REQ-088 — Conflicting declarations

Conflicting capability or profile declarations MUST be reported when they affect requested processing.

### VDP-0003-REQ-089 — Unknown capability security

Unknown capabilities, namespaces, and required dependencies MUST be treated as unsupported, partially supported, deferred, or unavailable when they affect security-relevant processing.

### VDP-0003-REQ-090 — Malicious configuration

Configuration or policy that attempts to bypass accepted specifications, capability boundaries, dependency closure, lifecycle authority, or profile limits MUST be reported and MUST NOT be silently applied.

### VDP-0003-REQ-091 — Environment injection

Execution Environment data MUST NOT be allowed to silently inject undeclared semantic inputs into Processing Context.

### VDP-0003-REQ-092 — Capability downgrade

Capability downgrade, lifecycle downgrade, dependency downgrade, or profile downgrade that affects requested processing MUST be reported.

### Deferred Boundaries

### VDP-0003-REQ-093 — Diagnostics deferral

This specification MUST NOT define a diagnostics format.

### VDP-0003-REQ-094 — Manifest schema deferral

This specification MUST NOT define the manifest schema.

### VDP-0003-REQ-095 — Interface deferral

This specification MUST NOT define CLI, MCP, HTTP, LSP, JSON, hosted API, or validator interfaces.

### VDP-0003-REQ-096 — Extension wire protocol deferral

This specification MUST NOT define the extension wire protocol.

### VDP-0003-REQ-097 — Repository graph serialization deferral

This specification MUST NOT define repository graph serialization.

### Semantic Corrections

### VDP-0003-REQ-098 — Pre-session ordering

Capability negotiation MUST occur before Processing Context construction, and the VDP-0002 Processing Session MUST begin only after Processing Context is frozen.

### VDP-0003-REQ-099 — Processor descriptor model

A Processor Descriptor MUST be a pre-session derived declaration of Processor identity or implementation identity, supported capability identifiers and versions, implementation-declared lifecycle claims, supported profile identifiers and versions, supported specification versions, material limitations, required environment assumptions, and extension support boundary when applicable.

### VDP-0003-REQ-100 — Processor descriptor non-authority

A Processor Descriptor MUST NOT be treated as authoritative merely because a Processor emits it.

### VDP-0003-REQ-101 — Processing request model

A Processing Request MUST identify requested capabilities, requested profile and version, requested mode, applicable policies, declared external inputs, required capability versions, optional capabilities, and required capabilities when those values are part of the caller request.

### VDP-0003-REQ-102 — Negotiation result model

A Negotiation Result MUST identify selected capabilities, rejected capabilities, partially available capabilities, support status, availability status, lifecycle status, dependency state, version compatibility, material limitations, and applicable lifecycle authority sources.

### VDP-0003-REQ-103 — Lifecycle authority

Authoritative capability or profile lifecycle status MUST derive from an Accepted Veridion specification, accepted capability registry or record, valid extension declaration under an accepted extension model, or another explicitly authorized artifact defined by an Accepted specification.

### VDP-0003-REQ-104 — Implementation lifecycle claims

Implementation-declared lifecycle claims MUST be identified as implementation-declared and non-authoritative when no authoritative lifecycle source exists.

### VDP-0003-REQ-105 — Lifecycle conflict handling

When an implementation-declared lifecycle claim conflicts with an authoritative lifecycle source, the authoritative source MUST govern, the conflict MUST be reported, and the Processor MUST NOT silently substitute its own claim.

### VDP-0003-REQ-106 — Context identity and result linkage

Every frozen Processing Context MUST have a stable context identity or equivalent reproducible provenance record, and a Processing Result MUST reference that exact identity or include an equivalent reproducible Context record.

### VDP-0003-REQ-107 — No fabricated reconstruction

If exact Context reconstruction is impossible, the Processing Result MUST disclose the limitation, MUST NOT claim full reproducibility, and MUST NOT fabricate missing provenance.

### VDP-0003-REQ-108 — Capability namespace categories

Capability identifiers MUST identify a source namespace or authority class such as core capability, organization-qualified capability, extension-qualified capability, or local experimental capability without defining a final registry format.

### VDP-0003-REQ-109 — Namespace collision handling

Two capability declarations with the same identifier but incompatible definitions MUST be reported as a conflict; unqualified identifiers MUST NOT be used by third-party extensions; unknown namespaces MUST be preserved and reported safely; and namespace ownership MUST NOT be inferred from repository hosting, popularity, or governance authority.

### VDP-0003-REQ-110 — Profile identity and authority

A Processing Profile MUST identify stable profile identifier, profile version, source or authority, composed capability identifiers and version constraints, required and optional capabilities, dependency closure, lifecycle status when applicable, material limitations, and compatibility expectations.

### VDP-0003-REQ-111 — Profile conflict handling

Profiles that share a display name but differ in identifier, version, source, or capability composition MUST be treated as distinct profiles and MUST NOT be silently merged.

### VDP-0003-REQ-112 — Dependency closure evidence

Negotiation Result and Processing Context MUST preserve the resolved required dependency closure or an equivalent reproducible reference, including transitive required dependencies, version constraints, lifecycle compatibility, support status, availability status, policy restrictions, extension requirements, unknown dependencies, and cycles.

## Informative Notes

VDP-0003 turns VDP-0002's corrected boundary into a concrete context and capability vocabulary. It does not create implementation behavior by itself. Concrete processors, validators, CLIs, MCP servers, hosted services, and IDE extensions may use this model without changing its authority boundary.

## Architecture

The abstract flow is:

```text
Candidate location
  -> VDP-0001 discovery
  -> Discovered Repository Result
  -> Processor Descriptor
  -> Processing Request
  -> Capability Negotiation
  -> Negotiation Result
  -> Processing Context construction
  -> Context freeze
  -> Processor execution under VDP-0002
  -> Processing Result Contract
```

Execution Environment surrounds the flow but is not the same as Processing Context.

## Interfaces

This specification defines no concrete interface. It defines semantic expectations for future interfaces that expose Processor Descriptors, receive Processing Requests, negotiate support and availability, construct Context after negotiation, and present Processing Results.

## Algorithms

Capability selection pseudocode:

```text
collect Processor Descriptor
collect Processing Request
collect authoritative capability and profile definitions when available
expand profile into existing capabilities
resolve required and optional capability dependencies
verify required dependency closure is acyclic and complete
classify support status as supported, partially_supported, or unsupported
classify availability status such as available, blocked_by_policy, unavailable_in_environment, dependency_unsatisfied, version_incompatible, or deferred
record lifecycle status and lifecycle authority source
produce Negotiation Result
construct Processing Context from request, descriptor, negotiation result, repository result, specifications, policies, configuration, extensions, mode, and declared external inputs
freeze Processing Context
```

The dependency graph must be acyclic. Unknown required dependencies are reported through negotiation rather than causing undefined behavior or false support claims.

## Evidence Requirements

Evidence for conformance may include Processor Descriptors, Processing Requests, capability definitions, profile definitions, dependency relationships, Negotiation Results, Context identity or provenance records, Result Contract records, lifecycle authority records, and examples of unsupported, partially supported, unavailable, deprecated, removed, unknown, and dependency-unsatisfied handling.

## Reasoning Requirements

Processors should distinguish Context facts, Environment limits, implementation support, availability, dependency state, capability claims, negotiated selections, profile composition, lifecycle maturity, lifecycle authority source, and derived result conclusions. A capability claim is not authority; it is a declared behavior boundary.

## Validation Strategy

Validation can check metadata, canonical sections, contiguous requirement identifiers, dependency references, negotiation ordering, Context identity, Context immutability language, Environment separation, capability lifecycle coverage and authority source, acyclic dependency closure requirements, support and availability status separation, profile identity rules, deferred boundary preservation, and consistency with VDP-0002.

## Scoring Considerations

Not applicable. Processing Context and Capability Model does not define scoring.

## Security Considerations

Capability and profile systems are security-sensitive because a malicious processor, extension, configuration, policy, or hosted surface could overclaim support, spoof lifecycle status, fake namespace authority, smuggle environment state into Context, downgrade capability versions, omit dependency closure, or use a profile to request behavior outside its negotiated boundary.

## Performance Considerations

Capability negotiation should be bounded by declared capability, profile, and dependency graphs. Processors may use caches or indexes for performance, but those aids remain derived and must not change Context identity, dependency closure, or negotiated capability meaning.

## Compatibility

This draft supports future concrete interfaces by defining the abstract model only. Unknown future capabilities, namespaces, profiles, lifecycle states, profile sources, and extension declarations should be preserved where possible, reported when relevant, and never silently reinterpreted.

## Migration

No current implementation is migrated by this draft. Future Processor implementations should align their Processor Descriptor, Processing Request handling, Negotiation Result, Context construction, capability advertisement, profile composition, and result reporting with VDP-0003 before claiming capability-model conformance.

## Extensibility

Future VDPs may define diagnostics, concrete capability registries, profile registries, manifest integration, extension protocols, result serialization, repository graph serialization, CLI, MCP, HTTP, LSP, hosted APIs, and validator interfaces. Those extensions must preserve Context immutability, Environment separation, and derived-output boundaries.

## Alternatives Considered

- Put Context and capability rules in VDP-0002: rejected because VDP-0002 defines the Processor and this draft defines the inputs and capability model used by it.
- Treat Execution Environment as Context: rejected because mutable runtime conditions would undermine determinism.
- Let profiles define new behavior: rejected because profiles should compose existing capabilities.
- Define JSON result contracts now: deferred to avoid prematurely binding the abstract model to serialization.

## Open Questions

- Which capability identifiers should be standardized first?
- Should capability and profile registries live in manifests, schemas, records, or separate specifications?
- How should extension-provided capabilities be isolated?
- Which Processing Result serialization should be specified first?
- How should deprecated and removed capabilities be tested in conformance fixtures?

## Future Work

- Define diagnostics.
- Define a concrete capability registry.
- Define a concrete profile registry.
- Define manifest integration for Context and capabilities.
- Define Processing Result serialization.
- Define extension wire protocol.
- Define CLI, MCP, HTTP, LSP, hosted API, and validator interfaces.
- Define repository graph serialization.

## References

- VDP--001: Specification Specification.
- VDP-0000: Veridion Constitution.
- VDP-0001: Repository Discovery and Canonical Layout.
- VDP-0002: Core Processor Model.
- `docs/processor/PROCESSING-CONTEXT.md`.
- `docs/processor/CAPABILITY-MODEL.md`.

## Appendices

### Appendix A: Capability Lifecycle Summary

| Lifecycle | Meaning |
| --- | --- |
| Experimental | Early capability with unstable semantics. |
| Draft | Capability under active specification or review. |
| Stable | Capability suitable for stable conformance claims. |
| Deprecated | Capability available but discouraged for new use. |
| Removed | Capability unavailable for new sessions unless legacy compatibility is explicitly allowed. |
