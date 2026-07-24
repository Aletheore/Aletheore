import threading
import time

import httpx

from app_server.hosted_generation import GENERATION_MODEL, generate_answer


def test_generate_answer_calls_ollama_chat_endpoint(monkeypatch):
    captured = {}

    def fake_post(self, path, json, timeout):
        captured["path"] = path
        captured["json"] = json

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"message": {"content": "It does X because Y."}}

        return FakeResponse()

    monkeypatch.setattr("httpx.Client.post", fake_post)

    result = generate_answer("why does foo exist", ["def foo(): pass"])

    assert result == "It does X because Y."
    assert "chat" in captured["path"]
    assert captured["json"]["model"] == GENERATION_MODEL


def test_generate_answer_returns_none_on_timeout(monkeypatch):
    def fake_post(self, path, json, timeout):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr("httpx.Client.post", fake_post)

    assert generate_answer("q", ["chunk"]) is None


def test_generation_semaphore_bounds_concurrency(monkeypatch):
    def fake_post(self, path, json, timeout):
        time.sleep(0.2)

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"message": {"content": "ok"}}

        return FakeResponse()

    monkeypatch.setattr("httpx.Client.post", fake_post)
    monkeypatch.setattr("app_server.hosted_generation.GENERATION_SEMAPHORE", threading.Semaphore(1))

    results = []

    def call():
        results.append(generate_answer("q", ["c"], acquire_timeout_seconds=0.05))

    t1 = threading.Thread(target=call)
    t2 = threading.Thread(target=call)
    t1.start()
    time.sleep(0.05)
    t2.start()
    t1.join()
    t2.join()

    assert results.count(None) == 1
    assert results.count("ok") == 1
