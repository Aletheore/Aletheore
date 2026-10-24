"""Rust-specific resolution helpers used by scanner.graph.

Kept in its own module so the shared graph.py only carries thin call sites:
  - exact-case directory lookups (a case-insensitive filesystem answers exists() for `Ctxt.rs` when only
    `ctxt.rs` is on disk, which produced edges to modules that do not exist),
  - Cargo workspace crate names (`use serde_core::de::Deserialize` resolves to the sibling crate),
  - `#[path = "..."]` module files,
  - zero-argument item macros that declare modules (`crate_root!();` expanding to `pub mod de;`),
  - fully qualified `crate::a::b` / `other_crate::a::b` paths used in code without a `use`.
"""

from __future__ import annotations

import os
import re
import tomllib
from functools import lru_cache
from pathlib import Path

_SKIP_DIRS = {"target", ".git", "node_modules", ".aletheore", "vendor", "__pycache__"}


@lru_cache(maxsize=4096)
def dir_names(directory: str) -> frozenset[str]:
    """Exact entry names of a directory (empty if unreadable). Used instead of Path.exists() so the match is
    case-sensitive on every filesystem."""
    try:
        return frozenset(os.listdir(directory))
    except OSError:
        return frozenset()


def file_exists_exact(directory: Path, name: str) -> bool:
    return name in dir_names(str(directory)) and (directory / name).is_file()


def dir_exists_exact(directory: Path, name: str) -> bool:
    return name in dir_names(str(directory)) and (directory / name).is_dir()


def crate_root_file(src_dir: Path) -> Path | None:
    for name in ("lib.rs", "main.rs"):
        if file_exists_exact(src_dir, name):
            return src_dir / name
    return None


def module_file_for_dir(directory: Path, src_dir: Path) -> Path | None:
    """The file that defines the module whose submodules live in `directory`."""
    if directory == src_dir:
        return crate_root_file(src_dir)
    sibling = directory.parent
    if file_exists_exact(sibling, directory.name + ".rs"):
        return sibling / (directory.name + ".rs")
    if file_exists_exact(directory, "mod.rs"):
        return directory / "mod.rs"
    return None


# ---------------------------------------------------------------- Cargo workspace


def _load_toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


@lru_cache(maxsize=32)
def _workspace(repo_str: str) -> tuple[dict[str, Path], dict[str, dict]]:
    """(package name -> lib root file, package dir -> manifest) for every Cargo.toml under the repo."""
    by_name: dict[str, Path] = {}
    manifests: dict[str, dict] = {}
    for dirpath, dirnames, filenames in os.walk(repo_str):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        if "Cargo.toml" not in filenames:
            continue
        manifest = _load_toml(Path(dirpath) / "Cargo.toml")
        manifests[dirpath] = manifest
        package = manifest.get("package")
        if not isinstance(package, dict) or "name" not in package:
            continue
        lib = manifest.get("lib") if isinstance(manifest.get("lib"), dict) else {}
        lib_path = lib.get("path")
        root = Path(dirpath) / lib_path if lib_path else Path(dirpath) / "src" / "lib.rs"
        if root.is_file():
            by_name[str(package["name"]).replace("-", "_")] = root
            if lib.get("name"):
                by_name.setdefault(str(lib["name"]).replace("-", "_"), root)
    return by_name, manifests


def nearest_manifest_dir(repo_path: Path, from_file: Path) -> Path | None:
    current = from_file.parent
    root = str(repo_path)
    while True:
        if "Cargo.toml" in dir_names(str(current)):
            return current
        if str(current) == root or current.parent == current:
            return None
        current = current.parent


def crate_aliases(repo_path: Path, from_file: Path) -> dict[str, Path]:
    """extern name -> lib root file of the workspace crates the file's own package can name: its declared
    dependencies that are workspace crates (honouring `package = "..."` renames) plus its own library."""
    by_name, manifests = _workspace(str(repo_path))
    if not by_name:
        return {}
    pkg_dir = nearest_manifest_dir(repo_path, from_file)
    if pkg_dir is None:
        return {}
    manifest = manifests.get(str(pkg_dir)) or {}
    aliases: dict[str, Path] = {}
    package = manifest.get("package")
    if isinstance(package, dict) and str(package.get("name", "")).replace("-", "_") in by_name:
        own = str(package["name"]).replace("-", "_")
        aliases[own] = by_name[own]
    tables = [manifest.get(k) for k in ("dependencies", "dev-dependencies", "build-dependencies")]
    for target in (manifest.get("target") or {}).values():
        if isinstance(target, dict):
            tables.extend(target.get(k) for k in ("dependencies", "dev-dependencies", "build-dependencies"))
    for table in tables:
        if not isinstance(table, dict):
            continue
        for key, spec in table.items():
            real = str(spec.get("package", key)) if isinstance(spec, dict) else str(key)
            real = real.replace("-", "_")
            if real in by_name:
                aliases[str(key).replace("-", "_")] = by_name[real]
    return aliases


