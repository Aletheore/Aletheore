---
identifier: VDP-0000
title: Veridion Constitution
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
supersedes: []
superseded_by: null
category: constitution
tags:
  - constitution
  - governance
  - authority
  - stewardship
---

# Veridion Constitution

## Abstract

This Draft Constitution defines the internal authority model for Veridion. It establishes constitutional supremacy, artifact-centered authority, bounded roles, phased governance, proposal authority, emergency limits, appeals, succession, repository portability, constitutional amendment rules, contributor protections, and the boundary between human governance and AI or automated assistance.

The constitutional model is phased: Founder Stewardship, then Maintainer Governance, then Constitutional Governance. The initial phase recognizes Arihant Kaul as the current repository owner and Constitutional Steward while creating a path to institutional governance under a future Veridion Steering Council. This draft does not ratify itself, does not activate the Steering Council, and does not create permanent founder authority.

## Motivation

Veridion is intended to become a long-lived, open, evidence-driven engineering framework. Long-lived open-source systems need clear authority before disputes, commercial pressure, implementation divergence, repository migration, automation, or contributor growth make authority ambiguous.

Without a Constitution, governance could silently collapse into repository ownership, implementation control, social popularity, vendor influence, private memory, or generated summaries. That would conflict with the Veridion Proposal System, which treats durable, inspectable artifacts as the source of specification authority.

## Goals

- Define the highest internal normative authority for Veridion.
- Make all governance authority explicit, traceable, bounded, and reviewable.
- Establish a practical early-stage founder stewardship model without making founder control permanent.
- Define a credible transition path to maintainer and constitutional governance.
- Define roles, powers, prohibitions, accountability, appointment, removal, continuity, and succession.
- Preserve the distinction between Accepted specifications and implementations.
- Protect independent conforming implementations from vendor, runtime, platform, or repository-forge favoritism.
- Define how VDPs become authoritative before and after institutional governance exists.
- Define dispute, appeal, emergency, inactivity, dormancy, repository migration, and fork behavior.
- Define how constitutional interpretation differs from constitutional amendment.
- Preserve contributor rights while allowing proportionate moderation and infrastructure protection.
- Keep AI and automated systems useful without granting them governance authority.
- Define one-time constituent ratification for the first Accepted Constitution without exercising it in this draft.

## Non Goals

- Ratify this Constitution.
- Mark VDP-0000 as Discussion or Accepted.
- Activate the Veridion Steering Council.
- Appoint Maintainers, Editors, Reviewers, Council members, or Working Groups.
- Define detailed election mechanics.
- Create a full code of conduct, trademark policy, or certification program.
- Implement governance software, CLI commands, MCP resources, or repository automation.
- Grant AI systems, agents, CI, or generated artifacts governance authority.
- Override VDP--001.
- Make GitHub constitutionally authoritative merely because it hosts the current repository.
- Resolve all future governance edge cases in Version 0.1.0.

## Terminology

- Constitution: The Accepted Veridion Constitution identified as `VDP-0000`.
- Constitutional amendment: A reviewed change to the Accepted Constitution.
- Constitutional Steward: The early-stage steward role held initially by Arihant Kaul during Founder Stewardship.
- Maintainer: A trusted participant with bounded repository or technical responsibilities.
- VDP Editor: A procedural custodian for VDP metadata, format, lifecycle gates, and transition recording.
- Reviewer: A person providing scoped evaluation of a proposal, implementation, evidence set, or governance action.
- Contributor: A human participant proposing, reviewing, implementing, reporting, or improving Veridion work.
- Working Group: A temporary or standing group chartered for a bounded scope.
- Veridion Steering Council: The future institutional governance body beneath the Constitution.
- Governance phase: One of Founder Stewardship, Maintainer Governance, or Constitutional Governance.
- Canonical repository: The repository or storage location designated by valid governance records as the current authoritative source location.
- Canonical source revision: A source revision identified by a durable revision identifier in the canonical repository or accepted archive.
- Official mirror: A mirror designated by governance records for availability or archival use without becoming independently authoritative.
- Archival copy: A preserved copy used for historical inspection.
- Authority transfer: A recorded change moving canonical authority to another repository, forge, storage system, or institutional custodian.
- Governance record: An inspectable record of authority, decision, appointment, delegation, transition, emergency, amendment, appeal, or ratification.
- Constituent Ratification Record: The one-time record by which the first Accepted revision of VDP-0000 may be ratified.
- Material conflict of interest: A financial, employment, vendor, personal, organizational, authorship, or implementation-control interest that materially compromises impartial governance judgment.

## Background

VDP--001 established the Veridion Proposal System and was accepted at version 1.0.0. Its bootstrap authority applied only to VDP--001 and expired immediately after that transition. VDP-0000 therefore requires its own constituent ratification mechanism rather than relying on the expired VDP--001 bootstrap authority.

