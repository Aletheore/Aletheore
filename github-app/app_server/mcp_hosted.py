"""Hosted MCP tools backed by AIRview evidence, git mirrors, and code embeddings."""

import math
import re
from pathlib import Path, PurePath

from aletheore.query import QUERY_FUNCTIONS, find_symbol_source
from aletheore.secrets import iter_all_files
from aletheore.toon_encoding import to_toon
from mcp.server.fastmcp import FastMCP

from app_server.config import get_settings
from app_server.db import (
    get_latest_evidence_for_mcp,
    get_mcp_git_mirror,
    list_mcp_code_embeddings,
)
from app_server.embedding_client import embed_text
from app_server.hosted_generation import generate_answer
from app_server.mcp_auth import CURRENT_INSTALLATION_ID, McpAuthMiddleware

_TOOL_NAME_TO_QUERY_KIND = {
    "aletheore_imports": "imports",
    "aletheore_imported_by": "imported-by",
    "aletheore_symbols": "symbols",
    "aletheore_branch": "branch",
    "aletheore_ownership": "ownership",
    "aletheore_secrets": "secrets",
    "aletheore_vulnerabilities": "vulnerabilities",
    "aletheore_licenses": "licenses",
    "aletheore_endpoints": "endpoints",
    "aletheore_cluster": "cluster",
    "aletheore_layer_violations": "layer-violations",
    "aletheore_dead_code": "dead-code",
    "aletheore_hotspots": "hotspots",
    "aletheore_database": "database",
    "aletheore_infrastructure": "infrastructure",
    "aletheore_environment_variables": "environment-variables",
    "aletheore_find_evidence_for_endpoint": "evidence-for-endpoint",
    "aletheore_find_evidence_for_symbol": "evidence-for-symbol",
    "aletheore_find_evidence_for_dependency": "evidence-for-dependency",
}
_SEARCH_MATCH_CAP = 200
DEFAULT_ANSWER_CONFIDENCE_THRESHOLD = 0.3


def _toon_result(data: object) -> str:
    return to_toon({"result": data})


def _current_installation_id() -> int:
    installation_id = CURRENT_INSTALLATION_ID.get()
    if installation_id is None:
        raise RuntimeError("hosted MCP tool called outside an authenticated request")
    return installation_id


def _load_evidence(repo_full_name: str) -> dict | None:
    settings = get_settings()
    return get_latest_evidence_for_mcp(settings.database_url, _current_installation_id(), repo_full_name)


def _hosted_query(kind: str, repo_full_name: str, target: str | None = None) -> str:
    evidence = _load_evidence(repo_full_name)
    if evidence is None:
        return _toon_result({"error": "no evidence found for this installation's repo"})
    query_fn, _requires_target = QUERY_FUNCTIONS[kind]
    try:
        return _toon_result(query_fn(evidence, target))
    except Exception as exc:  # noqa: BLE001
        return _toon_result({"error": str(exc)})


def _make_query_tool(kind: str):
    _func, requires_target = QUERY_FUNCTIONS[kind]

    if requires_target:

        def tool(repo_full_name: str, target: str) -> str:
            return _hosted_query(kind, repo_full_name, target)

    else:

        def tool(repo_full_name: str) -> str:
            return _hosted_query(kind, repo_full_name, None)

    return tool


_hosted_imports = _make_query_tool("imports")


def _resolve_mirror_path(repo_full_name: str) -> Path | None:
    settings = get_settings()
    row = get_mcp_git_mirror(settings.database_url, _current_installation_id(), repo_full_name)
    if row is None:
        return None
    mirror = Path(row["local_path"])
    if not mirror.exists():
        return None
    return mirror


def _resolve_file_in_mirror(mirror: Path, file_path: str) -> Path | None:
    candidate = (mirror / file_path).resolve()
    try:
        candidate.relative_to(mirror.resolve())
    except ValueError:
        return None
    return candidate


def _hosted_symbol_source(
    repo_full_name: str,
    module: str | None = None,
    symbol: str | None = None,
    file_path: str | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
) -> str:
    mirror = _resolve_mirror_path(repo_full_name)
    if mirror is None:
        return _toon_result({"error": "mirror not yet synced or resync pending"})
    evidence = _load_evidence(repo_full_name)
    if evidence is None:
        return _toon_result({"error": "no evidence found for this installation's repo"})
    try:
        if module and symbol:
            return _toon_result(find_symbol_source(evidence, mirror, module, symbol))
        if file_path and start_line is not None and end_line is not None:
            resolved = _resolve_file_in_mirror(mirror, file_path)
            if resolved is None or not resolved.exists():
                return _toon_result({"error": "file not found in mirror"})
            lines = resolved.read_text(encoding="utf-8", errors="ignore").splitlines()
            return _toon_result(
                {
                    "module": file_path,
                    "start_line": start_line,
                    "end_line": end_line,
                    "source": "\n".join(lines[start_line - 1 : end_line]),
                }
            )
    except Exception as exc:  # noqa: BLE001
        return _toon_result({"error": str(exc)})
    return _toon_result({"error": "provide module+symbol or file_path+start_line+end_line"})


