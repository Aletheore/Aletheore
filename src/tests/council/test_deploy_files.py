"""The deployment files are checked as files: the properties that keep the Council container from hurting its neighbours."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

DEPLOY = Path(__file__).resolve().parents[3] / "council" / "deploy"


@pytest.fixture(scope="module")
def compose():
    return yaml.safe_load((DEPLOY / "compose.yml").read_text())


def text(name):
    return (DEPLOY / name).read_text()


def test_compose_sets_cpu_memory_and_process_limits_for_both_services(compose):
    for name in ("council", "postgres"):
        svc = compose["services"][name]
        assert float(svc["cpus"]) > 0 and svc["mem_limit"] and svc["memswap_limit"] == svc["mem_limit"], name  # no swap: a hard memory ceiling
        assert svc.get("pids_limit", 0) > 0, name


def test_council_yields_cpu_priority_to_other_workloads(compose):
    for name in ("council", "postgres"):
        assert int(compose["services"][name]["cpu_shares"]) < 1024  # below the default weight, so the scanner wins any contention


def test_disk_weight_is_optional_because_some_kernels_refuse_to_start_a_container_that_sets_it(compose):
    # Found by running it: Docker Desktop's kernel failed to start the container with "io.weight: no such file".
    assert "blkio_config" not in compose["services"]["council"] and "blkio_config" not in compose["services"]["postgres"]
    override = yaml.safe_load(text("compose.io-weight.yml"))
    assert int(override["services"]["council"]["blkio_config"]["weight"]) < 500


def test_postgres_is_not_published_and_sits_on_an_internal_network(compose):
    pg = compose["services"]["postgres"]
    assert "ports" not in pg
    assert compose["networks"]["council_db"]["internal"] is True
    assert pg["networks"] == ["council_db"]


def test_only_the_server_port_is_published_and_only_on_loopback(compose):
    ports = compose["services"]["council"]["ports"]
    assert ports and all(str(p).startswith("127.0.0.1:") for p in ports)


def test_server_container_is_locked_down(compose):
    svc = compose["services"]["council"]
    assert svc["read_only"] is True
    assert str(svc["user"]).split(":")[0] not in ("0", "root")
    assert svc["cap_drop"] == ["ALL"] and "no-new-privileges:true" in svc["security_opt"]
    assert "privileged" not in svc and "network_mode" not in svc and "pid" not in svc
    for forbidden in ("/var/run/docker.sock", "/:/", "/etc", "/root"):
        assert not any(forbidden in str(v) for v in svc.get("volumes", []))


def test_services_have_health_checks_and_log_rotation(compose):
    for name in ("council", "postgres"):
        svc = compose["services"][name]
        assert svc["healthcheck"]["test"]
        assert svc["logging"]["options"]["max-size"] and svc["logging"]["options"]["max-file"]
    assert compose["services"]["council"]["depends_on"]["postgres"]["condition"] == "service_healthy"


def test_images_are_pinned_not_latest(compose):
    for name, svc in compose["services"].items():
        image = svc.get("image", "")
        assert ":" in image and not image.endswith(":latest"), name
    docker = text("Dockerfile")
    assert re.search(r"^FROM python:3\.\d+", docker, re.M) and ":latest" not in docker


def test_dockerfile_runs_as_non_root_with_a_healthcheck_and_no_remote_downloads():
    d = text("Dockerfile")
    assert re.search(r"^USER (?!root|0)\S+", d, re.M)
    assert "HEALTHCHECK" in d
    assert not re.search(r"^ADD\s+https?://", d, re.M) and "curl | sh" not in d and "| bash" not in d


def test_no_secret_values_in_any_deploy_file():
    suspicious = re.compile(r"(password|passwd|token|secret|dsn)\s*[:=]\s*(?!\s*$)(\S+)", re.I)
    local_only = {"council.env", "council_tokens.json"}  # a person's own secrets when running locally: git-ignored, checked below
    for f in DEPLOY.rglob("*"):
        if not f.is_file() or f.suffix == ".pyc" or f.name in local_only or "backups" in f.parts:
            continue
        for lineno, line in enumerate(f.read_text(errors="ignore").splitlines(), 1):
            code = line.split("#")[0]
            for m in suspicious.finditer(code):
                value = m.group(2).strip("\"'")
                ok = value in ("CHANGE_ME", "", "{}") or value.startswith(("${", "$", "/run/secrets", "/run", "COUNCIL_", "<")) or "CHANGE_ME" in value \
                    or value.lower() in ("true", "false", "null", "file", "none") or value.endswith((".json", ".env", ".yml")) \
                    or value.startswith(("%", "(", "[", "\\"))
                assert ok, f"{f.name}:{lineno} looks like a secret value: {line.strip()[:80]}"
    assert (DEPLOY / "council.env.example").exists() and "CHANGE_ME" in text("council.env.example")
    ignore = (DEPLOY.parent.parent / ".gitignore").read_text()
    for secret_file in ("council/deploy/council.env", "council/deploy/council_tokens.json", "council/deploy/backups/"):
        assert secret_file in ignore, secret_file  # the real secrets can never be committed by accident


def test_caddy_snippet_proxies_over_https_only_with_security_headers_and_a_trustworthy_forwarded_for():
    c = text("Caddyfile.snippet")
    assert "http://" not in c  # a bare host gets automatic HTTPS; no plain-HTTP site
    for needle in ("Strict-Transport-Security", "X-Content-Type-Options", "reverse_proxy 127.0.0.1:8765", "max_size", "encode"):
        assert needle in c, needle
    assert "header_up X-Forwarded-For {remote_host}" in c  # replaced, never appended to what a client sent


def test_headroom_script_is_read_only():
    s = text("headroom.sh")
    assert s.startswith("#!/bin/sh") or s.startswith("#!/usr/bin/env sh") or s.startswith("#!/bin/bash")
    assert "set -eu" in s
    forbidden = [r"\bdocker\s+(run|rm|stop|kill|start|restart|exec|compose|build|pull|network|volume|system\s+prune)\b", r"\brm\b", r"\bmv\b",
                 r"\bsystemctl\s+(start|stop|restart|reload|enable|disable)\b", r"\bchmod\b", r"\bchown\b", r"\bapt(-get)?\b", r"\bkill\b",
                 r"\bsysctl\s+-w\b", r"\biptables\b", r"\bufw\b", r"\btee\b", r"\bsudo\b", r">\s*/(?!tmp/|dev/null)"]
    for pat in forbidden:
        assert not re.search(pat, s), pat
    assert "docker stats --no-stream" in s and "docker ps" in s


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX shell")
def test_headroom_script_runs_here_and_prints_the_sections_a_person_needs():
    out = subprocess.run(["sh", str(DEPLOY / "headroom.sh")], capture_output=True, text=True, timeout=60).stdout
    for section in ("== cpu", "== memory", "== disk", "== load"):
        assert section in out, section


def test_restore_refuses_the_production_database_by_default():
    r = text("restore.sh")
    assert "council_restore_test" in r and "--into-production" in r
    assert re.search(r"exit\s+[12]", r)


def test_backup_script_rotates_and_never_prints_credentials():
    b = text("backup.sh")
    assert "pg_dump" in b and "gzip" in b and "-mtime" in b
    assert "PGPASSWORD" not in b and "echo $POSTGRES" not in b


def test_runbook_exists_and_says_nothing_is_deployed_without_approval():
    r = text("README.md").lower()
    assert "explicit" in r and "headroom" in r and "limits" in r


def test_image_has_the_fast_http_stack_because_the_pure_python_one_was_measured_to_be_a_bottleneck():
    d = text("Dockerfile")
    assert "uvloop" in d and "httptools" in d  # uvicorn uses them automatically when installed (profile: h11 and the default loop)