The current project is early-stage. Arihant Kaul is the repository owner and principal steward. The project does not yet have an active Steering Council or a broad maintainer body capable of institutional governance. The Constitution must work in that early context while preventing early convenience from becoming permanent personal rule.

## Problem Statement

Veridion needs a constitutional authority model that remains valid across growth, forks, repository migration, commercial involvement, implementation competition, automation, emergencies, inactivity, and founder succession.

The central problem is to make authority durable without freezing the project in its founding state. Governance must be strong enough to prevent silent capture by repository administrators, implementation owners, vendors, AI systems, or informal popularity, while remaining practical for an early project that has not yet formed a council.

## Proposed Design

The Constitution is organized around five pillars: Authority, Governance, Integrity, Evolution, and Stewardship.

Authority establishes that the Accepted Constitution is the highest internal normative authority, that all governance power must be traceable to records, and that implementations and generated artifacts do not override specifications.

Governance defines bounded roles, governance phases, proposal authority, decision thresholds, conflicts, appeals, and institutional transition.

Integrity protects history, provenance, review records, conflict disclosure, emergency limits, repository portability, and the separation between interpretation and amendment.

Evolution defines constitutional amendment, versioning, ratification, succession, and future governance growth.

Stewardship defines early founder responsibility, contributor protections, institutional independence, moderation boundaries, dormancy, archival behavior, and the role of humans and automated systems.

### Role Model Summary

| Role | Core powers | Prohibitions | Appointment and continuity | Removal or end | Accountability |
| --- | --- | --- | --- | --- | --- |
| Constitutional Steward | Early stewardship, ordinary VDP authorization after gates, initial role appointments, emergency protective action, transition initiation. | No silent gate bypass, fabricated review, AI approval substitution, permanent extra-constitutional grants, or unilateral authority after Council activation. | Initially Arihant Kaul during Founder Stewardship; successor may be designated by public succession record. | Broad powers end at Constitutional Governance; interim replacement is limited to continuity and transition. | Accepted Constitution and governance records. |
| Maintainer | Assigned technical maintenance, review, authorized merge, eligible governance participation, role nomination. | No automatic constitutional amendment authority, unilateral VDP acceptance authority, unrestricted repository authority, or authority beyond assignment. | Appointed by eligible authority with a record. | Resignation, inactivity, suspension, or removal record. | Constitution, assigned area records, and active governance authority. |
| VDP Editor | Metadata and format checks, identifier confirmation, lifecycle gate checks, review coordination, authorized transition recording. | No approval by editorship alone, no technical-merit decision solely as editor, no unauthorized normative change, no fabricated approval. | Appointed by eligible authority with a record. | Removal, inactivity, resignation, or replacement record. | VDP--001, Constitution, and active governance authority. |
| Reviewer | Scoped evaluation of architecture, security, performance, compatibility, governance, evidence, feasibility, or editorial correctness. | Reviewer listing does not automatically mean approval; conflicts must not be hidden. | Review assignment or participation record. | Review completion, withdrawal, conflict, or governance record. | Review scope and review record. |
| Contributor | Author proposals, submit implementation, report defects, challenge decisions, request review, fork under license. | No governance authority merely by contribution; no guaranteed merge, role, acceptance, or confidential access. | Human participation in project work. | Proportionate moderation or restriction process when required. | Constitution and applicable project records. |
| Working Group | Chartered delivery and recommendations within a bounded scope. | No constitutional authority through activity or popularity. | Charter defining scope, membership, leadership, decision process, and duration. | Charter expiry, dissolution, replacement, or removal record. | Charter and active governance authority. |
| Veridion Steering Council | Permanent governance authority beneath the Constitution after activation. | No override of Constitution, hidden conflicts, repository-owner supremacy, or AI delegation of accountable judgment. | Activated only through valid transition record; members serve recorded terms. | Term expiry, resignation, removal, vacancy procedure, or inactivity record. | Accepted Constitution and Council decision records. |
| Interim Constitutional Steward | Continuity protection and governance transition after pre-Council Steward unavailability. | No unrestricted founder replacement or permanent authority expansion. | Appointed by active Maintainers after 90 days unavailability, two-thirds approval, at least two affirmative votes, and public record. | Ends when successor, Steward, or institutional governance is validly established. | Constitution, appointment record, and active Maintainers. |

## Normative Requirements

### Constitutional Authority and Supremacy

### VDP-0000-REQ-001 — Constitutional supremacy

The Accepted Constitution MUST be the highest internal normative authority of Veridion.

### VDP-0000-REQ-002 — No overriding actor

No role, repository owner, maintainer, implementation, hosted service, company, agent, generated artifact, or automated system MAY override the Accepted Constitution outside a valid constitutional process.

### VDP-0000-REQ-003 — Traceable authority

Governance authority MUST be traceable to the Constitution, an Accepted VDP, an appointment record, an election or selection record, a delegation record, or a valid acceptance or decision record.

### VDP-0000-REQ-004 — No implied authority from access

Technical access, repository ownership, employment, popularity, convention, implementation control, or administrative permission MUST NOT by itself create governance authority.

