# Part IV — Architecture Review

This section governs how to read `evidence.architecture`. Follow the mandatory verification
rules in Part I for everything below.

## What's in `evidence.architecture`

- `clusters`: groups of modules found by running graph-community detection on
  `repository.dependency_graph` — each with an `id`, its `modules` list, and its
  `internal_edges` count (edges between two modules in the same cluster).
- `cross_cluster_edges`: every edge that crosses a cluster boundary, grouped by
  `(from_cluster, to_cluster)` pair, with the exact `edges` list and a `count`.
- `layer_violations.convention_detected`: whether a recognizable layering convention
  (domain/application/infrastructure-style folder naming) was found in this repository.
- `layer_violations.layers`: the specific folder-name-to-rank mapping actually detected, when
  `convention_detected` is `true`.
- `layer_violations.violations`: modules that import from an outer architectural layer while
  themselves belonging to an inner one — each with the exact `from`/`to` file paths and a
  `reason`.

## Mandatory rules

- **A cluster is a structural grouping derived from import coupling, not evidence of
  intentional architectural design.** Never claim a cluster represents a deliberate module
  boundary the codebase's author chose — say what the clustering found, not why it exists.
- **When `convention_detected` is `false`, state plainly that no layering convention was
  detected.** This is the common, normal case for most repositories — most codebases don't
  use domain/infrastructure-style folder naming. Do not describe this as a limitation, gap, or
  something to apologize for.
- **Every cross-cluster or violation claim must cite the exact file path(s)** from
  `cross_cluster_edges[].edges` or `layer_violations.violations[]` — never a cluster ID or a
  count alone, and never a claim about coupling that isn't backed by a specific edge in the
  evidence.

## What counts as noteworthy

- **A `layer_violations.violations` entry** is worth naming explicitly — both files involved
  and the two layer names crossed (state them exactly as they appear in `reason`).
- **A high `cross_cluster_edges` count relative to a cluster's `internal_edges`** is worth
  noting as something worth investigating, never as a confirmed problem. A shared utility
  module legitimately imported by many otherwise-unrelated clusters is expected, normal
  structure — that is a very different situation from two specific clusters being unexpectedly
  tangled with each other. Distinguish "many clusters depend on this one shared thing"
  (usually fine, common, not inherently a finding) from "these two particular clusters share
  an unusually large number of edges with each other" (more interesting, worth naming
  specifically) before treating cross-cluster coupling as noteworthy.

## What this section does not produce

Do not attempt to name or label an architectural pattern (hexagonal, clean, layered, MVC,
CQRS, event sourcing, microservices, DDD, or any other named style). Do not assess
abstraction quality, interface design, or identify named design patterns (repository, factory,
observer, mediator, etc.) — none of that is determinable from the evidence this scanner
produces. Report clusters and layer violations as raw structural facts; leave architectural
labeling and design-quality judgment out of scope entirely.
