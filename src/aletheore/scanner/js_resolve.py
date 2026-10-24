"""Resolution of non-relative JS/TS import specifiers to files inside the repo.

Covers the three ways a bare specifier can name a file of the scanned repository itself:
  * a workspace / self-referenced package (`zod/v4`, `axios`), found through the `name` of the repo's own
    package.json files and resolved through their `exports` (or `main`) with Node-style conditions;
  * tsconfig / jsconfig `paths` (and `baseUrl`);
  * custom conditions declared in a tsconfig (`customConditions`) or a zshy block, tried before the standard ones.

Anything that does not land on an existing, tracked-looking file inside the repo returns None (an external
dependency). Config is read once per repo path and cached; build_module_graph calls clear_cache() per scan.
"""

from __future__ import annotations

import json
import os
from pathlib import Path, PureWindowsPath

_SKIP_DIRS = {"node_modules", ".git", ".aletheore", "dist", "build", ".next", "coverage", "__pycache__"}
_CONFIG_NAMES = ("tsconfig.json", "jsconfig.json")
_STD_CONDITIONS = ("import", "default", "node", "require")
_EXTS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts")
_CACHE: dict[str, "_RepoConfig"] = {}


def clear_cache() -> None:
    _CACHE.clear()


def _strip_jsonc(text: str) -> str:
    out = []
    i, n, in_str = 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        elif c in "}]":
            # trailing comma: only ever looked for outside string literals (this branch is never reached
            # inside one), so a string such as "done,}" is left alone
            j = len(out) - 1
            while j >= 0 and out[j].isspace():
                j -= 1
            if j >= 0 and out[j] == ",":
                del out[j]
            out.append(c)
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(_strip_jsonc(path.read_text(encoding="utf-8", errors="ignore")))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


class _TsConfig:
    """`paths` / `baseUrl` of one config. Per the TS rules, `paths` are relative to baseUrl if set, else to
    the config that declares them."""

    def __init__(self, directory: Path, options: dict):
        self.directory = directory
        self.paths: dict[str, list[str]] = options.get("paths") or {}
        base_url = options.get("baseUrl")
        self.has_base_url = bool(base_url)
        self.base_dir = Path(base_url) if base_url else Path(options.get("_base_dir") or directory)


def _load_tsconfig(path: Path, seen: set[Path] | None = None) -> dict:
    """compilerOptions of a config with its relative `extends` chain merged (child wins)."""
    seen = seen or set()
    if path in seen:
        return {}
    seen.add(path)
    data = _read_json(path)
    raw_options = data.get("compilerOptions")
    options = _sanitize_options(raw_options if isinstance(raw_options, dict) else {})
    parent = data.get("extends")
    parents = [parent] if isinstance(parent, str) else [p for p in parent if isinstance(p, str)] if isinstance(parent, list) else []
    merged: dict = {}
    for parent in parents:
        if not parent.startswith("."):
            continue
        pp = (path.parent / parent).resolve()
        if not pp.suffix:
            pp = pp.with_suffix(".json")
        parent_opts = _load_tsconfig(pp, seen)
        merged.update(parent_opts)
        # paths/baseUrl inherited from a parent are relative to that parent
        if "paths" in parent_opts and "paths" not in options:
            merged["_base_dir"] = parent_opts.get("_base_dir", pp.parent)
    merged.update(options)
    if "paths" in options:
        merged["_base_dir"] = path.parent
    if "baseUrl" in options and not _is_absolute(options["baseUrl"]):
        merged["baseUrl"] = str((path.parent / options["baseUrl"]).resolve())
    return merged


def _is_absolute(value: str) -> bool:
    """POSIX or Windows absolute path ("/x", "C:/repo/src", "C:\\repo", "\\\\host\\share").

    A leading slash counts on every platform: tsconfig files are written with POSIX paths, and from Python 3.13 on
    Windows os.path.isabs("/x") is False (no drive), which would join the value onto the config's directory."""
    return value.startswith("/") or os.path.isabs(value) or PureWindowsPath(value).is_absolute()


def _sanitize_options(options: dict) -> dict:
    """Keep only well-typed values of the options this module reads; a malformed one is skipped, not raised on."""
    clean: dict = {}
    base = options.get("baseUrl")
    if isinstance(base, str) and base:
        clean["baseUrl"] = base
    paths = options.get("paths")
    if isinstance(paths, dict):
        ok = {
            k: [t for t in v if isinstance(t, str)]
            for k, v in paths.items()
            if isinstance(k, str) and isinstance(v, list)
        }
        if ok:
            clean["paths"] = ok
    cond = options.get("customConditions")
    if isinstance(cond, list):
        clean["customConditions"] = [c for c in cond if isinstance(c, str)]
    return clean


class _RepoConfig:
    def __init__(self, repo: Path):
        repo = repo.resolve()
        self.repo = repo
        self.packages: dict[str, tuple[Path, dict]] = {}
        self.tsconfigs: list[_TsConfig] = []
        custom: list[str] = []
        for base, dirs, names in os.walk(repo):
            dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS and not d.startswith("."))
            here = Path(base)
            if "package.json" in names:
                pkg = _read_json(here / "package.json")
                name = pkg.get("name")
                if isinstance(name, str) and name and name not in self.packages:
                    self.packages[name] = (here, pkg)
                zshy = pkg.get("zshy")
                if isinstance(zshy, dict) and isinstance(zshy.get("conditions"), dict):
                    custom.extend(zshy["conditions"])
            for cfg in _CONFIG_NAMES:
                if cfg in names:
                    try:
                        opts = _load_tsconfig((here / cfg).resolve())
                    except Exception:  # one unreadable config must not abort the scan
                        break
                    for cond in opts.get("customConditions") or []:
                        if isinstance(cond, str):
                            custom.append(cond)
                    if opts.get("paths") or opts.get("baseUrl"):
                        self.tsconfigs.append(_TsConfig(here.resolve(), opts))
                    break
        seen: set[str] = set()
        self.conditions = [c for c in (*custom, *_STD_CONDITIONS) if not (c in seen or seen.add(c))]
        # deepest config first so the nearest one wins
        self.tsconfigs.sort(key=lambda c: -len(c.directory.parts))