### VDP-0000-REQ-005 — Authority hierarchy

The Veridion authority hierarchy MUST be: Accepted Constitution, accepted constitutional amendments as incorporated into the current Constitution, Accepted VDPs, valid governance and acceptance records, repository state, implementations, generated artifacts, and AI-generated interpretations.

### VDP-0000-REQ-006 — Amendment incorporation

An accepted constitutional amendment MUST become part of the current Constitution and MUST NOT remain a separate authority above the Constitution.

### VDP-0000-REQ-007 — Specification supremacy

An implementation MUST NOT redefine an Accepted specification merely because it is official, deployed, popular, historically first, or controlled by a privileged maintainer.

### VDP-0000-REQ-008 — Implementation defects

When an implementation conflicts with an Accepted specification, the conflict MUST be treated as an implementation defect, deviation, or amendment proposal rather than silent specification change.

### VDP-0000-REQ-009 — Prototypes and experiments

Prototypes and experiments MAY precede specifications, but they MUST NOT become normative without the VDP process.

### VDP-0000-REQ-010 — Implementation neutrality

The Constitution MUST NOT privilege a programming language, runtime, vendor, hosted platform, CLI, MCP server, agent, repository forge, or commercial entity as the only legitimate Veridion implementation path.

### Foundational Principles

### VDP-0000-REQ-011 — Human accountability

Only accountable humans or human governance bodies MAY exercise constitutional authority.

### VDP-0000-REQ-012 — Automated assistance boundary

AI and automated systems MAY draft, analyze, review, validate, compare, summarize, discover evidence, and recommend decisions, but they MUST NOT hold roles, vote, accept or reject VDPs, appoint or remove people, exercise vetoes, authorize emergencies, accept risk, approve amendments, or act as accountable decision-makers.

### VDP-0000-REQ-013 — No AI approval substitution

AI-generated approval, model confidence, generated summaries, or validation output MUST NOT be recorded as human approval.

### VDP-0000-REQ-014 — Reconstructable governance

Normative governance decisions MUST be reconstructable from accepted artifacts and their review, decision, or authority records.

### VDP-0000-REQ-015 — Private discussion limits

Private conversations MAY inform governance decisions, but acceptance-critical reasoning and authority MUST be recorded in inspectable artifacts.

### VDP-0000-REQ-016 — Bounded roles

Every constitutional role MUST define powers, prohibitions, accountability, conflict obligations, appointment mechanism, removal mechanism, term or continuity rules, and succession behavior.

### VDP-0000-REQ-017 — No general undefined authority

No role MAY receive undefined general authority beyond its recorded scope.

### VDP-0000-REQ-018 — Interpretation versus amendment

Interpreting existing constitutional text and changing constitutional text MUST be separate governance acts.

### VDP-0000-REQ-019 — Ambiguity requiring amendment

If ambiguity materially affects governance rights, powers, obligations, or legitimacy, the ambiguity MUST be resolved through constitutional amendment or an accepted authoritative clarification process.

### VDP-0000-REQ-020 — Portability

The Constitution MUST remain valid if Veridion moves away from GitHub or any other current repository forge.

### Roles and Bounded Powers

### VDP-0000-REQ-021 — Constitutional Steward initial holder

During Founder Stewardship, Arihant Kaul is the initial Constitutional Steward.

### VDP-0000-REQ-022 — Steward powers

During Founder Stewardship, the Constitutional Steward MAY maintain the canonical repository, appoint initial Maintainers and Editors, authorize ordinary VDP lifecycle transitions after gates are satisfied, resolve routine governance questions, initiate emergency protective action, propose constitutional amendments, and initiate transition to institutional governance.

### VDP-0000-REQ-023 — Steward prohibitions

The Constitutional Steward MUST NOT treat implementation as specification, bypass VDP lifecycle gates silently, fabricate review or evidence, claim AI output as human approval, erase historical governance records, grant permanent authority outside the Constitution, accept constitutional amendments without the required process, or continue unilateral authority after institutional governance is activated.

### VDP-0000-REQ-024 — Steward accountability

Every material exercise of stewardship authority MUST be recorded in an inspectable artifact.

### VDP-0000-REQ-025 — Steward role termination

The broad Founder Stewardship powers MUST terminate when permanent constitutional governance is activated.

### VDP-0000-REQ-026 — Maintainer scope

Maintainers MAY review proposals and implementation, merge authorized changes, maintain assigned areas, participate in governance decisions where eligible, and nominate contributors for roles.

### VDP-0000-REQ-027 — Maintainer limits

Maintainer status MUST NOT automatically grant constitutional amendment authority, unilateral VDP acceptance authority, unrestricted repository authority, or authority outside assigned responsibilities.

### VDP-0000-REQ-028 — Maintainer records

Maintainer appointment, resignation, inactivity, suspension, and removal MUST be recorded in inspectable governance records.

### VDP-0000-REQ-029 — VDP Editor scope

