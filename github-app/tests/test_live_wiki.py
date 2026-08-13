import json
import logging
from unittest.mock import MagicMock

from scan_worker.live_wiki import (
    AIRVIEW_PROMPT_VERSION,
    FLASH_MODEL,
    SUBSYSTEM_DESCRIPTION_UNAVAILABLE,
    UPDATE_MODEL,
    build_subsystem_record,
    generate_overview,
    generate_subsystems,
    propose_cluster_names,
    _related_files,
    attach_file_pages,
    build_file_page_record,
    generate_file_pages,
    select_file_page_paths,
    _drop_test_only_briefs,
    _strip_unverified_lines,
)


def test_incremental_update_model_stays_on_flash():
    # Regression guard: incremental updates fire on every push, for every
    # paid tier, per this module's own docstring ("so it stays cheap even
    # for higher tiers"). UPDATE_MODEL was accidentally set to the Pro
    # model from the very first commit - this went undetected until real
    # billing data surfaced it, since nothing asserted the constant's
    # actual value.
    assert UPDATE_MODEL == FLASH_MODEL


def make_evidence() -> dict:
    return {
        "repository": {
            "modules": [
                {
                    "path": "auth/login.py",
                    "language": "python",
                    "imports": [],
                    "symbols": {
                        "functions": [{"name": "do_login", "start_line": 10, "end_line": 20}],
                        "classes": [],
                    },
                },
                {
                    "path": "auth/tokens.py",
                    "language": "python",
                    "imports": [],
                    "symbols": {"functions": [], "classes": []},
                },
            ],
            "dependency_graph": {"nodes": [], "edges": []},
        },
        "architecture": {
            "clusters": [{"id": 0, "modules": ["auth/login.py", "auth/tokens.py"], "internal_edges": 0}]
        },
    }


def _adapter(response_text: str) -> MagicMock:
    adapter = MagicMock()
    adapter.simple_completion.return_value = response_text
    return adapter


def test_propose_cluster_names_uses_model_response():
    briefs = [{"cluster_id": 0, "files": [{"path": "auth/login.py"}], "fallback_name": "auth"}]
    adapter = _adapter(json.dumps({"0": "Authentication"}))

    names = propose_cluster_names(briefs, adapter)

    assert names == {0: "Authentication"}


def test_propose_cluster_names_falls_back_on_missing_entry():
    briefs = [{"cluster_id": 0, "files": [], "fallback_name": "auth"}]
    adapter = _adapter(json.dumps({}))

    assert propose_cluster_names(briefs, adapter) == {0: "auth"}


def test_propose_cluster_names_falls_back_on_malformed_json():
    briefs = [{"cluster_id": 0, "files": [], "fallback_name": "auth"}]
    adapter = _adapter("not json at all")

    assert propose_cluster_names(briefs, adapter) == {0: "auth"}


def test_propose_cluster_names_returns_empty_for_no_briefs():
    adapter = MagicMock()
    assert propose_cluster_names([], adapter) == {}
    adapter.simple_completion.assert_not_called()


def _brief_for(evidence: dict) -> dict:
    from aletheore.wiki_mapping import build_cluster_briefs

    return build_cluster_briefs(evidence)[0]


def test_build_subsystem_record_happy_path():
    evidence = make_evidence()
    cluster = evidence["architecture"]["clusters"][0]
    brief = _brief_for(evidence)
    adapter = _adapter(
        json.dumps(
            {
                "description": "Handles user login and token issuance.",
                "files": [
                    {
                        "path": "auth/login.py",
                        "role": "Entry point for user login.",
                        "key_symbols": [
                            {"name": "do_login", "line": 10, "explanation": "Authenticates a user."}
                        ],
                    }
                ],
            }
        )
    )

    record = build_subsystem_record(evidence, cluster, brief, "Authentication", adapter)

    assert record["subsystem_id"] == "0"
    assert record["name"] == "Authentication"
    assert record["description"] == "Handles user login and token issuance."
    assert record["files"][0]["path"] == "auth/login.py"
    assert record["files"][0]["key_symbols"][0]["name"] == "do_login"
    assert "flowchart TD" in record["diagram_mermaid"]


