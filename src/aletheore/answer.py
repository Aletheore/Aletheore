from pathlib import Path

from aletheore.adapters.base import AgentAdapter
from aletheore.search_index import search_index

ANSWER_SYSTEM_PROMPT = """You answer questions about a specific codebase using only the code
chunks provided below. Answer in 2-5 sentences. Cite which chunk(s) you used by their
"module_path::symbol_name" label. If the provided chunks don't actually answer the question,
say so plainly rather than guessing."""

DEFAULT_CONFIDENCE_THRESHOLD = 0.85


def answer_question(
    repo_path: Path,
    question: str,
    adapter: AgentAdapter,
    k: int = 5,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> dict:
    results = search_index(repo_path, question, k=k)

    if not results or results[0]["score"] > confidence_threshold:
        return {
            "answer": "Not enough evidence in the indexed codebase to answer this confidently.",
            "cited_chunks": [],
            "confidence_gated": True,
        }

    context = "\n\n---\n\n".join(result["text"] for result in results)
    user_prompt = f"Question: {question}\n\nRetrieved code chunks:\n\n{context}"
    answer_text = adapter.simple_completion(ANSWER_SYSTEM_PROMPT, user_prompt, str(repo_path))
    cited_chunks = [
        f"{result['module_path']}::{result['symbol_name']}"
        if result["symbol_name"]
        else result["module_path"]
        for result in results
    ]

    return {"answer": answer_text, "cited_chunks": cited_chunks, "confidence_gated": False}
