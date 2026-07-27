"""Hosted persistence for the durable, incrementally-updated code graph:
files -> symbols -> dependency edges -> endpoints, keyed by
installation_id/repo_full_name/branch (same convention as
postgres_graph_store.py's git-history graph). The counterpart to
repo_history's evidence JSONB blob snapshot, but addressable at
file/symbol/edge/endpoint granularity and updated incrementally - only
the rows for files that actually changed (see aletheore.code_graph_diff)
are touched on each apply, instead of repo_history's whole-blob rewrite
on every single scan.
"""

from __future__ import annotations

from datetime import datetime


class CodeGraphStore:
    def __init__(self, dsn: str, installation_id: int, repo_full_name: str):
        self._dsn = dsn
        self._installation_id = installation_id
        self._repo_full_name = repo_full_name

    def load_content_hashes(self, branch: str) -> dict[str, str]:
        import psycopg

        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT path, content_hash FROM code_graph_files "
                    "WHERE installation_id = %s AND repo_full_name = %s AND branch = %s",
                    (self._installation_id, self._repo_full_name, branch),
                )
                return dict(cur.fetchall())

    def load_symbols_for_path(self, branch: str, path: str) -> list[dict]:
        import psycopg

        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT name, kind, start_line, end_line FROM code_graph_symbols "
                    "WHERE installation_id = %s AND repo_full_name = %s AND branch = %s AND path = %s",
                    (self._installation_id, self._repo_full_name, branch, path),
                )
                return [
                    {"name": name, "kind": kind, "start_line": start_line, "end_line": end_line}
                    for name, kind, start_line, end_line in cur.fetchall()
                ]

    def load_dependents(self, branch: str, path: str) -> list[str]:
        """Files that import `path` - the 'dependent graph regions' for a
        change to this file."""
        import psycopg

        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT from_path FROM code_graph_dependency_edges "
                    "WHERE installation_id = %s AND repo_full_name = %s AND branch = %s AND to_path = %s "
                    "ORDER BY from_path",
                    (self._installation_id, self._repo_full_name, branch, path),
                )
                return [row[0] for row in cur.fetchall()]

    def load_last_synced_sha(self, branch: str) -> str | None:
        import psycopg

        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT last_synced_sha FROM code_graph_sync_state "
                    "WHERE installation_id = %s AND repo_full_name = %s AND branch = %s",
                    (self._installation_id, self._repo_full_name, branch),
                )
                row = cur.fetchone()
                return row[0] if row else None

    def load_all_modules(self, branch: str) -> dict[str, dict]:
        """Reconstructs full build_module_graph-shaped module dicts (path,
        language, imports, symbols) for every currently-persisted file -
        the read side that powers the incremental scan cache
        (aletheore.evidence._load_unchanged_scan_cache): a file the
        caller has determined is unchanged since this data was computed
        can be fed straight back into build_module_graph without
        re-parsing it. imported_by is always left empty here -
        build_module_graph recomputes it fresh from every module's
        imports (cached or not) regardless of what's passed in.
        """
        import psycopg

        ids = (self._installation_id, self._repo_full_name, branch)
        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT path, language FROM code_graph_files "
                    "WHERE installation_id = %s AND repo_full_name = %s AND branch = %s",
                    ids,
                )
                modules = {
                    path: {
                        "path": path,
                        "language": language,
                        "imports": [],
                        "imported_by": [],
                        "symbols": {"functions": [], "classes": []},
                    }
                    for path, language in cur.fetchall()
                }

                cur.execute(
                    "SELECT path, name, kind, start_line, end_line FROM code_graph_symbols "
                    "WHERE installation_id = %s AND repo_full_name = %s AND branch = %s",
                    ids,
                )
                for path, name, kind, start_line, end_line in cur.fetchall():
                    if path not in modules:
                        continue
                    group = "functions" if kind == "function" else "classes"
                    modules[path]["symbols"][group].append(
                        {"name": name, "start_line": start_line, "end_line": end_line}
                    )

                cur.execute(
                    "SELECT from_path, to_path FROM code_graph_dependency_edges "
                    "WHERE installation_id = %s AND repo_full_name = %s AND branch = %s",
                    ids,
                )
                for from_path, to_path in cur.fetchall():
                    if from_path not in modules:
                        continue
                    modules[from_path]["imports"].append(to_path)

        return modules

    def load_all_endpoints(self, branch: str) -> dict[str, list[dict]]:
        """path -> list of endpoint dicts found in that file - the read
        side powering the incremental scan cache's endpoint reuse,
        mirroring load_all_modules above."""
        import psycopg

        grouped: dict[str, list[dict]] = {}
        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT method, endpoint_path, file_path, line FROM code_graph_endpoints "
                    "WHERE installation_id = %s AND repo_full_name = %s AND branch = %s",
                    (self._installation_id, self._repo_full_name, branch),
                )
                for method, endpoint_path, file_path, line in cur.fetchall():
                    grouped.setdefault(file_path, []).append(
                        {"method": method, "path": endpoint_path, "file": file_path, "line": line}
                    )
        return grouped

    def load_endpoint_keys(self, branch: str) -> dict[tuple, dict]:
        import psycopg

        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT method, endpoint_path, file_path, line FROM code_graph_endpoints "
                    "WHERE installation_id = %s AND repo_full_name = %s AND branch = %s",
                    (self._installation_id, self._repo_full_name, branch),
                )
                return {
                    (method, endpoint_path): {"file": file_path, "line": line}
                    for method, endpoint_path, file_path, line in cur.fetchall()
                }

    def apply_module_deltas(
        self,
        branch: str,
        changed_modules: list[dict],
        deleted_paths: list[str],
        new_sync_sha: str,
        new_sync_at: datetime,
    ) -> None:
        import psycopg

        ids = (self._installation_id, self._repo_full_name, branch)
        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO code_graph_sync_state "
                    "(installation_id, repo_full_name, branch, last_synced_sha, last_synced_at) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    "ON CONFLICT (installation_id, repo_full_name, branch) "
                    "DO UPDATE SET last_synced_sha = EXCLUDED.last_synced_sha, "
                    "last_synced_at = EXCLUDED.last_synced_at",
                    (*ids, new_sync_sha, new_sync_at),
                )

                # A deleted file's own rows go, and so does anything that
                # was pointing AT it - an edge from an unchanged file to a
                # now-deleted one is stale, not something to leave behind
                # for the next apply to clean up.
                for path in deleted_paths:
                    cur.execute(
                        "DELETE FROM code_graph_files WHERE installation_id = %s AND repo_full_name = %s "
                        "AND branch = %s AND path = %s",
                        (*ids, path),
                    )
                    cur.execute(
                        "DELETE FROM code_graph_symbols WHERE installation_id = %s AND repo_full_name = %s "
                        "AND branch = %s AND path = %s",
                        (*ids, path),
                    )
                    cur.execute(
                        "DELETE FROM code_graph_dependency_edges WHERE installation_id = %s "
                        "AND repo_full_name = %s AND branch = %s AND (from_path = %s OR to_path = %s)",
                        (*ids, path, path),
                    )

                for module in changed_modules:
                    path = module["path"]
                    cur.execute(
                        "INSERT INTO code_graph_files "
                        "(installation_id, repo_full_name, branch, path, language, content_hash, updated_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                        "ON CONFLICT (installation_id, repo_full_name, branch, path) "
                        "DO UPDATE SET language = EXCLUDED.language, content_hash = EXCLUDED.content_hash, "
                        "updated_at = EXCLUDED.updated_at",
                        (*ids, path, module.get("language"), module["content_hash"], new_sync_at),
                    )

                    # Symbols and this file's own outgoing edges are always
                    # a full replace for the file, not a row-by-row upsert -
                    # a changed file's old symbols/imports may no longer
                    # exist at all, so there's no stable per-symbol key to
                    # upsert against.
                    cur.execute(
                        "DELETE FROM code_graph_symbols WHERE installation_id = %s AND repo_full_name = %s "
                        "AND branch = %s AND path = %s",
                        (*ids, path),
                    )
                    symbols = module.get("symbols", {})
                    for kind, entries in (("function", symbols.get("functions", [])), ("class", symbols.get("classes", []))):
                        for entry in entries:
                            cur.execute(
                                "INSERT INTO code_graph_symbols "
                                "(installation_id, repo_full_name, branch, path, name, kind, start_line, end_line) "
                                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                                (*ids, path, entry["name"], kind, entry["start_line"], entry["end_line"]),
                            )

                    cur.execute(
                        "DELETE FROM code_graph_dependency_edges WHERE installation_id = %s "
                        "AND repo_full_name = %s AND branch = %s AND from_path = %s",
                        (*ids, path),
                    )
                    for target in module.get("imports", []):
                        cur.execute(
                            "INSERT INTO code_graph_dependency_edges "
                            "(installation_id, repo_full_name, branch, from_path, to_path) "
                            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                            (*ids, path, target),
                        )
            conn.commit()

    def apply_endpoint_deltas(
        self, branch: str, changed_endpoints: list[dict], deleted_keys: list[tuple]
    ) -> None:
        import psycopg

        ids = (self._installation_id, self._repo_full_name, branch)
        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                for method, endpoint_path in deleted_keys:
                    cur.execute(
                        "DELETE FROM code_graph_endpoints WHERE installation_id = %s AND repo_full_name = %s "
                        "AND branch = %s AND method = %s AND endpoint_path = %s",
                        (*ids, method, endpoint_path),
                    )
                for endpoint in changed_endpoints:
                    cur.execute(
                        "INSERT INTO code_graph_endpoints "
                        "(installation_id, repo_full_name, branch, method, endpoint_path, file_path, line) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                        "ON CONFLICT (installation_id, repo_full_name, branch, method, endpoint_path) "
                        "DO UPDATE SET file_path = EXCLUDED.file_path, line = EXCLUDED.line",
                        (*ids, endpoint["method"], endpoint["path"], endpoint.get("file"), endpoint.get("line")),
                    )
            conn.commit()
