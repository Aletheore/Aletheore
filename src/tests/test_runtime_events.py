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


def test_parse_sentry_event_pairs_the_exception_message_with_its_own_frame_not_a_causes_frame():
    # Real bug found via audit: the outermost exception in a chain can
    # legitimately have no stacktrace frames of its own (a wrapped/
    # re-raised exception with no captured traceback) while an earlier
    # cause does. The frame walk correctly falls back to that earlier
    # exception's frame, but exception_type/exception_value used to be
    # taken from exception_values[-1] unconditionally - pairing the OUTER
    # exception's message with the EARLIER exception's file:line, a
    # location that has nothing to do with the message being described.
    event = _event(
        exception={
            "values": [
                {
                    "type": "ValueError",
                    "value": "original failure in db layer",
                    "stacktrace": {
                        "frames": [{"filename": "db.py", "function": "query", "lineno": 42, "in_app": True}]
                    },
                },
                {
                    "type": "RuntimeError",
                    "value": "wrapped: something went wrong",
                    "stacktrace": {"frames": []},
                },
            ]
        }
    )

    result = parse_sentry_event(event)

    # The frame is still correctly the earlier exception's - but its
    # message must come from that SAME exception, not the frameless outer
    # one.
    assert result["file"] == "db.py"
    assert result["line"] == 42
    assert result["exception_type"] == "ValueError"
    assert result["exception_value"] == "original failure in db layer"


def test_parse_sentry_event_returns_none_without_a_usable_frame():
    assert parse_sentry_event({"exception": {"values": []}}) is None
    assert parse_sentry_event({}) is None


def test_parse_sentry_event_handles_missing_request():
    event = _event()
    del event["request"]

    result = parse_sentry_event(event)

    assert result["method"] == ""
    assert result["path"] == ""
