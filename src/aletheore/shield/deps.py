"""Find dependencies a change newly introduces: manifest additions and import-only additions.

Precision beats coverage: anything uncertain is treated as local (skipped) or reported
as NotChecked with a plain-words reason. This module never contacts a registry.

Cost scales with the change, not the repository:

- The file lists come from one shared `changes.ChangeSet` (three git calls per guard run).
- Manifest pins are parsed per manifest DIRECTORY (a "unit": the manifest files of one folder, which the
  vulnerability check's parsers read together). Only units holding a changed manifest are parsed from the working
  tree. The base side needs every unit (a name declared in any manifest at the base is not new), but each unit is
  keyed by its files' names and blob ids, so its pins are parsed once and kept in a small content-addressed cache in
  the git dir; a unit's blobs come from one `git cat-file --batch`. There is no cap on the number of manifests: when
  the time budget runs out before every unit is known, manifest additions are reported as not checked, never as new.
- The base text of changed source files comes from one `git cat-file --batch`, not one `git show` per file.
"""

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from aletheore.manifests import EXTRA_SKIPPED_DIRS, MANIFEST_FILENAMES, MANIFEST_SUFFIXES
from aletheore.scanner.detect import IGNORED_DIRS
from aletheore.shield import changes as _changes
from aletheore.shield import imports
from aletheore.shield.fsutil import debug_note
from aletheore.shield.gitutil import UNLIMITED, Budget, cat_file_batch, run_git

_CHECKED = {"PyPI": "Python", "npm": "JavaScript"}
_ECOSYSTEM_LABEL = {
    "Go": "Go", "crates.io": "Rust", "Maven": "Java/Kotlin", "RubyGems": "Ruby", "Packagist": "PHP",
    "NuGet": ".NET", "SwiftURL": "Swift",
}
_PUBLIC_NPM_HOSTS = ("registry.npmjs.org", "registry.yarnpkg.com")
_MAX_SOURCE_BYTES = 2_000_000
# Registry configs often hold credentials, so they are usually git-ignored: they are read from disk wherever they sit
# in a folder of the working tree, whether git lists them or not, so a private registry is never missed.
_REGISTRY_CONFIG_NAMES = (".npmrc", ".yarnrc.yml", ".yarnrc.yaml", "pip.conf", "pip.ini")
# Import names that two unrelated distributions provide (pyjwt and jwt, pymongo and bson, ...): with neither declared
# nor installed, which one the change means cannot be told, so the import is reported as not checked.
_AMBIGUOUS_IMPORTS = frozenset({"bson", "jwt", "fitz", "multipart", "magic"})
AMBIGUOUS_REASON = "the import name maps to multiple distributions"
_SKIPPED_DIRS = frozenset(IGNORED_DIRS) | frozenset(EXTRA_SKIPPED_DIRS)
_SOURCE_SUFFIXES = (".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")
_GIT_TIMEOUT_S = 30.0
MIN_STAGE_S = 0.05  # with less time than this left, a stage is not started

PINS_CACHE_NAME = "pins-cache.json"  # in <git dir>/aletheore-shield/
PINS_CACHE_MAX_UNITS = 4000
PINS_CACHE_MAX_BYTES = 16 * 1024 * 1024
_PINS_CACHE_VERSION = 1
STALE_TEMPORARY_S = 3600.0
_SYMLINK_MODE = b"120000"
_STATE_DIRNAME = "aletheore-shield"

TIME_BUDGET_MANIFESTS = "time budget reached; new manifest dependencies were not compared with the base"
TIME_BUDGET_IMPORTS = "time budget reached; new imports were not checked"
GIT_FAILED_MANIFESTS = "git did not answer; new manifest dependencies were not compared with the base"
GIT_FAILED_IMPORTS = "git did not answer; new imports were not checked"
TIME_BUDGET_PACKAGE_JSON = "time budget reached; changed package.json entries were not compared with the base"
GIT_FAILED_PACKAGE_JSON = "git did not answer; changed package.json entries were not compared with the base"
PARTIAL_BASE_IMPORT = "the base manifests were not read in full, so this import could not be proven new"


def _why_stopped(budget: Budget, timed_out: str, failed: str) -> str:
    """A step that could not finish: out of time, or git failing for another reason (same rule as run_guard)."""
    return timed_out if budget.spent(MIN_STAGE_S) else failed


@dataclass(frozen=True)
class DepRef:
    ecosystem: str
    name: str
    source: str  # "manifest" or "import"
    where: str


@dataclass(frozen=True)
class NotChecked:
    name: str  # "" when the reason is about the whole check, not about named dependencies
    reason: str


