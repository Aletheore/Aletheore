"""The Cloudflare provider against a fake transport: it checks which calls are made, in what order, and that the token stays private."""

import json

import httpx
import pytest

from aletheore.council.cloudflare_provider import CloudflareError, CloudflareProvider


def provider(handler):
    client = httpx.Client(base_url="https://api.cloudflare.com/client/v4", transport=httpx.MockTransport(handler))
    return CloudflareProvider("secret-token-value", "acct1", "zone1", "aletheorecouncil.com", 8765, client)


def ok(result=None):
    return httpx.Response(200, json={"success": True, "errors": [], "result": result})


def test_create_tunnel_makes_the_tunnel_then_a_fixed_ingress_then_dns():
    calls = []

    def handler(req):
        calls.append((req.method, req.url.path, json.loads(req.content) if req.content else None))
        if req.method == "GET":
            return ok([])  # nothing exists yet
        return ok({"id": "tid-1"}) if req.method == "POST" and req.url.path.endswith("cfd_tunnel") else ok({})

    assert provider(handler).create_tunnel("slot-001") == "tid-1"
    posts = [(m, p.rsplit("/", 1)[-1], b) for m, p, b in calls if m != "GET"]
    assert [(m, p) for m, p, _ in posts] == [("POST", "cfd_tunnel"), ("PUT", "configurations"), ("POST", "dns_records")]
    ingress = posts[1][2]["config"]["ingress"]
    assert ingress[0] == {"hostname": "slot-001.aletheorecouncil.com", "service": "http://localhost:8765"} and ingress[-1] == {"service": "http_status:404"}
    assert posts[2][2]["content"] == "tid-1.cfargotunnel.com" and posts[2][2]["name"] == "slot-001.aletheorecouncil.com"


def test_repeating_create_tunnel_after_a_half_finished_run_completes_without_duplicating():
    calls = []

    def handler(req):
        calls.append((req.method, req.url.path.rsplit("/", 1)[-1]))
        if req.method == "GET" and req.url.path.endswith("cfd_tunnel"):
            return ok([{"id": "tid-9"}])  # the tunnel exists already
        if req.method == "GET":
            return ok([{"id": "rec"}])  # and so does its DNS name
        return ok({})

    assert provider(handler).create_tunnel("slot-009") == "tid-9"
    assert ("POST", "cfd_tunnel") not in calls and ("POST", "dns_records") not in calls  # nothing created twice
    assert ("PUT", "configurations") in calls  # the fixed ingress is re-applied, which is harmless


def test_rotate_changes_the_secret_drops_connections_then_reads_the_new_token():
    seen = []

    def handler(req):
        seen.append(req.method)
        return ok("new-run-token") if req.method == "GET" else ok({})

    assert provider(handler).rotate("tid-1") == "new-run-token"
    assert seen == ["PATCH", "DELETE", "GET"]


def test_errors_are_plain_and_the_token_never_appears(capfd):
    def handler(req):
        return httpx.Response(403, json={"success": False, "errors": [{"message": "no permission for this zone"}]})

    p = provider(handler)
    with pytest.raises(CloudflareError, match="no permission for this zone"):
        p.rotate("tid-1")
    assert "secret-token-value" not in repr(p) and "secret-token-value" not in str(p)


def test_no_token_is_refused_with_advice_not_to_use_the_command_line(monkeypatch):
    monkeypatch.delenv("COUNCIL_CF_TOKEN", raising=False)
    monkeypatch.delenv("COUNCIL_CF_TOKEN_FILE", raising=False)
    with pytest.raises(CloudflareError, match="never put it on a command line"):
        CloudflareProvider.from_env("a", "z", "d")


def test_delete_slot_removes_dns_then_connections_then_the_tunnel():
    calls = []

    def handler(req):
        calls.append((req.method, req.url.path.rsplit("/", 2)[-2:] if req.method == "DELETE" else req.url.path.rsplit("/", 1)[-1]))
        if req.method == "GET":
            return ok([{"id": "rec-1"}])
        return ok({})

    provider(handler).delete_slot("slot-001", "tid-1")
    assert [m for m, _ in calls] == ["GET", "DELETE", "DELETE", "DELETE"]
    assert calls[1][1][-1] == "rec-1" and calls[2][1][-1] == "connections" and calls[3][1][-1] == "tid-1"