def test_build_subsystem_record_logs_which_citation_was_rejected(caplog):
    # A rejection with no record of which citation caused it makes a
    # degraded wiki section unexplainable after the fact.
    evidence = make_evidence()
    cluster = evidence["architecture"]["clusters"][0]
    brief = _brief_for(evidence)
    adapter = _adapter(
        json.dumps(
            {
                "description": "Handles login, see `totally/made/up.py:12` for the token path.",
                "files": [],
            }
        )
    )

    with caplog.at_level(logging.INFO, logger="scan_worker.live_wiki"):
        build_subsystem_record(evidence, cluster, brief, "Authentication", adapter)

    assert "totally/made/up.py:12" in caplog.text
    assert "Authentication" in caplog.text


def test_build_subsystem_record_keeps_deterministic_content_when_prose_fails():
    # The file list and diagram are derived from the scan, not from the
    # model - an unverifiable sentence must not delete them. Previously the
    # whole subsystem vanished from the wiki.
    evidence = make_evidence()
    cluster = evidence["architecture"]["clusters"][0]
    brief = _brief_for(evidence)
    adapter = _adapter(
        json.dumps(
            {
                "description": "See `totally/made/up.py:12`.",
                "files": [{"path": "auth/login.py", "role": "Entry point.", "key_symbols": []}],
            }
        )
    )

    record = build_subsystem_record(evidence, cluster, brief, "Authentication", adapter)

    assert record is not None
    assert record["name"] == "Authentication"
    assert record["description"] == SUBSYSTEM_DESCRIPTION_UNAVAILABLE
    assert "totally/made/up.py" not in record["description"]
    # Every file from the scan survives even though the prose was rejected -
    # the list never depended on the model, and now does not depend on it
    # finishing either.
    assert [f["path"] for f in record["files"]] == ["auth/login.py", "auth/tokens.py"]
    assert "flowchart TD" in record["diagram_mermaid"]


def test_build_subsystem_record_retries_once_and_accepts_a_clean_second_draft():
    evidence = make_evidence()
    cluster = evidence["architecture"]["clusters"][0]
    brief = _brief_for(evidence)
    adapter = MagicMock()
    adapter.simple_completion.side_effect = [
        json.dumps({"description": "Bad cite `nope/fake.py:9`.", "files": []}),
        json.dumps({"description": "Handles user login and token issuance.", "files": []}),
    ]

    record = build_subsystem_record(evidence, cluster, brief, "Authentication", adapter)

    assert adapter.simple_completion.call_count == 2
    assert record["description"] == "Handles user login and token issuance."


def test_build_subsystem_record_drops_hallucinated_file():
    evidence = make_evidence()
    cluster = evidence["architecture"]["clusters"][0]
    brief = _brief_for(evidence)
    adapter = _adapter(
        json.dumps(
            {
                "description": "Handles login.",
                "files": [
                    {"path": "auth/login.py", "role": "Real file.", "key_symbols": []},
                    {"path": "totally/made/up.py", "role": "Fabricated file.", "key_symbols": []},
                ],
            }
        )
    )

    record = build_subsystem_record(evidence, cluster, brief, "Authentication", adapter)

    paths = {f["path"] for f in record["files"]}
    # The fabricated file is dropped; every real file in the brief is present
    # whether or not the model mentioned it. The list is structural, built from
    # the scan - making it depend on the model finishing its output silently
    # shrank Flask's records from 83 files to 14 once the prompt grew.
    assert "totally/made/up.py" not in paths
    assert paths == {"auth/login.py", "auth/tokens.py"}


