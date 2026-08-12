from unittest.mock import MagicMock, patch

import pytest

from aletheore.search_index import (
    _file_header_comment,
    _is_declaration_only_file,
    HostedEmbeddingUnavailableError,
    EmbeddingProviderUnavailableError,
    IndexNotFoundError,
    MAX_CHUNKS_PER_FILE,
    MAX_EMBEDDING_CHARS,
    MODULE_CHUNK_MAX_LINES,
    _embed_in_batches,
    _escape_sql_literal,
    _fts_candidates,
    _repo_id,
    _reusable_vectors,
    _rrf_fuse,
    build_chunks,
    build_index,
    embed_texts,
    embed_texts_hosted,
    open_index,
    search_index,
)


def _evidence_with_module(module_path, functions, classes=None):
    return {
        "repository": {
            "modules": [
                {
                    "path": module_path,
                    "language": "python",
                    "symbols": {"functions": functions, "classes": classes or []},
                }
            ]
        }
    }


def test_build_chunks_slices_real_source_per_symbol(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\ndef greet():\n    return 'hi'\n")
    evidence = _evidence_with_module(
        "app.py", [{"name": "greet", "start_line": 2, "end_line": 3}]
    )

    chunks = build_chunks(evidence, tmp_path)

    # Two chunks now: a module overview covering the pre-symbol head, then
    # the symbol itself. Without the overview nothing in the index answers
    # "what is this file for" - see MODULE_CHUNK_MAX_LINES.
    assert len(chunks) == 2
    module_chunk, chunk = chunks
    assert module_chunk["symbol_name"] is None
    assert module_chunk["start_line"] == 1
    assert "x = 1" in module_chunk["text"]
    assert "defines: greet" in module_chunk["text"]
    assert chunk["module_path"] == "app.py"
    assert chunk["symbol_name"] == "greet"
    assert chunk["start_line"] == 2
    assert chunk["end_line"] == 3
    assert "app.py::greet" in chunk["text"]
    assert "def greet():" in chunk["text"]


def test_build_chunks_falls_back_to_whole_file_when_no_symbols(tmp_path):
    (tmp_path / "config.py").write_text("SETTING = 1\n")
    evidence = _evidence_with_module("config.py", [])

    chunks = build_chunks(evidence, tmp_path)

    assert len(chunks) == 1
    assert chunks[0]["symbol_name"] is None
    assert "SETTING = 1" in chunks[0]["text"]


def test_build_chunks_skips_module_when_file_missing_on_disk(tmp_path):
    evidence = _evidence_with_module(
        "gone.py", [{"name": "f", "start_line": 1, "end_line": 1}]
    )
    chunks = build_chunks(evidence, tmp_path)
    assert chunks == []


@patch("aletheore.search_index.OpenAI")
def test_embed_texts_returns_one_vector_per_input(mock_openai_class):
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client
    mock_client.embeddings.create.return_value = MagicMock(
        data=[MagicMock(embedding=[0.1, 0.2]), MagicMock(embedding=[0.3, 0.4])]
    )

    result = embed_texts(["chunk one", "chunk two"])

    assert result == [[0.1, 0.2], [0.3, 0.4]]
    call = mock_client.embeddings.create.call_args
    assert call.kwargs["input"] == ["chunk one", "chunk two"]
    assert call.kwargs["model"] == "nomic-embed-text"


@patch("aletheore.search_index.OpenAI")
def test_embed_texts_raises_actionable_error_when_model_unavailable(mock_openai_class):
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client
    mock_client.embeddings.create.side_effect = RuntimeError("model not found")

    with pytest.raises(EmbeddingProviderUnavailableError, match="ollama pull nomic-embed-text"):
        embed_texts(["chunk one"])


@patch("aletheore.search_index.has_api_key", return_value=False)
@patch("aletheore.search_index.OpenAI")
def test_embed_texts_raises_ollama_error_when_no_openai_key_configured(
    mock_openai_class, mock_has_api_key, tmp_path
):
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client
    mock_client.embeddings.create.side_effect = RuntimeError("connection refused")

    with pytest.raises(EmbeddingProviderUnavailableError, match="ollama pull nomic-embed-text"):
        embed_texts(["chunk one"], credentials_path=tmp_path / "credentials.json")

    mock_has_api_key.assert_called_once_with(
        "OPENAI_API_KEY", "OpenAI", tmp_path / "credentials.json"
    )


@patch("aletheore.search_index.sys")
@patch("aletheore.search_index.get_api_key", return_value="sk-test-key")
@patch("aletheore.search_index.has_api_key", return_value=True)
@patch("aletheore.search_index.OpenAI")
def test_embed_texts_falls_back_to_openai_when_ollama_unavailable(
    mock_openai_class, mock_has_api_key, mock_get_api_key, mock_sys, tmp_path
):
    mock_sys.stdin.isatty.return_value = True

    ollama_client = MagicMock()
    ollama_client.embeddings.create.side_effect = RuntimeError("connection refused")
    openai_client = MagicMock()
    openai_client.embeddings.create.return_value = MagicMock(
        data=[MagicMock(embedding=[0.5, 0.6])]
    )
    mock_openai_class.side_effect = [ollama_client, openai_client]

    confirm_fn = MagicMock(return_value=True)

    result = embed_texts(
        ["chunk one"], credentials_path=tmp_path / "credentials.json", confirm_fn=confirm_fn
    )

    assert result == [[0.5, 0.6]]
    confirm_fn.assert_called_once()
    openai_call = openai_client.embeddings.create.call_args
    assert openai_call.kwargs["model"] == "text-embedding-3-small"
    assert openai_call.kwargs["input"] == ["chunk one"]
    second_client_call = mock_openai_class.call_args_list[1]
    assert second_client_call.kwargs["base_url"] == "https://api.openai.com/v1"
    assert second_client_call.kwargs["api_key"] == "sk-test-key"


@patch("aletheore.search_index.sys")
@patch("aletheore.search_index.get_api_key", return_value="sk-test-key")
@patch("aletheore.search_index.has_api_key", return_value=True)
@patch("aletheore.search_index.OpenAI")
def test_embed_texts_raises_when_openai_fallback_declined(
    mock_openai_class, mock_has_api_key, mock_get_api_key, mock_sys, tmp_path
):
    mock_sys.stdin.isatty.return_value = True
    ollama_client = MagicMock()
    ollama_client.embeddings.create.side_effect = RuntimeError("connection refused")
    mock_openai_class.return_value = ollama_client

    confirm_fn = MagicMock(return_value=False)

    with pytest.raises(EmbeddingProviderUnavailableError, match="declined"):
        embed_texts(
            ["chunk one"], credentials_path=tmp_path / "credentials.json", confirm_fn=confirm_fn
        )

    confirm_fn.assert_called_once()
    assert mock_openai_class.call_count == 1


@patch("aletheore.search_index.sys")
@patch("aletheore.search_index.get_api_key", return_value="sk-test-key")
@patch("aletheore.search_index.has_api_key", return_value=True)
@patch("aletheore.search_index.OpenAI")
def test_embed_texts_refuses_fallback_when_not_interactive(
    mock_openai_class, mock_has_api_key, mock_get_api_key, mock_sys, tmp_path
):
    mock_sys.stdin.isatty.return_value = False
    ollama_client = MagicMock()
    ollama_client.embeddings.create.side_effect = RuntimeError("connection refused")
    mock_openai_class.return_value = ollama_client

    with pytest.raises(EmbeddingProviderUnavailableError, match="interactive"):
        embed_texts(["chunk one"], credentials_path=tmp_path / "credentials.json")

    assert mock_openai_class.call_count == 1
    mock_get_api_key.assert_not_called()


@patch("aletheore.search_index.sys")
@patch("aletheore.search_index.get_api_key", return_value="sk-test-key")
@patch("aletheore.search_index.has_api_key", return_value=True)
@patch("aletheore.search_index.OpenAI")
def test_embed_texts_names_both_failures_when_openai_also_fails(
    mock_openai_class, mock_has_api_key, mock_get_api_key, mock_sys, tmp_path
):
    mock_sys.stdin.isatty.return_value = True
    ollama_client = MagicMock()
    ollama_client.embeddings.create.side_effect = RuntimeError("connection refused")
    openai_client = MagicMock()
    openai_client.embeddings.create.side_effect = RuntimeError("invalid api key")
    mock_openai_class.side_effect = [ollama_client, openai_client]

    with pytest.raises(EmbeddingProviderUnavailableError, match="Ollama unavailable.*OpenAI"):
        embed_texts(
            ["chunk one"],
            credentials_path=tmp_path / "credentials.json",
            confirm_fn=lambda: True,
        )


@patch("aletheore.search_index.embed_texts")
def test_build_index_creates_lancedb_table(mock_embed_texts, tmp_path):
    (tmp_path / "app.py").write_text("def greet():\n    return 'hi'\n")
    evidence = _evidence_with_module(
        "app.py", [{"name": "greet", "start_line": 1, "end_line": 2}]
    )
    mock_embed_texts.return_value = [[0.1, 0.2]]

    count = build_index(tmp_path, evidence)

    assert count == 1
    assert (tmp_path / ".aletheore" / "index.lancedb").exists()


def test_open_index_raises_when_missing(tmp_path):
    with pytest.raises(IndexNotFoundError):
        open_index(tmp_path)


@patch("aletheore.search_index.embed_texts")
def test_search_index_returns_ranked_results(mock_embed_texts, tmp_path):
    (tmp_path / "auth.py").write_text("def login():\n    return True\n")
    (tmp_path / "math.py").write_text("def add(a, b):\n    return a + b\n")
    evidence = {
        "repository": {
            "modules": [
                {
                    "path": "auth.py",
                    "language": "python",
                    "symbols": {
                        "functions": [{"name": "login", "start_line": 1, "end_line": 2}],
                        "classes": [],
                    },
                },
                {
                    "path": "math.py",
                    "language": "python",
                    "symbols": {
                        "functions": [{"name": "add", "start_line": 1, "end_line": 2}],
                        "classes": [],
                    },
                },
            ]
        }
    }
    mock_embed_texts.side_effect = [[[0.9, 0.1], [0.1, 0.9]], [[0.85, 0.15]]]

    build_index(tmp_path, evidence)
    results = search_index(tmp_path, "how does authentication work", k=1)

    assert len(results) == 1
    assert results[0]["module_path"] == "auth.py"
    assert results[0]["symbol_name"] == "login"
    assert "score" in results[0]


def test_build_chunks_excludes_test_files(tmp_path):
    """Tests were 61% of this repo's index and took 64% of top-5 result
    slots, because a test shares its subject's identifiers while
    outnumbering it. Excluding them moved top-5 retrieval from 45% to 68%."""
    for path in ("tests/test_app.py", "app_test.py", "conftest.py", "spec/thing.py", "src/app.py"):
        full = tmp_path / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text("def f():\n    return 1\n")

    evidence = {"repository": {"modules": [
        {"path": p, "language": "python",
         "symbols": {"functions": [{"name": "f", "start_line": 1, "end_line": 2}], "classes": []}}
        for p in ("tests/test_app.py", "app_test.py", "conftest.py", "spec/thing.py", "src/app.py")
    ]}}

    indexed = {c["module_path"] for c in build_chunks(evidence, tmp_path)}
    assert indexed == {"src/app.py"}


def test_module_chunk_head_is_bounded(tmp_path):
    """A file with a huge constant table before its first function must not
    produce one enormous chunk that matches every query."""
    body = "\n".join(f"CONST_{i} = {i}" for i in range(500))
    (tmp_path / "big.py").write_text(f"{body}\ndef f():\n    return 1\n")
    evidence = _evidence_with_module("big.py", [{"name": "f", "start_line": 501, "end_line": 502}])

    module_chunk = build_chunks(evidence, tmp_path)[0]

    assert module_chunk["symbol_name"] is None
    assert module_chunk["end_line"] == MODULE_CHUNK_MAX_LINES


def test_search_caps_chunks_per_file_and_backfills(tmp_path):
    """A large class-per-file has dozens of plausibly-related symbol chunks
    and can take every slot, leaving the answering file invisible. Measured
    on Flask: app.py + sansio/app.py were 16% of chunks and took three of
    four top-5 misses, and one query returned sansio/blueprints.py twice.

    The displaced slots must be backfilled from lower-ranked candidates,
    which is why the search over-fetches before thinning.
    """
    hoggish = [
        {"module_path": "big.py", "symbol_name": f"f{i}", "start_line": i, "end_line": i,
         "language": "python", "text": f"f{i}", "_distance": 0.1 + i / 1000}
        for i in range(10)
    ]
    others = [
        {"module_path": f"other{i}.py", "symbol_name": "g", "start_line": 1, "end_line": 1,
         "language": "python", "text": "g", "_distance": 0.5 + i / 1000}
        for i in range(5)
    ]
    table = MagicMock()
    table.search.return_value.limit.return_value.to_list.return_value = hoggish + others

    with patch("aletheore.search_index.open_index", return_value=table), \
         patch("aletheore.search_index.embed_texts", return_value=[[0.0]]):
        results = search_index(tmp_path, "anything", k=5)

    paths = [r["module_path"] for r in results]
    assert paths.count("big.py") == MAX_CHUNKS_PER_FILE
    # Slots freed by the cap are backfilled rather than left short.
    assert len(results) == 5
    assert paths == ["big.py", "big.py", "other0.py", "other1.py", "other2.py"]


def test_embed_in_batches_splits_large_inputs():
    """One request for the whole repo is what this did before, and it fails:
    Ollama returned `Post "/tokenize": EOF` on a 1,535-chunk repo while 634
    and 510 succeeded, so indexing died outright on ordinary repositories."""
    calls = []

    def fake_embed(texts):
        calls.append(len(texts))
        return [[0.0]] * len(texts)

    with patch("aletheore.search_index.embed_texts", side_effect=fake_embed):
        vectors = _embed_in_batches([f"t{i}" for i in range(450)], batch_size=200)

    assert calls == [200, 200, 50]
    assert len(vectors) == 450


def test_rrf_fuse_ranks_a_chunk_found_by_both_retrievers_first():
    """Fusion is on rank, not score: vector search returns an L2 distance and
    full-text a BM25 relevance, on different scales with opposite polarity."""
    def chunk(path, name):
        return {"module_path": path, "symbol_name": name, "start_line": 1}

    both = chunk("shared.py", "f")
    fused = _rrf_fuse(
        [chunk("vec_only.py", "a"), both],
        [chunk("fts_only.py", "b"), both],
    )

    # Agreed-on chunk outranks either retriever's own first pick.
    assert (fused[0]["module_path"], fused[0]["symbol_name"]) == ("shared.py", "f")
    assert {c["module_path"] for c in fused} == {"shared.py", "vec_only.py", "fts_only.py"}


def test_rrf_fuse_demotes_a_declaration_only_hit_below_an_implementation():
    """A demotion, not an exclusion: slimphp/Slim's interfaces displaced the
    correct implementation on 4 of 6 misses by simply outranking it. Ranked
    #1 by both retrievers, the interface chunk should lose to an
    implementation chunk ranked #2 by both once the penalty applies."""
    def chunk(path, name, is_declaration_only=False):
        return {
            "module_path": path, "symbol_name": name, "start_line": 1,
            "is_declaration_only": is_declaration_only,
        }

    interface_hit = chunk("Interfaces/RouteInterface.php", "match", is_declaration_only=True)
    impl_hit = chunk("Routing/RouteResolver.php", "resolve")

    fused = _rrf_fuse(
        [interface_hit, impl_hit],
        [interface_hit, impl_hit],
    )

    assert fused[0]["module_path"] == "Routing/RouteResolver.php"


def test_rrf_fuse_still_surfaces_a_declaration_only_hit_when_nothing_else_matches():
    """Unlike a test path, which build_chunks excludes outright, an
    interface is legitimately the answer to "where is the contract for X
    defined?" - it must still come back when it's the only match."""
    def chunk(path, name, is_declaration_only=False):
        return {
            "module_path": path, "symbol_name": name, "start_line": 1,
            "is_declaration_only": is_declaration_only,
        }

    only_hit = chunk("Interfaces/RouteInterface.php", "match", is_declaration_only=True)

    fused = _rrf_fuse([only_hit], [only_hit])

    assert len(fused) == 1
    assert fused[0]["module_path"] == "Interfaces/RouteInterface.php"


def test_is_declaration_only_file_detects_a_php_interface():
    assert _is_declaration_only_file(
        "src/RouteInterface.php", "php", "<?php\ninterface RouteInterface\n{\n    public function match();\n}\n"
    )


def test_is_declaration_only_file_does_not_flag_a_php_class():
    assert not _is_declaration_only_file(
        "src/Route.php", "php", "<?php\nclass Route\n{\n    public function match() { return true; }\n}\n"
    )


def test_is_declaration_only_file_detects_path_under_an_interfaces_directory():
    # Path-level signal alone, regardless of language or content - a PHP,
    # Java, or C# codebase commonly puts every interface under one of these
    # directories.
    assert _is_declaration_only_file("src/Interfaces/RouteInterface.php", "php", "")
    assert _is_declaration_only_file("src/Contracts/Repository.php", "php", "")


def test_is_declaration_only_file_detects_a_typescript_declaration_file():
    assert _is_declaration_only_file("types.d.ts", "typescript", "")


def test_is_declaration_only_file_detects_a_typescript_file_with_only_types():
    """colinhacks/zod's enumUtil.ts: entirely `type X = ...` declarations
    inside a namespace, no function or class at all."""
    assert _is_declaration_only_file(
        "enumUtil.ts", "typescript",
        "export namespace EnumUtil {\n  export type Values<T> = T[keyof T];\n}\n",
    )


def test_is_declaration_only_file_does_not_flag_a_typescript_file_with_an_implementation():
    assert not _is_declaration_only_file(
        "route.ts", "typescript",
        "export interface Route {\n  match(): boolean;\n}\n\n"
        "export function createRoute(): Route {\n  return { match: () => true };\n}\n",
    )


def test_is_declaration_only_file_detects_a_rust_trait_with_no_default_bodies():
    assert _is_declaration_only_file(
        "resolver.rs", "rust", "pub trait Resolver {\n    fn resolve(&self) -> bool;\n}\n"
    )


def test_is_declaration_only_file_does_not_flag_a_rust_trait_with_a_default_body():
    assert not _is_declaration_only_file(
        "resolver.rs", "rust",
        "pub trait Resolver {\n    fn resolve(&self) -> bool { true }\n}\n",
    )


def test_is_declaration_only_file_detects_a_cpp_header_with_only_prototypes():
    assert _is_declaration_only_file(
        "resolver.h", "cpp", "class Resolver {\npublic:\n    bool resolve();\n};\n"
    )


def test_is_declaration_only_file_does_not_flag_a_cpp_header_with_a_defined_method():
    assert not _is_declaration_only_file(
        "resolver.h", "cpp",
        "class Resolver {\npublic:\n    bool resolve() { return true; }\n};\n",
    )


def test_is_declaration_only_file_does_not_flag_a_php_abstract_class():
    """Deliberately out of scope: unlike `interface`, PHP's `abstract class`
    routinely mixes abstract and fully-implemented methods."""
    assert not _is_declaration_only_file(
        "Base.php", "php",
        "<?php\nabstract class Base\n{\n    abstract public function match();\n"
        "    public function helper() { return true; }\n}\n",
    )


def test_build_chunks_tags_a_php_interface_files_chunks_as_declaration_only(tmp_path):
    (tmp_path / "RouteInterface.php").write_text(
        "<?php\ninterface RouteInterface\n{\n    public function match();\n}\n"
    )
    evidence = _evidence_with_module(
        "RouteInterface.php",
        functions=[{"name": "match", "start_line": 3, "end_line": 3}],
    )
    evidence["repository"]["modules"][0]["language"] = "php"

    chunks = build_chunks(evidence, tmp_path)

    assert chunks
    assert all(c["is_declaration_only"] for c in chunks)


def test_build_chunks_drops_a_banner_repeated_across_many_files_even_if_no_regex_catches_it(
    tmp_path,
):
    """The durable backstop: a context string shared by more than
    _BOILERPLATE_MIN_REPEAT_COUNT files is boilerplate by definition,
    regardless of whether any regex above recognizes its shape. A repo
    where every file opens with an identical banner comment must produce
    empty file context, not the banner."""
    banner = "/**\n * Totally Unrecognized Proprietary Banner Text\n */\n"
    modules = []
    for i in range(4):
        name = f"file{i}.js"
        (tmp_path / name).write_text(f"{banner}function fn{i}() {{ return {i}; }}\n")
        modules.append({
            "path": name,
            "language": "javascript",
            "symbols": {
                "functions": [{"name": f"fn{i}", "start_line": 4, "end_line": 4}],
                "classes": [],
            },
        })
    evidence = {"repository": {"modules": modules}}

    chunks = build_chunks(evidence, tmp_path)

    symbol_chunks = [c for c in chunks if c["symbol_name"] is not None]
    assert symbol_chunks
    assert all("[file]" not in c["text"] for c in symbol_chunks)
    assert all("Unrecognized Proprietary Banner" not in c["text"] for c in symbol_chunks)


def test_build_chunks_keeps_a_context_shared_by_only_a_couple_of_files(tmp_path):
    """The frequency guard must not fire on ordinary, small-scale
    coincidence - only real repo-wide boilerplate."""
    (tmp_path / "a.js").write_text(
        "/** Handles the request pipeline. */\nfunction fnA() { return 1; }\n"
    )
    (tmp_path / "b.js").write_text(
        "/** Handles the request pipeline. */\nfunction fnB() { return 2; }\n"
    )
    evidence = {
        "repository": {
            "modules": [
                {
                    "path": "a.js", "language": "javascript",
                    "symbols": {"functions": [{"name": "fnA", "start_line": 2, "end_line": 2}], "classes": []},
                },
                {
                    "path": "b.js", "language": "javascript",
                    "symbols": {"functions": [{"name": "fnB", "start_line": 2, "end_line": 2}], "classes": []},
                },
            ]
        }
    }

    chunks = build_chunks(evidence, tmp_path)

    symbol_chunks = [c for c in chunks if c["symbol_name"] is not None]
    assert symbol_chunks
    assert all("Handles the request pipeline" in c["text"] for c in symbol_chunks)


def test_fts_failure_degrades_to_vector_only(tmp_path):
    """An index built before full-text existed has no text_idx, and a query
    full of punctuation can be rejected by the tokenizer. Neither is worth
    losing search over."""
    table = MagicMock()
    table.search.return_value.limit.return_value.to_list.return_value = [
        {"module_path": "a.py", "symbol_name": "f", "start_line": 1, "end_line": 2,
         "language": "python", "imports": [], "text": "x", "_distance": 0.1}
    ]

    def search(arg, query_type=None):
        if query_type == "fts":
            raise RuntimeError("no fts index")
        return table.search.return_value

    table.search.side_effect = search

    with patch("aletheore.search_index.open_index", return_value=table), \
         patch("aletheore.search_index.embed_texts", return_value=[[0.0]]):
        results = search_index(tmp_path, "anything", k=5)

    assert [r["module_path"] for r in results] == ["a.py"]


def test_language_filter_is_escaped_before_reaching_the_where_clause():
    """The value arrives from an MCP tool argument, so it is caller-supplied
    even when the caller is an agent rather than a person."""
    assert _escape_sql_literal("python") == "python"
    assert _escape_sql_literal("python' OR '1'='1") == "python'' OR ''1''=''1"


def test_fts_candidates_applies_language_as_a_pre_filter():
    """Regression: the fts side used to have no language parameter at all,
    so a minority language's hits filled most of the over-fetched limit and
    were only discarded after fusion - exactly the situation a pre-filter
    exists to avoid. Must match the vector side's own where clause."""
    table = MagicMock()
    fts_query = table.search.return_value.limit.return_value
    fts_query.where.return_value.to_list.return_value = [
        {"module_path": "a.py", "symbol_name": "f", "start_line": 1, "end_line": 2,
         "language": "python", "imports": [], "text": "x"}
    ]

    results = _fts_candidates(table, "anything", 20, language="python")

    fts_query.where.assert_called_once_with("language = 'python'")
    assert results == fts_query.where.return_value.to_list.return_value


def test_fts_candidates_skips_the_where_clause_without_a_language():
    table = MagicMock()
    table.search.return_value.limit.return_value.to_list.return_value = []

    _fts_candidates(table, "anything", 20)

    table.search.return_value.limit.return_value.where.assert_not_called()


def test_search_index_filters_both_retrievers_by_language(tmp_path):
    """End-to-end version of the two tests above: search_index must pass the
    same where clause to both the vector query and the fts query, not only
    the vector one."""
    table = MagicMock()
    chain = table.search.return_value.limit.return_value
    chain.where.return_value.to_list.return_value = [
        {"module_path": "a.py", "symbol_name": "f", "start_line": 1, "end_line": 2,
         "language": "python", "imports": [], "text": "x", "_distance": 0.1}
    ]

    with patch("aletheore.search_index.open_index", return_value=table), \
         patch("aletheore.search_index.embed_texts", return_value=[[0.0]]):
        search_index(tmp_path, "anything", k=5, language="python")

    assert chain.where.call_count == 2
    assert [c.args[0] for c in chain.where.call_args_list] == [
        "language = 'python'",
        "language = 'python'",
    ]


def test_chunks_carry_language_and_imports_from_evidence(tmp_path):
    """AIR already computed both, so attaching them is free - and it turns
    the index into something a polyglot repo can pre-filter rather than only
    rank."""
    (tmp_path / "app.py").write_text("import os\ndef f():\n    return 1\n")
    evidence = {"repository": {"modules": [{
        "path": "app.py", "language": "python", "imports": ["os.py"],
        "symbols": {"functions": [{"name": "f", "start_line": 2, "end_line": 3}], "classes": []},
    }]}}

    for chunk in build_chunks(evidence, tmp_path):
        assert chunk["language"] == "python"
        assert chunk["imports"] == ["os.py"]


def test_vendored_and_minified_files_are_not_indexed(tmp_path):
    """One minified bundle was 98% of this repo's entire embedding cost:
    tree-sitter found 271 "functions" in website/vendor/motion.js, every one
    spanning lines 1-1, so each chunk held the whole 44,883-token file and it
    was embedded 271 times. The line-based module cap is no defense against a
    file that is a single line."""
    paths = ["website/vendor/motion.js", "dist/app.bundle.js", "src/lib.min.js",
             "third_party/x.js", "src/real.js"]
    for path in paths:
        full = tmp_path / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text("function f(){return 1}\n")

    evidence = {"repository": {"modules": [
        {"path": p, "language": "javascript", "imports": [],
         "symbols": {"functions": [{"name": "f", "start_line": 1, "end_line": 1}], "classes": []}}
        for p in paths
    ]}}

    assert {c["module_path"] for c in build_chunks(evidence, tmp_path)} == {"src/real.js"}


def test_chunk_text_is_truncated_to_the_embedding_limit(tmp_path):
    """nomic-embed-text has a hard 2048-token context. The hosted side already
    learned this: 6600 chars succeeded, 6990 failed, and its cache sat at a 0%
    hit rate for 38 hours because every call was silently failing."""
    huge = "x = 1  # " + "y" * 40_000
    (tmp_path / "big.py").write_text(f"def f():\n    {huge}\n")
    evidence = {"repository": {"modules": [{
        "path": "big.py", "language": "python", "imports": [],
        "symbols": {"functions": [{"name": "f", "start_line": 1, "end_line": 2}], "classes": []},
    }]}}

    chunk = build_chunks(evidence, tmp_path)[-1]

    assert len(chunk["text"]) <= MAX_EMBEDDING_CHARS + len("\n... (truncated for embedding)")
    # Marked, so a reader can tell a clipped chunk from a short one.
    assert chunk["text"].endswith("... (truncated for embedding)")


def test_build_index_only_embeds_chunks_whose_text_changed(tmp_path):
    """Embedding is the entire cost of indexing - 16-80 ms per chunk against
    microseconds for everything else - so the only optimization that matters
    is not re-embedding unchanged text. Measured on this repo: editing one
    function re-embedded 1 chunk of 631, 49.8s cold to 0.76s incremental."""
    (tmp_path / "a.py").write_text("def f():\n    return 1\n")
    (tmp_path / "b.py").write_text("def g():\n    return 2\n")

    def evidence(f_body):
        (tmp_path / "a.py").write_text(f"def f():\n    return {f_body}\n")
        return {"repository": {"modules": [
            {"path": "a.py", "language": "python", "imports": [],
             "symbols": {"functions": [{"name": "f", "start_line": 1, "end_line": 2}], "classes": []}},
            {"path": "b.py", "language": "python", "imports": [],
             "symbols": {"functions": [{"name": "g", "start_line": 1, "end_line": 2}], "classes": []}},
        ]}}

    embedded: list[list[str]] = []

    def fake_embed(texts):
        embedded.append(list(texts))
        return [[float(len(t))] * 3 for t in texts]

    with patch("aletheore.search_index.embed_texts", side_effect=fake_embed):
        build_index(tmp_path, evidence(1))
        first_call_count = len(embedded[0])
        embedded.clear()
        # Same content: nothing needs re-embedding for its own sake, but one
        # probe item still runs to confirm the current provider's dimension
        # still matches what's stored - see
        # test_switching_embedding_provider_rebuilds_even_when_nothing_else_changed.
        # The point of this assertion is that it stays bounded rather than
        # scaling with corpus size, not that it is exactly zero.
        build_index(tmp_path, evidence(1))
        assert sum(len(batch) for batch in embedded) <= 1

        embedded.clear()
        build_index(tmp_path, evidence(99))

    changed = [text for batch in embedded for text in batch]
    # One symbol chunk per file; neither gets a module chunk, since both
    # start at line 1 so there is no pre-symbol head to summarise.
    assert first_call_count == 2
    # Only a.py's chunks come back; b.py is untouched and reuses its vectors.
    assert changed and all("a.py" in text for text in changed)


def test_build_index_embeds_duplicate_chunk_hash_once(tmp_path):
    shared_text = "generated boilerplate\nreturn same"
    duplicate_chunks = [
        {
            "module_path": "a.py",
            "language": "python",
            "symbol_name": "first",
            "kind": "function",
            "start_line": 1,
            "end_line": 2,
            "text": shared_text,
        },
        {
            "module_path": "b.py",
            "language": "python",
            "symbol_name": "second",
            "kind": "function",
            "start_line": 1,
            "end_line": 2,
            "text": shared_text,
        },
    ]
    embedded: list[str] = []

    def fake_embed(texts):
        embedded.extend(texts)
        return [[0.1, 0.2, 0.3] for _ in texts]

    with patch("aletheore.search_index.build_chunks", return_value=duplicate_chunks), \
         patch("aletheore.search_index.embed_texts", side_effect=fake_embed):
        build_index(tmp_path, {"repository": {"modules": []}})

    assert embedded == [shared_text]
    rows = open_index(tmp_path).to_arrow().to_pylist()
    assert len(rows) == 2
    assert {row["symbol_name"] for row in rows} == {"first", "second"}
    assert rows[0]["chunk_hash"] == rows[1]["chunk_hash"]


def test_build_index_drops_chunks_that_no_longer_exist(tmp_path):
    """The table is rewritten wholesale rather than upserted, so a deleted
    function stops being searchable. An upsert would leave it findable
    forever."""
    (tmp_path / "a.py").write_text("def f():\n    return 1\n\ndef gone():\n    return 2\n")
    two = {"repository": {"modules": [{
        "path": "a.py", "language": "python", "imports": [], "symbols": {"classes": [], "functions": [
            {"name": "f", "start_line": 1, "end_line": 2},
            {"name": "gone", "start_line": 4, "end_line": 5}]}}]}}
    one = {"repository": {"modules": [{
        "path": "a.py", "language": "python", "imports": [], "symbols": {"classes": [], "functions": [
            {"name": "f", "start_line": 1, "end_line": 2}]}}]}}

    with patch("aletheore.search_index.embed_texts", side_effect=lambda t: [[0.0] * 3] * len(t)):
        build_index(tmp_path, two)
        build_index(tmp_path, one)

    names = {r["symbol_name"] for r in open_index(tmp_path).to_arrow().to_pylist()}
    assert "gone" not in names


def test_reusable_vectors_survives_an_unreadable_previous_index(tmp_path):
    """An index missing, corrupt, or written before chunk_hash existed must
    degrade to embedding everything - the old behavior - not raise."""
    assert _reusable_vectors(tmp_path / "nope.lancedb") == {}


def test_switching_embedding_provider_rebuilds_instead_of_crashing(tmp_path):
    """nomic-embed-text returns 768 dimensions and text-embedding-3-small
    returns 1536; LanceDB rejects the mix with "Vector column 'vector' has
    variable length vectors". Reproduced before this guard: index with
    Ollama, lose Ollama, and the next build crashed on the fallback.

    Re-embedding everything is also the only correct answer - vectors from
    two models are not comparable, so reusing the old ones would return
    nonsense rankings even if the write succeeded."""
    def evidence(body):
        (tmp_path / "a.py").write_text(f"def f():\n    return {body}\n")
        (tmp_path / "b.py").write_text("def g():\n    return 2\n")
        return {"repository": {"modules": [
            {"path": p, "language": "python", "imports": [],
             "symbols": {"functions": [{"name": p[0], "start_line": 1, "end_line": 2}], "classes": []}}
            for p in ("a.py", "b.py")]}}

    with patch("aletheore.search_index.embed_texts", side_effect=lambda t: [[0.1] * 768] * len(t)):
        build_index(tmp_path, evidence(1))

    with patch("aletheore.search_index.embed_texts", side_effect=lambda t: [[0.2] * 1536] * len(t)):
        build_index(tmp_path, evidence(99))

    rows = open_index(tmp_path).to_arrow().to_pylist()
    # b.py was unchanged, but its 768-dim vector cannot survive the switch.
    assert {len(row["vector"]) for row in rows} == {1536}
    assert len(rows) == 2


def test_switching_embedding_provider_rebuilds_even_when_nothing_else_changed(tmp_path):
    """Regression: the dimension guard used to only run when at least one
    chunk was stale, comparing against that chunk's freshly-embedded vector.
    If every chunk's hash already matched the previous index - the "rebuild,
    no changes" case the incremental-indexing commit measured at 0.2s -
    nothing got embedded to reveal the new dimension. That is precisely
    "index with Ollama, lose Ollama" with no code change in between: the
    table silently kept the old 768-dim vectors, and the next search() call
    crashed on the mismatch between the table and a freshly-embedded
    1536-dim query vector instead of degrading. A one-item probe embed now
    runs whenever a previous index exists and nothing was otherwise stale."""

    def evidence():
        (tmp_path / "a.py").write_text("def f():\n    return 1\n")
        return {"repository": {"modules": [{
            "path": "a.py", "language": "python", "imports": [],
            "symbols": {"functions": [{"name": "f", "start_line": 1, "end_line": 2}], "classes": []},
        }]}}

    with patch("aletheore.search_index.embed_texts", side_effect=lambda t: [[0.1] * 768] * len(t)):
        build_index(tmp_path, evidence())

    calls = []

    def fake_embed(texts):
        calls.append(len(texts))
        return [[0.2] * 1536] * len(texts)

    # Same content as the first build - every chunk hash already matches the
    # existing index - but the provider switched underneath it.
    with patch("aletheore.search_index.embed_texts", side_effect=fake_embed):
        build_index(tmp_path, evidence())

    assert calls, "a probe embed must run to detect the provider change"
    rows = open_index(tmp_path).to_arrow().to_pylist()
    assert {len(row["vector"]) for row in rows} == {1536}


def _hosted_response(status: int, payload: dict | None = None):
    response = MagicMock()
    response.status_code = status
    response.json.return_value = payload or {}
    response.reason_phrase = "Error"
    response.text = str(payload)
    return response


def test_repo_id_is_stable_for_the_same_path(tmp_path):
    """Same repo, same value every time - it's the key the hosted rate
    limit buckets requests by, not a random tag."""
    assert _repo_id(tmp_path) == _repo_id(tmp_path)


def test_repo_id_differs_between_repos(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    assert _repo_id(tmp_path) != _repo_id(other)


def test_repo_id_never_contains_the_raw_path(tmp_path):
    """A bucket key, not a filesystem disclosure - the server should learn
    nothing about the caller's directory layout from it."""
    assert str(tmp_path) not in _repo_id(tmp_path)
    assert tmp_path.name not in _repo_id(tmp_path)


def test_embed_texts_hosted_includes_repo_id_when_given():
    http = MagicMock()
    http.post.return_value = _hosted_response(200, {"vectors": [[0.1] * 1536]})

    embed_texts_hosted(["chunk"], "tok", http_client=http, repo_id="abc123")

    assert http.post.call_args.kwargs["json"]["repo_id"] == "abc123"


def test_embed_texts_hosted_omits_repo_id_when_not_given():
    """No repo_id, no key in the body - an older server that doesn't know
    the field yet should see exactly the request it always saw."""
    http = MagicMock()
    http.post.return_value = _hosted_response(200, {"vectors": [[0.1] * 1536]})

    embed_texts_hosted(["chunk"], "tok", http_client=http)

    assert "repo_id" not in http.post.call_args.kwargs["json"]


def test_embed_in_batches_forwards_repo_id_to_every_hosted_batch():
    http = MagicMock()
    http.post.return_value = _hosted_response(200, {"vectors": [[0.1] * 1536]})

    with patch("aletheore.search_index.get_api_key", return_value="tok"), \
         patch("aletheore.search_index.httpx.Client", return_value=http):
        _embed_in_batches(["a", "b"], batch_size=1, repo_id="repo-xyz")

    assert http.post.call_count == 2
    assert all(
        call.kwargs["json"]["repo_id"] == "repo-xyz" for call in http.post.call_args_list
    )


def test_build_index_sends_the_repos_own_repo_id_to_hosted_embeddings(tmp_path):
    """End-to-end wiring check: build_index computes repo_id from the path
    it was given and it has to actually reach the wire, not just exist as a
    parameter nothing calls."""
    (tmp_path / "app.py").write_text("def greet():\n    return 'hi'\n")
    evidence = _evidence_with_module(
        "app.py", [{"name": "greet", "start_line": 1, "end_line": 2}]
    )
    http = MagicMock()
    http.post.return_value = _hosted_response(200, {"vectors": [[0.1] * 1536]})

    with patch("aletheore.search_index.get_api_key", return_value="tok"), \
         patch("aletheore.search_index.httpx.Client", return_value=http):
        build_index(tmp_path, evidence)

    assert http.post.call_args.kwargs["json"]["repo_id"] == _repo_id(tmp_path)


def test_hosted_embeddings_are_preferred_when_a_token_exists():
    """Someone paying for hosted embeddings should not silently have their
    code sent to their own OpenAI account instead."""
    http = MagicMock()
    http.post.return_value = _hosted_response(200, {"vectors": [[0.1] * 1536]})

    with patch("aletheore.search_index.get_api_key", return_value="tok"), \
         patch("aletheore.search_index.httpx.Client", return_value=http), \
         patch("aletheore.search_index.embed_texts") as local:
        vectors = _embed_in_batches(["chunk"])

    assert len(vectors[0]) == 1536
    local.assert_not_called()
    assert http.post.call_args.kwargs["headers"]["Authorization"] == "Bearer tok"


def test_a_402_falls_back_to_local_and_says_why(capsys):
    """The gate is the server's 402. The CLI reports it rather than
    pre-judging entitlement locally."""
    http = MagicMock()
    http.post.return_value = _hosted_response(402, {"detail": "requires a paid plan"})

    with patch("aletheore.search_index.get_api_key", return_value="tok"), \
         patch("aletheore.search_index.httpx.Client", return_value=http), \
         patch("aletheore.search_index.embed_texts", side_effect=lambda t: [[0.0] * 768] * len(t)):
        vectors = _embed_in_batches(["chunk"])

    assert len(vectors[0]) == 768
    assert "requires a paid plan" in capsys.readouterr().out


def test_hosted_failure_partway_through_raises_rather_than_mixing_providers():
    """Switching providers mid-run would put 1536-dimension vectors beside
    768-dimension ones in one index, which LanceDB rejects outright. A
    half-built index that errors is recoverable; one built from two models
    is not."""
    http = MagicMock()
    http.post.side_effect = [
        _hosted_response(200, {"vectors": [[0.1] * 1536]}),
        _hosted_response(502, {"detail": "provider down"}),
    ]

    with patch("aletheore.search_index.get_api_key", return_value="tok"), \
         patch("aletheore.search_index.httpx.Client", return_value=http), \
         patch("aletheore.search_index.embed_texts") as local, \
         pytest.raises(HostedEmbeddingUnavailableError):
        _embed_in_batches(["a", "b"], batch_size=1)

    local.assert_not_called()


def test_no_token_means_no_hosted_call_at_all():
    http = MagicMock()
    with patch("aletheore.search_index.get_api_key", return_value=None), \
         patch("aletheore.search_index.httpx.Client", return_value=http), \
         patch("aletheore.search_index.embed_texts", side_effect=lambda t: [[0.0]] * len(t)):
        _embed_in_batches(["chunk"])

    http.post.assert_not_called()


def test_file_header_comment_stops_at_the_first_definition():
    """Skipping past code to find a comment does not find the file header - it
    finds the first class or function docstring, and then staples that one
    symbol's description onto every other symbol in the file. Measured at
    Flask top-1 71.9% -> 65.6% before this stopped at definitions."""
    lines = [
        "import os",
        "",
        "class Session:",
        '    """Expands a basic dictionary with session attributes."""',
    ]
    assert _file_header_comment(lines) == ""


def test_file_header_comment_survives_a_multiline_import_block():
    """serde's `/// An efficient way of discarding data...` sits after a braced
    `use crate::de::{...}` block; treating the block's continuation lines as
    the end of the header lost exactly the sentence worth carrying."""
    lines = [
        "use crate::lib::*;",
        "",
        "use crate::de::{",
        "    Deserialize, Deserializer, Visitor,",
        "};",
        "",
        "/// An efficient way of discarding data from a deserializer.",
        "pub struct IgnoredAny;",
    ]
    assert "efficient way of discarding data" in _file_header_comment(lines)


def test_file_header_comment_extracts_a_multiline_python_module_docstring():
    # The standard PEP 257 module-docstring shape: body lines carry no
    # per-line comment marker of their own, so a purely marker-driven scan
    # silently dropped the whole thing and returned "" for the most common
    # Python file-header idiom there is.
    lines = [
        '"""',
        "Handles authentication for the app.",
        '"""',
        "",
        "import os",
    ]
    assert _file_header_comment(lines) == "Handles authentication for the app."


def test_file_header_comment_strips_both_quote_marks_from_a_single_line_docstring():
    # lstrip() only strips the LEFT side, so the closing """ used to leak
    # into the indexed text.
    lines = ['"""Session attributes."""', "", "import os"]
    context = _file_header_comment(lines)
    assert context == "Session attributes."
    assert '"""' not in context


def test_file_header_comment_drops_licence_boilerplate():
    """Every file in a repo carries the same licence header, so it adds no
    signal and dilutes the symbol it rides on."""
    lines = [
        "// Copyright 2013 Julien Schmidt. All rights reserved.",
        "// Use of this source code is governed by a BSD-style licence.",
        "",
        "// Param is a single URL parameter.",
        "type Param struct {",
    ]
    context = _file_header_comment(lines)
    assert "Copyright" not in context
    assert "Param is a single URL parameter" in context


def test_file_header_comment_drops_a_project_banner_line():
    """"Slim Framework (https://slimframework.com)" carries no licence
    keyword at all, so it survived the old filter untouched - the single
    most common leaked string measured on slimphp/Slim (121 of 455
    chunks)."""
    lines = [
        "/**",
        " * Slim Framework (https://slimframework.com)",
        " *",
        " * @license https://github.com/slimphp/Slim/blob/4.x/LICENSE.md (MIT License)",
        " */",
        "",
        "class App {",
    ]
    context = _file_header_comment(lines)
    assert "Slim Framework" not in context
    assert "@license" not in context


def test_file_header_comment_drops_a_bare_doc_tag_and_its_trailing_comment_terminator():
    """The real banner closes with "@api */" on one line - @api carries no
    legal keyword, and the closing "*/" trails whatever's left after the
    tag itself is filtered, or would leak in on its own if it weren't."""
    lines = [
        "/**",
        " * Slim Framework (https://slimframework.com)",
        " *",
        " * @api */",
        "",
        "class App {",
    ]
    context = _file_header_comment(lines)
    assert "@api" not in context
    assert "*/" not in context


def test_file_header_comment_strips_a_trailing_comment_terminator_from_real_content():
    """A legitimate doc line closing the block on the same line
    ("real content */") must still lose the terminator, independent of
    whether the line is otherwise noise."""
    lines = ["/**", " * Handles authentication for the app. */", "", "class Auth {"]
    context = _file_header_comment(lines)
    assert context == "Handles authentication for the app."
