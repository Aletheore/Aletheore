# Review Context

A standalone, source-backed documentation generator for code review. It writes repository contract documents and PR-specific evidence packets. It uses no model, API key or network connection. It does not change AIRview or Flash Review.

Ordinary file summaries can omit the exact relationship that makes a change unsafe. This generator keeps the source relationship: a module attribute used by a test, a callback passed into a consumer, or a caller depending on a return value. Documents contain the actual code and a specific review obligation, rather than an inferred bug verdict.

## Use

Python 3.10+ and Git are sufficient. From this directory:

```sh
# Repository documents and a machine-readable dependency index.
python -m review_context build --repo /path/to/repo --revision COMMIT \
  --include src --include tests --output /tmp/contracts-COMMIT

# Documentation can be limited to selected files while indexing all source roots.
python -m review_context build --repo /path/to/repo --revision COMMIT \
  --file src/package/worker.py --output /tmp/worker-contracts-COMMIT

# Evidence selected automatically from a PR's changed symbols and removed imports.
python -m review_context pr --repo /path/to/repo --base BASE --head HEAD \
  --include src --include tests --max-chars 16000 --output /tmp/pr-contracts-HEAD

python -m unittest discover -s tests -v
```

Use a new output directory per revision/run. Outputs never overwrite an existing nonempty directory. Source is read from committed Git objects, never the working tree. Include tests as well as application code; dynamic test patch targets are important dependencies.

## Change-safety documents (v0.2)

Each file page has required sections for enforced checks/invariants, failure behavior, state and resource lifecycle, configuration reads, tests/dynamic consumers, and callers/return-value contracts. Empty sections say coverage is unknown. Assertions and exception handlers are observed syntax, not automatically inferred business invariants or safety guarantees.

Each material claim carries an ID, a change-plan consequence, source evidence, and revision/blob/text hashes. Repeated observations are collapsed; symbol inventories are not promoted into claims. Human pages show at most 12 claims per section and point to the complete structured contract data. PR packets retain raw dependency evidence first, then add only seed-related material claims that fit. Excluded claim IDs and raw-evidence omissions remain visible.

Up to five workflow pages are selected from validated graph relationships. PR workflows must touch changed symbols. Callback workflows include their destination consumer implementation, with large partial definitions explicitly labeled. These are observed dependency paths, not claims about complete runtime ordering. `relationships.json` stores the explicit from/to/label graph; `routing.json` and the generated landing page route a changed file to its contracts, observed tests and related workflows.

## Documents

- `relationships.json`: explicit graph-backed from/to/label relationships with source evidence.
- `workflows.json` and `workflows/*.md`: bounded, validated dependency workflows.
- `routing.json` and `README.md`: task routing from changed files to contracts, tests and workflows.
- `index.json`: all resolved symbols and reference edges, indexed revision, and explicit coverage gaps.
- `files/<source-path>.md`: source-backed contract documentation or PR evidence, with cited code and review obligations.
- `contracts/<source-path>.json`: structured evidence for repository pages (`build`).
- `packets.json`: per-changed-file evidence for a review (`pr`).
- `manifest.json`: revision provenance, configuration, index hash, and coverage.

Every excerpt records its path, exact revision, Git blob, line interval, text hash, relation type and dependency chain. Individual excerpts are preserved whole. Budget omissions are listed; unindexed changed files are listed. Do not interpret missing evidence as a clean review.

## Dependency contracts

| Relationship | Evidence retained | Review question |
|---|---|---|
| String-based patch/getattr target | The unchanged consumer's source | Does an import or attribute removal break consumers outside the diff? |
| Function passed as a callback | The call site and consumer implementation | Is the return shape and failure signaling compatible with the consumer? |
| Callback result handled by a consumer | The full small consumer body | Is an incomplete result stored, aggregated, or later reused as if complete? |
| Direct caller | The call site and surrounding code | Does the changed interface still satisfy existing callers? |
| Direct callee | The small implementation | What does this dependency actually do? |

Consumer priority is higher than incidental callee context. Selection is deterministic and uses the diff and static source relationships. It contains no named regression rules, manually selected source paths, bug labels, or model-written explanations. The graph is also retained separately from the bounded review packet, so omitted relationships remain available for deeper inspection.

## Integration boundary

For each focal file, append `packets[file]["text"]` to the existing review prompt. Keep system instructions and model settings unchanged when evaluating. The raw code is untrusted input; the packet labels it as evidence, not instructions. Keep the structured `evidence` and `omitted` records for auditing rather than sending metadata wholesale.

The generator observes relationships. It does not prove a defect or decide severity. Flash makes that judgment. Maintainer-written contracts and patterns from other documentation projects can be added later, with their own source evidence and provenance; they are not synthesized or assumed here.

## Limits and validation

Python is the first language adapter. JavaScript, TypeScript and other languages are explicitly unindexed. Resolution is static and incomplete for star imports, aliases/rebinding, receiver types, reflection and runtime dispatch. Parameters and assigned local names conservatively shadow global targets; function-local imports are not treated as module attributes. Ambiguous module suffixes are rejected. Large functions may receive a clearly labeled partial excerpt. Deleted files and dependencies outside indexed roots need additional handling. This is a working first version, not a claim of complete repository understanding.

Tests cover removed-import consumers, callback consumers with storage, same-name false matches, scoped imports and shadowing, ambiguous modules, deterministic budget omissions, mandatory safety sections, stale/edited claims, disconnected/invented workflow edges, task routing, revision pinning and unsupported input reporting. Two known historical regressions are used as an integration diagnostic. Their selection informed the design, so passing them is not held-out quality evidence. A full public-corpus/held-out evaluation remains necessary before production integration.

## Validation boundaries

Claim validation checks source provenance and content freshness. It cannot prove arbitrary prose is semantically true; generated claims are restricted to deterministic syntax rules. Relationship/workflow validation checks the indexed static graph, whose own language-resolution limits still apply. Maintainer claims or external documents need their own explicit assertion/evidence policy before ingestion.

Artifact schema is version 2 (`change-safety-v2`), package version 0.2. Older frozen experiments are historical outputs and must not be relabeled as evaluations of this version. The original AIRview-summary experiment retained only bounded Overview/How it works/Gotchas/Why it exists prose. This generator consumes structured evidence and does not depend on those headings or a future AIRview heading rename.
