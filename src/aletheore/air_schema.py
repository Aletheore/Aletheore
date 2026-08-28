"""The AIR evidence contract.

AIR (air.json) is consumed by the CLI, the MCP server, the hosted dashboard,
the GitHub App worker, and anything a customer writes against it. Until this
module, "the schema" existed only as the literal dict built at the end of
scan_repository, and the only guard was EVIDENCE_VERSION - which said whether
two builds *claimed* the same schema, never whether the evidence in hand
actually had the shape a consumer was about to index into. A producer-side
change that renamed or dropped a key shipped silently and surfaced as a
KeyError deep inside an unrelated module.

This module makes the contract explicit and machine-checkable:

  - AIR_JSON_SCHEMA is the contract, written as JSON Schema (draft 2020-12)
    so it can be published for external consumers verbatim.
  - validate_evidence() checks evidence against it, returning readable paths
    rather than raising from wherever the first bad index happened to be.
  - schema_fingerprint() lets a test fail when the contract changes without
    a matching EVIDENCE_VERSION bump. That test is the actual mechanism -
    the schema alone documents drift, the fingerprint prevents it.

The validator implements only the JSON Schema subset used below (type,
properties, required, items). That is a deliberate trade: a real jsonschema
dependency would pull a transitive tree into a CLI whose install size users
notice, to check four keywords. If the schema ever needs more expressive
power than this, take the dependency rather than growing the validator.
"""

import hashlib
import json

_STR: dict = {"type": "string"}
_INT: dict = {"type": "integer"}
_NUM: dict = {"type": "number"}
_BOOL: dict = {"type": "boolean"}
_ANY_LIST: dict = {"type": "array"}
_ANY_OBJECT: dict = {"type": "object"}


def _obj(properties: dict, required: list[str] | None = None) -> dict:
    """An object schema. Everything listed is required unless `required` is
    given explicitly - AIR sections are built unconditionally by
    scan_repository, so "present" is the norm and optionality is the
    exception worth spelling out.
    """
    return {
        "type": "object",
        "properties": properties,
        "required": sorted(properties) if required is None else sorted(required),
    }


def _arr(items: dict | None = None) -> dict:
    return {"type": "array"} if items is None else {"type": "array", "items": items}


# Findings arrays deliberately require only the fields every consumer reads.
# Detectors add their own extra keys and must stay free to; over-specifying
# here would turn a harmless additive change into a CI failure.
_SECRET_FINDING = _obj({"path": _STR, "line": _INT, "pattern": _STR})
_ENDPOINT = _obj({"file": _STR, "line": _INT, "method": _STR, "path": _STR})

# Same "only what consumers read" discipline as the findings arrays above.
# The extractor emits more per column (unique, default); those stay
# unspecified so a later ORM or Prisma source can add its own keys without a
# version bump. `schema` itself is gated - see schema_map.skipped_schema -
# but the section is always present with these keys, so `checked` is the
# only thing a consumer ever branches on.
_SCHEMA_COLUMN = _obj({"name": _STR, "type": _STR, "primary_key": _BOOL, "nullable": _BOOL})
_SCHEMA_TABLE = _obj({"name": _STR, "columns": _arr(_SCHEMA_COLUMN), "file": _STR, "line": _INT})
_SCHEMA_RELATION = _obj(
    {
        "from_table": _STR,
        "from_column": _STR,
        "to_table": _STR,
        "to_column": _STR,
        "file": _STR,
        "line": _INT,
    }
)
_SCHEMA_INDEX = _obj({"name": _STR, "table": _STR, "columns": _ANY_LIST, "unique": _BOOL})
_SCHEMA_MAP = _obj(
    {
        "checked": _BOOL,
        "tables": _arr(_SCHEMA_TABLE),
        "relations": _arr(_SCHEMA_RELATION),
        "indexes": _arr(_SCHEMA_INDEX),
        "unsupported": _ANY_LIST,
        "sources": _ANY_LIST,
    }
)
# import_confidence is optional and, when present, a plain object mapping a
# resolved import target (a value already present in this module's own
# "imports") to "inferred" or "ambiguous" - see scanner/graph.py's
# _extract_module. Omitted entirely for a module with no non-exact edges,
# which is the common case and every module in six of Aletheore's eleven
# supported languages (js/ts, go, rust, ruby, c/cpp), whose resolvers are
# never ambiguous at all.
_MODULE = _obj(
    {
        "path": _STR,
        "language": _STR,
        "imports": _ANY_LIST,
        "imported_by": _ANY_LIST,
        "import_confidence": _ANY_OBJECT,
    },
    required=["path", "language", "imports", "imported_by"],
)
_LANGUAGE = _obj({"name": _STR, "file_count": _INT, "loc": _INT})
_OWNERSHIP = _obj({"email": _STR, "commit_count": _INT, "percent": _NUM})
_BRANCH = _obj({"name": _STR, "type": _STR})
_CLUSTER = _obj({"id": _INT, "modules": _ANY_LIST})

