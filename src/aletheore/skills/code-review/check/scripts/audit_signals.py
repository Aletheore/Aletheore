#!/usr/bin/env python3
"""Project audit signals (Phase 1) for /check audit mode.

Walks a project root and emits structured signal blocks to stdout.
Each block ends with `status: PASS|WARN|FAIL|N/A` so the LLM driving the
4-axis Linus-style scorecard can skim quickly.

Pure stdlib. Read-only. Exits 0 even on WARN/FAIL so the harness does
not confuse "finding surfaced" with "script broken".

Run as: python3 skills/check/scripts/audit_signals.py --root <path>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


EXCLUDED_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "dist", "build", ".next",
    "__pycache__", ".turbo", "target", ".venv", "venv", "vendor",
    "coverage", ".cache", ".parcel-cache", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", "Pods", "Carthage", ".swiftpm", ".gradle",
}

# Kept identical with skills/health/scripts/check_maintainability.py by
# tests/python/test_auditor_alignment.py (thresholds stay per-product).
SOURCE_EXTS = {
    ".bash", ".c", ".cc", ".cpp", ".cs", ".css", ".go", ".h", ".hpp",
    ".html", ".java", ".js", ".jsx", ".kt", ".lua", ".m", ".mjs", ".mm",
    ".md", ".php", ".py", ".rb", ".rs", ".scss", ".sh", ".swift", ".ts",
    ".tsx", ".vue", ".yaml", ".yml", ".zsh",
}

HOTSPOT_LINES = 500
HOTSPOT_FAIL = 1500
HEREDOC_LINES = 100
DRIFT_WARN = 50
DRIFT_FAIL = 150
DUP_JACCARD = 0.70
MAX_TEXT_BYTES = 2_000_000

MARKER_RE = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b", re.IGNORECASE)
HEREDOC_OPEN_RE = re.compile(
    r"(python3?|node|ruby|perl|php)\b[^|\n]*?<<-?\s*['\"]?(\w+)['\"]?"
)
INSTALL_URL_RE = re.compile(
    r"raw\.githubusercontent\.com/[^/\s]+/[^/\s]+/([^/\s]+)/"
)
# --exclude requires = or trailing value to avoid matching git's --exclude-standard
DENYLIST_HINT_RE = re.compile(
    r"(^\s*(skip|exclude)\s*=|\s--exclude=|!\*\.\w+|grep\s+-v\b|--ignore=)",
    re.IGNORECASE,
)
MINIFIED_RE = re.compile(r"\.min\.[a-z]+$", re.IGNORECASE)
CLI_CONTRACT_BUCKETS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("help_or_usage", re.compile(r"(--help|\busage\b|\bhelp output\b)", re.IGNORECASE)),
    ("version", re.compile(r"(--version|\bversion output\b)", re.IGNORECASE)),
    ("exit_code", re.compile(r"\b(exit code|exit status|return code|exit_code|\$\?)\b", re.IGNORECASE)),
    ("stdout", re.compile(r"\b(stdout|standard output)\b|>\s*\"\$?[A-Za-z0-9_./-]*stdout", re.IGNORECASE)),
    ("stderr", re.compile(r"\b(stderr|standard error)\b|2>\s*\"\$?[A-Za-z0-9_./-]*stderr", re.IGNORECASE)),
    ("non_interactive_or_tty", re.compile(r"\b(non-interactive|noninteractive|tty|isatty|/dev/null|CI=1)\b", re.IGNORECASE)),
    (
        "install_run",
        re.compile(
            r"(\binstall\s+-m\b|\binstalled command\b|\binstalled-runtime\b|"
            r"\binstall/run\b|\binstall run\b|\btemp prefix\b|\bPATH shim\b|"
            r"\bpackage-manager path\b|\bnpm link\b|\bpipx install\b|"
            r"\bcargo install\b|\bbrew install\b|\bmake install\b)",
            re.IGNORECASE,
        ),
    ),
    ("json_or_schema", re.compile(r"\b(json|schema)\b", re.IGNORECASE)),
    ("completion", re.compile(r"\bcompletion\b", re.IGNORECASE)),
)
CLI_CORE_BUCKETS = (
    "help_or_usage",
    "version",
    "exit_code",
    "stdout",
    "stderr",
    "install_run",
)


# The file-walk helpers below are deliberately duplicated in
# skills/health/scripts/check_maintainability.py. Both scripts ship
# standalone (see packaging.allowlist) and run inside an arbitrary target
# project, so they import only stdlib. Do not hoist them into a shared
# scripts/ module: it is dev-only, not on the ship allowlist, and would
# couple a standalone tool to the install layout.
def is_excluded(path: Path, root: Path) -> bool:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        parts = path.parts
    if any(p in EXCLUDED_DIRS for p in parts):
        return True
    return bool(MINIFIED_RE.search(path.name))


def is_repo_file(path: Path, root: Path) -> bool:
    """Return true only for a regular file reached without any symlink hop."""
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    if not relative.parts:
        return False
    current = root
    try:
        for part in relative.parts:
            current /= part
            if current.is_symlink():
                return False
        return current.is_file()
    except OSError:
        return False


def is_repo_dir(path: Path, root: Path) -> bool:
    """Return true only for a directory reached without any symlink hop."""
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    current = root
    try:
        for part in relative.parts:
            current /= part
            if current.is_symlink():
                return False
        return current.is_dir()
    except OSError:
        return False


def iter_files(root: Path) -> list[Path]:
    try:
        proc = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-C", str(root), "ls-files",
             "--cached", "--others", "--exclude-standard", "-z"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, check=False,
        )
        if proc.returncode == 0 and proc.stdout:
            out = []
            for raw_path in proc.stdout.split(b"\0"):
                if not raw_path:
                    continue
                p = root / os.fsdecode(raw_path)
                if is_repo_file(p, root) and not is_excluded(p, root):
                    out.append(p)
            return out
    except OSError:
        pass
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        dirnames[:] = [
            d for d in dirnames
            if d not in EXCLUDED_DIRS and is_repo_dir(current / d, root)
        ]
        if is_excluded(current, root):
            continue
        for fname in filenames:
            p = current / fname
            if is_repo_file(p, root) and not is_excluded(p, root):
                out.append(p)
    return out


def line_count(path: Path, root: Path) -> int:
    if not is_repo_file(path, root):
        return 0
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(path, flags), "rb") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def read_text(path: Path, root: Path, limit: int = 0) -> str:
    if not is_repo_file(path, root):
        return ""
    byte_limit = limit or MAX_TEXT_BYTES
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
    except OSError:
        return ""
    try:
        chunks: list[bytes] = []
        remaining = byte_limit
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    except OSError:
        return ""
    finally:
        os.close(descriptor)
    return b"".join(chunks).decode("utf-8", errors="replace")


def rel(path: Path, root: Path) -> str:
    try:
        value = path.relative_to(root).as_posix()
    except ValueError:
        value = path.as_posix()
    return safe_label(value)


def safe_label(value: str, limit: int = 500) -> str:
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        value = json.dumps(value, ensure_ascii=False)
    return value if len(value) <= limit else f"{value[: limit - 3]}..."


def header(name: str) -> None:
    print(f"=== {name} ===")


def status(label: str) -> None:
    print(f"status: {label}")


def block_hotspots(files: list[Path], root: Path) -> None:
    header("FILE SIZE HOTSPOTS")
    sized = ((p, line_count(p, root)) for p in files if p.suffix.lower() in SOURCE_EXTS)
    big = sorted(
        (item for item in sized if item[1] >= HOTSPOT_LINES),
        key=lambda x: -x[1],
    )[:10]
    if not big:
        print(f"(no source files >= {HOTSPOT_LINES} lines)")
        status("PASS")
        return
    for path, n in big:
        print(f"  {n:>5}  {rel(path, root)}")
    status("FAIL" if any(n >= HOTSPOT_FAIL for _, n in big) else "WARN")


def block_heredoc(files: list[Path], root: Path) -> None:
    header("HEREDOC BLOAT")
    hits: list[tuple[str, int, str, int]] = []
    for path in files:
        if path.suffix.lower() not in {".sh", ".bash", ".zsh"}:
            continue
        text = read_text(path, root)
        if not text:
            continue
        lines = text.splitlines()
        i = 0
        while i < len(lines):
            m = HEREDOC_OPEN_RE.search(lines[i])
            if not m:
                i += 1
                continue
            lang, marker = m.group(1), m.group(2)
            j = i + 1
            close = re.compile(r"^\s*" + re.escape(marker) + r"\s*$")
            while j < len(lines) and not close.match(lines[j]):
                j += 1
            size = j - i
            if size >= HEREDOC_LINES:
                hits.append((rel(path, root), i + 1, lang, size))
            i = j + 1
    if not hits:
        print("(no python/node/ruby/perl/php heredocs >= 100 lines)")
        status("PASS")
        return
    for f, ln, lang, sz in hits:
        print(f"  {f}:{ln}  lang={lang}  block_lines={sz}")
    status("WARN")


def block_test_ci(files: list[Path], root: Path) -> None:
    header("TEST AND CI SURFACE")
    test_files = [
        p for p in files
        if p.suffix.lower() in SOURCE_EXTS
        and (("test" in p.name.lower()) or ("spec" in p.name.lower()))
    ]
    src_files = [p for p in files if p.suffix.lower() in SOURCE_EXTS]
    wf_dir = root / ".github" / "workflows"
    workflows = []
    if is_repo_dir(wf_dir, root):
        workflows = sorted(
            path
            for path in list(wf_dir.glob("*.yml")) + list(wf_dir.glob("*.yaml"))
            if is_repo_file(path, root)
        )
    job_names: list[str] = []
    for wf in workflows:
        text = read_text(wf, root, 50_000)
        for m in re.finditer(r"^name:\s*(.+?)\s*$", text, re.MULTILINE):
            job_names.append(safe_label(f"{wf.name}: {m.group(1)[:60]}"))
            break
    ratio = len(test_files) / max(len(src_files), 1)
    print(f"tests_count={len(test_files)} source_count={len(src_files)} "
          f"ratio={ratio:.1%}")
    print(f"ci_workflow_files={len(workflows)}")
    for j in job_names[:10]:
        print(f"  workflow: {j}")
    if not test_files and not workflows:
        status("FAIL")
    elif not test_files or not workflows:
        status("WARN")
    else:
        status("PASS")


def _package_bin_entrypoints(root: Path) -> list[str]:
    path = root / "package.json"
    if not is_repo_file(path, root):
        return []
    text = read_text(path, root, 200_000)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    bin_field = data.get("bin")
    name = str(data.get("name") or "package")
    if isinstance(bin_field, str):
        return [f"package.json bin:{name} -> {bin_field}"]
    if isinstance(bin_field, dict):
        return [
            f"package.json bin:{cmd} -> {target}"
            for cmd, target in sorted(bin_field.items())
            if isinstance(cmd, str) and isinstance(target, str)
        ]
    return []


def _pyproject_script_entrypoints(root: Path) -> list[str]:
    path = root / "pyproject.toml"
    if not is_repo_file(path, root):
        return []
    text = read_text(path, root, 200_000)
    entries: list[str] = []
    in_scripts = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_scripts = stripped in {
                "[project.scripts]",
                "[tool.poetry.scripts]",
            }
            continue
        if not in_scripts or not stripped or stripped.startswith("#"):
            continue
        m = re.match(r'([A-Za-z0-9_.-]+)\s*=\s*["\']([^"\']+)["\']', stripped)
        if m:
            entries.append(f"pyproject.toml script:{m.group(1)} -> {m.group(2)}")
    return entries


def _cargo_entrypoints(root: Path) -> list[str]:
    entries: list[str] = []
    cargo = root / "Cargo.toml"
    if is_repo_file(cargo, root):
        text = read_text(cargo, root, 200_000)
        if "[[bin]]" in text:
            names = re.findall(r'(?m)^\s*name\s*=\s*["\']([^"\']+)["\']', text)
            if names:
                entries.extend(f"Cargo.toml bin:{name}" for name in sorted(set(names)))
            else:
                entries.append("Cargo.toml [[bin]]")
    if is_repo_file(root / "src" / "main.rs", root):
        entries.append("src/main.rs")
    return entries


def cli_entrypoints(files: list[Path], root: Path) -> list[str]:
    entries: set[str] = set()
    entries.update(_package_bin_entrypoints(root))
    entries.update(_pyproject_script_entrypoints(root))
    entries.update(_cargo_entrypoints(root))

    for path in files:
        try:
            parts = path.relative_to(root).parts
        except ValueError:
            continue
        if not parts:
            continue
        if parts[0] == "bin" and len(parts) >= 2:
            entries.add("/".join(parts[:2]))
        if parts[0] == "cmd" and len(parts) >= 3 and path.suffix == ".go":
            entries.add(f"cmd/{parts[1]}")
    return sorted(entries)


def _is_cli_contract_candidate(path: Path, root: Path) -> bool:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return False
    if not parts:
        return False
    lower_parts = tuple(p.lower() for p in parts)
    name = lower_parts[-1]
    if name in {"readme.md", "readme.txt", "agents.md", "claude.md"}:
        return True
    if lower_parts[0] in {"tests", "test", "spec", "scripts"}:
        return True
    if "test" in name or "spec" in name:
        return True
    if len(lower_parts) >= 3 and lower_parts[:2] == (".github", "workflows"):
        return True
    return False


def cli_contract_evidence(files: list[Path], root: Path) -> dict[str, list[tuple[str, str]]]:
    hits: dict[str, list[tuple[str, str]]] = {}
    for path in files:
        if not _is_cli_contract_candidate(path, root):
            continue
        text = read_text(path, root, 200_000)
        if not text:
            continue
        for bucket, pattern in CLI_CONTRACT_BUCKETS:
            m = pattern.search(text)
            if m:
                hits.setdefault(bucket, []).append((rel(path, root), m.group(0)))
    return {bucket: sorted(values) for bucket, values in sorted(hits.items())}


def block_cli_contract_surface(files: list[Path], root: Path) -> None:
    header("CLI CONTRACT SURFACE")
    entries = cli_entrypoints(files, root)
    if not entries:
        print("(no CLI entrypoints detected)")
        status("N/A")
        return

    print(f"entrypoints={len(entries)}")
    for entry in entries[:12]:
        print(f"  entry: {safe_label(entry)}")
    if len(entries) > 12:
        print(f"  ... {len(entries) - 12} more")

    evidence = cli_contract_evidence(files, root)
    covered = tuple(bucket for bucket, _ in CLI_CONTRACT_BUCKETS if bucket in evidence)
    missing = tuple(bucket for bucket in CLI_CORE_BUCKETS if bucket not in evidence)
    print(f"covered={','.join(covered) if covered else 'none'}")
    print(f"missing={','.join(missing) if missing else 'none'}")
    printed = 0
    for bucket in covered:
        for path, signal in evidence[bucket][:3]:
            print(
                f"  evidence: {bucket}  {safe_label(path)}  "
                f"signal={safe_label(signal)}"
            )
            printed += 1
            if printed >= 12:
                break
        if printed >= 12:
            break
    if not missing:
        status("PASS")
    else:
        status("WARN")


def _grep_version(path: Path, root: Path, pattern: str) -> str | None:
    text = read_text(path, root, 20_000)
    if not text:
        return None
    m = re.search(pattern, text, re.MULTILINE)
    return m.group(1).strip() if m else None


def block_version_sources(root: Path) -> None:
    header("VERSION SOURCE COUNT")
    found: list[tuple[str, str]] = []
    v = root / "VERSION"
    if is_repo_file(v, root):
        first = read_text(v, root).strip().splitlines()
        if first:
            found.append(("VERSION", first[0]))
    probes = [
        ("package.json", r'"version"\s*:\s*"([^"]+)"'),
        ("Cargo.toml", r'^\s*version\s*=\s*"([^"]+)"'),
        ("pyproject.toml", r'^\s*version\s*=\s*"([^"]+)"'),
        ("setup.py", r"version\s*=\s*['\"]([^'\"]+)['\"]"),
    ]
    for fname, pat in probes:
        p = root / fname
        if is_repo_file(p, root):
            v_str = _grep_version(p, root, pat)
            if v_str:
                found.append((fname, v_str))
    for pat in ("*.podspec", "*.csproj"):
        for path in root.glob(pat):
            if not is_repo_file(path, root):
                continue
            v_str = _grep_version(
                path, root, r'(?i)version\s*[:=]\s*["\']?(\d+\.\d+\.\d+[\w.-]*)'
            )
            if v_str:
                found.append((path.name, v_str))
    for path in list(root.glob("build.gradle*")):
        if not is_repo_file(path, root):
            continue
        v_str = _grep_version(
            path, root, r'(?i)version\s*[:=]\s*["\']?(\d+\.\d+\.\d+[\w.-]*)'
        )
        if v_str:
            found.append((path.name, v_str))
    if not found:
        print("(no declared version source found)")
        status("PASS")
        return
    for f, val in found:
        print(f"  {safe_label(f)}: {safe_label(val)}")
    distinct = {val for _, val in found if val}
    print(f"sources={len(found)} distinct_values={len(distinct)}")
    if len(found) > 1 and len(distinct) > 1:
        status("WARN")
    else:
        status("PASS")


def block_packaging_posture(root: Path) -> None:
    header("PACKAGING FILTER POSTURE")
    allowlist_files = [
        path for path in list(root.glob("*.allowlist")) + list(root.glob("MANIFEST.in"))
        if is_repo_file(path, root)
    ]
    pkg_scripts = [
        path for path in (
            list(root.glob("scripts/package*.sh"))
            + list(root.glob("scripts/release*.sh"))
        )
        if is_repo_file(path, root)
    ]
    denylist_hits = 0
    for sp in pkg_scripts:
        for line in read_text(sp, root).splitlines():
            if DENYLIST_HINT_RE.search(line):
                denylist_hits += 1
    if allowlist_files:
        for f in allowlist_files:
            print(f"  allowlist: {rel(f, root)}")
        print(f"posture=allowlist denylist_hits_in_scripts={denylist_hits}")
        status("PASS")
        return
    if denylist_hits:
        for sp in pkg_scripts:
            print(f"  script: {rel(sp, root)}")
        print(f"posture=denylist denylist_hits_in_scripts={denylist_hits}")
        status("WARN")
        return
    print("posture=none (no packaging scripts)")
    status("N/A")


def block_install_url(root: Path) -> None:
    header("INSTALL URL PINNING")
    targets: list[Path] = [root / "README.md"]
    targets += list(root.glob("scripts/setup*.sh"))
    targets += list(root.glob("scripts/install*.sh"))
    findings: list[tuple[str, int, str]] = []
    for path in targets:
        if not is_repo_file(path, root):
            continue
        text = read_text(path, root, 200_000)
        for i, line in enumerate(text.splitlines(), start=1):
            for m in INSTALL_URL_RE.finditer(line):
                findings.append((rel(path, root), i, m.group(1)))
    if not findings:
        print("(no raw.githubusercontent.com refs found)")
        status("PASS")
        return
    moving = [f for f in findings if f[2] in ("main", "master", "HEAD")]
    for f, ln, ref in findings[:20]:
        marker = " [MOVING]" if ref in ("main", "master", "HEAD") else ""
        print(f"  {f}:{ln}  ref={ref}{marker}")
    print(f"total={len(findings)} moving={len(moving)}")
    if moving:
        status("WARN")
    else:
        status("PASS")


def block_agent_doc_dedup(root: Path) -> None:
    header("AGENT DOC DEDUP")
    claude = root / "CLAUDE.md"
    agents = root / "AGENTS.md"
    have_c = claude.exists() or claude.is_symlink()
    have_a = agents.exists() or agents.is_symlink()
    if not have_c and not have_a:
        print("posture=none")
        status("PASS")
        return
    if not (have_c and have_a):
        print(f"posture=single-file ({'CLAUDE.md' if have_c else 'AGENTS.md'} only)")
        status("PASS")
        return
    if claude.is_symlink() and claude.resolve(strict=False).name == "AGENTS.md":
        print("posture=symlink (CLAUDE.md -> AGENTS.md)")
        status("PASS")
        return
    if agents.is_symlink() and agents.resolve(strict=False).name == "CLAUDE.md":
        print("posture=symlink (AGENTS.md -> CLAUDE.md)")
        status("PASS")
        return
    a = read_text(claude, root)
    b = read_text(agents, root)
    if a and a == b:
        print("posture=identical (consider symlink to dedup)")
        status("WARN")
        return
    cross = ("AGENTS.md" in a) or ("CLAUDE.md" in b)
    a_set = {ln.strip() for ln in a.splitlines()
             if ln.strip() and not ln.strip().startswith("#")}
    b_set = {ln.strip() for ln in b.splitlines()
             if ln.strip() and not ln.strip().startswith("#")}
    union = a_set | b_set
    jaccard = len(a_set & b_set) / len(union) if union else 0.0
    print(f"jaccard={jaccard:.2f} cross_refs={cross}")
    if jaccard >= 0.20:
        print("posture=divergent-overlap (drift risk; consider symlink)")
        status("WARN")
        return
    if cross:
        print("posture=cross-ref (one references the other)")
        status("WARN")
        return
    print("posture=independent")
    status("PASS")


def block_drift_markers(files: list[Path], root: Path) -> None:
    header("DRIFT MARKERS")
    counts: list[tuple[str, int]] = []
    total = 0
    for path in files:
        if path.suffix.lower() not in SOURCE_EXTS:
            continue
        text = read_text(path, root, 200_000)
        if not text:
            continue
        n = sum(1 for line in text.splitlines() if MARKER_RE.search(line))
        if n:
            counts.append((rel(path, root), n))
            total += n
    counts.sort(key=lambda x: -x[1])
    for f, n in counts[:5]:
        print(f"  {n:>4}  {f}")
    print(f"total={total}")
    if total >= DRIFT_FAIL:
        status("FAIL")
    elif total >= DRIFT_WARN:
        status("WARN")
    else:
        status("PASS")


def block_duplicate_setup(root: Path) -> None:
    header("DUPLICATE SETUP SCRIPTS")
    scripts = [
        path for path in (
            list(root.glob("scripts/setup-*.sh"))
            + list(root.glob("scripts/install-*.sh"))
        )
        if is_repo_file(path, root)
    ]
    if len(scripts) < 2:
        print("(fewer than 2 setup-* scripts to compare)")
        status("N/A")
        return
    sets: dict[Path, set[str]] = {}
    for sp in scripts:
        sets[sp] = {ln.strip() for ln in read_text(sp, root).splitlines()
                    if ln.strip() and not ln.strip().startswith("#")}
    pairs: list[tuple[str, str, float]] = []
    names = list(sets.keys())
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            union = sets[a] | sets[b]
            if not union:
                continue
            j = len(sets[a] & sets[b]) / len(union)
            if j >= DUP_JACCARD:
                pairs.append((rel(a, root), rel(b, root), j))
    if not pairs:
        print("(no setup pairs with jaccard >= 0.70)")
        status("PASS")
        return
    for a, b, j in pairs:
        print(f"  {a} vs {b}  jaccard={j:.2f}")
    status("WARN")


def block_denylist_in_build(root: Path) -> None:
    header("DENYLIST IN BUILD")
    targets = (list(root.glob("scripts/package*.sh"))
               + list(root.glob("scripts/release*.sh"))
               + [root / "Makefile", root / "Justfile"])
    real_targets = [p for p in targets if is_repo_file(p, root)]
    if not real_targets:
        print("(no build scripts present)")
        status("N/A")
        return
    hits: list[tuple[str, int, str]] = []
    for path in real_targets:
        text = read_text(path, root, 100_000)
        for i, line in enumerate(text.splitlines(), start=1):
            if DENYLIST_HINT_RE.search(line):
                hits.append((rel(path, root), i, line.strip()[:80]))
    if not hits:
        print("(no denylist patterns found in build scripts)")
        status("PASS")
        return
    for f, ln, s in hits[:20]:
        print(f"  {f}:{ln}  {s}")
    status("WARN")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path.cwd(),
        help="Project root to audit (default: current working directory)",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_dir():
        print(
            f"audit_signals: not a directory: {safe_label(root.as_posix())}",
            file=sys.stderr,
        )
        return 2
    files = iter_files(root)
    print(f"project_root: {safe_label(root.as_posix())}")
    print(f"files_scanned: {len(files)}")
    print()
    block_hotspots(files, root); print()
    block_heredoc(files, root); print()
    block_test_ci(files, root); print()
    block_cli_contract_surface(files, root); print()
    block_version_sources(root); print()
    block_packaging_posture(root); print()
    block_install_url(root); print()
    block_agent_doc_dedup(root); print()
    block_drift_markers(files, root); print()
    block_duplicate_setup(root); print()
    block_denylist_in_build(root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
