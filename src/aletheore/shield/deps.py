"""Find dependencies a change newly introduces: manifest additions and import-only additions.

Precision beats coverage: anything uncertain is treated as local (skipped) or reported
as NotChecked with a plain-words reason. This module never contacts a registry.
"""

import importlib.metadata
import json
import re
import tempfile
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath

from aletheore.manifests import EXTRA_SKIPPED_DIRS, MANIFEST_FILENAMES, MANIFEST_SUFFIXES
from aletheore.scanner.detect import IGNORED_DIRS
from aletheore.shield import imports
from aletheore.shield.gitutil import run_git

_CHECKED = {"PyPI": "Python", "npm": "JavaScript"}
_ECOSYSTEM_LABEL = {
    "Go": "Go", "crates.io": "Rust", "Maven": "Java/Kotlin", "RubyGems": "Ruby", "Packagist": "PHP",
    "NuGet": ".NET", "SwiftURL": "Swift",
}
_PUBLIC_NPM_HOSTS = ("registry.npmjs.org", "registry.yarnpkg.com")
_MAX_BASE_MANIFESTS = 300
_MAX_SOURCE_BYTES = 2_000_000


@dataclass(frozen=True)
class DepRef:
    ecosystem: str
    name: str
    source: str  # "manifest" or "import"
    where: str


@dataclass(frozen=True)
class NotChecked:
    name: str
    reason: str