def test_build_subsystem_record_drops_hallucinated_symbol():
    evidence = make_evidence()
    cluster = evidence["architecture"]["clusters"][0]
    brief = _brief_for(evidence)
    adapter = _adapter(
        json.dumps(
            {
                "description": "Handles login.",
                "files": [
                    {
                        "path": "auth/login.py",
                        "role": "Real file.",
                        "key_symbols": [
                            {"name": "do_login", "line": 10, "explanation": "real"},
                            {"name": "fake_fn", "line": 999, "explanation": "fabricated"},
                        ],
                    }
                ],
            }
        )
    )

    record = build_subsystem_record(evidence, cluster, brief, "Authentication", adapter)

    names = {s["name"] for s in record["files"][0]["key_symbols"]}
    assert names == {"do_login"}


def test_build_subsystem_record_keeps_the_subsystem_for_malformed_json():
    # Even when the model returns nothing usable at all, the scan-derived
    # diagram and file list are still correct and still belong in the wiki.
    evidence = make_evidence()
    cluster = evidence["architecture"]["clusters"][0]
    brief = _brief_for(evidence)
    adapter = _adapter("not valid json")

    record = build_subsystem_record(evidence, cluster, brief, "Authentication", adapter)

    assert record is not None
    assert record["description"] == SUBSYSTEM_DESCRIPTION_UNAVAILABLE
    assert "flowchart TD" in record["diagram_mermaid"]


def test_build_subsystem_record_withholds_a_description_with_a_hallucinated_citation():
    evidence = make_evidence()
    cluster = evidence["architecture"]["clusters"][0]
    brief = _brief_for(evidence)
    adapter = _adapter(
        json.dumps({"description": "See `totally/fake/path.py:42` for details.", "files": []})
    )

    record = build_subsystem_record(evidence, cluster, brief, "Authentication", adapter)

    # The unverifiable claim itself must never reach the customer.
    assert "totally/fake/path.py" not in record["description"]
    assert record["description"] == SUBSYSTEM_DESCRIPTION_UNAVAILABLE


def test_build_subsystem_record_rejects_description_citation_beyond_real_line_count():
    # Closes the same documented gap as citation_verifier.py's
    # verify_citations: without a real line count, a citation naming a
    # real file but a fabricated line is reported as verified. When
    # fetch_line_count is given, a citation beyond the file's real length
    # is rejected the same way an unknown file already is.
    evidence = make_evidence()
    cluster = evidence["architecture"]["clusters"][0]
    brief = _brief_for(evidence)
    adapter = _adapter(
        json.dumps({"description": "See `auth/login.py:99999` for details.", "files": []})
    )

    record = build_subsystem_record(
        evidence, cluster, brief, "Authentication", adapter, fetch_line_count=lambda path: 20
    )

    assert record["description"] == SUBSYSTEM_DESCRIPTION_UNAVAILABLE


def test_build_subsystem_record_keeps_description_citation_within_real_line_count():
    evidence = make_evidence()
    cluster = evidence["architecture"]["clusters"][0]
    brief = _brief_for(evidence)
    adapter = _adapter(
        json.dumps({"description": "See `auth/login.py:10` for details.", "files": []})
    )

    record = build_subsystem_record(
        evidence, cluster, brief, "Authentication", adapter, fetch_line_count=lambda path: 20
    )

    assert record is not None


def test_build_subsystem_record_uses_cache_hit_and_skips_model_call():
    evidence = make_evidence()
    cluster = evidence["architecture"]["clusters"][0]
    brief = _brief_for(evidence)
    cached_output = {
        "description": "Handles authentication via do_login in auth/login.py.",
        "files": [
            {
                "path": "auth/login.py",
                "role": "Login entry point.",
                "key_symbols": [{"name": "do_login", "line": 10, "explanation": "Logs a user in."}],
            }
        ],
    }
    cache_lookup = MagicMock(return_value=(cached_output, "deepseek-v4-pro"))
    cache_write = MagicMock()
    writing_adapter = _adapter("should never be called")

    record = build_subsystem_record(
        evidence,
        cluster,
        brief,
        "Authentication",
        writing_adapter,
        cache_lookup=cache_lookup,
        cache_write=cache_write,
    )

    assert record is not None
    assert record["description"] == cached_output["description"]
    writing_adapter.simple_completion.assert_not_called()
    cache_write.assert_not_called()


