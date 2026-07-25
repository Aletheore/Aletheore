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