VDP Editors MAY verify metadata and format, assign or confirm proposal identifiers, check lifecycle gates, coordinate reviews, record status transitions after authorization, and identify process defects.

### VDP-0000-REQ-030 — VDP Editor limits

VDP Editors MUST NOT approve their own proposal by virtue of editorship, decide technical merit solely through the editor role, change normative meaning without author and review authorization, or fabricate governance approval.

### VDP-0000-REQ-031 — Reviewer scope

Reviewer records MUST state review scope and outcome, and reviewer listing MUST NOT automatically mean approval.

### VDP-0000-REQ-032 — Contributor participation

Any human Contributor MAY author VDPs, submit implementations, report defects, challenge decisions, request review, and participate without holding a governance role.

### VDP-0000-REQ-033 — Working Group charter

Each Working Group MUST have a charter defining scope, authority, membership, leadership, deliverables, decision process, reporting obligations, duration or review date, and dissolution process.

### VDP-0000-REQ-034 — Working Group limits

A Working Group MUST NOT acquire constitutional authority merely through activity, popularity, or implementation control.

### VDP-0000-REQ-035 — Steering Council structure

When active, the Veridion Steering Council MUST have at least three and at most seven voting members, with odd-numbered membership where practicable.

### VDP-0000-REQ-036 — Council records

Council membership, terms, conflicts, recusals, removals, vacancies, decisions, and succession actions MUST be recorded in inspectable artifacts.

### Governance Phases and Transition

### VDP-0000-REQ-037 — Founder Stewardship initial phase

Founder Stewardship MUST be the initial active governance phase.

### VDP-0000-REQ-038 — Founder Stewardship disclosure

During Founder Stewardship, lack of independent review MUST be disclosed, and the Constitutional Steward MUST NOT describe self-review as independent review.

### VDP-0000-REQ-039 — Founder Stewardship transitionality

Founder Stewardship MUST be transitional and MUST NOT be interpreted as permanent personal constitutional authority.

### VDP-0000-REQ-040 — Maintainer Governance activation

Maintainer Governance MAY begin only through an inspectable Governance Transition Record confirming at least three active Maintainers, at least two non-founder Maintainers, role and conflict records, distributable governance responsibilities, public review, and constitutional authorization.

### VDP-0000-REQ-041 — No automatic phase transition

Governance phase transition MUST NOT occur automatically based solely on contributor count, repository activity, popularity, funding, or time.

### VDP-0000-REQ-042 — Maintainer Governance purpose

Maintainer Governance MAY distribute decision authority among Maintainers while forming the Steering Council, but it MUST remain bounded by the Constitution.

### VDP-0000-REQ-043 — Constitutional Governance activation

Constitutional Governance begins only when the Steering Council is activated through a valid transition record.

### VDP-0000-REQ-044 — Post-Council founder limit

After Steering Council activation, unilateral Constitutional Steward authority MUST expire, and repository ownership MUST NOT provide additional constitutional authority.

### Proposal and Decision Authority

### VDP-0000-REQ-045 — VDP authoring

Any Contributor MAY author a VDP.

### VDP-0000-REQ-046 — Discussion coordination

Editors and Maintainers MAY coordinate Discussion, but they MUST NOT own proposal ideas merely because they manage process.

### VDP-0000-REQ-047 — Founder Stewardship ordinary acceptance

During Founder Stewardship, the Constitutional Steward MAY authorize an ordinary VDP transition to Accepted only when VDP--001 gates pass, required evidence exists, conflicts are disclosed, available review is recorded, unresolved Blocking objections are addressed or explicitly resolved, and an inspectable acceptance record is created.

### VDP-0000-REQ-048 — Council ordinary acceptance

After Council activation, an ordinary VDP transition to Accepted MUST require at least two affirmative votes, majority participation by non-recused Council members, no unresolved Blocking finding, and an inspectable decision record.

### VDP-0000-REQ-049 — Ordinary decisions

Council ordinary decisions MUST pass by majority of participating non-recused members, MUST have at least two affirmative votes, MUST treat ties as no decision, and MUST NOT count abstentions as affirmative votes.

### VDP-0000-REQ-050 — Constitutional decisions

Constitutional amendments after Council activation MUST require at least 21 calendar days of public review, explicit amendment text, compatibility and migration analysis, two-thirds approval of non-recused Council membership, at least three affirmative votes, an amendment record, and an updated constitutional version.

### VDP-0000-REQ-051 — Rejection records

A VDP rejection MUST include rationale and MUST NOT permanently prevent a revised proposal.

### VDP-0000-REQ-052 — Supersession and deprecation authority

Supersession and deprecation of Accepted VDPs MUST require the same authority class as acceptance unless a valid emergency rule temporarily applies.

### VDP-0000-REQ-053 — Self-approval boundary

No person MAY count their own authorship as independent review.

### VDP-0000-REQ-054 — Author participation

