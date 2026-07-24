"""Self-hosted generation for the hosted MCP answer tool."""

import logging
import threading

import httpx

GENERATION_MODEL = "qwen2.5:3b-instruct"
GENERATION_BASE_URL = "http://ollama:11434"
GENERATION_SEMAPHORE = threading.Semaphore(2)

ANSWER_SYSTEM_PROMPT = (
    "You answer questions about a specific codebase using only the code chunks provided below. "
    "Answer in 2-5 sentences. If the chunks do not answer the question, say so plainly."
)

logger = logging.getLogger(__name__)


def generate_answer(
    question: str,
    chunks: list[str],
    timeout_seconds: float = 20.0,
    acquire_timeout_seconds: float = 15.0,
) -> str | None:
    acquired = GENERATION_SEMAPHORE.acquire(timeout=acquire_timeout_seconds)
    if not acquired:
        logger.warning("generation semaphore saturated; rejecting hosted MCP answer")
        return None
    try:
        context = "\n\n---\n\n".join(chunks)
        user_prompt = f"Question: {question}\n\nRetrieved code chunks:\n\n{context}"
        try:
            with httpx.Client(base_url=GENERATION_BASE_URL) as client:
                response = client.post(
                    "/api/chat",
                    json={
                        "model": GENERATION_MODEL,
                        "messages": [
                            {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
                            {"role": "user", "content": user_prompt},
                        ],
                        "stream": False,
                    },
                    timeout=timeout_seconds,
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("generation call failed (%s)", type(exc).__name__)
            return None
        content = data.get("message", {}).get("content")
        return content if isinstance(content, str) and content else None
    finally:
        GENERATION_SEMAPHORE.release()
