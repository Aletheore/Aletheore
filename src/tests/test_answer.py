from unittest.mock import MagicMock, patch

from aletheore.answer import answer_question


@patch("aletheore.answer.search_index")
def test_answer_question_calls_adapter_with_retrieved_context(mock_search_index, tmp_path):
    mock_search_index.return_value = [
        {
            "module_path": "auth.py",
            "symbol_name": "login",
            "start_line": 1,
            "end_line": 3,
            "language": "python",
            "text": "auth.py::login\ndef login():\n    return True",
            "score": 0.1,
        }
    ]
    adapter = MagicMock()
    adapter.simple_completion.return_value = "Login is handled in auth.py::login."

    result = answer_question(tmp_path, "how does login work", adapter)

    assert result["confidence_gated"] is False
    assert result["answer"] == "Login is handled in auth.py::login."
    assert "auth.py::login" in result["cited_chunks"]
    adapter.simple_completion.assert_called_once()
    assert "how does login work" in adapter.simple_completion.call_args.args[1]


@patch("aletheore.answer.search_index")
def test_answer_question_confidence_gate_skips_adapter_call(mock_search_index, tmp_path):
    mock_search_index.return_value = [
        {
            "module_path": "unrelated.py",
            "symbol_name": "noop",
            "start_line": 1,
            "end_line": 1,
            "language": "python",
            "text": "unrelated.py::noop\ndef noop(): pass",
            "score": 0.95,
        }
    ]
    adapter = MagicMock()

    result = answer_question(tmp_path, "how does login work", adapter, confidence_threshold=0.5)

    assert result["confidence_gated"] is True
    assert "not enough evidence" in result["answer"].lower()
    adapter.simple_completion.assert_not_called()


@patch("aletheore.answer.search_index")
def test_answer_question_gates_when_nothing_retrieved(mock_search_index, tmp_path):
    mock_search_index.return_value = []
    adapter = MagicMock()

    result = answer_question(tmp_path, "how does login work", adapter)

    assert result["confidence_gated"] is True
    adapter.simple_completion.assert_not_called()


@patch("aletheore.answer.search_index")
def test_answer_question_forwards_allow_hosted_to_search_index(mock_search_index, tmp_path):
    # Regression test: answer_question used to call search_index with no
    # allow_hosted argument at all, so search_index's (and transitively
    # _embed_in_batches's) default of True applied regardless of what the
    # caller actually consented to - mcp_server.py's aletheore_answer tool
    # forwards the operator's real EFFECT_EXTERNAL decision here, the same
    # way aletheore_search_codebase and aletheore_index already do for
    # their own hosted-embedding calls.
    mock_search_index.return_value = []
    adapter = MagicMock()

    answer_question(tmp_path, "how does login work", adapter, allow_hosted=False)

    assert mock_search_index.call_args.kwargs["allow_hosted"] is False


@patch("aletheore.answer.search_index")
def test_answer_question_allow_hosted_defaults_to_true(mock_search_index, tmp_path):
    # Preserves existing callers' behavior (the CLI's own interactive use)
    # that never passed allow_hosted at all.
    mock_search_index.return_value = []
    adapter = MagicMock()

    answer_question(tmp_path, "how does login work", adapter)

    assert mock_search_index.call_args.kwargs["allow_hosted"] is True