Authors MAY participate in discussion and decision processes, but they MUST disclose material conflicts when voting on or approving their own proposal.

### VDP-0000-REQ-055 — Decision model

The default decision model SHOULD be evidence-seeking consensus, then recorded objections, then formal decision when consensus is not achievable; departures should explain the urgency or process reason.

### VDP-0000-REQ-056 — Silence and consensus

Consensus MUST NOT require unanimity, and silence MUST NOT automatically mean consent.

### Conflicts, Recusal, and Accountability

### VDP-0000-REQ-057 — Material conflict definition

A material conflict of interest MUST include interests that materially compromise impartial governance judgment, including financial interest, employment relationship, vendor interest, direct personal dispute, authorship combined with sole approval, organizational pressure, or control of a competing implementation.

### VDP-0000-REQ-058 — Technical preference not conflict

Ordinary technical preference MUST NOT be treated as a conflict of interest by itself.

### VDP-0000-REQ-059 — Conflict disclosure

Governance participants MUST disclose material conflicts before exercising affected authority.

### VDP-0000-REQ-060 — Recusal

A participant MUST recuse when impartiality is materially compromised, and the recusal MUST be recorded.

### VDP-0000-REQ-061 — Quorum recalculation

Decision thresholds that depend on eligible participants MUST be recalculated using non-recused members when the Constitution permits recusal.

### VDP-0000-REQ-062 — No retaliation

Good-faith conflict disclosure, technical criticism, governance criticism, or appeal MUST NOT be grounds for retaliation.

### Emergency Governance

### VDP-0000-REQ-063 — Emergency categories

Emergency authority MAY be used only for active security incidents, legal requirements, credential compromise, repository compromise, imminent data loss, severe infrastructure risk, or severe supply-chain risk.

### VDP-0000-REQ-064 — Emergency actions

Emergency action MAY temporarily restrict access, revert or disable unsafe functionality, freeze publication, rotate credentials, suspend compromised automation, or preserve evidence.

### VDP-0000-REQ-065 — Emergency amendment prohibition

Emergency action MUST NOT permanently amend the Constitution or an Accepted VDP.

### VDP-0000-REQ-066 — Emergency record

Emergency action MUST produce a written record identifying scope, authority, evidence, start time, affected artifacts, review deadline, and expiry or ratification path.

### VDP-0000-REQ-067 — Emergency expiry

Emergency authority MUST expire after 14 calendar days unless ratified through the applicable governance process.

### VDP-0000-REQ-068 — Emergency reviewability

Emergency protective action MUST remain reviewable, and failure to review it by the deadline MUST be recorded as a governance defect.

### Appeals and Dispute Resolution

### VDP-0000-REQ-069 — Appeal stages

Disputes SHOULD proceed through direct clarification, recorded technical or governance review, appeal to the next higher authority, and final constitutional decision; skipped stages should be justified by urgency, safety, or unavailable authority.

### VDP-0000-REQ-070 — Founder Stewardship appeals

During Founder Stewardship, final ordinary appeals MAY reach the Constitutional Steward when no higher eligible authority exists.

### VDP-0000-REQ-071 — Council appeals

After Council activation, final ordinary appeals MUST reach the Steering Council unless the Council itself is conflicted or below valid operating threshold.

### VDP-0000-REQ-072 — No self-review appeal

A person MUST NOT decide an appeal of their own disputed decision when another eligible authority exists.

### VDP-0000-REQ-073 — Appeals and emergencies

Appeals MUST NOT automatically suspend emergency protective action, but emergency action remains time-limited and reviewable.

### Succession, Inactivity, and Archival

### VDP-0000-REQ-074 — Steward succession

Before Council activation, the Constitutional Steward MAY designate a successor through a public succession record.

### VDP-0000-REQ-075 — Interim Steward

If the Constitutional Steward is unavailable for at least 90 consecutive days and no successor is recorded, active Maintainers MAY appoint an Interim Constitutional Steward with two-thirds approval, at least two affirmative Maintainer votes, and a public appointment record.

### VDP-0000-REQ-076 — Interim Steward limit

An Interim Constitutional Steward MUST be limited to continuity protection and governance transition.

### VDP-0000-REQ-077 — Maintainer inactivity

A role MAY be marked inactive after a published inactivity period and reasonable contact attempt, and historical attribution MUST NOT be erased.

### VDP-0000-REQ-078 — Council vacancy limits

Council vacancy rules MUST NOT allow one remaining person to exercise unrestricted Council authority.

### VDP-0000-REQ-079 — Dormancy

Dormant status MUST be recorded when no authorized governance activity occurs for a substantial recorded period, and dormancy MUST NOT imply current maintenance, security support, or specification freshness.

### VDP-0000-REQ-080 — Archival

Formal archival MUST preserve accepted specifications, history, provenance, final governance records, and known security and support status unless legal or security constraints require restricted access.

### Repository and Publication Authority

### VDP-0000-REQ-081 — Canonical repository

The canonical repository MUST be identified by valid governance records rather than assumed from current hosting.

