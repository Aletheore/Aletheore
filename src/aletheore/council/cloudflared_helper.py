"""Finding, or fetching, the `cloudflared` program a host needs to open a tunnel.

Never installs silently: if it is missing Council says so, and with the host's yes it downloads the official release into Council's own
folder (no admin rights needed), checks it against the SHA-256 GitHub publishes for that release file, and uses that copy. Otherwise it
prints the one-line install command for the host's system."""

from __future__ import annotations

import hashlib
import io
import os
import platform
import shutil
import stat
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Callable

import httpx

RELEASE_API = "https://api.github.com/repos/cloudflare/cloudflared/releases/latest"


def binary_name(system: str = sys.platform) -> str:
    return "cloudflared.exe" if system.startswith("win") else "cloudflared"


def find_cloudflared(home: Path, which: Callable[[str], str | None] = shutil.which, system: str = sys.platform) -> Path | None:
    """The copy on the PATH first (the host's own install), then the one Council downloaded."""
    found = which("cloudflared")
    if found:
        return Path(found)
    mine = Path(home) / "bin" / binary_name(system)
    return mine if mine.is_file() else None


def asset_for(system: str = sys.platform, machine: str | None = None) -> str | None:
    machine = (machine or platform.machine()).lower()
    arm = machine in ("arm64", "aarch64")
    x86 = machine in ("x86_64", "amd64")
    if system == "darwin" and (arm or x86):
        return f"cloudflared-darwin-{'arm64' if arm else 'amd64'}.tgz"
    if system.startswith("win") and x86:
        return "cloudflared-windows-amd64.exe"
    if system.startswith("linux") and (arm or x86):
        return f"cloudflared-linux-{'arm64' if arm else 'amd64'}"
    return None


def install_command(system: str = sys.platform) -> str:
    if system == "darwin":
        return "brew install cloudflared"
    if system.startswith("win"):
        return "winget install Cloudflare.cloudflared"
    return "see https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/downloads/"


def download(home: Path, http: httpx.Client | None = None, system: str = sys.platform, machine: str | None = None) -> Path:
    """Fetch the latest official release for this system into <home>/bin. Raises ValueError with a plain reason on any problem."""
    asset = asset_for(system, machine)
    if asset is None:
        raise ValueError("there is no official cloudflared download for this system; install it yourself and run council serve again")
    client = http or httpx.Client(timeout=60, follow_redirects=True)
    try:
        release = client.get(RELEASE_API).raise_for_status().json()
        entry = next((a for a in release["assets"] if a["name"] == asset), None)
        if entry is None:
            raise ValueError("the latest cloudflared release has no file for this system")
        wanted = str(entry.get("digest", "")).removeprefix("sha256:").lower()
        if len(wanted) != 64:
            raise ValueError("GitHub did not publish a checksum for the cloudflared file, so it was not downloaded")
        data = client.get(entry["browser_download_url"]).raise_for_status().content
    except (httpx.HTTPError, KeyError, TypeError, StopIteration) as exc:
        raise ValueError(f"could not download cloudflared ({type(exc).__name__}); check your connection") from None
    if hashlib.sha256(data).hexdigest() != wanted:
        raise ValueError("the downloaded cloudflared did not match its published checksum, so it was thrown away")
    exe = _unpack(data, asset, system)
    dest_dir = Path(home) / "bin"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / binary_name(system)
    fd, tmp = tempfile.mkstemp(dir=dest_dir, suffix=".part")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(exe)
        os.chmod(tmp, os.stat(tmp).st_mode | stat.S_IXUSR | stat.S_IRUSR)
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return dest


def _unpack(data: bytes, asset: str, system: str) -> bytes:
    if not asset.endswith(".tgz"):
        return data  # the Windows and Linux files are the program itself
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar.getmembers():
            if member.isfile() and Path(member.name).name == "cloudflared" and "/" not in member.name.strip("./"):
                extracted = tar.extractfile(member)
                if extracted is not None:
                    return extracted.read()  # read only that one entry's bytes: nothing is ever extracted by path
    raise ValueError("the cloudflared download did not contain the program")


def ensure_cloudflared(home: Path, ask: Callable[[str], bool | None], say: Callable[[str], None], system: str = sys.platform,
                       which: Callable[[str], str | None] = shutil.which, http: httpx.Client | None = None) -> Path | None:
    """The path to a usable cloudflared, or None (with the reason said) when there is none and the host chose not to fetch one."""
    found = find_cloudflared(home, which, system)
    if found:
        return found
    say("The tunnel program (cloudflared) is not installed on this machine, and the free tunnel needs it.")
    answer = ask(f"Download the official cloudflared into {Path(home) / 'bin'}? It is checked against its published checksum before it is used.")
    if answer:
        try:
            path = download(home, http, system)
        except ValueError as exc:
            say(f"{exc}. You can install it yourself: {install_command(system)}")
            return None
        say(f"Installed cloudflared at {path}")
        return path
    say(f"Not downloaded. To install it yourself: {install_command(system)}. Until then this room is reachable on this machine only.")
    return None