def _norm_py(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _norm(ecosystem: str, name: str) -> str:
    return _norm_py(name) if ecosystem == "PyPI" else name.lower()


def _skipped_dir(rel: str) -> bool:
    return any(part in _SKIPPED_DIRS for part in rel.split("/")[:-1])


def _name(rel: str) -> str:
    return rel.rpartition("/")[2]


def _is_manifest(rel: str) -> bool:
    name = _name(rel)
    return name in MANIFEST_FILENAMES or name.endswith(MANIFEST_SUFFIXES)


def _read(repo: Path, rel: str) -> str | None:
    try:
        path = repo / rel
        if not path.is_file() or path.stat().st_size > _MAX_SOURCE_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def pins_in_directory(directory: Path) -> list[tuple[str, str, str]]:
    """The pins of one manifest directory, through aletheore.vulnerabilities' parsers, imported on first use (the
    module costs about 100 ms to import and is only needed when a change touches a dependency manifest)."""
    from aletheore.vulnerabilities import directory_pins

    return list(dict.fromkeys(directory_pins(directory)))


def _ignored_patterns(repo: Path) -> list[str]:
    from aletheore.repo_config import load_repo_config

    try:
        return list(load_repo_config(repo).get("ignored_paths") or [])
    except Exception as error:  # noqa: BLE001 - a broken config means "no extra ignores", as in the scanner
        debug_note("could not read .aletheore.json ignored_paths", error)
        return []


def _counted_manifest(rel: str, patterns: list[str]) -> bool:
    """A manifest the vulnerability check would read: not vendored or generated, not in .aletheore.json's
    ignored_paths (the same rules as manifests.discover_manifests; git itself already left out ignored files and
    nested repositories)."""
    if not _is_manifest(rel) or _skipped_dir(rel):
        return False
    if not patterns:
        return True
    from aletheore.repo_config import is_ignored

    return not is_ignored(rel, patterns)


def _unit_of(rel: str) -> tuple[str, str]:
    folder, _, name = rel.rpartition("/")
    return folder or ".", name


# ---- the pins cache (base side) ----------------------------------------------------------


def _unit_key(files: dict[str, str]) -> str:
    """Content address of a unit: its file names and blob ids (the parsers' only inputs)."""
    text = "\n".join(f"{name}\0{blob}" for name, blob in sorted(files.items()))
    return hashlib.sha256(text.encode("utf-8", "surrogateescape")).hexdigest()


def _parser_version() -> str:
    """Stored with the cache: pins parsed by another Aletheore version are never served (its parsers may differ)."""
    from aletheore import __version__

    return str(__version__)


class _PinCache:
    """{unit key: pins} in <git dir>/aletheore-shield/pins-cache.json. An entry never goes stale while the parsers stay
    the same (the key is the content), and the file records the Aletheore version that parsed it: a file from another
    version, or a corrupt, oversized or hostile one, is ignored. Writes are atomic and the file is bounded by
    PINS_CACHE_MAX_UNITS and PINS_CACHE_MAX_BYTES: units parsed by this run are kept first, then the ones it read,
    then the most recent others, and older entries are evicted until it fits, so the cache always catches up."""

    def __init__(self, git_dir: Path | None) -> None:
        self.path = git_dir / _STATE_DIRNAME / PINS_CACHE_NAME if git_dir is not None else None
        self.units: dict[str, list[tuple[str, str, str]]] = {}  # file order: oldest first
        self.used: list[str] = []
        self.parsed: list[str] = []
        if self.path is None:
            return
        try:
            if self.path.stat().st_size > PINS_CACHE_MAX_BYTES:
                return
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("v") != _PINS_CACHE_VERSION \
                    or data.get("parser") != _parser_version() or not isinstance(data.get("units"), dict):
                return
            for key, pins in data["units"].items():
                if isinstance(key, str) and isinstance(pins, list) and all(
                        isinstance(p, list) and len(p) == 3 and all(isinstance(x, str) for x in p) for p in pins):
                    self.units[key] = [tuple(p) for p in pins]
        except FileNotFoundError:
            self.units = {}
        except Exception as error:  # noqa: BLE001 - unreadable, corrupt or hostile: start empty
            debug_note("ignoring an unreadable pins cache", error)
            self.units = {}

    def get(self, key: str) -> list[tuple[str, str, str]] | None:
        pins = self.units.get(key)
        if pins is not None:
            self.used.append(key)
        return pins

    def put(self, key: str, pins: list[tuple[str, str, str]]) -> None:
        self.units[key] = pins
        self.parsed.append(key)

    def save(self) -> None:
        if self.path is None or not self.parsed:
            return
        self._remove_stale_temporaries()
        priority = list(dict.fromkeys([*self.parsed, *self.used, *reversed(list(self.units))]))
        header = {"v": _PINS_CACHE_VERSION, "parser": _parser_version(), "units": {}}
        size = len(json.dumps(header, separators=(",", ":")))
        kept: list[str] = []
        for key in priority:
            if len(kept) >= PINS_CACHE_MAX_UNITS:
                break
            entry = len(json.dumps({key: [list(p) for p in self.units[key]]}, separators=(",", ":"))) - 1
            if size + entry > PINS_CACHE_MAX_BYTES:
                continue  # evicted; a smaller entry further down may still fit
            size += entry
            kept.append(key)
        header["units"] = {key: [list(p) for p in self.units[key]] for key in reversed(kept)}  # most valuable last
        text = json.dumps(header, separators=(",", ":"))
        temporary = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
        try:
            self.path.parent.mkdir(exist_ok=True)  # inside the git dir, which exists
            temporary.write_text(text, encoding="utf-8")
            os.replace(temporary, self.path)
        except OSError as error:
            debug_note("could not save the pins cache", error)
            try:
                temporary.unlink()
            except OSError as cleanup:
                debug_note("could not remove a temporary file", cleanup)

    def _remove_stale_temporaries(self) -> None:
        """Temporary files a killed hook left behind (the hard stop can end a process mid-write)."""
        import time

        cutoff = time.time() - STALE_TEMPORARY_S
        try:
            for leftover in self.path.parent.glob(f"{PINS_CACHE_NAME}.*.tmp"):
                try:
                    if leftover.is_file() and not leftover.is_symlink() and leftover.stat().st_mtime < cutoff:
                        leftover.unlink()
                except OSError as error:
                    debug_note("could not remove a stale temporary file", error)
        except OSError as error:
            debug_note("could not list the pins cache folder", error)


# ---- manifest pins, base and working side --------------------------------------------------


def _base_units(repo: Path, base_commit: str, patterns: list[str], budget: Budget) -> dict[str, dict[str, str]] | None:
    """{unit dir: {file name: blob id}} for every counted manifest at the base commit; None when git did not answer."""
    listing = run_git(repo, "ls-tree", "-r", "-z", "--full-tree", base_commit,
                      timeout=budget.timeout(_GIT_TIMEOUT_S))
    if listing.returncode != 0:
        return None
    units: dict[str, dict[str, str]] = {}
    for entry in listing.stdout.split(b"\0"):
        meta, tab, raw = entry.partition(b"\t")
        fields = meta.split(b" ")
        if not tab or len(fields) != 3 or fields[1] != b"blob" or fields[0] == _SYMLINK_MODE:
            continue  # a symbolic link's blob is its target path, not a manifest: left out on both sides
        rel = raw.decode("utf-8", "replace")
        if _counted_manifest(rel, patterns):
            folder, name = _unit_of(rel)
            units.setdefault(folder, {})[name] = fields[2].decode("ascii", "replace")
    return units


def _parse_into(root: Path, label: str, files: dict[str, bytes]) -> list[tuple[str, str, str]]:
    unit = root / label
    unit.mkdir()
    for name, data in files.items():
        (unit / name).write_bytes(data)
    return pins_in_directory(unit)


def _base_unit_pins(
    repo: Path, units: dict[str, dict[str, str]], cache: _PinCache, budget: Budget
) -> tuple[dict[str, list[tuple[str, str, str]]], bool]:
    """Pins of every base unit (cached, or parsed from one cat-file batch) and whether every unit is known. A unit
    whose manifests cannot be parsed counts as declaring nothing, as the vulnerability check treats it."""
    found: dict[str, list[tuple[str, str, str]]] = {}
    missing: dict[str, dict[str, str]] = {}
    for folder, files in units.items():
        pins = cache.get(_unit_key(files))
        if pins is None:
            missing[folder] = files
        else:
            found[folder] = pins
    if not missing:
        return found, True
    if budget.spent(MIN_STAGE_S):
        return found, False
    blobs = cat_file_batch(repo, [b for files in missing.values() for b in files.values()],
                           timeout=budget.timeout(_GIT_TIMEOUT_S))
    if blobs is None:
        return found, False
    complete = True
    with tempfile.TemporaryDirectory(prefix="aletheore-shield-pins-") as tmp:
        for index, (folder, files) in enumerate(missing.items()):
            if budget.spent(MIN_STAGE_S):
                complete = False
                break
            try:
                pins = _parse_into(Path(tmp), str(index), {n: blobs.get(b, b"") for n, b in files.items()})
            except Exception as error:  # noqa: BLE001 - an unreadable manifest declares nothing
                debug_note("could not parse the base manifests of a folder", error)
                pins = []
            cache.put(_unit_key(files), pins)
            found[folder] = pins
    return found, complete


def _working_unit_pins(
    repo: Path, folders: list[str], names_by_folder: dict[str, set[str]], budget: Budget
) -> tuple[dict[str, list[tuple[str, str, str]]], list[str], bool]:
    """Pins of the changed units as they are in the working tree, the units that could not be read, and whether every
    unit was parsed in time."""
    found: dict[str, list[tuple[str, str, str]]] = {}
    unreadable: list[str] = []
    with tempfile.TemporaryDirectory(prefix="aletheore-shield-pins-") as tmp:
        for index, folder in enumerate(folders):
            if budget.spent(MIN_STAGE_S):
                return found, unreadable, False
            unit = Path(tmp) / str(index)
            unit.mkdir()
            try:
                for name in names_by_folder.get(folder, ()):
                    rel = name if folder == "." else f"{folder}/{name}"
                    if os.path.islink(repo / rel):
                        continue  # as on the base side: a linked manifest belongs to its target's folder
                    shutil.copyfile(repo / rel, unit / name)
                found[folder] = pins_in_directory(unit)
            except Exception as error:  # noqa: BLE001
                debug_note("could not parse the manifests of a folder", error)
                unreadable.append(folder)
    return found, unreadable, True


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
    npm_alias: dict[str, str] = field(default_factory=dict)  # "short": "npm:@acme/real@^1" -> short: @acme/real
    npm_remote: set[str] = field(default_factory=set)  # git host or URL specs: not registry packages


def _with_registry_configs(repo: Path, working: list[str]) -> list[str]:
    """`working` plus every registry config git does not list (an ignored .npmrc or pip.conf) in a folder of the
    working tree or one above it, outside skipped folders."""
    folders = {"."}
    for rel in working:
        folder = rel.rpartition("/")[0]
        while folder and folder not in folders:
            folders.add(folder)
            folder = folder.rpartition("/")[0]
    listed = set(working)
    extra = []
    for folder in sorted(folders):
        if folder != "." and any(part in _SKIPPED_DIRS for part in folder.split("/")):
            continue
        for name in _REGISTRY_CONFIG_NAMES:
            rel = name if folder == "." else f"{folder}/{name}"
            if rel not in listed and os.path.isfile(repo / rel):
                extra.append(rel)
    return [*working, *extra] if extra else working


def _gather(repo: Path, working: list[str]) -> _Facts:
    facts = _Facts(set(), set(), set(), set(), set(), {})
    pip_hits: list[str] = []
    npm_hits: list[str] = []
    folders_seen: set[str] = set()
    for rel in _with_registry_configs(repo, working):
        folder, _, name = rel.rpartition("/")
        skipped = False
        if folder:
            parts = folder.split("/")
            skipped = any(part in _SKIPPED_DIRS for part in parts)
            if not skipped and folder not in folders_seen:
                folders_seen.add(folder)
                # Deliberately broad (favours precision): any directory name or module stem anywhere in the repo
                # counts as local, which may hide a real import of a package whose name matches a local folder.
                facts.local_dirs.update(parts)
        if not skipped and name.endswith(_SOURCE_SUFFIXES):
            facts.local_dirs.add(name.rpartition(".")[0])
        if name == "package.json" and not skipped:
            data = _json(_read(repo, rel))
            if isinstance(data.get("name"), str):
                facts.local_npm.add(data["name"].lower())
            for key in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
                section = data.get(key)
                if isinstance(section, dict):
                    facts.declared_npm.update(n.lower() for n in section if isinstance(n, str))
                    _npm_specs(section, facts)
        elif name == "pyproject.toml" and not skipped:
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
        elif name in ("tsconfig.json", "jsconfig.json") and not skipped:
            paths = (_json(re.sub(r"(?m)^\s*//.*$", "", _read(repo, rel) or "")).get("compilerOptions") or {}).get("paths")
            if isinstance(paths, dict):
                for key in paths:
                    facts.aliases.add(re.split(r"[/*]", key.lstrip("@"), maxsplit=1)[0].lower())
    if pip_hits:
        facts.private["PyPI"] = f"a private package index is configured ({pip_hits[0]}), so new Python packages cannot be checked against the public index"
    if npm_hits:
        facts.private["npm"] = f"a private npm registry is configured ({npm_hits[0]}), so new JavaScript packages cannot be checked against the public registry"
    return facts


# Version specs that do not name a registry package: a path, a link, a workspace, a git host or a tarball URL.
# Version specs that do not name a registry package. A path (only "./", "../", "~/", "/" or a drive: "~1.0.0" is a
# version range), a link or a workspace is local. A git host, "user/repo" shorthand or a URL is remote: not from the
# registry, so it is reported as not checked rather than looked up.
_NPM_LOCAL = re.compile(r"^(?:file:|link:|workspace:|portal:|patch:|\.{1,2}(?:/|\\|$)|~[/\\]|/|\\|[A-Za-z]:[/\\])")
_NPM_REMOTE = re.compile(r"^(?:git[+:]|git@|github:|gitlab:|bitbucket:|gist:|https?:|[\w.-]+/[\w.-]+(?:#.*)?$)")
_NPM_SECTIONS = ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")


def _npm_spec_kind(spec: str) -> tuple[str, str]:
    """("alias", real name) | ("local", "") | ("remote", "") | ("registry", "") for a package.json version spec."""
    spec = spec.strip()
    if spec.startswith("npm:"):
        target = spec[4:]
        at = target.find("@", 1)  # the version separator; a scoped name starts with its own "@"
        return ("alias", target[:at] if at > 0 else target) if target else ("registry", "")
    if _NPM_LOCAL.match(spec):
        return "local", ""
    if _NPM_REMOTE.match(spec):
        return "remote", ""
    return "registry", ""


def _npm_entries(data: dict) -> dict[str, str]:
    """{name: spec} of every dependency section of one package.json."""
    found: dict[str, str] = {}
    for key in _NPM_SECTIONS:
        section = data.get(key)
        if isinstance(section, dict):
            found.update({n: v for n, v in section.items() if isinstance(n, str) and isinstance(v, str)})
    return found


def _npm_specs(section: dict, facts: _Facts) -> None:
    """package.json entries that are not the registry package of that name: an `npm:` alias names the real package
    (`"short": "npm:@acme/real@^1"`); a path, link or workspace spec is local; a git or URL spec is remote. None of
    them is looked up under its own name."""
    for name, spec in section.items():
        if not isinstance(name, str) or not isinstance(spec, str):
            continue
        kind, target = _npm_spec_kind(spec)
        if kind == "alias":
            facts.npm_alias[name.lower()] = target
        elif kind == "local":
            facts.local_npm.add(name.lower())
        elif kind == "remote":
            facts.npm_remote.add(name.lower())


REMOTE_NPM_REASON = "a git or URL dependency, not from the npm registry, so it was not checked"


def _package_json_changes(
    repo: Path, change: "_changes.ChangeSet", patterns: list[str], known: set, local: set[str], budget: Budget
) -> tuple[list[DepRef], list[NotChecked]]:
    """Dependencies a changed package.json declares now and did not declare at the base, read from the file itself
    (a lock file may be missing, stale, or list an alias under its local name):

    - a registry entry is new when its name was not declared there before and is not a known base pin;
    - an `npm:` alias is new when it points to another package than before (checked under the real name);
    - a git or URL entry that is new or changed is reported as not checked; path, link and workspace entries are
      local."""
    rels = [rel for rel in change.present_changed if _name(rel) == "package.json" and _counted_manifest(rel, patterns)]
    if not rels:
        return [], []
    base: dict[str, bytes] = {}
    if change.has_base:
        untracked = set(change.untracked)
        asked = {f"{change.base_commit}:{rel}": rel for rel in rels if rel not in untracked}
        found = cat_file_batch(repo, list(asked), timeout=budget.timeout(_GIT_TIMEOUT_S)) if asked else {}
        if found is None:
            unread = sorted(asked.values())
            shown = unread[0] if len(unread) == 1 else f"{unread[0]} and {len(unread) - 1} more"
            return [], [NotChecked(shown, _why_stopped(budget, TIME_BUDGET_PACKAGE_JSON, GIT_FAILED_PACKAGE_JSON))]
        base = {asked[name]: data for name, data in found.items()}
    refs: list[DepRef] = []
    not_checked: list[NotChecked] = []
    for rel in rels:
        now = _npm_entries(_json(_read(repo, rel)))
        before = _npm_entries(_json(base[rel].decode("utf-8", "replace"))) if rel in base else {}
        for name, spec in now.items():
            kind, target = _npm_spec_kind(spec)
            old = before.get(name)
            if kind == "local" or (kind == "registry" and name.lower() in local):  # a workspace package
                continue
            if kind == "remote":
                if old is None or old.strip() != spec.strip():
                    not_checked.append(NotChecked(name, REMOTE_NPM_REASON))
                continue
            real = target if kind == "alias" else name
            if old is not None:
                old_kind, old_target = _npm_spec_kind(old)
                if old_kind != "remote" and (old_target if old_kind == "alias" else name) == real:
                    continue  # the same package as before, perhaps another version (a git or URL spec is not)
            if ("npm", real.lower()) in known:
                continue
            refs.append(DepRef("npm", real, "manifest", rel))
    return refs, not_checked


def _npmrc_private(text: str) -> bool:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(("#", ";")) or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key.startswith("@") and key.endswith(":registry"):
            return True
        if key == "registry":
            try:
                public = urlsplit(value).hostname in _PUBLIC_NPM_HOSTS
            except ValueError:
                public = False
            if not public:  # compared by host name: registry.npmjs.org.corp.example is not the public registry
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
def _import_map() -> dict[str, tuple[str, ...]]:
    """{import name: distributions that provide it, the usual one first}. In the data file a value is one name or a
    list of names (cv2 comes from opencv-python, opencv-python-headless, ...); any declared one satisfies the import."""
    path = Path(__file__).resolve().parents[1] / "data" / "import_to_dist.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    found: dict[str, tuple[str, ...]] = {}
    for key, value in data.items() if isinstance(data, dict) else ():
        names = [value] if isinstance(value, str) else value
        if isinstance(key, str) and isinstance(names, list) and names and all(isinstance(n, str) for n in names):
            found[key] = tuple(names)
    return found


def _real_installed_import_names() -> set[str]:
    """Top-level import names of the installed distributions, plus `root.sub` for namespace roots (google.protobuf),
    which is how the import check names those imports."""
    import importlib.metadata

    try:
        mapping = importlib.metadata.packages_distributions()
    except Exception as error:  # noqa: BLE001 - a broken installed distribution must not stop the check
        debug_note("could not list the installed distributions", error)
        return set()
    names = set(mapping)
    for root in imports.PY_NAMESPACE_ROOTS & names:
        for dist in set(mapping[root]):
            try:
                files = importlib.metadata.distribution(dist).files or []
            except Exception as error:  # noqa: BLE001
                debug_note("could not list the files of an installed distribution", error)
                continue
            for file in files:
                parts = file.parts
                if len(parts) >= 2 and parts[0] == root and not parts[1].startswith("__"):
                    names.add(f"{root}.{parts[1].removesuffix('.py')}")
    return names


def _installed_import_names() -> set[str]:
    return _real_installed_import_names()


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
_EGG = re.compile(r"[#&]egg=([A-Za-z0-9][A-Za-z0-9._-]*)")
# A requirement line that is a location rather than a name: a VCS or archive URL, or a local path.
_LOCATION = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.-]*://|(?:git|hg|svn|bzr)\+|[./~]|[A-Za-z]:[\\/])")