### VDP-0000-REQ-082 — Source revision identifiers

Governance records and conformance claims SHOULD identify canonical source revisions using durable revision identifiers; omission should explain why a revision identifier is unavailable.

### VDP-0000-REQ-083 — Official mirrors

Official mirrors MAY support availability and archival inspection, but they MUST preserve source provenance and MUST NOT become independently authoritative without an authority transfer.

### VDP-0000-REQ-084 — Fork claims

Forks MUST NOT claim to be canonical Veridion unless a valid authority transfer or succession record establishes that status.

### VDP-0000-REQ-085 — Repository migration

Repository migration MUST preserve proposal identifiers, accepted specifications, decision records, lifecycle history, and provenance mappings.

### VDP-0000-REQ-086 — Repository compromise

A compromised repository state MUST NOT make malicious or unauthorized changes constitutionally valid merely because they appear on the default branch.

### VDP-0000-REQ-087 — Administrative access boundary

Repository administrator access MUST NOT equal constitutional authority.

### Constitutional Interpretation and Amendment

### VDP-0000-REQ-088 — Interpretations

A constitutional interpretation MAY clarify ambiguity only when it does not change normative meaning.

### VDP-0000-REQ-089 — Interpretation record

An interpretation record MUST identify relevant text, ambiguity, interpretation, deciding authority, preserved dissent, and confirmation that it creates no new obligation.

### VDP-0000-REQ-090 — Amendment requirement

If new obligations, powers, prohibitions, rights, or processes are necessary, a constitutional amendment MUST be used.

### VDP-0000-REQ-091 — Constitutional versioning

Constitutional PATCH versions MUST be editorial with no normative change, MINOR versions SHOULD be compatible constitutional clarifications or additions, and MAJOR versions SHOULD be used for incompatible authority, governance, role, or rights changes.

### VDP-0000-REQ-092 — Amendment process

Material constitutional amendments MUST enter Discussion, include impact analysis, identify affected roles and decisions, preserve the prior Accepted revision, receive enhanced approval, and produce amendment and ratification records.

### VDP-0000-REQ-093 — No emergency amendment

Emergency action MUST NOT permanently amend the Constitution.

### Contributor Rights and Institutional Independence

### VDP-0000-REQ-094 — Contributor protections

Contributors MUST have the right to inspect governing artifacts, propose changes, receive rationale for rejection, disclose conflicts, appeal, fork under the project license, and make good-faith technical or governance criticism without retaliation.

### VDP-0000-REQ-095 — Protection limits

Contributor protections MUST NOT guarantee merge, appointment, acceptance, access to confidential information, or immunity from proportionate moderation.

### VDP-0000-REQ-096 — Moderation and removal

Suspension or removal from a role MUST have stated grounds, recorded evidence appropriate to sensitivity, proportionality, conflict handling, opportunity to respond where safe and practical, appeal route, and protection of confidential or security-sensitive details.

### VDP-0000-REQ-097 — Temporary suspension

Immediate temporary suspension MAY occur during active risk, but it MUST be reviewed and time-limited.

### VDP-0000-REQ-098 — Commercial independence

No sponsor, employer, vendor, customer, commercial implementation, or funding source MUST automatically receive constitutional authority.

### VDP-0000-REQ-099 — Organizational conflict disclosure

Governance participants MUST disclose material organizational conflicts.

### VDP-0000-REQ-100 — Name and conformance claims

A party MAY truthfully state that an implementation targets Veridion specifications, but MUST NOT imply governance endorsement, official status, or conformance without applicable records.

### Initial Ratification and Transitional Provisions

### VDP-0000-REQ-101 — Initial constituent ratification

Arihant Kaul, as repository owner and current project steward, MAY ratify the first Accepted revision of VDP-0000 only after a complete acceptance-readiness audit, an inspectable review period and review record, and an explicit Constituent Ratification Record.

### VDP-0000-REQ-102 — Ratification scope

Initial constituent ratification authority applies only to the first Accepted revision of VDP-0000, expires immediately upon ratification, does not authorize unilateral future amendments, does not authorize bypassing VDP--001, and MUST be identified as a founding act rather than permanent governance.

### VDP-0000-REQ-103 — No ratification in draft

This Draft Constitution MUST NOT be treated as ratified, Accepted, or constitutionally active.

### VDP-0000-REQ-104 — Bootstrap history

VDP-0000 records that VDP--001 established the proposal system, VDP--001 bootstrap authority applied only to VDP--001, that authority expired, and future governance derives from the Accepted Constitution after valid ratification.

### VDP-0000-REQ-105 — Deferred topic safety

Deferred governance topics MUST have safe interim rules and MUST NOT leave core constitutional authority unresolved.

### VDP-0000-REQ-106 — Pre-Council constitutional amendments

Before Steering Council activation, constitutional amendments MUST require enhanced public review, explicit amendment text, compatibility and migration analysis, affected-role analysis, and an explicit constitutional ratification record by the eligible transitional authority.