def test_build_subsystem_record_falls_through_to_model_when_cache_hit_fails_reverification():
    evidence = make_evidence()
    cluster = evidence["architecture"]["clusters"][0]
    brief = _brief_for(evidence)
    cached_output = {"description": "See `gone_file.py:1` for details.", "files": []}
    cache_lookup = MagicMock(return_value=(cached_output, "deepseek-v4-pro"))
    cache_write = MagicMock()
    fresh_output = {
        "description": "Handles authentication.",
        "files": [{"path": "auth/login.py", "role": "Login.", "key_symbols": []}],
    }
    writing_adapter = _adapter(json.dumps(fresh_output))

    record = build_subsystem_record(
        evidence,
        cluster,
        brief,
        "Authentication",
        writing_adapter,
        cache_lookup=cache_lookup,
        cache_write=cache_write,
        model_used="deepseek-v4-pro",
    )

    assert record is not None
    assert record["description"] == "Handles authentication."
    writing_adapter.simple_completion.assert_called_once()
    cache_write.assert_called_once()


def test_build_subsystem_record_falls_through_to_model_when_cache_lookup_raises():
    evidence = make_evidence()
    cluster = evidence["architecture"]["clusters"][0]
    brief = _brief_for(evidence)
    fresh_output = {
        "description": "Handles authentication.",
        "files": [{"path": "auth/login.py", "role": "Login.", "key_symbols": []}],
    }
    writing_adapter = _adapter(json.dumps(fresh_output))

    def broken_lookup(packet):
        raise RuntimeError("cache unavailable")

    record = build_subsystem_record(
        evidence,
        cluster,
        brief,
        "Authentication",
        writing_adapter,
        cache_lookup=broken_lookup,
        model_used="deepseek-v4-pro",
    )

    assert record is not None
    assert record["description"] == "Handles authentication."
    writing_adapter.simple_completion.assert_called_once()


def test_build_subsystem_record_without_cache_callables_is_unchanged():
    evidence = make_evidence()
    cluster = evidence["architecture"]["clusters"][0]
    brief = _brief_for(evidence)
    fresh_output = {"description": "Handles authentication.", "files": []}
    writing_adapter = _adapter(json.dumps(fresh_output))

    record = build_subsystem_record(evidence, cluster, brief, "Authentication", writing_adapter)

    assert record["description"] == "Handles authentication."
    writing_adapter.simple_completion.assert_called_once()


def test_generate_subsystems_full_build_covers_every_cluster():
    evidence = make_evidence()
    naming_adapter = _adapter(json.dumps({"0": "Authentication"}))
    writing_adapter = _adapter(json.dumps({"description": "Auth stuff.", "files": []}))

    records = generate_subsystems(evidence, naming_adapter, writing_adapter)

    assert len(records) == 1
    assert records[0]["name"] == "Authentication"


def test_generate_subsystems_incremental_filters_to_given_clusters():
    evidence = make_evidence()
    naming_adapter = _adapter(json.dumps({"0": "Authentication"}))
    writing_adapter = _adapter(json.dumps({"description": "Auth stuff.", "files": []}))

    records = generate_subsystems(evidence, naming_adapter, writing_adapter, cluster_ids={99})

    assert records == []
    naming_adapter.simple_completion.assert_not_called()


def test_generate_overview_happy_path():
    evidence = make_evidence()
    subsystem_records = [{"subsystem_id": "0", "name": "Authentication", "description": "Handles login."}]
    adapter = _adapter(json.dumps({"description": "This system handles authentication."}))

    overview = generate_overview(evidence, subsystem_records, adapter)

    assert overview["description"] == "This system handles authentication."
    assert "flowchart TD" in overview["diagram_mermaid"]
    assert "Authentication" in overview["diagram_mermaid"]


def test_generate_overview_falls_back_on_malformed_response():
    evidence = make_evidence()
    subsystem_records = [{"subsystem_id": "0", "name": "Authentication", "description": "Handles login."}]
    adapter = _adapter("not json")

    overview = generate_overview(evidence, subsystem_records, adapter)

    assert overview["description"] == "Overview description unavailable."


