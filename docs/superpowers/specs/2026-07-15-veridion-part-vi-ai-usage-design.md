# Veridion Part VI (AI/LLM Usage Detection) Design

**Status:** Draft, pending review
**Date:** 2026-07-15

## Problem

The original "Part VI — AI Review" vision (prompt quality, prompt injection, RAG/vector DB
quality, agent architecture, tool calling, guardrails, latency, model selection, inference
costs, GPU utilization, caching, safety, alignment, evaluation, benchmarks) was ruled out
earlier this session as needing real semantic understanding of code — not something a
deterministic scanner can ground. This spec covers the one piece that survived that cut:
**detecting which AI/LLM providers, orchestration frameworks, vector stores, local-inference
tooling, and MCP packages a repository uses** — the same kind of fact `detect_frameworks`
already establishes for FastAPI/React/etc., just for a different package category.

## Goals

- Detect AI/LLM-related package usage via the same manifest-matching mechanism
  `detect_frameworks` already uses (`requirements.txt` / `package.json`), extended with a
  curated marker list across five categories.
- Add `evidence.repository.ai_usage` with sub-lists per category, each entry shaped
  `{"name": str, "evidence": str}` — identical per-entry shape to `frameworks`.
- Ship a Part VI manual section, same two-tier structure as the rest.

## Non-Goals

- No prompt-injection risk assessment, RAG pipeline quality, agent-architecture review,
  guardrail evaluation, latency/cost analysis, or model-selection judgment — all explicitly
  ruled out as needing semantic code understanding no scanner can ground. Detecting *that* a
  repo imports `openai` is groundable; judging whether its prompts are well-constructed is not.
- No new file-scanning beyond what `detect_frameworks` already reads (`requirements.txt`,
  `package.json`) — no new manifest formats, no source-code-level AST inspection for this part.
- No claims about which provider/framework a repo *should* use, or whether its AI usage is
  "good practice" — detection only, no evaluation.

## Detection List (curated, not exhaustive — matches `FRAMEWORK_MARKERS_*`'s existing style)

- **Providers** (pip): `openai`, `anthropic`, `google-generativeai`, `google-genai`, `cohere`,
  `mistralai`. (npm): `openai`, `@anthropic-ai/sdk`, `@google/generative-ai`.
- **Orchestration** (pip): `langchain`, `llama-index`, `llama_index`, `crewai`, `autogen`.
  (npm): `langchain`.
- **Vector stores** (pip): `pinecone-client`, `pinecone`, `chromadb`, `weaviate-client`,
  `qdrant-client`, `faiss-cpu`.
- **Local inference** (pip): `transformers`, `ollama`, `llama-cpp-python`, `vllm`.
- **MCP** (pip): `mcp`. (npm): `@modelcontextprotocol/sdk`.

## Implementation Approach

Factor two helpers out of `detect.py`'s existing inline parsing (currently duplicated once
inside `detect_frameworks`, about to be needed five more times for `detect_ai_usage`'s
categories):

- `_iter_pip_package_lines(repo_path) -> list[tuple[str, str]]`: yields
  `(lowercased_package_name, full_requirements_line)` per non-comment, non-blank
  `requirements.txt` line — the exact parsing `detect_frameworks` already does, extracted.
- `_npm_dependencies(repo_path) -> dict[str, str]`: returns the merged
  `dependencies`/`devDependencies` map from `package.json` — same extraction.

`detect_frameworks` is rewritten to call these helpers (identical behavior, no duplication).
`detect_ai_usage` calls them once and matches the result against each category's marker dict,
producing the five-list `ai_usage` shape.

## Manual Content

Same two-tier structure as Parts II-V: **mandatory** — a detected entry is a fact about
package presence only, evidenced by the exact manifest line, never a judgment about how well
it's used, how safe it is, or whether it's architected correctly. **Interpretation
guidance** — multiple providers detected together is worth noting factually (e.g. "both
`openai` and `anthropic` are present") without speculating about multi-provider routing logic
or fallback strategies, since evidence doesn't show how they're actually used in code, only
that both are declared dependencies.

## Testing Strategy

Unit tests for `detect_ai_usage` against synthetic `requirements.txt`/`package.json` fixtures
covering at least one match per category, plus a fixture with no AI packages at all (empty
lists, not omitted keys). Existing `detect_frameworks` tests must continue passing unchanged
after the helper refactor — this is a regression risk to check explicitly, not assume.

## Success Criteria

1. Running against Veridion's own `prototype/pyproject.toml`... note: `detect_ai_usage` reads
   `requirements.txt`/`package.json` only, and Veridion's own prototype uses `pyproject.toml`
   for its dependencies, not `requirements.txt` — so Veridion's own repo is expected to show
   `ai_usage` entries as all empty lists despite depending on `tree-sitter`, `certifi`, and
   `networkx` (none of which are AI packages anyway, so this is a moot point for this specific
   repo, but worth being precise that the manifest-detection mechanism has this known,
   inherited limitation from `detect_frameworks` — not something this spec needs to fix).
2. Running against Procta (which does use `requirements.txt` and `package.json`) either finds
   real `ai_usage` entries or a clean set of empty lists — either result is a valid pass, the
   criterion is that the mechanism runs correctly and produces the right shape either way.
3. `detect_frameworks`'s existing test suite passes unchanged after the shared-helper refactor.
