# Part VI — AI/LLM Usage Detection

This section governs how to read `evidence.repository.ai_usage`. Its findings belong in the
Repository Intelligence report section (Part II governs that section's overall structure) —
this part only covers how to interpret the `ai_usage` sub-key specifically. Follow the
mandatory verification rules in Part I for everything below.

## What's in `evidence.repository.ai_usage`

Five categories, each a list of `{name, evidence}` entries in the same shape as
`repository.frameworks`:

- `providers`: detected LLM API client packages (e.g. `openai`, `anthropic`).
- `orchestration`: detected agent/orchestration frameworks (e.g. `langchain`, `llama-index`).
- `vector_stores`: detected vector database client packages (e.g. `chromadb`, `pinecone`).
- `local_inference`: detected local/self-hosted model tooling (e.g. `transformers`, `ollama`).
- `mcp`: detected Model Context Protocol packages.

All five keys are always present, even when empty — an empty list means no package in that
category was found in `requirements.txt` or `package.json`, not that detection didn't run.

## Mandatory rules

- **A detected entry is a fact about package presence only**, evidenced by the exact manifest
  line in its `evidence` field — never a judgment about how well the package is used, how
  safely, or how it's architected. Cite the entry's `name` and `evidence` field directly.
- **Do not speculate about AI/LLM usage beyond what `ai_usage`'s five lists contain.** If none
  of the five categories have any entries, state plainly that no AI/LLM package usage was
  detected — do not infer AI usage from file names, comments, or general repository purpose.
- **`ai_usage` only reflects `requirements.txt` and `package.json`.** A repository whose
  dependencies are declared elsewhere (`pyproject.toml`, `Pipfile`, a lockfile without a
  manifest) will show empty lists here even if it does use AI/LLM packages — if
  `repository.build_tools` or other evidence suggests a dependency file format outside this
  scanner's coverage, note that as a gap rather than asserting "no AI usage."

## What counts as noteworthy

- **Any non-empty category** is worth naming explicitly with the exact package name(s) and
  their `evidence` manifest lines.
- **Multiple providers detected together** (e.g. both `openai` and `anthropic` present) is
  worth stating as a fact — evidence does not show how they're actually used in code (a
  fallback strategy, a multi-provider router, or simply two unrelated features), so do not
  speculate about the reason both are present.
- **A provider or orchestration package alongside no vector store** (or vice versa) is not
  inherently noteworthy — many legitimate AI integrations use only a provider client with no
  RAG/vector-store component at all. Do not imply an architecture is incomplete just because
  it doesn't span all five categories.

## What this section does not produce

No prompt-injection risk assessment, no RAG pipeline quality evaluation, no agent-architecture
review, no guardrail or safety evaluation, no latency or inference-cost analysis, no model
selection judgment. None of that is determinable from a manifest-matching scan — only package
presence is.
