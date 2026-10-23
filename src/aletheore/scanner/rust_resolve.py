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


_NOISE = re.compile(
    r"//[^\n]*|/\*.*?\*/|r(#*)\".*?\"\1|\"(?:\\.|[^\"\\])*\"|'(?:\\.[^']*|[^\\'])'",
    re.DOTALL,
)


def clean_source(text: str) -> str:
    return _NOISE.sub(lambda m: '""' if m.group(0)[0] in "r\"" else " ", text)


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


def flatten_use(tree: str) -> list[str]:
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
        if s.endswith("::*"):
            s = s[:-3]
        if s in ("*", "self", ""):
            return [prefix] if prefix else []
        return [f"{prefix}::{s}" if prefix else s]

    return [p for p in walk("", tree) if p]




# ---------------------------------------------------------------- macro-declared modules and paths


@lru_cache(maxsize=64)
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


_INVOKE = re.compile(r"(?m)^[ \t]*([A-Za-z_]\w*)!\s*(?:\(\s*\)|\{\s*\})")
_INLINE_CRATE = re.compile(r"(?<![\w$:.])crate((?:::[A-Za-z_]\w*)+)")


def extra_imports(repo_path: Path, from_file: Path, source: str) -> list[str]:
    """Use-path strings the tree-sitter `use`/`mod` walk cannot see: modules and uses produced by zero-argument
    item macros, and fully qualified paths written inline."""
    out: list[str] = []
    cleaned = clean_source(source)
    crate_dir = nearest_manifest_dir(repo_path, from_file) or repo_path
    registry = _macro_registry(str(crate_dir)) if "!" in cleaned else {}
    if registry:
        for name in _INVOKE.findall(cleaned):
            if name in registry:
                mods, uses = registry[name]
                out.extend(f"self::{m}" for m in mods)
                out.extend(uses)
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