def _dist_names(lines) -> set[str]:
    """The distribution names requirement lines declare. A URL or path line declares only its `#egg=` name (never the
    scheme, so `git+https://...` does not declare "git"); a name followed by `@ url` declares the name."""
    found = set()
    for line in lines:
        if isinstance(line, str):
            line = line.strip()
            if _LOCATION.match(line):
                egg = _EGG.search(line)
                if egg:
                    found.add(_norm_py(egg.group(1)))
                continue
            match = _DIST_START.match(line)
            if match:
                found.add(_norm_py(match.group(0)))
    return found


_EDITABLE = re.compile(r"^(?:-e|--editable)(?:\s+|=)")


def _requirement_lines(text: str):
    """Requirement lines of a requirements file: comments and pip options are left out, except that an editable
    install (`-e` / `--editable`) is read for the `#egg=` name its location carries."""
    for line in text.splitlines():
        line = line.strip()
        editable = _EDITABLE.match(line)
        if editable:
            yield line[editable.end():].strip()
        elif line and not line.startswith(("#", "-")):
            yield line


_SETUP_CFG_KEYS = ("install_requires", "setup_requires", "tests_require")


def _setup_cfg_names(text: str) -> set[str]:
    """Names setup.cfg declares in [options] install_requires / setup_requires / tests_require and in every
    [options.extras_require] entry, as setuptools reads them: a value on several lines has one requirement per line
    (markers after ";"), a value on one line is ";"-separated. Other keys (entry points, tool settings) declare
    nothing."""
    import configparser

    parser = configparser.ConfigParser(interpolation=None, strict=False, delimiters=("=",))
    parser.optionxform = str  # keys keep their case; extras names are not requirements anyway
    try:
        parser.read_string(text)
    except configparser.Error:
        return set()
    values = []
    if parser.has_section("options"):
        values += [parser.get("options", key) for key in _SETUP_CFG_KEYS if parser.has_option("options", key)]
    if parser.has_section("options.extras_require"):
        values += [value for _, value in parser.items("options.extras_require")]
    found: set[str] = set()
    for value in values:
        value = value.strip()
        lines = value.splitlines() if "\n" in value else value.split(";")
        found |= _dist_names(line.split(";", 1)[0] for line in lines if not line.strip().startswith("#"))
    return found