## Informative Notes

The constitutional pillars operate together. Authority without stewardship becomes rigid; stewardship without authority records becomes personal rule; governance without integrity becomes social convention; evolution without amendment discipline becomes silent drift.

This Draft Constitution deliberately names the current founder role because the project is still in an early phase. Naming the current steward is not intended to create permanent founder sovereignty. The intended trajectory is institutional authority under accepted constitutional artifacts.

## Architecture

The constitutional architecture has three layers: authoritative artifacts, accountable human roles, and supporting operational systems.

Authoritative artifacts include the Accepted Constitution, accepted constitutional amendments incorporated into it, Accepted VDPs, decision records, appointment records, delegation records, transition records, emergency records, appeal records, and ratification records.

Accountable human roles include the Constitutional Steward, Maintainers, VDP Editors, Reviewers, Contributors, Working Groups, Interim Constitutional Steward, and, when activated, the Veridion Steering Council.

Operational systems include the canonical repository, official mirrors, CI, validation tooling, hosted rendering, issue trackers, agents, and future MCP or CLI surfaces. These systems support governance but do not possess constitutional authority.

## Interfaces

The primary interface is the Markdown VDP. Governance interfaces include governance records, decision records, appointment records, review records, appeal records, emergency records, and transition records.

Future tools may expose constitutional status, role records, authority chains, or governance diagnostics. Such tools must preserve source provenance and must not convert generated output into authority.

## Algorithms

Constitutional evaluation is not a purely algorithmic process. Mechanical checks may extract metadata, sections, requirement identifiers, role records, decision thresholds, review periods, and source revision references.

A governance auditor may follow this conceptual process: identify the relevant constitutional text, identify the claimed authority, locate the supporting record, verify phase and role eligibility, check conflicts and recusals, verify thresholds, verify timing requirements, and record unevaluated human-judgment dimensions.

## Evidence Requirements

Governance evidence should be inspectable, durable, attributable, and scoped. Evidence may include accepted VDPs, review records, decision records, Git commits, signed or otherwise attributable statements, issue or pull request records, security incident records, appointment records, and migration records.

Sensitive evidence may be summarized or access-controlled when publication would create harm. The public record should preserve enough scope, rationale, and authority to make the decision reviewable without exposing secrets.

## Reasoning Requirements

Constitutional reasoning should distinguish facts, assumptions, interpretations, risk judgments, authority claims, and normative requirements. Acceptance-critical reasoning must not depend on private memory, hidden model output, or inaccessible discussions.

Where the Constitution uses SHOULD, deviation is permitted only with an understood and recorded reason.

## Validation Strategy

Constitutional conformance may be assessed through these classes:

- Repository-inspectable: files, metadata, source revisions, records, and history.
- Record-verifiable: appointments, decisions, votes, recusals, ratifications, transitions, and emergency actions.
- Role-verifiable: whether a person or body held the relevant role at the relevant time.
- Process-verifiable: whether required stages, reviews, thresholds, and records occurred.
- Time-verifiable: review periods, emergency expiry, inactivity periods, and archival dates.
- Human-judgment-dependent: good faith, fairness, proportionality, evidence sufficiency, conflict materiality, and legitimacy.

Governance legitimacy must not be reduced to automated schema validation. A future tool may identify missing records or invalid transitions, but it cannot establish good faith, fairness, or legitimate human judgment automatically.

## Scoring Considerations

Constitutional legitimacy must not be reduced to one numeric score. Governance audits may report dimensions, findings, confidence, and unevaluated areas, but no automated score may substitute for authority, due process, or human judgment.

## Security Considerations

Threats include repository administrator compromise, forged governance records, unauthorized status transitions, account takeover, coercion, undisclosed conflicts, malicious forks, hostile governance capture, maintainer collusion, founder unavailability, compromised automation, AI impersonation of human approval, emergency-power abuse, deletion of historical records, and publication of sensitive evidence.

Mitigations include artifact-centered authority, source revision identifiers, inspectable records, conflict disclosure, recusal, role limits, emergency expiry, provenance preservation, canonical repository records, no AI approval authority, repository-compromise rules, and archival obligations.

## Performance Considerations

Constitutional governance should remain usable without complex software. A small project must be able to inspect Markdown records manually. As Veridion grows, indexes and tools may help locate authority chains, role records, conflicts, and decisions, but those tools remain supporting systems.

## Compatibility

This Draft Constitution depends on VDP--001 and is written in VDP format version 1.0. It preserves the VDP--001 identifier model by using `VDP-0000` as the standard constitutional identifier.

The phased model is compatible with the current project state and with future institutional governance. It is intended to remain valid across repository migration, official mirrors, forks, commercial support, and independent implementations.

## Migration

Migration from founder-only stewardship to institutional governance requires recorded governance transition. Existing repository history and accepted VDPs must remain inspectable. Authority must migrate through records, not through unrecorded social assumption.

