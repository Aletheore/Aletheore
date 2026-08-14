"""Hosted embeddings for the CLI's semantic index.

Why this exists: `aletheore index` needs an embedding model, and the free
tier makes the user supply one - a local Ollama, or their own OpenAI key,
with a consent prompt before any code leaves the machine. That works, but it
is a setup step in front of the feature, and "install a model server and
pull a 274MB GGUF" is where most people stop. What a paid plan buys here is
not a capability the free tier lacks - the search is identical either way -
it is not having to do that.

The gate is this endpoint returning 402, not a check inside the CLI. A
client-side check in an open-source binary is a suggestion; a server that
refuses unentitled callers is a gate. Same effort, and only one of them
survives someone reading the source.

Nothing is stored. Chunk text arrives, vectors go back, and the index lives
on the caller's disk. That is a deliberately smaller promise than "we index
your code": no retention policy to write, no deletion path to honour, and
nothing here for a subpoena to reach.
"""

import asyncio
import functools
import hashlib
import logging
import os

from fastapi import APIRouter, HTTPException, Request
from openai import OpenAI
from pydantic import BaseModel, Field

from app_server.db import (
    get_extra_seats,
    get_installation_by_token_hash,
    get_llm_spend_this_month,
    record_llm_spend,
)
from app_server.llm_cost import base_cap_for_plan, cost_for_usage, monthly_cap_for_installation
from app_server.rate_limit import is_rate_limited
from app_server.redis_client import get_redis_client

embeddings_router = APIRouter()
logger = logging.getLogger(__name__)

# Must match aletheore.search_index.OPENAI_EMBEDDING_MODEL. Vectors from two
# models cannot share one index - 768 dimensions against 1536 - and the CLI
# discards its whole reuse cache when the dimension changes, so a silent
# change here costs every hosted user a full re-embed of every repository.
EMBEDDING_MODEL = "text-embedding-3-small"

# One `aletheore index` run sends its chunks in batches of 200 (see
# search_index.EMBED_BATCH_SIZE). This bounds one request, not one index
# build - a repository of any size arrives as many requests, each of which
# is separately authenticated, rate-limited and charged.
MAX_TEXTS_PER_REQUEST = 256

# Chunk text is already truncated to 5000 chars client-side before it is
# ever embedded, so this is a ceiling on a caller who ignores that rather
# than a limit real usage approaches.
MAX_CHARS_PER_TEXT = 8_000

# Keyed per (installation, repo_id) below, not just installation - see the
# repo_id field. `aletheore watch`'s 2-second debounce means one repo alone
# can theoretically send up to 1,800 requests/hour (one settled batch every
# debounce interval); real editing sessions land far below that, but the
# ceiling has to clear it with room, not just the common case. Generous
# beyond that too, because a legitimate first index of a large repository
# is a burst: ~1,500 chunks at 200 per request is 8 calls, and a monorepo
# several times that. The spend cap is the real cost control; this only
# stops a caller from turning one token into unbounded upstream volume.
RATE_LIMIT_REQUESTS = 2000
RATE_LIMIT_WINDOW_SECONDS = 3600

# Bucket key length, not a content limit: repo_id is a 16-char hex prefix of
# a sha256 (see aletheore.search_index._repo_id) client-side, but the field
# is caller-supplied and unauthenticated-in-meaning - it only ever affects
# which rate-limit counter a request lands in, never authorization or
# billing, so nothing worse than a wasted Redis key results from an odd
# value. Bounded anyway against a pathological string bloating a key.
MAX_REPO_ID_LENGTH = 128


class EmbeddingsRequest(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=MAX_TEXTS_PER_REQUEST)
    # Optional so an older CLI that predates this field still works - it
    # just shares the coarser, installation-only bucket every caller used
    # to share (see the fallback in create_embeddings below).
    repo_id: str | None = Field(default=None, max_length=MAX_REPO_ID_LENGTH)


@functools.lru_cache(maxsize=1)
def get_openai_client() -> OpenAI:
    return OpenAI(api_key=os.environ["OPENAI_API_KEY"])


async def _authenticated_installation(request: Request) -> dict:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")

    token_hash = hashlib.sha256(auth_header.removeprefix("Bearer ").encode()).hexdigest()
    installation = await get_installation_by_token_hash(request.app.state.db_pool, token_hash)
    if installation is None:
        raise HTTPException(status_code=401, detail="invalid or revoked token")
    return installation