def test_generate_overview_falls_back_on_hallucinated_citation():
    evidence = make_evidence()
    subsystem_records = [{"subsystem_id": "0", "name": "Authentication", "description": "Handles login."}]
    adapter = _adapter(json.dumps({"description": "See `fake/path.py:1` for the entry point."}))

    overview = generate_overview(evidence, subsystem_records, adapter)

    assert overview["description"] == "Overview description unavailable."


def test_generate_overview_rejects_citation_beyond_real_line_count():
    evidence = make_evidence()
    subsystem_records = [{"subsystem_id": "0", "name": "Authentication", "description": "Handles login."}]
    adapter = _adapter(json.dumps({"description": "See `auth/login.py:99999` for the entry point."}))

    overview = generate_overview(evidence, subsystem_records, adapter, fetch_line_count=lambda path: 20)

    assert overview["description"] == "Overview description unavailable."


def test_generate_overview_keeps_citation_within_real_line_count():
    evidence = make_evidence()
    subsystem_records = [{"subsystem_id": "0", "name": "Authentication", "description": "Handles login."}]
    adapter = _adapter(json.dumps({"description": "See `auth/login.py:10` for the entry point."}))

    overview = generate_overview(evidence, subsystem_records, adapter, fetch_line_count=lambda path: 20)

    assert overview["description"] == "See `auth/login.py:10` for the entry point."


def test_affected_cluster_ids_maps_changed_files_to_clusters():
    from scan_worker.live_wiki import affected_cluster_ids

    evidence = {
        "architecture": {
            "clusters": [
                {"id": 0, "modules": ["auth/login.py", "auth/tokens.py"]},
                {"id": 1, "modules": ["billing/charge.py"]},
            ]
        }
    }

    assert affected_cluster_ids(evidence, ["auth/login.py"]) == {0}
    assert affected_cluster_ids(evidence, ["billing/charge.py"]) == {1}
    assert affected_cluster_ids(evidence, ["auth/login.py", "billing/charge.py"]) == {0, 1}
    assert affected_cluster_ids(evidence, ["unrelated/file.py"]) == set()
    assert affected_cluster_ids(evidence, []) == set()


def test_select_file_page_paths_puts_important_files_first_and_respects_budget():
    evidence = make_evidence()
    evidence["repository"]["modules"][0]["imported_by"] = ["auth/tokens.py"]
    paths = select_file_page_paths(evidence, max_files=1)
    assert paths == ["auth/login.py"]


def test_build_file_page_record_returns_detail_when_citations_verify():
    evidence = make_evidence()
    detail = "## Overview\nHandles login at auth/login.py:10."
    record = build_file_page_record(evidence, "auth/login.py", _adapter(json.dumps({"detail": detail})))
    assert record == detail


def test_build_file_page_record_rejects_page_citing_a_file_not_in_the_scan():
    evidence = make_evidence()
    adapter = _adapter(json.dumps({"detail": "## Overview\nSee totally/made/up.py:4."}))
    assert build_file_page_record(evidence, "auth/login.py", adapter) is None


def test_build_file_page_record_skips_files_with_no_symbols():
    """auth/tokens.py has no functions or classes, so a page would be padding -
    and the call is skipped entirely rather than spent."""
    adapter = _adapter(json.dumps({"detail": "## Overview\nAnything."}))
    assert build_file_page_record(make_evidence(), "auth/tokens.py", adapter) is None
    adapter.simple_completion.assert_not_called()


