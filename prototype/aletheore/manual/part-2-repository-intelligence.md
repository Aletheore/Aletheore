# Part II — Repository Intelligence

This section governs how to read `evidence.repository`. Follow the mandatory verification
rules in Part I for everything below.

## What's in `evidence.repository`

- `languages`: detected languages with file counts and rough line counts.
- `frameworks`: detected frameworks, each with an `evidence` string naming the manifest line
  that proves it (e.g. `"requirements.txt:fastapi==0.110.0"`).
- `ai_usage`: detected AI/LLM provider, orchestration, vector-store, local-inference, and MCP
  package usage — see Part VI for how to interpret this sub-key specifically.
- `build_tools`: detected build tooling, same evidence-string pattern.
- `monorepo`: whether workspace/monorepo tooling was detected, and the workspace list if so.
- `modules`: one entry per parsed source file, with `imports` (what it imports),
  `imported_by` (what imports it), and `symbols` (top-level functions/classes found).
- `dependency_graph`: `nodes` and `edges` derived from `modules`.
- `unparseable_files`: files that could not be parsed, with a `reason` per file.

## Do not speculate rule

**Do not speculate about languages or frameworks absent from the `languages` and
`frameworks` arrays.** If a language or framework isn't listed, evidence does not confirm
its presence — say so rather than guessing from file names or conventions.

## What counts as noteworthy

- **High fan-in modules**: a module with a long `imported_by` list. Worth flagging if it also
  has no obviously corresponding test file among the other `modules` entries (a module named
  `test_x.py` or `x.test.js` importing it) — state this as Medium confidence unless you can
  point to the specific absence.
- **Circular import chains**: any path in `dependency_graph.edges` that returns to its
  starting node. State the exact node sequence you found (High confidence — this is a direct
  graph read, not an inference).
- **Single-file god-modules**: a module whose `symbols.functions` + `symbols.classes` count is
  far larger than the repository's average for its language. State the actual counts you
  compared.
- **Evidence coverage gaps**: always report `unparseable_files` count and list, even if empty
  (say "none" explicitly rather than omitting the section).