@embeddings_router.post("/v1/embeddings")
async def create_embeddings(request: Request, body: EmbeddingsRequest):
    installation = await _authenticated_installation(request)
    installation_id = installation["installation_id"]
    pool = request.app.state.db_pool

    if installation["plan"] == "free":
        raise HTTPException(
            status_code=402,
            detail=(
                "hosted embeddings require a paid plan - run 'aletheore index' with a local "
                "Ollama or your own OPENAI_API_KEY instead, which is free and produces the "
                "same index"
            ),
        )

    for text in body.texts:
        if len(text) > MAX_CHARS_PER_TEXT:
            raise HTTPException(
                status_code=413,
                detail=f"each text must be at most {MAX_CHARS_PER_TEXT} characters",
            )

    # Per-repo when the caller sends one (see EmbeddingsRequest.repo_id):
    # `aletheore watch` running against several repos on one token would
    # otherwise share a single counter, and one repo's rebase-heavy burst
    # could starve the others' embeddings on the same installation. Falls
    # back to the old installation-only bucket for a caller that doesn't
    # send it, rather than treating a missing repo_id as its own bucket -
    # an empty-string "no repo" bucket would just recreate the shared-budget
    # problem for every caller that omits the field.
    rate_limit_key = (
        f"ratelimit:embeddings:{installation_id}:{body.repo_id}"
        if body.repo_id
        else f"ratelimit:embeddings:{installation_id}"
    )
    try:
        rate_limited = is_rate_limited(
            get_redis_client(),
            rate_limit_key,
            RATE_LIMIT_REQUESTS,
            RATE_LIMIT_WINDOW_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        # Fails open, matching every other rate limit here: a Redis outage
        # should cost abuse protection, not availability. The spend cap
        # below is the control that actually bounds cost, and it reads from
        # Postgres rather than Redis.
        logger.warning("embeddings rate limit check failed (%s); allowing request", exc)
        rate_limited = False
    if rate_limited:
        raise HTTPException(
            status_code=429,
            detail="too many embedding requests",
            headers={"Retry-After": str(RATE_LIMIT_WINDOW_SECONDS)},
        )

    # Checked before the call, recorded after. H-4 in the 2026-08-10 audit
    # was exactly this gap on AIRview and Docs - LLM spend that was neither
    # capped nor recorded, so the figure shown to the customer understated
    # what they had used and the cap was enforced against an undercount.
    extra_seats = await get_extra_seats(pool, installation_id)
    monthly_cap = monthly_cap_for_installation(
        base_cap_for_plan(installation["plan"]), extra_seats
    )
    if await get_llm_spend_this_month(pool, installation_id) >= monthly_cap:
        raise HTTPException(
            status_code=402,
            detail=(
                f"monthly spend cap reached for this installation (${monthly_cap:.2f}) - "
                "hosted embeddings resume next month, or contact support@aletheore.com to "
                "raise the limit sooner. 'aletheore index' with a local Ollama or your own "
                "OPENAI_API_KEY works right now and is free, with no cap"
            ),
        )

    # Read straight from the process environment rather than through
    # credentials.get_api_key, whose fallback path prompts on stdin - a
    # server has nobody to answer it and would hang or raise EOFError.
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        logger.error("hosted embeddings requested but OPENAI_API_KEY is not configured")
        raise HTTPException(status_code=503, detail="hosted embeddings are not configured")

    client = get_openai_client()
    try:
        response = await asyncio.to_thread(
            client.embeddings.create,
            model=EMBEDDING_MODEL,
            input=body.texts,
        )
    except Exception as exc:  # noqa: BLE001 - provider errors of any shape degrade to 502
        # The upstream message can quote the input back, which here is the
        # caller's own source code - logged, never returned.
        logger.warning(
            "hosted embedding call failed for installation=%s (%s)",
            installation_id,
            type(exc).__name__,
        )
        raise HTTPException(status_code=502, detail="embedding provider unavailable") from exc

    # Billed on the provider's own token count rather than a local estimate,
    # so the recorded spend matches the invoice.
    prompt_tokens = response.usage.prompt_tokens if response.usage else 0
    await record_llm_spend(
        pool,
        installation_id,
        cost_for_usage(EMBEDDING_MODEL, prompt_tokens, 0),
        monthly_cap=monthly_cap,
    )

    return {
        "model": EMBEDDING_MODEL,
        "vectors": [item.embedding for item in response.data],
    }