def _norm_py(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _norm(ecosystem: str, name: str) -> str:
    return _norm_py(name) if ecosystem == "PyPI" else name.lower()


def _paths(out: bytes) -> list[str]:
    return [p.decode("utf-8", "replace") for p in out.split(b"\0") if p]


def _skipped_dir(rel: str) -> bool:
    return any(part in IGNORED_DIRS or part in EXTRA_SKIPPED_DIRS for part in PurePosixPath(rel).parts[:-1])


def _is_manifest(rel: str) -> bool:
    name = PurePosixPath(rel).name
    return name in MANIFEST_FILENAMES or name.endswith(MANIFEST_SUFFIXES)


def _read(repo: Path, rel: str) -> str | None:
    try:
        path = repo / rel
        if not path.is_file() or path.stat().st_size > _MAX_SOURCE_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _base_text(repo: Path, base: str, rel: str) -> str:
    result = run_git(repo, "show", f"{base}:{rel}")
    return result.stdout.decode("utf-8", "replace") if result.returncode == 0 else ""


def _has_ref(repo: Path, base: str) -> bool:
    return run_git(repo, "rev-parse", "--verify", "-q", f"{base}^{{commit}}").returncode == 0


def _working_files(repo: Path) -> list[str]:
    out = run_git(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard", timeout=30.0).stdout
    return [p for p in dict.fromkeys(_paths(out)) if (repo / p).exists()]


def _changed_files(repo: Path, base: str, has_base: bool, working: list[str]) -> list[str]:
    if not has_base:
        return working
    tracked = run_git(repo, "diff", "--name-only", "-z", "--no-renames", "--diff-filter=AM", base, "--", timeout=30.0)
    others = run_git(repo, "ls-files", "-z", "--others", "--exclude-standard", timeout=30.0)
    return [p for p in dict.fromkeys(_paths(tracked.stdout) + _paths(others.stdout)) if (repo / p).exists()]


def collect_pins(directory: Path):
    """aletheore.vulnerabilities.collect_pins, imported on first use (it costs about 100 ms to import and is only
    needed when a change touches a dependency manifest)."""
    from aletheore.vulnerabilities import collect_pins as _collect

    return _collect(directory)


# Files whose change can add or re-route a dependency. Only when one of these changed are the manifests on both
# sides of the diff read and compared; otherwise the manifest pins cannot differ and that work is skipped.
_DEPENDENCY_CONFIG_NAMES = frozenset(
    {"pip.conf", "pip.ini", ".npmrc", ".yarnrc.yml", ".yarnrc.yaml", "Pipfile", "Pipfile.lock", "setup.cfg", "setup.py"}
)


def _touches_dependencies(rel: str) -> bool:
    name = PurePosixPath(rel).name
    return (
        _is_manifest(rel)
        or name in _DEPENDENCY_CONFIG_NAMES
        or (name.startswith("requirements") and name.endswith(".txt"))
        or (PurePosixPath(rel).parent.name == "requirements" and name.endswith(".txt"))
    )


def _safe_pins(directory: Path) -> list[tuple[str, str, str]] | None:
    try:
        return collect_pins(directory)[0]
    except Exception:  # an unreadable manifest must not break the guard
        return None


def _base_pins(repo: Path, base: str, has_base: bool) -> list[tuple[str, str, str]]:
    if not has_base:
        return []
    listing = run_git(repo, "ls-tree", "-r", "-z", "--name-only", base, timeout=30.0)
    wanted = [p for p in _paths(listing.stdout) if _is_manifest(p) and not _skipped_dir(p)][:_MAX_BASE_MANIFESTS]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for rel in wanted:
            target = root / rel
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(_base_text(repo, base, rel), encoding="utf-8")
            except OSError:
                continue
        return _safe_pins(root) or []


# ---- local / declared names -------------------------------------------------------------

def _json(text: str | None) -> dict:
    try:
        data = json.loads(text or "")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _toml(text: str | None) -> dict:
    try:
        return tomllib.loads(text or "")
    except tomllib.TOMLDecodeError:
        return {}


@dataclass
class _Facts:
    local_npm: set[str]
    local_py: set[str]
    declared_npm: set[str]
    local_dirs: set[str]  # directory names and module stems anywhere in the repo
    aliases: set[str]
    private: dict[str, str]  # ecosystem -> reason


def _gather(repo: Path, working: list[str]) -> _Facts:
    facts = _Facts(set(), set(), set(), set(), set(), {})
    pip_hits: list[str] = []
    npm_hits: list[str] = []
    for rel in working:
        pure = PurePosixPath(rel)
        name = pure.name
        if not _skipped_dir(rel):
            # Deliberately broad (favours precision): any directory name or module stem anywhere in the repo
            # counts as local, which may hide a real import of a package whose name matches a local folder.
            facts.local_dirs.update(pure.parts[:-1])
            if name.endswith((".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")):
                facts.local_dirs.add(pure.stem)
        if name == "package.json" and not _skipped_dir(rel):
            data = _json(_read(repo, rel))
            if isinstance(data.get("name"), str):
                facts.local_npm.add(data["name"].lower())
            for key in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
                section = data.get(key)
                if isinstance(section, dict):
                    facts.declared_npm.update(n.lower() for n in section)
        elif name == "pyproject.toml" and not _skipped_dir(rel):
            _collect_pyproject(_toml(_read(repo, rel)), facts, rel, pip_hits)
        elif name in ("pip.conf", "pip.ini"):
            if re.search(r"(?mi)^\s*(extra-)?index[-_]url\s*[=:]", _read(repo, rel) or ""):
                pip_hits.append(rel)
        elif name.startswith("requirements") and name.endswith(".txt"):
            if re.search(r"(?m)^\s*(--index-url|--extra-index-url|-i)(\s|=)", _read(repo, rel) or ""):
                pip_hits.append(rel)
        elif name == ".npmrc":
            if _npmrc_private(_read(repo, rel) or ""):
                npm_hits.append(rel)
        elif name in (".yarnrc.yml", ".yarnrc.yaml"):
            if re.search(r"(?m)^\s*(npmRegistryServer|npmScopes)\s*:", _read(repo, rel) or ""):
                npm_hits.append(rel)
        elif name in ("tsconfig.json", "jsconfig.json") and not _skipped_dir(rel):
            paths = (_json(re.sub(r"(?m)^\s*//.*$", "", _read(repo, rel) or "")).get("compilerOptions") or {}).get("paths")
            if isinstance(paths, dict):
                for key in paths:
                    facts.aliases.add(re.split(r"[/*]", key.lstrip("@"), maxsplit=1)[0].lower())
    if pip_hits:
        facts.private["PyPI"] = f"a private package index is configured ({pip_hits[0]}), so new Python packages cannot be checked against the public index"
    if npm_hits:
        facts.private["npm"] = f"a private npm registry is configured ({npm_hits[0]}), so new JavaScript packages cannot be checked against the public registry"
    return facts


def _npmrc_private(text: str) -> bool:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(("#", ";")) or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key.startswith("@") and key.endswith(":registry"):
            return True
        if key == "registry" and not any(host in value for host in _PUBLIC_NPM_HOSTS):
            return True
    return False


def _collect_pyproject(data: dict, facts: _Facts, rel: str, pip_hits: list[str]) -> None:
    project = data.get("project") if isinstance(data.get("project"), dict) else {}
    tool = data.get("tool") if isinstance(data.get("tool"), dict) else {}
    poetry = tool.get("poetry") if isinstance(tool.get("poetry"), dict) else {}
    uv = tool.get("uv") if isinstance(tool.get("uv"), dict) else {}
    for name in (project.get("name"), poetry.get("name")):
        if isinstance(name, str):
            facts.local_py.add(_norm_py(name))
    groups = [poetry.get("dependencies"), poetry.get("dev-dependencies")]
    for group in (poetry.get("group") or {}).values() if isinstance(poetry.get("group"), dict) else []:
        if isinstance(group, dict):
            groups.append(group.get("dependencies"))
    for deps in groups:
        if isinstance(deps, dict):
            for dep, spec in deps.items():
                if isinstance(spec, dict) and any(k in spec for k in ("path", "git", "url")):
                    facts.local_py.add(_norm_py(dep))
    sources = uv.get("sources")
    if isinstance(sources, dict):
        facts.local_py.update(_norm_py(k) for k in sources)  # any uv source override is not a plain registry dependency
    if poetry.get("source") or uv.get("index") or uv.get("index-url") or uv.get("extra-index-url") \
            or (isinstance(uv.get("pip"), dict) and (uv["pip"].get("index-url") or uv["pip"].get("extra-index-url"))) \
            or (isinstance(tool.get("pdm"), dict) and tool["pdm"].get("source")):
        pip_hits.append(rel)


@lru_cache(maxsize=1)
def _import_map() -> dict[str, str]:
    path = Path(__file__).resolve().parents[1] / "data" / "import_to_dist.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _installed_import_names() -> set[str]:
    try:
        return set(importlib.metadata.packages_distributions())
    except Exception:
        return set()


def _manifest_where(repo: Path, owners: list[str], ecosystem: str, name: str) -> str:
    candidates = ("requirements.txt", "pyproject.toml") if ecosystem == "PyPI" else ("package.json",)
    first = None
    for owner in owners:
        for candidate in candidates:
            rel = candidate if owner in (".", "") else f"{owner}/{candidate}"
            text = _read(repo, rel)
            if text is None:
                continue
            first = first or rel
            haystack = _norm_py(text) if ecosystem == "PyPI" else text.lower()
            if _norm(ecosystem, name) in haystack:
                return rel
    return first or (owners[0] if owners else ".")


_DIST_START = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")


def _dist_names(lines) -> set[str]:
    found = set()
    for line in lines:
        if isinstance(line, str):
            match = _DIST_START.match(line.strip())
            if match:
                found.add(_norm_py(match.group(0)))
    return found


def _declared_python_names(repo: Path, working: list[str]) -> set[str]:
    """Distribution names declared anywhere in the tree, including files collect_pins does not read."""
    found: set[str] = set()
    for rel in working:
        name = PurePosixPath(rel).name
        if _skipped_dir(rel):
            continue
        try:
            if name.startswith("requirements") and name.endswith(".txt") or (
                    PurePosixPath(rel).parent.name == "requirements" and name.endswith(".txt")):
                found |= _dist_names(l for l in (_read(repo, rel) or "").splitlines()
                                     if not l.strip().startswith(("#", "-")))
            elif name == "pyproject.toml":
                data = _toml(_read(repo, rel))
                project = data.get("project") if isinstance(data.get("project"), dict) else {}
                dependencies = project.get("dependencies")
                found |= _dist_names(dependencies if isinstance(dependencies, list) else [])
                for group in (project.get("optional-dependencies") or {}).values():
                    found |= _dist_names(group if isinstance(group, list) else [])
                for group in (data.get("dependency-groups") or {}).values():
                    found |= _dist_names(group if isinstance(group, list) else [])
                tool = data.get("tool") if isinstance(data.get("tool"), dict) else {}
                poetry = tool.get("poetry") if isinstance(tool.get("poetry"), dict) else {}
                groups = [poetry.get("dependencies"), poetry.get("dev-dependencies")]
                groups += [g.get("dependencies") for g in (poetry.get("group") or {}).values() if isinstance(g, dict)]
                pdm = tool.get("pdm") if isinstance(tool.get("pdm"), dict) else {}
                for group in (pdm.get("dev-dependencies") or {}).values():
                    found |= _dist_names(group if isinstance(group, list) else [])
                for deps in groups:
                    if isinstance(deps, dict):
                        found |= _dist_names(deps)
            elif name == "Pipfile":
                data = _toml(_read(repo, rel))
                for key in ("packages", "dev-packages"):
                    if isinstance(data.get(key), dict):
                        found |= _dist_names(data[key])
            elif name == "setup.cfg":
                text = _read(repo, rel) or ""
                found |= _dist_names(
                    l for m in re.finditer(r"(?ms)^(?:install_requires|[\w-]+)\s*=\s*\n((?:[ \t]+.*\n?)+)", text)
                    for l in m.group(1).splitlines() if l.strip())
        except Exception:  # unparseable files are skipped silently
            continue
    return found


# ---- main entry point -------------------------------------------------------------------

def added_dependencies(repo: Path, base: str = "HEAD") -> tuple[list[DepRef], list[NotChecked]]:
    repo = Path(repo)
    has_base = _has_ref(repo, base)
    working = _working_files(repo)
    facts = _gather(repo, working)
    changed = _changed_files(repo, base, has_base, working)

    pins_now: list = []
    owners_by_pin: dict = {}
    not_checked: list[NotChecked] = []
    base_pins: list = []
    if any(_touches_dependencies(rel) for rel in changed):
        try:
            pins_now, owners_by_pin, _ = collect_pins(repo)
        except Exception:
            not_checked.append(NotChecked("dependency manifests", "a dependency manifest could not be read, so new dependencies were not checked"))
        base_pins = _base_pins(repo, base, has_base)
    # With no manifest in the change, import-only additions are judged against the names the manifests in the
    # working tree declare (read directly by _declared_python_names and _gather), not against parsed pins.
    known = {(eco, _norm(eco, name)) for name, _v, eco in base_pins}
    declared_py = {_norm_py(name) for name, _v, eco in pins_now + base_pins if eco == "PyPI"}
    declared_py |= _declared_python_names(repo, working)
    declared_npm = {name.lower() for name, _v, eco in pins_now + base_pins if eco == "npm"} | facts.declared_npm

    candidates: dict[tuple[str, str], DepRef] = {}
    unsupported: dict[str, list[str]] = {}
    for pin in pins_now:
        name, _version, eco = pin
        key = (eco, _norm(eco, name))
        if key in known:
            continue
        if eco == "PyPI":
            if key[1] in facts.local_py:
                continue
        elif eco == "npm":
            # Lock files list transitive packages; only names a package.json declares count.
            if key[1] not in facts.declared_npm or key[1] in facts.local_npm:
                continue
        else:
            unsupported.setdefault(eco, []).append(name)
            continue
        candidates.setdefault(key, DepRef(eco, key[1] if eco == "PyPI" else name, "manifest",
                                          _manifest_where(repo, owners_by_pin.get(pin, ["."]), eco, name)))

    for eco, names in unsupported.items():
        label = _ECOSYSTEM_LABEL.get(eco, eco)
        shown = names[0] if len(names) == 1 else f"{names[0]} and {len(names) - 1} more"
        not_checked.append(NotChecked(shown, f"{label} ({eco}) packages are not checked yet"))

    import_map = _import_map()
    installed = _LazyNames(_installed_import_names)
    for rel in changed:
        suffix = PurePosixPath(rel).suffix.lower()
        is_py, is_js = suffix in imports.PY_SUFFIXES, suffix in imports.JS_SUFFIXES
        if not (is_py or is_js) or _skipped_dir(rel):
            continue
        text = _read(repo, rel)
        if text is None:
            continue
        parse = imports.python_imports if is_py else imports.js_imports
        new_names = parse(text) - (parse(_base_text(repo, base, rel)) if has_base else set())
        for imp in sorted(new_names):
            ref = _import_ref(imp, rel, is_py, facts, import_map, installed, declared_py, declared_npm)
            if ref and (ref.ecosystem, _norm(ref.ecosystem, ref.name)) not in known:
                candidates.setdefault((ref.ecosystem, _norm(ref.ecosystem, ref.name)), ref)

    refs: list[DepRef] = []
    for eco in ("PyPI", "npm"):
        mine = [ref for key, ref in candidates.items() if key[0] == eco]
        if mine and eco in facts.private:
            shown = mine[0].name if len(mine) == 1 else f"{mine[0].name} and {len(mine) - 1} more"
            not_checked.append(NotChecked(shown, facts.private[eco]))
        else:
            refs.extend(mine)
    return refs, not_checked


class _LazyNames:
    """A set computed on first membership test: listing installed distributions costs up to a second, and most
    changes add no import that needs it."""

    def __init__(self, compute):
        self._compute = compute
        self._names: set[str] | None = None

    def __contains__(self, name: object) -> bool:
        if self._names is None:
            self._names = set(self._compute())
        return name in self._names


def _import_ref(imp, rel, is_py, facts, import_map, installed, declared_py, declared_npm) -> DepRef | None:
    if is_py:
        if imp in facts.local_dirs or imp.lower() in facts.local_dirs or imp in imports.PY_NAMESPACE_ROOTS:
            return None
        if imp in installed:
            return None
        dist = import_map.get(imp)
        if dist:
            dist = _norm_py(dist)
            if dist in declared_py or dist in facts.local_py:
                return None
            return DepRef("PyPI", dist, "import", rel)
        name = _norm_py(imp)
        if name in declared_py or name in facts.local_py:
            return None
        # Heuristic: import name and distribution name often differ only by a prefix/suffix
        # (foo-bar provides `foo`), so a declared name containing or contained in it counts as declared.
        if any(len(name) >= 3 and (name in d or d in name) for d in declared_py if len(d) >= 3):
            return None
        return DepRef("PyPI", name, "import", rel)
    lowered = imp.lower()
    bare = lowered.lstrip("@").split("/", 1)[0]
    if lowered in facts.local_npm or lowered in declared_npm or bare in facts.aliases or bare in {d.lower() for d in facts.local_dirs}:
        return None
    return DepRef("npm", imp, "import", rel)