# ---------------------------------------------------------------- source cleaning


_LITERAL = re.compile(r"r(#*)\".*?\"\1|\"(?:\\.|[^\"\\])*\"|'(?:\\.[^']*|[^\\'])'", re.DOTALL)


def clean_source(text: str, keep_literals: bool = False) -> str:
    """Blank out comments (block comments nest in Rust) and string/char literals; strings become `""`.
    With keep_literals only the comments go, so a path inside a string literal can still be read."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        two = text[i : i + 2]
        if two == "//":
            j = text.find("\n", i)
            i = n if j < 0 else j
            out.append(" ")
        elif two == "/*":
            depth, i = 1, i + 2
            while i < n and depth:
                pair = text[i : i + 2]
                if pair == "/*":
                    depth, i = depth + 1, i + 2
                elif pair == "*/":
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
            out.append(" ")
        else:
            m = _LITERAL.match(text, i)
            if m:
                out.append(m.group(0) if keep_literals else '""' if m.group(0)[0] in "r\"" else " ")
                i = m.end()
            else:
                out.append(text[i])
                i += 1
    return "".join(out)


def clear_cache() -> None:
    """Forget directory listings, Cargo manifests, macro registries and module summaries (called once per scan,
    so edits between scans in one process are seen). Also clears the Go package cache."""
    for cached in (dir_names, _workspace, _macro_registry, _arg_macro_registry, module_items):
        cached.cache_clear()
    from aletheore.scanner import go_resolve

    go_resolve.clear_cache()


def _balanced(text: str, open_index: int) -> int:
    """Index of the brace closing the one at `open_index` (len(text) - 1 if unbalanced)."""
    opener = text[open_index]
    closer = {"{": "}", "(": ")", "[": "]"}[opener]
    depth = 0
    for i in range(open_index, len(text)):
        if text[i] == opener:
            depth += 1
        elif text[i] == closer:
            depth -= 1
            if depth == 0:
                return i
    return len(text) - 1


_MACRO_DEF = re.compile(r"\bmacro_rules!\s*([A-Za-z_]\w*)\s*([{(\[])")


def _strip_macro_defs(text: str) -> str:
    out, pos = [], 0
    for m in _MACRO_DEF.finditer(text):
        if m.start() < pos:
            continue
        end = _balanced(text, m.end() - 1)
        out.append(text[pos:m.start()])
        pos = end + 1
    out.append(text[pos:])
    return "".join(out)


# ---------------------------------------------------------------- use flattening


def flatten_use(tree: str, keep_glob: bool = False) -> list[str]:
    """`a::{b, c::{d, self}, e as f, g::*}` -> ['a::b', 'a::c::d', 'a::c', 'a::e', 'a::g'] (wildcards drop the star)."""
    tree = re.sub(r"\s+", " ", tree).strip()

    def split_top(s: str) -> list[str]:
        parts, depth, cur = [], 0, ""
        for ch in s:
            depth += (ch == "{") - (ch == "}")
            if ch == "," and depth == 0:
                parts.append(cur)
                cur = ""
            else:
                cur += ch
        return parts + ([cur] if cur.strip() else [])

    def walk(prefix: str, s: str) -> list[str]:
        s = s.strip()
        brace = s.find("{")
        if brace >= 0:
            head = s[:brace].strip().rstrip(":")
            inner = s[brace + 1 : s.rfind("}")]
            full = f"{prefix}::{head}" if prefix and head else (prefix or head)
            out: list[str] = []
            for part in split_top(inner):
                out.extend(walk(full, part))
            return out
        s = re.sub(r"\s+as\s+\w+$", "", s).strip()
        if keep_glob and (s == "*" or s.endswith("::*")):
            if s == "*":
                return [f"{prefix}::*"] if prefix else []
            return [f"{prefix}::{s}" if prefix else s]
        if s.endswith("::*"):
            s = s[:-3]
        if s in ("*", "self", ""):
            return [prefix] if prefix else []
        return [f"{prefix}::{s}" if prefix else s]

    return [p for p in walk("", tree) if p]




# ---------------------------------------------------------------- macro-declared modules and paths


@lru_cache(maxsize=1024)
def _macro_registry(crate_dir: str) -> dict[str, tuple[list[str], list[str]]]:
    """macro name -> (module names declared at the top of its expansion, flattened `use` paths), for zero-argument
    macros (`() => { ... }` as the only arm) defined anywhere in the crate directory."""
    registry: dict[str, tuple[list[str], list[str]]] = {}
    for dirpath, dirnames, filenames in os.walk(crate_dir):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            if not name.endswith(".rs"):
                continue
            try:
                text = Path(dirpath, name).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if "macro_rules!" not in text:
                continue
            text = clean_source(text)
            for m in _MACRO_DEF.finditer(text):
                end = _balanced(text, m.end() - 1)
                body = text[m.end() : end]
                arm = re.match(r"\s*\(\s*\)\s*=>\s*([{(\[])", body)
                if not arm:
                    continue
                inner_open = m.end() + arm.end() - 1
                inner_close = _balanced(text, inner_open)
                if text[inner_close + 1 : end].strip(" \n\t;"):
                    continue  # more than one arm
                inner = text[inner_open + 1 : inner_close]
                flat = inner
                mods = re.findall(r"(?:^|[;\s\]])(?:pub(?:\([^)]*\))?\s+)?mod\s+([A-Za-z_]\w*)\s*;", flat)
                uses: list[str] = []
                for u in re.findall(r"(?:^|[;\s\]])use\s+([^;]+);", flat):
                    uses.extend(flatten_use(u))
                registry[m.group(1)] = (mods, uses)
    return registry


_DECL = re.compile(r"\b(?:fn|struct|enum|trait|type|const|static|union|mod|macro_rules!)\s+([A-Za-z_]\w*)")
_PUB_USE = re.compile(r"\bpub(?:\([^)]*\))?\s+use\s+([^;]+);")
_INCLUDE = re.compile(r"\binclude!\s*\(\s*\"([^\"]+)\"\s*\)")


@lru_cache(maxsize=2048)
def module_items(file_path: str) -> tuple[frozenset[str], tuple[str, ...]]:
    """(names declared in the file, flattened `pub use` paths with a trailing ::* kept for globs)."""
    try:
        text = clean_source(Path(file_path).read_text(encoding="utf-8", errors="ignore"))
    except OSError:
        return frozenset(), ()
    text = _strip_macro_defs(text)
    declared = frozenset(_DECL.findall(text))
    uses: list[str] = []
    for m in _PUB_USE.finditer(text):
        uses.extend(flatten_use(m.group(1), keep_glob=True))
    return declared, tuple(uses)


_INVOKE = re.compile(r"(?m)^[ \t]*([A-Za-z_]\w*)!\s*(?:\(\s*\)|\{\s*\})")
_INLINE_CRATE = re.compile(r"(?<![\w$:.])crate((?:::[A-Za-z_]\w*)+)")


_REPEAT_PATTERN = re.compile(
    r"^\$\(\s*\$(\w+)\s*:\s*ident\s*\)\s*([,;]?)\s*[*+]\s*(?:\$\(\s*[,;]\s*\)\s*\?)?$"
)
_FIXED_ITEM = re.compile(r"^\$(\w+)\s*:\s*ident$")
_MOD_VAR = re.compile(r"\bmod\s+\$(\w+)\s*;")
_IDENT = re.compile(r"^[A-Za-z_]\w*$")
_INVOKE_ARGS = re.compile(r"(?<![\w$])([A-Za-z_]\w*)!\s*([({\[])")


def _parse_arm_pattern(pattern: str):
    """("repeat", var, sep) for `$($x:ident),*`, ("fixed", [vars], sep) for `$a:ident, $b:ident`; None for
    anything else. Deliberately tiny: literals, other fragment kinds, nested repetitions and tt-munching
    patterns are not expanded because the module names they declare cannot be read off the invocation."""
    pattern = re.sub(r"\s+", " ", pattern).strip()
    repeat = _REPEAT_PATTERN.match(pattern)
    if repeat:
        return ("repeat", [repeat.group(1)], repeat.group(2) or ",")
    for sep in (",", ";"):
        parts = [p.strip() for p in pattern.split(sep)] if pattern else []
        if len(parts) > 1 and parts[-1] == "":  # a trailing separator, as _expand_arg_macro tolerates in the call
            parts.pop()
        if parts and all(_FIXED_ITEM.match(p) for p in parts):
            return ("fixed", [_FIXED_ITEM.match(p).group(1) for p in parts], sep)
    return None


@lru_cache(maxsize=1024)
def _arg_macro_registry(crate_dir: str) -> dict[str, list[tuple]]:
    """macro name -> arms (spec, module variables) for macros whose arms bind plain `$x:ident` fragments and
    contain `mod $x;` in the body."""
    registry: dict[str, list[tuple]] = {}
    for dirpath, dirnames, filenames in os.walk(crate_dir):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            if not name.endswith(".rs"):
                continue
            try:
                text = Path(dirpath, name).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if "macro_rules!" not in text or "mod $" not in re.sub(r"\s+", " ", text):
                continue
            text = clean_source(text)
            for m in _MACRO_DEF.finditer(text):
                end = _balanced(text, m.end() - 1)
                pos, arms = m.end(), []
                while pos < end:
                    open_p = text.find("(", pos, end)
                    if open_p < 0:
                        break
                    close_p = _balanced(text, open_p)
                    arrow = re.match(r"\s*=>\s*([{(\[])", text[close_p + 1 : end])
                    if not arrow:
                        break
                    body_open = close_p + 1 + arrow.end() - 1
                    body_close = _balanced(text, body_open)
                    spec = _parse_arm_pattern(text[open_p + 1 : close_p])
                    variables = _MOD_VAR.findall(text[body_open + 1 : body_close])
                    if spec and variables and set(variables) <= set(spec[1]):
                        arms.append((spec, tuple(dict.fromkeys(variables))))
                    pos = body_close + 1
                if arms:
                    registry[m.group(1)] = arms
    return registry


def _expand_arg_macro(arms: list[tuple], args: str) -> list[str]:
    """Module names declared by `name!(args)`: the first arm whose pattern matches plain identifiers."""
    for (kind, variables, sep), mod_vars in arms:
        parts = [p.strip() for p in args.split(sep)]
        if parts and parts[-1] == "":
            parts = parts[:-1]  # trailing separator
        if not parts or not all(_IDENT.match(p) for p in parts):
            continue
        if kind == "fixed":
            if len(parts) != len(variables):
                continue
            binding = dict(zip(variables, parts))
            return [binding[v] for v in mod_vars]
        return parts  # repeat: every bound identifier is declared by `mod $x;`
    return []


def extra_imports(repo_path: Path, from_file: Path, source: str) -> list[str]:
    """Use-path strings the tree-sitter `use`/`mod` walk cannot see: modules and uses produced by zero-argument
    item macros, and fully qualified paths written inline."""
    out: list[str] = []
    # include!("relative/file.rs") textually splices another file in.
    out.extend(f"@path:{p}" for p in _INCLUDE.findall(clean_source(source, keep_literals=True)))
    cleaned = clean_source(source)
    crate_dir = nearest_manifest_dir(repo_path, from_file) or repo_path
    registry = _macro_registry(str(crate_dir)) if "!" in cleaned else {}
    if registry:
        for name in _INVOKE.findall(cleaned):
            if name in registry:
                mods, uses = registry[name]
                out.extend(f"self::{m}" for m in mods)
                out.extend(uses)
    if "!" in cleaned:
        arg_registry = _arg_macro_registry(str(crate_dir))
        if arg_registry:
            body_without_defs = _strip_macro_defs(cleaned)
            for m in _INVOKE_ARGS.finditer(body_without_defs):
                if m.group(1) in arg_registry:
                    close = _balanced(body_without_defs, m.end() - 1)
                    args = body_without_defs[m.end() : close]
                    out.extend(f"self::{n}" for n in _expand_arg_macro(arg_registry[m.group(1)], args))
    # `use` statements are already handled by the tree walk (strip them so paths are not counted twice).
    body = re.sub(r"\buse\s+[^;]+;", " ", _strip_macro_defs(cleaned))
    for m in _INLINE_CRATE.finditer(body):
        out.append("crate" + m.group(1))
    aliases = crate_aliases(repo_path, from_file)
    if aliases:
        pattern = re.compile(r"(?<![\w$:.])(" + "|".join(re.escape(a) for a in aliases) + r")((?:::[A-Za-z_]\w*)+)")
        for m in pattern.finditer(body):
            out.append(m.group(1) + m.group(2))
    seen: set[str] = set()
    unique = []
    for p in out:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique
