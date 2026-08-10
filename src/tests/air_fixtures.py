"""Schema-valid AIR documents for tests.

Fixtures used to hand-build partial evidence dicts like
`{"aletheore_version": "0.2.0", "repository": {"modules": []}}`. Nothing
ever produced such a file - save_snapshot and write_evidence both write
whatever scan_repository returned, which is always the full document - so
those fixtures were asserting behavior against inputs that cannot occur.
Once load_evidence_file started enforcing the schema they broke, correctly.

Building the skeleton from AIR_JSON_SCHEMA rather than restating it keeps
these fixtures from rotting the next time the contract grows a section.
"""

from aletheore.air_schema import AIR_JSON_SCHEMA
from aletheore.evidence import EVIDENCE_VERSION


def minimal_instance(schema: dict):
    """The smallest instance satisfying `schema`."""
    types = schema.get("type")
    kind = types[0] if isinstance(types, list) else types
    if kind == "object":
        properties = schema.get("properties", {})
        return {key: minimal_instance(properties[key]) for key in schema.get("required", [])}
    return {"array": [], "string": "", "integer": 0, "number": 0, "boolean": False}.get(kind)


def minimal_air_evidence() -> dict:
    """A schema-valid AIR document with every collection empty, stamped with
    this build's EVIDENCE_VERSION so load_evidence_file accepts it.
    """
    evidence = minimal_instance(AIR_JSON_SCHEMA)
    evidence["aletheore_version"] = EVIDENCE_VERSION
    return evidence
