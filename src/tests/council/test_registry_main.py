import os
import stat
import threading

import pytest

from aletheore.council import registry_main as rm
from aletheore.council.registry_server import create_registry_app
from aletheore.council.tunnel_registry import FakeProvider, GRACE_S, Registry


def test_the_signing_key_is_made_once_kept_private_and_never_replaced(tmp_path):
    sk, vk = rm.load_signing_key(tmp_path)
    again = rm.load_signing_key(tmp_path)
    assert again == (sk, vk)  # a restart keeps the same key, so client pins keep matching
    if os.name != "nt":
        assert stat.S_IMODE((tmp_path / "registry-signing.key").stat().st_mode) == 0o600
    (tmp_path / "registry-signing.key").write_text("abcd")
    with pytest.raises(ValueError, match="damaged"):
        rm.load_signing_key(tmp_path)  # a damaged key is an error, never silently replaced by a new one


def test_secrets_come_from_files_and_a_missing_one_is_explained(tmp_path):
    f = tmp_path / "tok"
    f.write_text("  secret-value\n")
    assert rm.read_secret("X_FILE", {"X_FILE": str(f)}) == "secret-value"
    with pytest.raises(ValueError, match="Docker secret"):
        rm.read_secret("X_FILE", {})
    assert rm.read_secret("X_FILE", {}, required=False) == ""


def test_main_refuses_to_start_without_its_settings(capsys):
    assert rm.main({}) == 2
    assert "cannot start" in capsys.readouterr().err


def test_the_sweeper_frees_expired_leases_with_nobody_asking_and_survives_errors(tmp_path):
    class Clock:
        t = 1_000_000.0

        def __call__(self):
            return self.t

    clock = Clock()
    from aletheore.council import crypto
    from aletheore.council.identity import Keystore

    sk, vk = crypto.generate_signing_keypair()
    reg = Registry(tmp_path / "r.db", FakeProvider(), "example.test", (sk, vk), slots=1, clock=clock)
    reg.claim(Keystore(tmp_path / "k").load_or_create("a").public())
    clock.t += GRACE_S + 5
    stop, errors = threading.Event(), []
    calls = {"n": 0}
    real = reg.sweep

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("disk hiccup")
        stop.set()
        return real()

    reg.sweep = flaky
    rm.sweep_loop(reg, stop, every=0.01, on_error=errors.append)
    assert len(errors) == 1 and calls["n"] >= 2


def test_the_lookup_limit_uses_the_tunnels_client_address_only_from_a_trusted_proxy(tmp_path):
    from fastapi.testclient import TestClient

    from aletheore.council import crypto

    sk, vk = crypto.generate_signing_keypair()
    reg = Registry(tmp_path / "r.db", FakeProvider(), "example.test", (sk, vk), slots=1)
    # the test client's own peer address is "testclient": not a trusted proxy, so its forwarded header is ignored
    ignored = TestClient(create_registry_app(reg, trusted_proxies=["10.0.0.0/8"]))
    codes = [ignored.get("/v1/lookup/dev_x", headers={"cf-connecting-ip": f"1.2.3.{i}"}).status_code for i in range(80)]
    assert 429 in codes  # one real source: the per-address limit still applies, the spoofed header gave it no fresh quota


def test_the_pool_grows_one_slot_at_a_time_survives_a_failure_and_stops_at_the_target(tmp_path):
    from aletheore.council import crypto

    class Flaky(FakeProvider):
        def create_tunnel(self, slot):
            if slot == "slot-003" and not getattr(self, "failed", False):
                self.failed = True
                raise RuntimeError("Cloudflare hiccup")
            return super().create_tunnel(slot)

    sk, vk = crypto.generate_signing_keypair()
    reg = Registry(tmp_path / "r.db", Flaky(), "example.test", (sk, vk), slots=0)
    said = []
    rm.grow_pool(reg, 5, threading.Event(), pause=0, retry_s=0.01, say=said.append)
    assert reg.counter()["total"] == 5 and said[-1] == "pool complete: 5 slots"
    assert any("could not make slot 3" in m for m in said) and "slots: 5/5" in said  # one failure, then it carried on
    again = []
    rm.grow_pool(reg, 5, threading.Event(), say=again.append)
    assert again == ["pool complete: 5 slots"] and reg.counter()["total"] == 5  # nothing made twice


def test_a_stop_ends_pool_growth_promptly(tmp_path):
    from aletheore.council import crypto

    sk, vk = crypto.generate_signing_keypair()
    reg = Registry(tmp_path / "r.db", FakeProvider(), "example.test", (sk, vk), slots=0)
    stop = threading.Event()
    stop.set()
    rm.grow_pool(reg, 50, stop, say=lambda m: None)
    assert reg.counter()["total"] == 0