If Veridion migrates repositories or forges, the migration record must identify the prior canonical repository, new canonical repository, effective source revision, official mirrors, archived copies, and authority-transfer rationale.

## Extensibility

Future specifications may define elections, Working Group charter templates, a formal contributor code of conduct, trademark policy, certification program, cryptographic attestations, confidential security-review procedure, conformance manifests, and governance tooling.

Extensions must preserve the constitutional supremacy and human accountability boundaries defined here.

## Alternatives Considered

Permanent founder control was rejected because it would make the project permanently personal and conflict with constitutional supremacy.

Pure contributor voting was rejected because broad voting can be vulnerable to low-context participation, capture, identity ambiguity, and lack of accountable stewardship.

Pure maintainer meritocracy was rejected because technical contribution alone does not define constitutional legitimacy or protect contributors from opaque authority.

A fully centralized council from day one was rejected because Veridion does not yet have a broad, independent maintainer body.

Corporate governance was rejected because funding, employment, and commercial implementation should not automatically create constitutional authority.

AI-assisted voting was rejected because AI may assist analysis but must not exercise human governance authority.

Implementation-owner authority was rejected because implementation popularity must not redefine specifications.

GitHub-owner authority was rejected because repository hosting and constitutional authority must remain separate.

Informal consensus without recorded decisions was rejected because it cannot reliably preserve authority, dissent, evidence, or historical accountability.

The phased constitutional model is preferred because it works for the current founding state while requiring a transition to institutional authority.

## Open Questions

- Detailed election mechanics: deferred to a future governance election specification. Interim rule: no election is required until the Constitution or a transition record invokes one.
- Formal contributor code of conduct: deferred to future community governance work. Interim rule: moderation and removal must follow the proportionality and review rules in this Constitution.
- Trademark policy: deferred to future legal or governance policy. Interim rule: no party may imply governance endorsement without an endorsement record.
- Certification program: deferred to future conformance or certification specifications. Interim rule: conformance claims must follow applicable VDP requirements and must not imply official endorsement.
- Cryptographic governance attestations: deferred to a future integrity specification. Interim rule: source revision identifiers and inspectable records remain required.
- Confidential security-review procedure: deferred to future security governance. Interim rule: sensitive evidence may be restricted while public records preserve scope and authority.
- Exact Working Group charter template: deferred to future governance templates. Interim rule: each charter must contain the elements required by VDP-0000.

These deferrals do not block constitutional interpretation because the interim rules preserve authority, accountability, and reviewability.

## Future Work

Future work includes moving VDP-0000 from Draft to Discussion, conducting external constitutional review, performing an acceptance-readiness audit, defining a Constituent Ratification Record, and later developing specifications for governance elections, community conduct, trademarks, certification, attestations, confidential security review, and Working Group templates.

## References

- VDP--001: Specification Specification.
- VDP--001 Bootstrap Acceptance Record.
- VDP--001 Acceptance Readiness Audit.
- RFC 2119: Key words for use in RFCs to Indicate Requirement Levels.
- RFC 8174: Ambiguity of Uppercase vs Lowercase in RFC 2119 Key Words.
- Semantic Versioning 2.0.0.
- JSON Schema Draft 2020-12.
- CommonMark.

## Appendices

### Appendix A — Governance Phases

Founder Stewardship is active initially. Maintainer Governance may begin through a Governance Transition Record. Constitutional Governance begins only when the Steering Council is activated through a valid transition record.

### Appendix B — Failure Scenario Behavior

If the founder leaves without notice or becomes inactive, succession or Interim Constitutional Steward rules apply after 90 consecutive days. If the founder attempts to bypass the Constitution after Council activation, the attempted action lacks constitutional authority. If a sole Maintainer claims project authority, the claim fails unless supported by valid records. If the Council deadlocks, no decision occurs. If Council membership falls below three, Council authority is limited to vacancy and continuity handling. If a repository owner removes legitimate maintainers, repository access does not by itself change constitutional authority. If the repository is compromised, malicious default-branch changes are not constitutionally valid. If a hostile fork claims to be canonical, it must show a valid authority transfer or succession record. If company funding creates pressure, organizational conflicts must be disclosed and recusal may be required. If AI-generated approval is submitted as human approval, it is invalid. If emergency powers are not reviewed, the action expires and the failure is a governance defect. If an amendment removes contributor protections, it requires the constitutional amendment threshold and impact analysis. If the project becomes dormant, dormancy must be recorded and must not imply current support. If all active governance participants share the same organizational conflict, the conflict must be disclosed and governance action should be limited to preservation until independent review is possible.

### Appendix C — Bootstrap History

VDP--001 established the Veridion Proposal System. VDP--001 bootstrap authority applied only to VDP--001 and expired immediately after its first acceptance. VDP-0000 uses a separate one-time constituent ratification process. That process has not been exercised in this draft. The ratification mechanism will expire after the first Accepted Constitution. Future governance derives from the Accepted Constitution.
