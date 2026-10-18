#!/usr/bin/env python3
"""Deterministic release-gate signals for /check Ship mode.

Emits the machine-checkable half of the Release Gate 2.0 matrix as labelled
blocks, each ending with `status: PASS|WARN|FAIL|N/A`, so the reviewer can
paste evidence instead of re-deriving it. Judgment surfaces (distribution
lane, package contents, release assets, registry state) stay with the skill.

Pure stdlib. Read-only. No network. Exits 0 even on WARN/FAIL so the harness
does not confuse "finding surfaced" with "script broken".

Run as: python3 skills/check/scripts/release_gate.py --root <path>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path


SEMVER_RE = re.compile(
    r"^[vV]?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
INPUT_LIMITS = {
    "VERSION": 4_096,
    "package.json": 1_000_000,
    "Cargo.toml": 1_000_000,
    "pyproject.toml": 1_000_000,
    "CHANGELOG.md": 5_000_000,
    "CHANGELOG": 5_000_000,
    "CHANGES.md": 5_000_000,
    "HISTORY.md": 5_000_000,
}


def run_git(root: Path, *args: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    # Preserve leading spaces because porcelain status uses them as the index
    # column. Trimming them turns the first unstaged file into a staged one.
    return proc.returncode, (proc.stdout or "").rstrip()


def block(name: str, lines: list[str], status: str) -> None:
    print(f"=== {name} ===")
    for line in lines:
        print(line)
    print(f"status: {status}")
    print()


def is_git_repo(root: Path) -> bool:
    code, out = run_git(root, "rev-parse", "--is-inside-work-tree")
    return code == 0 and out == "true"


def repo_file(root: Path, name: str) -> tuple[Path | None, str | None]:
    """Resolve a repository input without following it outside the root."""
    candidate = root / name
    if not candidate.exists() and not candidate.is_symlink():
        return None, None
    if candidate.is_symlink():
        return None, f"{name} must not be a symlink"
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None, f"{name} does not resolve to a file inside the repository"
    if not resolved.is_file():
        return None, f"{name} is not a regular file"
    return resolved, None


def read_repo_text(path: Path, name: str) -> tuple[str | None, str | None]:
    """Read a validated repository input with no symlink follow and a size cap."""
    limit = INPUT_LIMITS[name]
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return None, f"{name} became unreadable after validation"
    try:
        chunks: list[bytes] = []
        total = 0
        while total <= limit:
            chunk = os.read(descriptor, min(65_536, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
    except OSError:
        return None, f"{name} could not be read completely"
    finally:
        os.close(descriptor)
    if total > limit:
        return None, f"{name} exceeds the {limit}-byte release-gate limit"
    return b"".join(chunks).decode("utf-8", errors="replace"), None


def parse_semver(value: str) -> tuple[tuple[int, int, int], str | None] | None:
    match = SEMVER_RE.fullmatch(value.strip())
    if not match:
        return None
    core = tuple(int(match.group(index)) for index in (1, 2, 3))
    prerelease = match.group(4)
    if prerelease and any(
        part.isdigit() and len(part) > 1 and part.startswith("0")
        for part in prerelease.split(".")
    ):
        return None
    return core, prerelease


def evidence_value(value: str) -> str:
    """Render repository-controlled text as one bounded evidence line."""
    has_controls = any(ord(char) < 32 or ord(char) == 127 for char in value)
    rendered = json.dumps(value, ensure_ascii=True) if has_controls else value
    return rendered if len(rendered) <= 160 else f"{rendered[:157]}..."


def worktree_state(root: Path) -> None:
    code, out = run_git(root, "status", "--porcelain", "-uall")
    if code != 0:
        block("WORKTREE STATE", [f"git status failed: {out}"], "N/A")
        return
    staged = modified = untracked = 0
    for line in out.splitlines():
        if line.startswith("??"):
            untracked += 1
            continue
        if line[:1].strip():
            staged += 1
        if line[1:2].strip():
            modified += 1
    lines = [f"staged: {staged}", f"modified: {modified}", f"untracked: {untracked}"]
    status = "PASS" if staged == modified == untracked == 0 else "WARN"
    if status == "WARN":
        lines.append("dirty worktree: account for every file before a release claim")
    block("WORKTREE STATE", lines, status)


def remote_sync(root: Path) -> None:
    code, out = run_git(
        root, "rev-list", "--left-right", "--count", "@{upstream}...HEAD"
    )
    if code != 0:
        block("REMOTE SYNC", ["no upstream configured for current branch"], "N/A")
        return
    try:
        behind, ahead = (int(n) for n in out.split())
    except (TypeError, ValueError):
        block("REMOTE SYNC", [f"unexpected git rev-list output: {out!r}"], "N/A")
        return
    lines = [f"ahead of upstream: {ahead}", f"behind upstream: {behind}"]
    if behind:
        lines.append("branch is behind upstream: sync before releasing")
        status = "FAIL"
    elif ahead:
        lines.append("unpushed commits: remote does not have this state yet")
        status = "WARN"
    else:
        lines.append(
            "local tracking ref matches HEAD; fetch or ls-remote evidence is still required"
        )
        status = "WARN"
    block("REMOTE SYNC", lines, status)


def latest_stable_reachable_tag(root: Path) -> str | None:
    code, out = run_git(root, "tag", "--merged", "HEAD", "--sort=-version:refname")
    if code != 0 or not out:
        return None
    for line in out.splitlines():
        tag = line.strip()
        parsed = parse_semver(tag)
        if parsed and parsed[1] is None:
            return tag
    return None


def tag_baseline(root: Path) -> str | None:
    tag = latest_stable_reachable_tag(root)
    if tag is None:
        block("TAG BASELINE", ["no stable tag reachable from HEAD"], "N/A")
        return None
    code, count = run_git(root, "rev-list", "--count", f"{tag}..HEAD")
    lines = [f"latest stable reachable tag: {tag}"]
    if code == 0:
        lines.append(f"commits since tag: {count}")
        if count == "0":
            lines.append("HEAD is the tagged commit")
    lines.append("local reachability only; confirm this tag is the latest published release")
    block("TAG BASELINE", lines, "WARN")
    return tag


def toml_version(text: str, sections: tuple[str, ...]) -> str | None:
    current = ""
    for line in text.splitlines():
        header = re.match(r"\s*\[([^\]]+)\]", line)
        if header:
            current = header.group(1).strip()
            continue
        if current in sections:
            m = re.match(r"\s*version\s*=\s*[\"']([^\"']+)[\"']", line)
            if m:
                return m.group(1)
    return None


def collect_versions(root: Path) -> tuple[dict[str, str], list[str]]:
    found: dict[str, str] = {}
    errors: list[str] = []
    version_file, error = repo_file(root, "VERSION")
    if error:
        errors.append(error)
    if version_file:
        text, read_error = read_repo_text(version_file, "VERSION")
        if read_error:
            errors.append(read_error)
        elif text:
            first = text.strip()
            if first:
                found["VERSION"] = first.splitlines()[0].strip()
    pkg, error = repo_file(root, "package.json")
    if error:
        errors.append(error)
    if pkg:
        text, read_error = read_repo_text(pkg, "package.json")
        if read_error:
            errors.append(read_error)
        else:
            try:
                data = json.loads(text or "")
            except json.JSONDecodeError:
                found["package.json"] = "(unparseable)"
            else:
                if isinstance(data, dict) and isinstance(data.get("version"), str):
                    found["package.json"] = data["version"]
    cargo, error = repo_file(root, "Cargo.toml")
    if error:
        errors.append(error)
    if cargo:
        text, read_error = read_repo_text(cargo, "Cargo.toml")
        if read_error:
            errors.append(read_error)
        elif text:
            v = toml_version(text, ("package",))
            if v:
                found["Cargo.toml"] = v
    pyproject, error = repo_file(root, "pyproject.toml")
    if error:
        errors.append(error)
    if pyproject:
        text, read_error = read_repo_text(pyproject, "pyproject.toml")
        if read_error:
            errors.append(read_error)
        elif text:
            v = toml_version(text, ("project", "tool.poetry"))
            if v:
                found["pyproject.toml"] = v
    return found, errors


def version_sync(root: Path, tag: str | None) -> str | None:
    found, errors = collect_versions(root)
    if not found and not errors:
        block(
            "VERSION FIELD SYNC",
            ["no VERSION / package.json / Cargo.toml / pyproject.toml version found"],
            "N/A",
        )
        return None
    lines = [f"{name}: {evidence_value(value)}" for name, value in sorted(found.items())]
    status = "FAIL" if errors else "PASS"
    lines.extend(f"unsafe repository input: {error}" for error in errors)
    invalid_evidence = [
        name
        for name, value in found.items()
        if len(value) > 128
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ]
    if invalid_evidence:
        lines.append(
            "version fields contain control characters or exceed 128 characters: "
            + ", ".join(sorted(invalid_evidence))
        )
        status = "FAIL"
    if "(unparseable)" in found.values():
        lines.append("unparseable manifest: fix it before trusting version sync")
        status = "FAIL"
    normalized = {
        v[1:] if v.startswith(("v", "V")) else v
        for v in found.values()
        if v != "(unparseable)"
    }
    if len(normalized) > 1:
        lines.append("version fields disagree: align them before releasing")
        status = "FAIL"
    manifest = next(iter(normalized)) if len(normalized) == 1 else None
    raw_manifest = next(iter(found.values())) if len(normalized) == 1 and found else None
    parsed_manifest = parse_semver(raw_manifest) if raw_manifest else None
    if manifest and parsed_manifest is None:
        lines.append("manifest version is not exact SemVer")
        status = "FAIL"
    if parsed_manifest and parsed_manifest[1] is not None:
        lines.append("manifest is a prerelease version; do not treat it as stable-tag parity")
        if status == "PASS":
            status = "WARN"
    if parsed_manifest and tag:
        lines.append(f"stable reachable tag: {tag}")
        parsed_tag = parse_semver(tag)
        if parsed_tag:
            manifest_core, manifest_pre = parsed_manifest
            tag_core, tag_pre = parsed_tag
            if parsed_manifest == parsed_tag:
                lines.append("manifest exactly matches stable reachable tag")
            elif manifest_core < tag_core or (
                manifest_core == tag_core and manifest_pre is not None and tag_pre is None
            ):
                lines.append("manifest BEHIND stable reachable tag: version regressed")
                status = "FAIL"
            elif manifest_core > tag_core:
                lines.append(
                    "manifest ahead of stable reachable tag (unreleased version in progress)"
                )
            else:
                lines.append("manifest does not exactly match stable reachable tag")
                if status == "PASS":
                    status = "WARN"
    block("VERSION FIELD SYNC", lines, status)
    return manifest


def changelog_mentions(root: Path, manifest: str | None) -> None:
    changelog = None
    for name in ("CHANGELOG.md", "CHANGELOG", "CHANGES.md", "HISTORY.md"):
        candidate, error = repo_file(root, name)
        if error:
            block("CHANGELOG VERSION", [f"unsafe repository input: {error}"], "FAIL")
            return
        if candidate:
            changelog = candidate
            break
    if changelog is None or not manifest:
        reason = "no changelog file" if changelog is None else "no single manifest version"
        block("CHANGELOG VERSION", [reason], "N/A")
        return
    text, error = read_repo_text(changelog, changelog.name)
    if error:
        block("CHANGELOG VERSION", [error], "FAIL")
        return
    assert text is not None
    version_pattern = re.compile(
        rf"(?<![0-9A-Za-z.+-])(?:v|V)?{re.escape(manifest)}"
        rf"(?![0-9A-Za-z.+-])"
    )
    if version_pattern.search(text):
        block(
            "CHANGELOG VERSION",
            [f"{changelog.name} mentions {manifest}"],
            "PASS",
        )
    else:
        block(
            "CHANGELOG VERSION",
            [f"{changelog.name} does not mention {manifest}"],
            "WARN",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="project root to inspect")
    args = parser.parse_args()
    root = Path(args.root).resolve()

    if not root.is_dir():
        block("RELEASE GATE", [f"root not found: {root}"], "N/A")
        return 0

    if not is_git_repo(root):
        block("WORKTREE STATE", ["not a git repository"], "N/A")
        block("REMOTE SYNC", ["not a git repository"], "N/A")
        block("TAG BASELINE", ["not a git repository"], "N/A")
        manifest = version_sync(root, None)
        changelog_mentions(root, manifest)
        return 0

    worktree_state(root)
    remote_sync(root)
    tag = tag_baseline(root)
    manifest = version_sync(root, tag)
    changelog_mentions(root, manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
