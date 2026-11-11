import hashlib
import io
import tarfile

import httpx
import pytest

from aletheore.council import cloudflared_helper as h


def tgz(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def release(asset: str, payload: bytes, digest: str | None = None):
    digest = digest if digest is not None else "sha256:" + hashlib.sha256(payload).hexdigest()

    def handler(req):
        if req.url.host == "api.github.com":
            return httpx.Response(200, json={"assets": [{"name": asset, "digest": digest, "browser_download_url": "https://example.test/dl"}]})
        return httpx.Response(200, content=payload)

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_asset_names_per_system():
    assert h.asset_for("darwin", "arm64") == "cloudflared-darwin-arm64.tgz" and h.asset_for("darwin", "x86_64") == "cloudflared-darwin-amd64.tgz"
    assert h.asset_for("win32", "AMD64") == "cloudflared-windows-amd64.exe" and h.asset_for("linux", "aarch64") == "cloudflared-linux-arm64"
    assert h.asset_for("freebsd", "x86_64") is None and h.asset_for("win32", "arm64") is None
    assert h.install_command("darwin") == "brew install cloudflared" and "winget" in h.install_command("win32")


def test_find_prefers_the_hosts_own_install_then_councils_copy(tmp_path):
    assert h.find_cloudflared(tmp_path, which=lambda n: None, system="darwin") is None
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "cloudflared").write_text("x")
    assert h.find_cloudflared(tmp_path, which=lambda n: None, system="darwin") == tmp_path / "bin" / "cloudflared"
    from pathlib import Path

    assert h.find_cloudflared(tmp_path, which=lambda n: "/usr/local/bin/cloudflared", system="darwin") == Path("/usr/local/bin/cloudflared")


def test_download_verifies_the_checksum_unpacks_and_makes_it_runnable(tmp_path):
    exe = b"#!/bin/sh\necho hi\n"
    path = h.download(tmp_path, release("cloudflared-darwin-arm64.tgz", tgz({"cloudflared": exe})), "darwin", "arm64")
    assert path == tmp_path / "bin" / "cloudflared" and path.read_bytes() == exe
    import os

    if os.name != "nt":
        assert os.access(path, os.X_OK)
    plain = h.download(tmp_path / "w", release("cloudflared-windows-amd64.exe", b"MZexe"), "win32", "AMD64")
    assert plain.name == "cloudflared.exe" and plain.read_bytes() == b"MZexe"


def test_a_wrong_checksum_or_a_missing_one_or_a_bad_archive_is_refused_and_leaves_nothing(tmp_path):
    good = tgz({"cloudflared": b"x"})
    with pytest.raises(ValueError, match="did not match its published checksum"):
        h.download(tmp_path, release("cloudflared-darwin-arm64.tgz", good, "sha256:" + "0" * 64), "darwin", "arm64")
    with pytest.raises(ValueError, match="did not publish a checksum"):
        h.download(tmp_path, release("cloudflared-darwin-arm64.tgz", good, ""), "darwin", "arm64")
    with pytest.raises(ValueError, match="did not contain the program"):
        h.download(tmp_path, release("cloudflared-darwin-arm64.tgz", tgz({"../evil": b"x", "readme": b"y"})), "darwin", "arm64")
    with pytest.raises(ValueError, match="no official cloudflared download"):
        h.download(tmp_path, None, "freebsd", "x86_64")
    assert not (tmp_path / "bin").exists() or not list((tmp_path / "bin").iterdir())


def test_a_network_failure_says_so_in_plain_words(tmp_path):
    def boom(req):
        raise httpx.ConnectError("no route")

    with pytest.raises(ValueError, match="could not download cloudflared"):
        h.download(tmp_path, httpx.Client(transport=httpx.MockTransport(boom)), "darwin", "arm64")


def test_ensure_finds_asks_downloads_or_prints_the_install_command(tmp_path):
    said = []
    assert h.ensure_cloudflared(tmp_path, lambda q: True, said.append, "darwin", lambda n: "/opt/cloudflared") is not None and said == []  # present: no talk
    client = release("cloudflared-darwin-arm64.tgz", tgz({"cloudflared": b"x"}))
    import aletheore.council.cloudflared_helper as mod

    asked = []
    orig = mod.asset_for
    mod.asset_for = lambda system=None, machine=None: orig("darwin", "arm64")
    try:
        got = h.ensure_cloudflared(tmp_path, lambda q: asked.append(q) or True, said.append, "darwin", lambda n: None, client)
    finally:
        mod.asset_for = orig
    assert got is not None and "checksum" in asked[0] and any("not installed" in m for m in said) and any("Installed" in m for m in said)
    said.clear()
    declined = h.ensure_cloudflared(tmp_path / "other", lambda q: False, said.append, "darwin", lambda n: None)
    assert declined is None and "brew install cloudflared" in " ".join(said) and "this machine only" in " ".join(said)
    said.clear()
    assert h.ensure_cloudflared(tmp_path / "o2", lambda q: None, said.append, "win32", lambda n: None) is None  # nobody at the keyboard
    assert "winget install Cloudflare.cloudflared" in " ".join(said)
