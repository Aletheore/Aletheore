from unittest.mock import MagicMock, patch

import pytest

from aletheore.search_index import (
    EmbeddingProviderUnavailableError,
    IndexNotFoundError,
    MAX_CHUNKS_PER_FILE,
    MAX_EMBEDDING_CHARS,
    MODULE_CHUNK_MAX_LINES,
    _embed_in_batches,
    _escape_sql_literal,
    _reusable_vectors,
    _rrf_fuse,
    build_chunks,
    build_index,
    embed_texts,
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
        # Same content: nothing to re-embed at all.
        build_index(tmp_path, evidence(1))
        assert embedded == [] or all(not batch for batch in embedded)

        embedded.clear()
        build_index(tmp_path, evidence(99))

    changed = [text for batch in embedded for text in batch]
    # One symbol chunk per file; neither gets a module chunk, since both
    # start at line 1 so there is no pre-symbol head to summarise.
    assert first_call_count == 2
    # Only a.py's chunks come back; b.py is untouched and reuses its vectors.
    assert changed and all("a.py" in text for text in changed)


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
