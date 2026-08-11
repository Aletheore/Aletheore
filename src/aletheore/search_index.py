import sys
from collections.abc import Callable
from pathlib import Path

import lancedb
from openai import OpenAI

from aletheore.credentials import DEFAULT_CREDENTIALS_PATH, get_api_key, has_api_key

FALLBACK_CHUNK_MAX_LINES = 200
DEFAULT_EMBEDDING_BASE_URL = "http://localhost:11434/v1"
DEFAULT_EMBEDDING_MODEL = "nomic-embed-text"
OPENAI_EMBEDDING_BASE_URL = "https://api.openai.com/v1"
OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"
INDEX_DIRNAME = "index.lancedb"
TABLE_NAME = "chunks"


class EmbeddingProviderUnavailableError(Exception):
    pass


class IndexNotFoundError(Exception):
    pass


def _default_confirm_openai_fallback() -> bool:
    print(
        "Ollama is unavailable. Aletheore can fall back to OpenAI's "
        f"'{OPENAI_EMBEDDING_MODEL}' embeddings API instead - this sends this "
        "repository's source code chunks to OpenAI's API."
    )
    return input("Continue with OpenAI embeddings? [y/N]: ").strip().lower() == "y"


# How much of a file's pre-symbol head (docstring, imports, top-level
# constants) goes into its module chunk. Generous enough for a real module
# docstring, bounded so a file with a 2,000-line lookup table before its
# first function doesn't produce one enormous chunk that matches everything.
MODULE_CHUNK_MAX_LINES = 80


def _is_test_path(module_path: str) -> bool:
    """Whether a path is test code rather than the implementation.

    Measured on this repo: tests were 485 of 793 indexed chunks (61%) and
    took 64% of all top-5 result slots, because a test shares its subject's
    identifiers and domain vocabulary while outnumbering it. Retrieval
    accuracy for "how does X work" went from 45% to 68% top-5 with these
    excluded. Someone asking how something works wants the implementation;
    if they want the test, they ask for the test by name and grep finds it.
    """
    parts = module_path.split("/")
    if any(part in {"tests", "test", "spec", "__tests__", "testing"} for part in parts):
        return True
    name = parts[-1]
    stem = name.rsplit(".", 1)[0]
    return (
        stem == "conftest"
        or stem.startswith("test_")
        or stem.endswith("_test")
        or stem.endswith(".test")
        or stem.endswith(".spec")
    )


def build_chunks(evidence: dict, repo_path: Path) -> list[dict]:
    chunks: list[dict] = []
    for module in evidence["repository"]["modules"]:
        module_path = module["path"]
        if _is_test_path(module_path):
            continue
        file_path = repo_path / module_path
        if not file_path.exists():
            continue
        try:
            lines = file_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue

        symbols = module["symbols"]["functions"] + module["symbols"]["classes"]
        if not symbols:
            end_line = min(len(lines), FALLBACK_CHUNK_MAX_LINES)
            snippet = "\n".join(lines[:FALLBACK_CHUNK_MAX_LINES])
            chunks.append(
                {
                    "module_path": module_path,
                    "symbol_name": None,
                    "start_line": 1,
                    "end_line": end_line,
                    "language": module.get("language", "unknown"),
                    "text": f"{module_path} (no extracted symbols)\n{snippet}",
                }
            )
            continue

        # One chunk describing the module itself, before the per-symbol ones.
        # Without it nothing in the index answers "what is this file for" -
        # every chunk was a single function, so "how is the dependency graph
        # built" matched private helpers like _rel and _symbol_entry and
        # missed graph.py entirely. Measured on this repo: graph.py had 55
        # chunks, the earliest starting at line 66, leaving the module
        # docstring, imports and the LANGUAGE_BY_EXTENSION table unindexed -
        # and only 5 of 793 chunks described a whole module at all.
        #
        # The head of a file is where a docstring, imports and top-level
        # constants live, so it is both the best summary available and the
        # part symbol extraction structurally cannot reach.
        head_end = min(symbols[0]["start_line"] - 1, MODULE_CHUNK_MAX_LINES)
        if head_end > 0:
            head = "\n".join(lines[:head_end]).strip()
            if head:
                symbol_names = ", ".join(s["name"] for s in symbols[:40])
                chunks.append(
                    {
                        "module_path": module_path,
                        # None marks a module chunk, matching the symbol-less
                        # fallback above so consumers need no new concept.
                        "symbol_name": None,
                        "start_line": 1,
                        "end_line": head_end,
                        "language": module.get("language", "unknown"),
                        # Path and symbol list join the docstring so the chunk
                        # is reachable by what the module *contains*, not only
                        # by how its author happened to describe it.
                        "text": f"{module_path} (module overview)\n{head}\n\ndefines: {symbol_names}",
                    }
                )

        for symbol in symbols:
            start_line = symbol["start_line"]
            end_line = symbol["end_line"]
            source = "\n".join(lines[start_line - 1:end_line])
            header = f"{module_path}::{symbol['name']} ({module.get('language', 'unknown')})"
            chunks.append(
                {
                    "module_path": module_path,
                    "symbol_name": symbol["name"],
                    "start_line": start_line,
                    "end_line": end_line,
                    "language": module.get("language", "unknown"),
                    "text": f"{header}\n{source}",
                }
            )

    return chunks


