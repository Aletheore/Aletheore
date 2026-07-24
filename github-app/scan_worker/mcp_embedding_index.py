"""Embedding re-index for hosted MCP code-search tools."""

import hashlib
from pathlib import Path

from aletheore.search_index import build_chunks

from scan_worker.db import (
    delete_mcp_code_embeddings_for_file,
    get_mcp_code_embedding_hashes,
    list_mcp_code_embeddings,
    upsert_mcp_code_embedding,
)
from scan_worker.embedding_client import embed_text


def _chunk_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def reindex_mcp_embeddings(
    dsn: str,
    installation_id: int,
    repo_full_name: str,
    evidence: dict,
    mirror_path: Path,
) -> None:
    chunks_by_file: dict[str, list[dict]] = {}
    for chunk in build_chunks(evidence, mirror_path):
        chunks_by_file.setdefault(chunk["module_path"], []).append(chunk)

    for file_path, file_chunks in chunks_by_file.items():
        existing_hashes = get_mcp_code_embedding_hashes(dsn, installation_id, repo_full_name, file_path)
        expected_indexes = set(range(len(file_chunks)))
        if set(existing_hashes) - expected_indexes:
            delete_mcp_code_embeddings_for_file(dsn, installation_id, repo_full_name, file_path)
            existing_hashes = {}

        for index, chunk in enumerate(file_chunks):
            text = chunk["text"]
            chunk_hash = _chunk_hash(text)
            if existing_hashes.get(index) == chunk_hash:
                continue
            embedding = embed_text(text)
            if embedding is None:
                continue
            upsert_mcp_code_embedding(
                dsn,
                installation_id,
                repo_full_name,
                file_path,
                index,
                chunk_hash,
                text,
                embedding,
            )

    present_files = set(chunks_by_file)
    indexed_files = {
        row["file_path"] for row in list_mcp_code_embeddings(dsn, installation_id, repo_full_name)
    }
    for stale_file in indexed_files - present_files:
        delete_mcp_code_embeddings_for_file(dsn, installation_id, repo_full_name, stale_file)