def _hosted_search(
    repo_full_name: str,
    pattern: str | None = None,
    regex: bool = False,
    path_glob: str | None = None,
    query: str | None = None,
) -> str:
    mirror = _resolve_mirror_path(repo_full_name)
    if mirror is None:
        return _toon_result({"error": "mirror not yet synced or resync pending"})
    needle = pattern if pattern is not None else query
    if not needle:
        return _toon_result({"error": "pattern is required"})
    compiled = re.compile(needle) if regex else None
    matches = []
    truncated = False
    for path in iter_all_files(mirror):
        rel_path = path.relative_to(mirror).as_posix()
        if path_glob is not None and not PurePath(rel_path).match(path_glob):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            found = compiled.search(line) if compiled else needle in line
            if found:
                if len(matches) >= _SEARCH_MATCH_CAP:
                    truncated = True
                    break
                matches.append({"path": rel_path, "line": line_no, "text": line})
        if truncated:
            break
    return _toon_result({"matches": matches, "truncated": truncated})


def _hosted_scan(repo_full_name: str) -> str:
    return _toon_result(
        {
            "error": (
                "hosted MCP reflects AIRview's already-scanned server-side evidence; "
                "use local `aletheore mcp` for ad hoc scans"
            ),
            "repo_full_name": repo_full_name,
        }
    )


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _rank_code_chunks(repo_full_name: str, query: str, k: int) -> list[dict] | None:
    query_embedding = embed_text(query, base_url="http://ollama:11434")
    if query_embedding is None:
        return None
    settings = get_settings()
    rows = list_mcp_code_embeddings(settings.database_url, _current_installation_id(), repo_full_name)
    return sorted(
        rows,
        key=lambda row: _cosine_similarity(query_embedding, row["embedding"]),
        reverse=True,
    )[:k]


def _hosted_search_codebase(repo_full_name: str, query: str, k: int = 10) -> str:
    rows = _rank_code_chunks(repo_full_name, query, k)
    if rows is None:
        return _toon_result({"error": "embedding model temporarily unavailable"})
    if not rows:
        return _toon_result({"error": "no indexed code for this repo yet"})
    return _toon_result(
        [
            {
                "module_path": row["file_path"],
                "chunk_index": row["chunk_index"],
                "text": row["chunk_text"],
            }
            for row in rows
        ]
    )


def _hosted_answer(repo_full_name: str, question: str, k: int = 5) -> str:
    rows = _rank_code_chunks(repo_full_name, question, k)
    if rows is None:
        return _toon_result({"error": "embedding model temporarily unavailable"})
    if not rows:
        return _toon_result({"error": "no indexed code for this repo yet"})
    query_embedding = embed_text(question, base_url="http://ollama:11434")
    if query_embedding is None:
        return _toon_result({"error": "embedding model temporarily unavailable"})
    if _cosine_similarity(query_embedding, rows[0]["embedding"]) < DEFAULT_ANSWER_CONFIDENCE_THRESHOLD:
        return _toon_result({"answer": "Not enough evidence in the indexed codebase to answer this confidently."})
    answer = generate_answer(question, [row["chunk_text"] for row in rows])
    if answer is None:
        return _toon_result({"error": "model temporarily unavailable"})
    return _toon_result({"answer": answer})


def build_hosted_mcp_app():
    mcp = FastMCP("aletheore-hosted", stateless_http=True)
    for tool_name, kind in _TOOL_NAME_TO_QUERY_KIND.items():
        tool_func = _make_query_tool(kind)
        tool_func.__name__ = tool_name
        tool_func.__doc__ = f"Query '{kind}' from hosted AIRview evidence."
        mcp.tool(name=tool_name)(tool_func)
    mcp.tool(name="aletheore_symbol_source")(_hosted_symbol_source)
    mcp.tool(name="aletheore_search")(_hosted_search)
    mcp.tool(name="aletheore_scan")(_hosted_scan)
    mcp.tool(name="aletheore_search_codebase")(_hosted_search_codebase)
    mcp.tool(name="aletheore_answer")(_hosted_answer)
    app = mcp.streamable_http_app()
    app.add_middleware(McpAuthMiddleware)
    return app