def test_build_file_page_record_sends_real_symbols_for_related_files():
    """The prompt tells the model it may cite imported/importing files, so it
    needs real (name, line) targets there - a bare path list gives it nothing
    to cite but a guess, which fails verify_citations and gets stripped by
    salvage. Measured: this is why raising the word cap alone (v6) didn't
    close the AIRview gap - the extra words had nowhere safe to go."""
    evidence = make_evidence()
    evidence["repository"]["modules"][0]["imports"] = ["auth/tokens.py"]
    evidence["repository"]["modules"][1]["symbols"] = {
        "functions": [{"name": "issue_token", "start_line": 5, "end_line": 9}],
        "classes": [],
    }
    adapter = _adapter(json.dumps({"detail": "## Overview\nSee auth/login.py:10."}))

    build_file_page_record(evidence, "auth/login.py", adapter)

    sent = json.loads(adapter.simple_completion.call_args[0][1])
    assert sent["related_symbols"] == {"auth/tokens.py": [{"name": "issue_token", "line": 5}]}


def test_build_file_page_record_omits_related_files_with_no_symbols():
    evidence = make_evidence()
    evidence["repository"]["modules"][0]["imports"] = ["auth/tokens.py"]
    adapter = _adapter(json.dumps({"detail": "## Overview\nSee auth/login.py:10."}))

    build_file_page_record(evidence, "auth/login.py", adapter)

    sent = json.loads(adapter.simple_completion.call_args[0][1])
    assert sent["related_symbols"] == {}


def test_generate_file_pages_keys_pages_by_path():
    detail = "## Overview\nLogin lives at auth/login.py:10."
    pages = generate_file_pages(
        make_evidence(), _adapter(json.dumps({"detail": detail})), paths=["auth/login.py"]
    )
    assert pages == {"auth/login.py": detail}


def test_attach_file_pages_leaves_files_without_a_page_untouched():
    records = [{"files": [{"path": "auth/login.py", "role": "r"}, {"path": "auth/tokens.py", "role": "r"}]}]
    attach_file_pages(records, {"auth/login.py": "## Overview\nx"})
    assert records[0]["files"][0]["detail"] == "## Overview\nx"
    assert "detail" not in records[0]["files"][1]


def test_related_files_offers_neighbours_outside_the_subsystem():
    """The description prose is allowed to cross subsystem boundaries, so the
    model has to be told which files those are - otherwise it can only cite
    within the cluster and cannot explain a cross-cutting flow."""
    evidence = make_evidence()
    evidence["repository"]["modules"].append(
        {"path": "web/app.py", "language": "python", "imports": ["auth/login.py"], "symbols": {}}
    )
    evidence["repository"]["modules"][0]["imported_by"] = ["web/app.py"]
    brief = {"files": [{"path": "auth/login.py"}, {"path": "auth/tokens.py"}]}
    assert _related_files(evidence, brief) == ["web/app.py"]


def test_evidence_packet_carries_prompt_version_so_edits_invalidate_cache():
    from aletheore.evidence_packet import build_evidence_packet

    packet = build_evidence_packet({}, {"modules": []}, {}, "", prompt_version=AIRVIEW_PROMPT_VERSION)
    assert packet["prompt_version"] == AIRVIEW_PROMPT_VERSION


def test_select_file_page_paths_floor_is_not_anchored_to_an_outlier():
    """One re-export hub used to set the floor: Flask's __init__.py scores 2.7x
    the runner-up, which put the cutoff so high that max_files could never bind -
    raising it from 22 to 83 selected the same 22 files. The floor is anchored to
    the median instead, so the budget is the control."""
    modules = [{"path": "hub.py", "imported_by": [f"m{i}.py" for i in range(79)], "symbols": {}}]
    modules += [
        {"path": f"m{i}.py", "imported_by": ["hub.py"], "symbols": {"functions": [{"name": "f"}]}}
        for i in range(12)
    ]
    evidence = {"repository": {"modules": modules}}
    assert len(select_file_page_paths(evidence, max_files=100)) > 1
    assert len(select_file_page_paths(evidence, max_files=3)) == 3


def _brief(cid, *paths):
    return {"cluster_id": cid, "files": [{"path": p, "key_symbols": []} for p in paths],
            "fallback_name": "x"}