def embed_texts(
    texts: list[str],
    base_url: str = DEFAULT_EMBEDDING_BASE_URL,
    model: str = DEFAULT_EMBEDDING_MODEL,
    credentials_path: Path = DEFAULT_CREDENTIALS_PATH,
    confirm_fn: Callable[[], bool] | None = None,
) -> list[list[float]]:
    client = OpenAI(base_url=base_url, api_key="not-needed")
    try:
        response = client.embeddings.create(model=model, input=texts)
        return [item.embedding for item in response.data]
    except Exception as ollama_exc:
        ollama_hint = (
            f"could not reach embedding model '{model}' at {base_url} "
            f"({type(ollama_exc).__name__}) - try 'ollama pull {model}' and confirm "
            "ollama is running"
        )
        if not has_api_key("OPENAI_API_KEY", "OpenAI", credentials_path):
            raise EmbeddingProviderUnavailableError(ollama_hint) from ollama_exc

        if not sys.stdin.isatty():
            raise EmbeddingProviderUnavailableError(
                f"{ollama_hint}. An OPENAI_API_KEY is configured and could be used as a "
                "fallback, but this isn't an interactive terminal, so Aletheore won't send "
                "code to OpenAI without being asked - run this from a real terminal to be "
                "prompted."
            ) from ollama_exc

        confirm = confirm_fn if confirm_fn is not None else _default_confirm_openai_fallback
        if not confirm():
            raise EmbeddingProviderUnavailableError(
                "Ollama is unavailable and the OpenAI embeddings fallback was declined - "
                "no data was sent."
            ) from ollama_exc

        api_key = get_api_key("OPENAI_API_KEY", "OpenAI", credentials_path)
        openai_client = OpenAI(base_url=OPENAI_EMBEDDING_BASE_URL, api_key=api_key)
        try:
            response = openai_client.embeddings.create(
                model=OPENAI_EMBEDDING_MODEL, input=texts
            )
        except Exception as openai_exc:
            raise EmbeddingProviderUnavailableError(
                f"Ollama unavailable ({type(ollama_exc).__name__}) and OpenAI embeddings "
                f"also failed ({type(openai_exc).__name__}) - confirm ollama is running or "
                "OPENAI_API_KEY is valid"
            ) from openai_exc
        return [item.embedding for item in response.data]


def _index_path(repo_path: Path) -> Path:
    return repo_path / ".aletheore" / INDEX_DIRNAME


def build_index(repo_path: Path, evidence: dict) -> int:
    chunks = build_chunks(evidence, repo_path)
    if not chunks:
        return 0

    vectors = embed_texts([chunk["text"] for chunk in chunks])
    rows = [{**chunk, "vector": vector} for chunk, vector in zip(chunks, vectors)]

    index_path = _index_path(repo_path)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(index_path))
    db.create_table(TABLE_NAME, data=rows, mode="overwrite")
    return len(rows)


def open_index(repo_path: Path):
    index_path = _index_path(repo_path)
    if not index_path.exists():
        raise IndexNotFoundError(
            f"no index found at {index_path} - run 'aletheore index {repo_path}' first"
        )
    db = lancedb.connect(str(index_path))
    return db.open_table(TABLE_NAME)


def search_index(repo_path: Path, query_text: str, k: int = 10) -> list[dict]:
    table = open_index(repo_path)
    query_vector = embed_texts([query_text])[0]
    raw_results = table.search(query_vector).limit(k).to_list()
    return [
        {
            "module_path": result["module_path"],
            "symbol_name": result["symbol_name"],
            "start_line": result["start_line"],
            "end_line": result["end_line"],
            "language": result["language"],
            "text": result["text"],
            "score": result.get("_distance"),
        }
        for result in raw_results
    ]