def _config(repo: Path) -> _RepoConfig:
    key = str(repo)
    cfg = _CACHE.get(key)
    if cfg is None:
        try:
            cfg = _RepoConfig(repo)
        except Exception:  # cache an empty config so a broken repo is not re-walked for every specifier
            cfg = _RepoConfig.__new__(_RepoConfig)
            cfg.repo, cfg.packages, cfg.tsconfigs, cfg.conditions = repo.resolve(), {}, [], list(_STD_CONDITIONS)
        _CACHE[key] = cfg
    return cfg


def _flatten(target, conditions: list[str]) -> list[str]:
    """Candidate target strings of an exports value, in condition priority order."""
    if isinstance(target, str):
        return [target]
    if isinstance(target, list):
        out: list[str] = []
        for item in target:
            out.extend(_flatten(item, conditions))
        return out
    if isinstance(target, dict):
        out = []
        for cond in conditions:
            if cond in target:
                out.extend(_flatten(target[cond], conditions))
        return out
    return []


def _exists_as_file(repo: Path, candidate: Path) -> str | None:
    """candidate itself, its source-extension swaps, or a directory index, as a repo-relative path."""
    # an exact path only counts when it is code (exports often list ./package.json or a css file)
    options = [candidate] if candidate.suffix in _EXTS else []
    suffix = candidate.suffix
    swaps = {".js": (".ts", ".tsx", ".jsx"), ".mjs": (".mts",), ".cjs": (".cts",), ".jsx": (".tsx",)}
    for alt in swaps.get(suffix, ()):
        options.append(candidate.with_suffix(alt))
    for ext in _EXTS:
        options.append(Path(str(candidate) + ext))
    for ext in _EXTS:
        options.append(candidate / f"index{ext}")
    for option in options:
        try:
            if option.is_file():
                rel = option.resolve().relative_to(repo.resolve())
                if "node_modules" in rel.parts:
                    return None
                return rel.as_posix()
        except (OSError, ValueError):
            continue
    return None


def _from_package(cfg: _RepoConfig, spec: str) -> str | None:
    best = None
    for name in cfg.packages:
        if (spec == name or spec.startswith(name + "/")) and (best is None or len(name) > len(best)):
            best = name
    if best is None:
        return None
    pkg_dir, pkg = cfg.packages[best]
    sub = "." + spec[len(best):]
    candidates: list[str] = []
    exports = pkg.get("exports")
    if exports is not None:
        if isinstance(exports, (str, list)) or (
            isinstance(exports, dict) and exports and not any(k.startswith(".") for k in exports)
        ):
            table = {".": exports}
        else:
            table = exports
        if sub in table:
            candidates = _flatten(table[sub], cfg.conditions)
        else:
            for key, value in table.items():
                if "*" in key:
                    head, _, tail = key.partition("*")
                    if sub.startswith(head) and sub.endswith(tail) and len(sub) >= len(head) + len(tail):
                        star = sub[len(head): len(sub) - len(tail)]
                        candidates = [c.replace("*", star) for c in _flatten(value, cfg.conditions)]
                        break
    elif sub == ".":
        candidates = [str(pkg.get("module") or ""), str(pkg.get("main") or ""), "index.js"]
    else:
        candidates = [sub]
    for cand in candidates:
        if not cand:
            continue
        found = _exists_as_file(cfg.repo, pkg_dir / cand)
        if found:
            return found
    return None


def _from_paths(cfg: _RepoConfig, from_file: Path, spec: str) -> str | None:
    for ts in cfg.tsconfigs:
        try:
            from_file.relative_to(ts.directory)
        except ValueError:
            continue
        for pattern, targets in ts.paths.items():
            if "*" in pattern:
                head, _, tail = pattern.partition("*")
                if not (spec.startswith(head) and spec.endswith(tail) and len(spec) >= len(head) + len(tail)):
                    continue
                star = spec[len(head): len(spec) - len(tail)]
            elif spec == pattern:
                star = ""
            else:
                continue
            for target in targets if isinstance(targets, list) else []:
                found = _exists_as_file(cfg.repo, ts.base_dir / str(target).replace("*", star))
                if found:
                    return found
        if ts.has_base_url:
            found = _exists_as_file(cfg.repo, ts.base_dir / spec)
            if found:
                return found
    return None


def resolve_bare_import(repo_path: Path, from_file: Path, spec: str) -> str | None:
    """Repo-relative path a non-relative specifier resolves to inside the repo, or None (external)."""
    if not spec or spec.startswith((".", "/")) or ":" in spec.split("/")[0]:
        return None
    try:
        cfg = _config(repo_path)
        return _from_paths(cfg, from_file.resolve(), spec) or _from_package(cfg, spec)
    except Exception:  # malformed package.json / tsconfig content: treat the specifier as external
        return None