def test_generate_subsystems_skips_clusters_that_are_only_tests():
    """Community detection groups by import topology and readily produces
    clusters made entirely of test files - 7 of Flask's 12, 150 of serde's 208.
    Each cost a naming call and a writing call for a page nobody opens."""
    kept = _drop_test_only_briefs([
        _brief(0, "src/app.py"),
        _brief(1, "tests/test_app.py", "tests/conftest.py"),
        _brief(2, "examples/demo/main.py"),
    ])
    assert [b["cluster_id"] for b in kept] == [0]


def test_generate_subsystems_keeps_tests_when_the_repo_is_all_tests():
    """A test-suite repository should still get a wiki rather than an empty one."""
    briefs = [_brief(0, "tests/test_a.py"), _brief(1, "tests/test_b.py")]
    assert _drop_test_only_briefs(briefs) == briefs


def test_generate_subsystems_keeps_a_mixed_cluster():
    briefs = [_brief(0, "src/app.py", "tests/test_app.py")]
    assert _drop_test_only_briefs(briefs) == briefs


def test_file_page_salvages_verified_prose_instead_of_discarding_the_page():
    """Dropping a page over one bad citation threw away correct, verified prose:
    on Flask that lost debughelpers.py - 7 functions, 4 classes - entirely.
    Subsystems already degrade this way; file pages now match."""
    evidence = make_evidence()
    detail = (
        "## Overview\nHandles login.\n"
        "## How it works\nEntry at auth/login.py:10.\n"
        "- `ghost` (auth/nowhere.py:99): does not exist.\n"
        "## Gotchas\nNone.\n"
    )
    page = build_file_page_record(evidence, "auth/login.py", _adapter(json.dumps({"detail": detail})))
    assert page is not None
    assert "auth/nowhere.py:99" not in page
    assert "auth/login.py:10" in page


def test_file_page_salvage_gives_up_when_too_little_survives():
    """A page that was mostly fabricated citations is not worth showing."""
    evidence = make_evidence()
    detail = "## Overview\nSee a/x.py:1.\nAnd b/y.py:2.\nAnd c/z.py:3.\n"
    assert build_file_page_record(
        evidence, "auth/login.py", _adapter(json.dumps({"detail": detail}))
    ) is None


def test_strip_unverified_lines_keeps_everything_when_nothing_failed():
    assert _strip_unverified_lines("## A\nline\n", []) == "## A\nline\n"


def test_strip_unverified_lines_does_not_strip_a_different_valid_line_number():
    # A plain substring test would treat "app.py:1" as present inside
    # "app.py:10" or "app.py:100", wrongly stripping those verified lines
    # too - the bad citation's line number must not match as a prefix of a
    # different, longer one.
    detail = "Bad at app.py:1.\nGood at app.py:10.\nAlso good at app.py:100.\n"
    result = _strip_unverified_lines(detail, [{"file": "app.py", "line": 1}])
    assert "app.py:1." not in result
    assert "app.py:10." in result
    assert "app.py:100." in result


def test_subsystem_files_survive_a_truncated_model_response():
    """The file list is structural. When the prompt grows large enough that the
    model stops finishing its output, the wiki must not silently lose files -
    on Flask that took the records from 83 files to 14 and stranded 23
    already-generated file pages, since a page can only attach to a file entry
    that exists."""
    evidence = make_evidence()
    cluster = evidence["architecture"]["clusters"][0]
    brief = _brief_for(evidence)
    # Model returns only the first file, as if it ran out of output budget.
    adapter = _adapter(json.dumps({
        "description": "Handles login.",
        "files": [{"path": "auth/login.py", "role": "Entry point.", "key_symbols": []}],
    }))

    record = build_subsystem_record(evidence, cluster, brief, "Authentication", adapter)

    paths = [f["path"] for f in record["files"]]
    assert paths == ["auth/login.py", "auth/tokens.py"]
    # The file the model did describe keeps its prose; the other is structural only.
    by_path = {f["path"]: f for f in record["files"]}
    assert by_path["auth/login.py"]["role"] == "Entry point."
    assert by_path["auth/tokens.py"]["role"] == ""
