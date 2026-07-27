from aletheore.runtime_events import parse_sentry_event


def _event(**overrides):
    base = {
        "exception": {
            "values": [
                {
                    "type": "ZeroDivisionError",
                    "value": "division by zero",
                    "stacktrace": {
                        "frames": [
                            {"filename": "app/wsgi.py", "function": "wsgi_app", "lineno": 5, "in_app": False},
                            {"filename": "app/handler.py", "function": "handle_request", "lineno": 42, "in_app": True},
                        ]
                    },
                }
            ]
        },
        "request": {"url": "https://api.example.com/v1/users", "method": "GET"},
    }
    base.update(overrides)
    return base


def test_parse_sentry_event_extracts_last_in_app_frame():
    result = parse_sentry_event(_event())

    assert result["file"] == "app/handler.py"
    assert result["line"] == 42
    assert result["function"] == "handle_request"


def test_parse_sentry_event_extracts_exception_type_and_value():
    result = parse_sentry_event(_event())

    assert result["exception_type"] == "ZeroDivisionError"
    assert result["exception_value"] == "division by zero"


def test_parse_sentry_event_extracts_method_and_path_from_request():
    result = parse_sentry_event(_event())

    assert result["method"] == "GET"
    assert result["path"] == "/v1/users"


def test_parse_sentry_event_falls_back_to_last_frame_when_none_marked_in_app():
    event = _event(
        exception={
            "values": [
                {
                    "type": "ValueError",
                    "value": "bad input",
                    "stacktrace": {
                        "frames": [
                            {"filename": "a.py", "function": "f", "lineno": 1},
                            {"filename": "b.py", "function": "g", "lineno": 2},
                        ]
                    },
                }
            ]
        }
    )

    result = parse_sentry_event(event)

    assert result["file"] == "b.py"
    assert result["line"] == 2


def test_parse_sentry_event_uses_the_last_exception_when_chained():
    event = _event(
        exception={
            "values": [
                {"type": "OriginalError", "value": "root cause", "stacktrace": {"frames": []}},
                {
                    "type": "WrappedError",
                    "value": "outer wrapper",
                    "stacktrace": {
                        "frames": [{"filename": "outer.py", "function": "wrap", "lineno": 9, "in_app": True}]
                    },
                },
            ]
        }
    )

    result = parse_sentry_event(event)

    assert result["exception_type"] == "WrappedError"
    assert result["file"] == "outer.py"


def test_parse_sentry_event_returns_none_without_a_usable_frame():
    assert parse_sentry_event({"exception": {"values": []}}) is None
    assert parse_sentry_event({}) is None


def test_parse_sentry_event_handles_missing_request():
    event = _event()
    del event["request"]

    result = parse_sentry_event(event)

    assert result["method"] == ""
    assert result["path"] == ""