def _declared_python_names(repo: Path, working: list[str]) -> set[str]:
    """Distribution names declared anywhere in the tree, including files collect_pins does not read."""
    found: set[str] = set()
    for rel in working:
        folder, _, name = rel.rpartition("/")
        is_requirements = name.endswith(".txt") and (
            name.startswith("requirements") or folder.rpartition("/")[2] == "requirements")
        if not (is_requirements or name in ("pyproject.toml", "Pipfile", "setup.cfg")) or _skipped_dir(rel):
            continue
        try:
            if is_requirements:
                found |= _dist_names(_requirement_lines(_read(repo, rel) or ""))
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
                found |= _setup_cfg_names(_read(repo, rel) or "")
        except Exception as error:  # noqa: BLE001 - an unparseable file declares nothing
            debug_note("could not read the declared names in a manifest", error)
            continue
    return found


# ---- main entry point -------------------------------------------------------------------

def _manifest_pins(
    repo: Path, change: "_changes.ChangeSet", working: list[str], budget: Budget, not_checked: list[NotChecked]
) -> tuple[list, dict, list, bool]:
    """(pins in the changed units now, owners of each such pin, pins of every unit at the base, complete).

    Only called when a counted manifest changed. `complete` is False when the base could not be fully known (time
    budget, git failure) or a changed unit was not parsed: the caller then reports no manifest addition at all,
    since a name missing from an incomplete base would be a false "new dependency"."""
    patterns = _ignored_patterns(repo)
    names_by_folder: dict[str, set[str]] = {}
    for rel in working:
        if _counted_manifest(rel, patterns):
            folder, name = _unit_of(rel)
            names_by_folder.setdefault(folder, set()).add(name)
    changed_folders = list(dict.fromkeys(
        _unit_of(rel)[0] for rel in change.changed if _counted_manifest(rel, patterns)))
    if not changed_folders:  # every changed manifest is in ignored_paths
        return [], {}, [], True
    cache = _PinCache(change.git_dir)
    base_pins: list = []
    complete = True
    try:
        if change.has_base:
            units = _base_units(repo, change.base_commit, patterns, budget)
            if units is None:
                complete = False
            else:
                by_unit, complete = _base_unit_pins(repo, units, cache, budget)
                base_pins = [pin for folder in sorted(by_unit, key=_shallow_first) for pin in by_unit[folder]]
    finally:
        cache.save()  # keep whatever was parsed, even when the budget ran out: the next run continues from there
    if not complete:
        not_checked.append(NotChecked("", _why_stopped(budget, TIME_BUDGET_MANIFESTS, GIT_FAILED_MANIFESTS)))
        return [], {}, base_pins, False
    now_by_unit, unreadable, parsed_all = _working_unit_pins(repo, changed_folders, names_by_folder, budget)
    if not parsed_all:
        not_checked.append(NotChecked("", TIME_BUDGET_MANIFESTS))
        return [], {}, base_pins, False
    if unreadable:
        not_checked.append(NotChecked(
            "dependency manifests", "a dependency manifest could not be read, so new dependencies were not checked"))
    pins_now: list = []
    owners: dict = {}
    for folder in sorted(now_by_unit, key=_shallow_first):
        for pin in now_by_unit[folder]:
            holders = owners.setdefault(pin, [])
            if not holders:
                pins_now.append(pin)
            holders.append(folder)
    return pins_now, owners, base_pins, True


