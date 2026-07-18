# OpenAI Embedding Fallback for Semantic Search

**Status:** Draft, pending review
**Date:** 2026-07-18

## Problem

`aletheore index` (and `query search-codebase`/`query answer`/the MCP `aletheore_search_codebase`
tool, all of which embed text through the same `embed_texts` function in
`prototype/aletheore/search_index.py`) hard-codes embeddings to local Ollama
(`http://localhost:11434/v1`, `nomic-embed-text`) with no alternative. Confirmed by reading the
code: `embed_texts` calls Ollama's OpenAI-compatible endpoint unconditionally and raises
`EmbeddingProviderUnavailableError` with an Ollama-specific troubleshooting message on any
failure - there is no other embedding path at all.

This is inconsistent with the rest of the product: `aletheore audit` and `query answer` already
support 12 provider/agent choices via the existing adapter infrastructure
(`aletheore/adapters/`, `select_adapter`, `credentials.py`'s `get_api_key`/`has_api_key`). A user
who already has `OPENAI_API_KEY` configured for `audit` but hasn't installed Ollama cannot use
semantic search at all today, even though a real, usable embeddings API (OpenAI's
`text-embedding-3-small`) is sitting right there through infrastructure this project already has.

## Goals

- When Ollama's embedding endpoint is unreachable, fall back to OpenAI's embeddings API
  (`text-embedding-3-small`) if `OPENAI_API_KEY` is configured (via the existing
  `credentials.py` env-var-or-saved-credential lookup - no new credential storage mechanism).
- Reuse the existing `OpenAI` Python client already imported in `search_index.py` - just pointed
  at `https://api.openai.com/v1` with a real key instead of Ollama's local, keyless endpoint.
- Match the project's own established consent principle for any API-based provider ("a fresh
  consent prompt naming the exact provider before any data leaves the machine - never
  remembered, every single time," per `_audit`'s `adapter.requires_consent` flow in `cli.py`).
  This is a *stronger* case for consent than `audit`'s: `audit` sends already-computed evidence;
  this fallback sends real source code chunks (`build_chunks`' `text` field is literal source
  lines), which is more sensitive, not less.

## Non-Goals

- **Not adding Gemini/Mistral/other embedding providers.** OpenAI is the only other provider with
  a real embeddings API reachable through infrastructure already in this codebase (Anthropic has
  no native embeddings endpoint at all - Claude cannot be an embedding fallback regardless of
  scope). Expanding beyond OpenAI is a separate, later decision if ever needed.
- **Not adding a new CLI flag.** No `--embedding-provider` option - the fallback is automatic
  (try Ollama, fall back only on failure), matching how `embed_texts`'s call sites already work
  today with zero new interface surface for the common case (Ollama present and working).
- **Not silently consenting in non-interactive contexts.** The MCP server's
  `aletheore_search_codebase`/`aletheore_answer` tools call the same `embed_texts` function with
  no real terminal attached - an `input()`-based prompt there would hang or silently pass with no
  real human watching. In that context (`sys.stdin.isatty()` is false), the fallback is refused
  outright with a clear error explaining why, never attempted silently. This is a deliberate,
  conservative choice: better to fail clearly than to exfiltrate code from an automated/agent
  context no human is actively supervising.

## Design

### `embed_texts`'s new fallback path

`prototype/aletheore/search_index.py`'s `embed_texts` keeps its exact current signature and
Ollama-first behavior unchanged for the success path. On failure:

1. Check `has_api_key("OPENAI_API_KEY", "OpenAI", credentials_path)` (imported from
   `aletheore.credentials`, the same function `audit`'s adapters already use). If no key is
   configured (env var or saved credential), raise today's existing
   `EmbeddingProviderUnavailableError` unchanged - no behavior change for a user without an
   OpenAI key either.
2. If a key *is* available but the process isn't attached to a real terminal
   (`not sys.stdin.isatty()`), raise `EmbeddingProviderUnavailableError` with a message that
   explains an OpenAI fallback exists but requires running interactively - never silently used
   from the MCP server or a script.
3. If interactive, prompt for explicit consent naming OpenAI specifically, styled after
   `_audit`'s existing consent text (`cli.py:259-265`) but accurately describing what's being
   sent (source code chunks, not "evidence"):
   ```
   Ollama is unavailable. Aletheore can fall back to OpenAI's 'text-embedding-3-small'
   embeddings API instead - this sends this repository's source code chunks to OpenAI's API.
   Continue with OpenAI embeddings? [y/N]:
   ```
   A "no" (or anything but `y`) raises `EmbeddingProviderUnavailableError` with a "declined, no
   data was sent" message - same shape as `audit`'s own cancel path.
4. On "yes," call OpenAI's real embeddings endpoint (`base_url="https://api.openai.com/v1"`,
   `model="text-embedding-3-small"`, the real API key from `get_api_key`) via the same `OpenAI`
   client class already imported. If that call itself fails, raise
   `EmbeddingProviderUnavailableError` naming both failures (Ollama unreachable, OpenAI attempt
   also failed) so the user isn't left guessing which one to fix.

The consent check is a real function call (`confirm_fn`), not inlined `input()`, so it can be
substituted in tests exactly like `credentials.py`'s existing `prompt_fn` parameter pattern -
consistent with how this codebase already makes interactive prompts testable.

### Interactivity detection at the right layer

`sys.stdin.isatty()` is checked inside `embed_texts` itself (not passed in by the caller), since
`embed_texts` is called from three places today (`build_index` for indexing, `search_index` for
query-time embedding) that would otherwise all need to independently thread an `interactive: bool`
parameter through. Checking it directly inside `embed_texts` keeps every call site's existing
signature untouched.

### What doesn't change

- `build_index`, `search_index`'s public signatures - unchanged.
- The default, working-Ollama path - identical behavior, zero added latency or prompts.
- `cli.py`'s `_index`/`_query` functions - unchanged; the consent prompt surfaces through
  `embed_texts`'s own `print`/`input` (or `confirm_fn` override), not through new CLI-layer code,
  since `_index`/`_query` already just propagate whatever exception `build_index`/`search_index`
  raise today.

## Testing / Success Criteria

- A test confirms the existing Ollama-only-failure path is unchanged when no `OPENAI_API_KEY` is
  configured (today's exact error, verified via a fixture with no env var set and no saved
  credential).
- A test confirms that with `OPENAI_API_KEY` set and a fake `confirm_fn` returning `True`,
  `embed_texts` calls the OpenAI client with `text-embedding-3-small` and returns its embeddings,
  using mocked `urlopen`/mocked `OpenAI` class exactly like the existing
  `test_embed_texts_returns_one_vector_per_input`/`test_embed_texts_raises_actionable_error_when_model_unavailable`
  tests already do.
- A test confirms declining consent (`confirm_fn` returning `False`) raises
  `EmbeddingProviderUnavailableError` and never calls the OpenAI client.
- A test confirms that with `OPENAI_API_KEY` set but `sys.stdin.isatty()` mocked to `False` (and
  no `confirm_fn` override), the fallback is refused without ever calling `confirm_fn` or the
  OpenAI client - proving the non-interactive/MCP path never prompts or silently sends code.
- A test confirms that when both Ollama and the OpenAI fallback fail, the raised error message
  names both failures.
- Full existing `search_index`/`cli` test suites continue passing unchanged.