AIR_JSON_SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://aletheore.com/schemas/air.schema.json",
    "title": "Aletheore Intermediate Representation (AIR)",
    "description": "Deterministic repository evidence produced by `aletheore scan`.",
    "type": "object",
    "required": [
        "aletheore_version",
        "architecture",
        "git",
        "repo_path",
        "repository",
        "scanned_at",
        "security",
    ],
    "properties": {
        "aletheore_version": _STR,
        "scanned_at": _STR,
        "repo_path": _STR,
        "repository": _obj(
            {
                "languages": _arr(_LANGUAGE),
                "frameworks": _ANY_LIST,
                "ai_usage": _obj(
                    {
                        "providers": _ANY_LIST,
                        "orchestration": _ANY_LIST,
                        "vector_stores": _ANY_LIST,
                        "local_inference": _ANY_LIST,
                        "mcp": _ANY_LIST,
                    }
                ),
                "policy_docs": _ANY_LIST,
                "build_tools": _ANY_LIST,
                "monorepo": _obj({"detected": _BOOL, "workspaces": _ANY_LIST}),
                "database": _obj(
                    {
                        "orm_frameworks": _ANY_LIST,
                        "migration_directories": _ANY_LIST,
                        "schema_files": _ANY_LIST,
                        "schema": _SCHEMA_MAP,
                    }
                ),
                "infrastructure": _obj(
                    {
                        "docker_compose_services": _ANY_LIST,
                        "kubernetes_manifests": _ANY_LIST,
                        "terraform_files": _ANY_LIST,
                        "helm_charts": _ANY_LIST,
                    }
                ),
                "environment_variables": _obj({"declared": _ANY_LIST}),
                "modules": _arr(_MODULE),
                "dependency_graph": _obj({"nodes": _ANY_LIST, "edges": _ANY_LIST}),
                "unparseable_files": _ANY_LIST,
                "api_endpoints": _obj({"checked": _BOOL, "endpoints": _arr(_ENDPOINT)}),
                "dead_code": _obj(
                    {
                        "unreachable_modules": _ANY_LIST,
                        "unused_dependencies": _ANY_LIST,
                        "entry_points_detected": _ANY_LIST,
                    }
                ),
            }
        ),
        # A repo with no commits yields {"available": False} and nothing
        # else, so `available` is the only unconditionally required key here.
        # Consumers must branch on it before reading anything below.
        "git": _obj(
            {
                "available": _BOOL,
                "branches": _arr(_BRANCH),
                "commit_cadence": _obj(
                    {
                        "weekly_counts": _ANY_LIST,
                        "trend": _STR,
                        "most_recent_week_partial": _BOOL,
                    }
                ),
                "ownership": _arr(_OWNERSHIP),
                "file_ownership": _ANY_OBJECT,
                "repo_age_days": _INT,
                "total_commits": _INT,
                "history_depth_limited": _BOOL,
                "hotspots": _ANY_LIST,
            },
            required=["available"],
        ),
        "security": _obj(
            {
                "secrets": _obj(
                    {
                        "scanned_files": _INT,
                        "findings": _arr(_SECRET_FINDING),
                        "history_scanned_commits": _INT,
                        "history_findings": _ANY_LIST,
                    }
                ),
                "dependency_vulnerabilities": _obj({"checked": _BOOL, "findings": _ANY_LIST}),
                "dependency_licenses": _obj({"checked": _BOOL, "findings": _ANY_LIST}),
            }
        ),
        "architecture": _obj(
            {
                "clusters": _arr(_CLUSTER),
                "cross_cluster_edges": _ANY_LIST,
                "layer_violations": _obj(
                    {
                        "convention_detected": _BOOL,
                        "layers": _ANY_LIST,
                        "violations": _ANY_LIST,
                    }
                ),
                # null whenever the repo ships no architecture config, which
                # is the common case.
                "config_applied": {"type": ["object", "null"]},
            }
        ),
    },
}

_JSON_TYPES: dict[str, type | tuple[type, ...]] = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "null": type(None),
}


def _type_matches(value: object, expected: str) -> bool:
    python_type = _JSON_TYPES[expected]
    # bool is a subclass of int in Python; AIR distinguishes them and a
    # counter that started returning True would be a real defect.
    if expected in ("integer", "number") and isinstance(value, bool):
        return False
    return isinstance(value, python_type)


def _validate(value: object, schema: dict, path: str, errors: list[str], deep: bool) -> None:
    expected = schema.get("type")
    if expected is not None:
        allowed = [expected] if isinstance(expected, str) else list(expected)
        if not any(_type_matches(value, option) for option in allowed):
            errors.append(
                f"{path}: expected {' or '.join(allowed)}, got {type(value).__name__}"
            )
            return

    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}.{key}: required key missing")
        for key, subschema in schema.get("properties", {}).items():
            if key in value:
                _validate(value[key], subschema, f"{path}.{key}", errors, deep)

    elif isinstance(value, list) and deep:
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, item in enumerate(value):
                _validate(item, item_schema, f"{path}[{index}]", errors, deep)


def validate_evidence(evidence: object, *, deep: bool = False) -> list[str]:
    """Problems found in `evidence`, as readable paths. Empty means valid.

    `deep=False` (the default, and what the read path uses) checks the
    section skeleton: every required key present, every container the right
    type. That is what a consumer about to index into evidence actually
    depends on, and it stays O(sections) regardless of repo size.

    `deep=True` additionally validates every element of every typed array.
    On a large repo that is tens of thousands of objects, so it is meant for
    the conformance test rather than the hot path.
    """
    errors: list[str] = []
    _validate(evidence, AIR_JSON_SCHEMA, "evidence", errors, deep)
    return errors


def schema_fingerprint() -> str:
    """Stable hash of the contract.

    Pinned by a test against EVIDENCE_VERSION so the schema cannot change
    without the version changing too - which is the whole point of having a
    version. See tests/test_air_schema.py.
    """
    canonical = json.dumps(AIR_JSON_SCHEMA, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]