def _shallow_first(folder: str) -> tuple[int, str]:
    return (0, "") if folder == "." else (1 + folder.count("/"), folder)


def _base_sources(repo: Path, change: "_changes.ChangeSet", rels: list[str], budget: Budget) -> dict[str, bytes] | None:
    """Base content of changed source files from one cat-file batch: {rel: bytes}, a file absent at the base is
    missing from the dict. None when git did not answer (in time)."""
    if not change.has_base or not rels:
        return {}
    untracked = set(change.untracked)
    asked = {f"{change.base_commit}:{rel}": rel for rel in rels if rel not in untracked}
    found = cat_file_batch(repo, list(asked), timeout=budget.timeout(_GIT_TIMEOUT_S))
    if found is None:
        return None
    texts = {asked[name]: data for name, data in found.items()}
    for name, rel in asked.items():
        # The batch protocol is line based, so a path holding a newline is asked for on its own.
        if "\n" in name or "\r" in name:
            shown = run_git(repo, "show", name, timeout=budget.timeout(_GIT_TIMEOUT_S))
            if shown.returncode == 0:
                texts[rel] = shown.stdout
    return texts


def added_dependencies(
    repo: Path, base: str = "HEAD", *, change: "_changes.ChangeSet | None" = None, budget: Budget = UNLIMITED
) -> tuple[list[DepRef], list[NotChecked]]:
    """New dependencies in the working tree compared with `base`. `change` is the guard run's shared file listing
    (computed here when not given); `budget` bounds every git call and stage, and what it cuts short is reported as
    a NotChecked with an empty name."""
    repo = Path(repo)
    if change is None:
        change = _changes.compute(repo, base, budget=budget)
    not_checked: list[NotChecked] = []
    if not change.complete:
        return [], [NotChecked("", "git did not list the changed files in time; new dependencies were not checked")]
    working = change.working
    facts = _gather(repo, working)
    changed = change.present_changed

    pins_now: list = []
    owners_by_pin: dict = {}
    base_pins: list = []
    npm_refs: list[DepRef] = []
    base_complete = True  # False when the base manifests were only partly read (budget or git)
    if any(_is_manifest(rel) and not _skipped_dir(rel) for rel in change.changed):
        pins_now, owners_by_pin, base_pins, complete = _manifest_pins(repo, change, working, budget, not_checked)
        base_complete = complete
        if complete:
            base_known = {(eco, _norm(eco, name)) for name, _v, eco in base_pins}
            npm_refs, remote = _package_json_changes(
                repo, change, _ignored_patterns(repo), base_known, facts.local_npm, budget)
            not_checked.extend(remote)
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
            if key[1] in facts.npm_alias or key[1] in facts.npm_remote:
                continue  # not the registry package of that name: _package_json_changes judges these entries
        else:
            unsupported.setdefault(eco, []).append(name)
            continue
        candidates.setdefault(key, DepRef(eco, key[1] if eco == "PyPI" else name, "manifest",
                                          _manifest_where(repo, owners_by_pin.get(pin, ["."]), eco, name)))

    for ref in npm_refs:
        candidates.setdefault(("npm", ref.name.lower()), ref)

    for eco, names in unsupported.items():
        label = _ECOSYSTEM_LABEL.get(eco, eco)
        shown = names[0] if len(names) == 1 else f"{names[0]} and {len(names) - 1} more"
        not_checked.append(NotChecked(shown, f"{label} ({eco}) packages are not checked yet"))

    sources = [rel for rel in changed
               if rel.lower().endswith(imports.PY_SUFFIXES + imports.JS_SUFFIXES) and not _skipped_dir(rel)]
    if sources and budget.spent(MIN_STAGE_S):
        not_checked.append(NotChecked("", TIME_BUDGET_IMPORTS))
        sources = []
    base_texts = _base_sources(repo, change, sources, budget) if sources else {}
    if base_texts is None:
        not_checked.append(NotChecked("", _why_stopped(budget, TIME_BUDGET_IMPORTS, GIT_FAILED_IMPORTS)))
        sources, base_texts = [], {}
    import_map = _import_map()
    installed = _LazyNames(_installed_import_names)
    unproven: dict[str, None] = {}
    for rel in sources:
        is_py = rel.lower().endswith(imports.PY_SUFFIXES)
        text = _read(repo, rel)
        if text is None:
            continue
        before = base_texts.get(rel)
        if is_py:
            parse = imports.python_imports
        else:
            # Both sides together get at most JS_PARSE_TIMEOUT_S, and never more than the budget has left.
            parse_deadline = time.monotonic() + min(imports.JS_PARSE_TIMEOUT_S, max(budget.left(), MIN_STAGE_S))

            def parse(source: str) -> set[str]:
                return imports.js_imports(source, timeout=max(parse_deadline - time.monotonic(), 0.001), repo=repo)
        try:
            new_names = parse(text) - (parse(before.decode("utf-8", "replace")) if before is not None else set())
        except imports.ParseTimeout:
            not_checked.append(NotChecked("", f"imports in {rel} were not checked: it could not be parsed in time"))
            continue
        for imp in sorted(new_names):
            if is_py and imp in _AMBIGUOUS_IMPORTS and imp not in installed and not any(
                    _norm_py(d) in declared_py for d in (*import_map.get(imp, ()), imp)):
                not_checked.append(NotChecked(imp, AMBIGUOUS_REASON))
                continue
            ref = _import_ref(imp, rel, is_py, facts, import_map, installed, declared_py, declared_npm)
            if ref and (ref.ecosystem, _norm(ref.ecosystem, ref.name)) not in known:
                if not base_complete:  # it may be declared in a base manifest that was not read
                    unproven.setdefault(ref.name, None)
                    continue
                candidates.setdefault((ref.ecosystem, _norm(ref.ecosystem, ref.name)), ref)
    for name in unproven:
        not_checked.append(NotChecked(name, PARTIAL_BASE_IMPORT))

    refs: list[DepRef] = []
    for eco in ("PyPI", "npm"):
        mine = [ref for key, ref in candidates.items() if key[0] == eco]
        if mine and eco in facts.private:
            shown = mine[0].name if len(mine) == 1 else f"{mine[0].name} and {len(mine) - 1} more"
            not_checked.append(NotChecked(shown, facts.private[eco]))
        else:
            refs.extend(mine)
    return refs, list(dict.fromkeys(not_checked))  # one stage's failure is said once, in the order found


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
        root = imp.split(".")[0]
        if root in facts.local_dirs or root.lower() in facts.local_dirs or imp in imports.PY_NAMESPACE_ROOTS:
            return None
        if imp in installed:
            return None
        dists = import_map.get(imp)
        if dists:
            normed = [_norm_py(d) for d in dists]
            if any(d in declared_py or d in facts.local_py for d in normed):
                return None
            return DepRef("PyPI", normed[0], "import", rel)
        if root in imports.PY_NAMESPACE_ROOTS:
            return None  # google.cloud and the like: many distributions, the name alone does not say which
        name = _norm_py(imp)
        if name in declared_py or name in facts.local_py:
            return None
        # A declared foo-bar may provide `foo`; a declared foo does not cover a new foo-bar (or foo_extra) import, and a
        # declared foo-bar does not cover a new `bar`.
        if len(name) >= 3 and any(d.startswith(name + "-") for d in declared_py):
            return None
        return DepRef("PyPI", name, "import", rel)
    lowered = imp.lower()
    bare = lowered.lstrip("@").split("/", 1)[0]
    if lowered in facts.local_npm or lowered in declared_npm or bare in facts.aliases or bare in {d.lower() for d in facts.local_dirs}:
        return None
    return DepRef("npm", imp, "import", rel)
