from pathlib import Path

import tree_sitter_go as tsgo
import tree_sitter_java as tsjava
import tree_sitter_javascript as tsjavascript
import tree_sitter_python as tspython
import tree_sitter_rust as tsrust
import tree_sitter_typescript as tstypescript
from tree_sitter import Language, Node, Parser

from veridion.scanner.detect import IGNORED_DIRS

PY_LANGUAGE = Language(tspython.language())
JS_LANGUAGE = Language(tsjavascript.language())
TS_LANGUAGE = Language(tstypescript.language_typescript())
TSX_LANGUAGE = Language(tstypescript.language_tsx())
GO_LANGUAGE = Language(tsgo.language())
RUST_LANGUAGE = Language(tsrust.language())
JAVA_LANGUAGE = Language(tsjava.language())

LANGUAGE_BY_EXTENSION = {
    ".py": ("python", PY_LANGUAGE),
    ".js": ("javascript", JS_LANGUAGE),
    ".jsx": ("javascript", JS_LANGUAGE),
    ".ts": ("typescript", TS_LANGUAGE),
    ".tsx": ("typescript", TSX_LANGUAGE),
    ".go": ("go", GO_LANGUAGE),
    ".rs": ("rust", RUST_LANGUAGE),
    ".java": ("java", JAVA_LANGUAGE),
}

# Extensions that are recognizable programming languages we don't yet have a grammar
# for. Only these count as "unparseable" coverage gaps. Everything else (assets, docs,
# configs, lock files, tool caches not already excluded by IGNORED_DIRS) was never
# source code and is skipped silently rather than reported as a gap - otherwise
# unparseable_files balloons with noise (a real repo scan turned up 19k+ .json files
# from an untracked cache directory before IGNORED_DIRS was widened, none of which
# were ever "unparseable source").
KNOWN_SOURCE_EXTENSIONS_WITHOUT_GRAMMAR = {
    ".swift", ".rb", ".c", ".cpp", ".cc", ".h", ".hpp",
    ".cs", ".kt", ".kts", ".m", ".mm", ".scala", ".php",
}


def _iter_source_files(repo_path: Path):
    for path in sorted(repo_path.rglob("*")):
        if not path.is_file():
            continue
        if any(part in IGNORED_DIRS for part in path.parts):
            continue
        yield path


def _rel(repo_path: Path, path: Path) -> str:
    return path.relative_to(repo_path).as_posix()


def _extract_python(
    node: Node, source: bytes
) -> tuple[list[str], list[tuple[str, list[str]]], list[str], list[str]]:
    """Return plain imports, from-imports, functions, and classes."""
    plain_imports: list[str] = []
    from_imports: list[tuple[str, list[str]]] = []
    functions: list[str] = []
    classes: list[str] = []

    def walk(n: Node):
        if n.type == "import_from_statement":
            module_node = n.child_by_field_name("module_name")
            module_name = (
                source[module_node.start_byte:module_node.end_byte].decode()
                if module_node is not None
                else ""
            )
            names: list[str] = []
            for child in n.named_children:
                if child == module_node:
                    continue
                if child.type in ("dotted_name", "identifier"):
                    names.append(source[child.start_byte:child.end_byte].decode())
                elif child.type == "aliased_import":
                    name_node = child.child_by_field_name("name")
                    if name_node is not None:
                        names.append(source[name_node.start_byte:name_node.end_byte].decode())
            from_imports.append((module_name, names))
        elif n.type == "import_statement":
            for child in n.named_children:
                if child.type == "dotted_name":
                    plain_imports.append(source[child.start_byte:child.end_byte].decode())
                elif child.type == "aliased_import":
                    name_node = child.child_by_field_name("name")
                    if name_node is not None:
                        plain_imports.append(
                            source[name_node.start_byte:name_node.end_byte].decode()
                        )
        elif n.type == "function_definition":
            name_node = n.child_by_field_name("name")
            if name_node is not None:
                functions.append(source[name_node.start_byte:name_node.end_byte].decode())
        elif n.type == "class_definition":
            name_node = n.child_by_field_name("name")
            if name_node is not None:
                classes.append(source[name_node.start_byte:name_node.end_byte].decode())
        for child in n.children:
            walk(child)

    walk(node)
    return plain_imports, from_imports, functions, classes


def _extract_javascript(node: Node, source: bytes) -> tuple[list[str], list[str], list[str]]:
    imports: list[str] = []
    functions: list[str] = []
    classes: list[str] = []

    def walk(n: Node):
        if n.type == "import_statement":
            source_node = n.child_by_field_name("source")
            if source_node is not None:
                raw = source[source_node.start_byte:source_node.end_byte].decode()
                imports.append(raw.strip("'\""))
        elif n.type == "function_declaration":
            name_node = n.child_by_field_name("name")
            if name_node is not None:
                functions.append(source[name_node.start_byte:name_node.end_byte].decode())
        elif n.type == "class_declaration":
            name_node = n.child_by_field_name("name")
            if name_node is not None:
                classes.append(source[name_node.start_byte:name_node.end_byte].decode())
        for child in n.children:
            walk(child)

    walk(node)
    return imports, functions, classes


def _extract_go(node: Node, source: bytes) -> tuple[list[str], list[str], list[str]]:
    """Return raw import path strings, function/method names, and type names."""
    imports: list[str] = []
    functions: list[str] = []
    types: list[str] = []

    def string_content(n: Node) -> str | None:
        for child in n.children:
            if child.type == "interpreted_string_literal_content":
                return source[child.start_byte:child.end_byte].decode()
        return None

    def walk(n: Node):
        if n.type == "import_spec":
            # import_spec is either just a string literal ("fmt") or an alias followed
            # by one ("svc2 \"pkg/path\"") - the alias identifier itself is never the
            # thing we resolve, only the string literal's content is a real import path.
            for child in n.children:
                if child.type == "interpreted_string_literal":
                    content = string_content(child)
                    if content is not None:
                        imports.append(content)
        elif n.type in ("function_declaration", "method_declaration"):
            name_node = n.child_by_field_name("name")
            if name_node is None:
                # method_declaration names the method via a field_identifier child
                # rather than a "name"-labeled field.
                for child in n.children:
                    if child.type == "field_identifier":
                        name_node = child
                        break
            if name_node is not None:
                functions.append(source[name_node.start_byte:name_node.end_byte].decode())
        elif n.type == "type_spec":
            name_node = n.child_by_field_name("name")
            if name_node is not None:
                types.append(source[name_node.start_byte:name_node.end_byte].decode())
        for child in n.children:
            walk(child)

    walk(node)
    return imports, functions, types


def _load_go_module_prefix(repo_path: Path) -> str | None:
    go_mod = repo_path / "go.mod"
    if not go_mod.exists():
        return None
    for line in go_mod.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line.startswith("module "):
            return line[len("module "):].strip()
    return None


def _resolve_go_import(repo_path: Path, module_prefix: str | None, import_path: str) -> list[str]:
    # Go doesn't import individual files, it imports whole packages (directories) - every
    # non-test .go file in that directory is part of what gets pulled in, so one import
    # statement can fan out to several edges. An import that doesn't start with the
    # module's own declared prefix is external (stdlib or a third-party module) and never
    # resolves to a local file, matching how an unresolved Python/JS import is silently
    # dropped rather than treated as an error.
    if not module_prefix or not import_path.startswith(module_prefix):
        return []

    remainder = import_path[len(module_prefix):].lstrip("/")
    package_dir = repo_path if not remainder else repo_path / Path(*remainder.split("/"))
    if not package_dir.is_dir():
        return []

    targets = []
    for candidate in sorted(package_dir.glob("*.go")):
        if candidate.name.endswith("_test.go"):
            continue
        targets.append(_rel(repo_path, candidate))
    return targets


def _rust_use_paths(node: Node, source: bytes) -> list[str]:
    """A use_declaration's path child, flattened to one or more full path strings.

    tree-sitter's scoped_identifier span already covers the whole "a::b::c" text
    (the "::" tokens are literal source between the segments), so a plain slice of
    the source bytes is the flattened path - no manual tree-walking-and-rejoining
    needed for the common case. The four special forms (aliased, grouped, wildcard)
    each need their own handling before falling back to that slice.
    """

    def text(n: Node) -> str:
        return source[n.start_byte:n.end_byte].decode()

    if node.type == "use_as_clause":
        # "crate::foo::Bar as MyBar" - only the path before "as" matters for
        # resolution; the alias is a local name, never a thing to resolve.
        return _rust_use_paths(node.children[0], source)

    if node.type == "use_wildcard":
        # "std::io::*" - drop the "*", resolve the prefix module itself.
        prefix_nodes = [c for c in node.children if c.type not in ("::", "*")]
        return [text(prefix_nodes[0])] if prefix_nodes else []

    if node.type == "scoped_use_list":
        # "crate::foo::{Bar, Baz}" - both names share the same prefix module; each
        # becomes prefix::name so the existing crate/self/super resolver can treat
        # them exactly like any other full path.
        prefix_node = node.children[0]
        list_node = node.children[-1]
        prefix = text(prefix_node)
        names = [text(c) for c in list_node.children if c.type in ("identifier", "self")]
        return [f"{prefix}::{name}" for name in names]

    if node.type == "use_list":
        # "use {a, b};" - rare, no prefix at all.
        return [text(c) for c in node.children if c.type in ("identifier", "self")]

    # scoped_identifier, identifier, crate, self, or super used directly.
    return [text(node)]


def _extract_rust(node: Node, source: bytes) -> tuple[list[str], list[str], list[str]]:
    """Return flattened use-path strings, function/method names, and type names."""
    imports: list[str] = []
    functions: list[str] = []
    types: list[str] = []

    def walk(n: Node):
        if n.type == "use_declaration":
            # use_declaration's single meaningful child is whichever path-shaped
            # node follows the "use" keyword and precedes the ";".
            for child in n.children:
                if child.type not in ("use", ";"):
                    imports.extend(_rust_use_paths(child, source))
                    break
        elif n.type in ("function_item", "function_signature_item"):
            name_node = n.child_by_field_name("name")
            if name_node is not None:
                functions.append(source[name_node.start_byte:name_node.end_byte].decode())
        elif n.type in ("struct_item", "enum_item", "trait_item"):
            name_node = n.child_by_field_name("name")
            if name_node is not None:
                types.append(source[name_node.start_byte:name_node.end_byte].decode())
        for child in n.children:
            walk(child)

    walk(node)
    return imports, functions, types


def _rust_crate_root(repo_path: Path) -> Path | None:
    # Workspace repos (multiple crates, each with its own Cargo.toml + src/) aren't
    # supported in this first pass - same documented scope limit as Go only looking
    # at a repo-root go.mod, rather than every nested module.
    for candidate in (repo_path / "src" / "lib.rs", repo_path / "src" / "main.rs"):
        if candidate.exists():
            return candidate
    return None


def _rust_module_search_dir(repo_path: Path, file_path: Path) -> Path:
    """The directory this file's own OWN submodules would live in.

    Directory structure is assumed to mirror the module tree (true for the vast
    majority of real Rust code; #[path = "..."] escape hatches aren't supported).
    The crate root's submodules live directly in src/; a foo/mod.rs's submodules
    live in that same foo/ directory; a leaf foo.rs's submodules live in an
    adjacent foo/ directory (the 2018-edition convention that doesn't require
    foo/mod.rs to exist just to hold further submodules).
    """
    src_dir = repo_path / "src"
    if file_path in (src_dir / "lib.rs", src_dir / "main.rs"):
        return src_dir
    if file_path.name == "mod.rs":
        return file_path.parent
    return file_path.parent / file_path.stem


def _resolve_rust_module_dir(search_dir: Path, name: str) -> Path | None:
    if (search_dir / f"{name}.rs").exists():
        return search_dir / f"{name}.rs"
    if (search_dir / name / "mod.rs").exists():
        return search_dir / name / "mod.rs"
    return None


def _walk_rust_segments(search_dir: Path, segments: list[str]) -> Path | None:
    # Walks as far as it can and returns whatever was last resolved - which
    # naturally gives the right answer for both cases: every segment resolving
    # as a further submodule (the walk completes), and the last segment being an
    # item (a struct/fn/const/etc) rather than a submodule (the walk stops one
    # short and returns the containing module's own file, matching how a Python
    # from-import of a plain name falls back to the containing package).
    resolved_file: Path | None = None
    current_dir = search_dir
    for segment in segments:
        candidate = _resolve_rust_module_dir(current_dir, segment)
        if candidate is None:
            break
        resolved_file = candidate
        current_dir = candidate.parent if candidate.name == "mod.rs" else candidate.parent / candidate.stem
    return resolved_file


def _resolve_rust_path(repo_path: Path, from_file: Path, path: str) -> str | None:
    segments = path.split("::")
    if not segments:
        return None

    head = segments[0]
    rest = segments[1:]

    if head == "crate":
        target = _walk_rust_segments(repo_path / "src", rest)
    elif head == "self":
        target = _walk_rust_segments(_rust_module_search_dir(repo_path, from_file), rest)
    elif head == "super":
        # "super" always climbs at least one level; each further leading "super"
        # segment climbs one more - directory mirrors the module tree, so that's
        # one more parent directory each time.
        search_dir = _rust_module_search_dir(repo_path, from_file).parent
        while rest and rest[0] == "super":
            search_dir = search_dir.parent
            rest = rest[1:]
        target = _walk_rust_segments(search_dir, rest)
    else:
        # No crate/self/super prefix: could be an implicit crate-relative path
        # ("use handlers::Handler;" from the crate root, valid since the 2018
        # edition) or an external crate name (std, or a real third-party
        # dependency) - both look identical syntactically. Walking the whole
        # path from src/ disambiguates them the only way possible without a
        # full Cargo.toml dependency parse: if the first segment doesn't exist
        # on disk, nothing resolves, exactly as an external import should.
        target = _walk_rust_segments(repo_path / "src", segments)

    return _rel(repo_path, target) if target is not None else None


def _extract_java_package(node: Node, source: bytes) -> str | None:
    for child in node.children:
        if child.type == "package_declaration":
            for grandchild in child.children:
                if grandchild.type in ("scoped_identifier", "identifier"):
                    return source[grandchild.start_byte:grandchild.end_byte].decode()
            return None
    return None


def _extract_java(
    node: Node, source: bytes
) -> tuple[list[tuple[str, bool, bool]], list[str], list[str]]:
    """Return (import path, is_static, is_wildcard) tuples, method names, and type names."""
    imports: list[tuple[str, bool, bool]] = []
    functions: list[str] = []
    types: list[str] = []

    def text(n: Node) -> str:
        return source[n.start_byte:n.end_byte].decode()

    def walk(n: Node):
        if n.type == "import_declaration":
            is_static = any(c.type == "static" for c in n.children)
            is_wildcard = any(c.type == "asterisk" for c in n.children)
            for child in n.children:
                if child.type in ("scoped_identifier", "identifier"):
                    imports.append((text(child), is_static, is_wildcard))
                    break
        elif n.type == "method_declaration":
            name_node = n.child_by_field_name("name")
            if name_node is not None:
                functions.append(text(name_node))
        elif n.type in (
            "class_declaration", "interface_declaration", "enum_declaration", "record_declaration",
        ):
            name_node = n.child_by_field_name("name")
            if name_node is not None:
                types.append(text(name_node))
        for child in n.children:
            walk(child)

    walk(node)
    return imports, functions, types


def _java_source_root_for(file_path: Path, package: str | None) -> Path | None:
    """Infer the source root from what this file itself declares: the directory such
    that source_root / package-as-a-path is this file's own containing directory.
    Convention-agnostic on purpose - works for Maven/Gradle's src/main/java, a bare
    src/, or a flat repo root, since it's derived per-file rather than assumed
    upfront, and different files (main vs test source sets) can imply different
    roots that all get tried when resolving any given import.
    """
    if not package:
        return file_path.parent
    segments = package.split(".")
    parts = file_path.parent.parts
    if len(parts) < len(segments) or list(parts[-len(segments):]) != segments:
        return None
    root = file_path.parent
    for _ in range(len(segments)):
        root = root.parent
    return root


def _java_class_file(root: Path, segments: list[str]) -> Path | None:
    if not segments:
        return None
    candidate = root.joinpath(*segments[:-1], f"{segments[-1]}.java")
    return candidate if candidate.is_file() else None


def _resolve_java_import(
    source_roots: list[Path], dotted: str, is_static: bool, is_wildcard: bool
) -> list[Path]:
    segments = dotted.split(".")
    if not segments:
        return []

    if is_wildcard:
        # dotted is a package path with no class name - every .java file directly
        # in that package's directory is what a wildcard import pulls in, the same
        # "import the whole package" fan-out Go's package-level imports need.
        for root in source_roots:
            package_dir = root.joinpath(*segments)
            if package_dir.is_dir():
                return sorted(package_dir.glob("*.java"))
        return []

    if is_static:
        # "import static a.b.C.MEMBER" - MEMBER is a field or method, not a class;
        # the file that actually exists is a.b.C.java.
        segments = segments[:-1]
        if not segments:
            return []

    for root in source_roots:
        target = _java_class_file(root, segments)
        if target is not None:
            return [target]

    # One segment short: the same fallback Python/Rust already use - the last
    # segment might be a nested class rather than its own top-level file, in
    # which case the containing class's own file is the real target.
    if len(segments) > 1:
        for root in source_roots:
            target = _java_class_file(root, segments[:-1])
            if target is not None:
                return [target]

    return []


def _resolve_python_module(repo_path: Path, dotted: str, from_file: Path | None = None) -> str | None:
    if not dotted:
        return None

    if dotted.startswith("."):
        # Relative import ("from ..services.sessions import x"). tree-sitter hands us
        # the leading dots as literal text in the dotted string, so dot_count is how
        # many levels up from from_file's own package to resolve from: one dot means
        # "the package containing from_file" (from_file.parent itself), each
        # additional dot goes up one more parent directory.
        if from_file is None:
            return None
        dot_count = len(dotted) - len(dotted.lstrip("."))
        remainder = dotted[dot_count:]
        base_dir = from_file.parent
        for _ in range(dot_count - 1):
            base_dir = base_dir.parent
        as_path = base_dir if not remainder else base_dir / Path(*remainder.split("."))
    else:
        as_path = repo_path / Path(*dotted.split("."))

    candidate_module = Path(as_path.as_posix() + ".py")
    candidate_package = as_path / "__init__.py"
    if candidate_module.exists():
        return _rel(repo_path, candidate_module)
    if candidate_package.exists():
        return _rel(repo_path, candidate_package)
    return None


def _resolve_python_from_import(
    repo_path: Path, module_name: str, imported_name: str, from_file: Path
) -> str | None:
    # A relative module_name already ends in the dots that separate it from what
    # follows ("." or ".." or "..services.sessions"); appending imported_name with an
    # extra "." separator only when module_name does NOT already end in a dot avoids
    # turning "from . import helpers" (single dot: current package) into an
    # accidental double dot (parent package) - which silently resolves to the wrong
    # file rather than raising an error, so it's easy to miss without a real repo to
    # test against.
    if module_name and not module_name.endswith("."):
        submodule_dotted = f"{module_name}.{imported_name}"
    else:
        submodule_dotted = f"{module_name}{imported_name}"
    target = _resolve_python_module(repo_path, submodule_dotted, from_file)
    if target is not None:
        return target
    return _resolve_python_module(repo_path, module_name, from_file)


JS_FAMILY_EXTENSIONS = (".js", ".jsx", ".ts", ".tsx")


def _resolve_js_import(repo_path: Path, from_file: Path, spec: str) -> str | None:
    if not spec.startswith("."):
        return None
    base = (from_file.parent / spec).resolve()
    candidates = [base]
    for ext in JS_FAMILY_EXTENSIONS:
        candidates.append(base.with_suffix(ext))
        candidates.append(base / f"index{ext}")
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            try:
                return _rel(repo_path, candidate)
            except ValueError:
                return None
    return None


def build_module_graph(repo_path: Path) -> tuple[list[dict], dict, list[dict]]:
    modules: list[dict] = []
    unparseable: list[dict] = []
    imported_by_map: dict[str, list[str]] = {}
    edges: list[list[str]] = []
    go_module_prefix = _load_go_module_prefix(repo_path)
    has_rust_crate_root = _rust_crate_root(repo_path) is not None

    # Java has no single repo-root config naming a module prefix (no go.mod, no
    # Cargo.toml equivalent) - the source root (src/main/java, a bare src/, or the
    # repo root itself) has to be inferred from what each file's own package
    # declaration implies about its directory, so every .java file needs a quick
    # pre-parse before any of them can have their imports resolved.
    java_source_roots: list[Path] = []
    pre_parser = Parser()
    pre_parser.language = JAVA_LANGUAGE
    for path in _iter_source_files(repo_path):
        if path.suffix != ".java":
            continue
        pre_source = path.read_bytes()
        tree = pre_parser.parse(pre_source)
        package = _extract_java_package(tree.root_node, pre_source)
        root = _java_source_root_for(path, package)
        if root is not None and root not in java_source_roots:
            java_source_roots.append(root)

    parser = Parser()

    for path in _iter_source_files(repo_path):
        rel_path = _rel(repo_path, path)
        language_info = LANGUAGE_BY_EXTENSION.get(path.suffix)
        if language_info is None:
            if path.suffix in KNOWN_SOURCE_EXTENSIONS_WITHOUT_GRAMMAR:
                unparseable.append(
                    {"path": rel_path, "reason": f"no grammar registered for {path.suffix}"}
                )
            continue

        language_name, ts_language = language_info
        parser.language = ts_language
        source = path.read_bytes()
        tree = parser.parse(source)

        if language_name == "python":
            plain_imports, from_imports, functions, classes = _extract_python(
                tree.root_node, source
            )
            resolved_imports: list[str] = []

            for dotted in plain_imports:
                target = _resolve_python_module(repo_path, dotted, path)
                if target is not None:
                    resolved_imports.append(target)
                    edges.append([rel_path, target])
                    imported_by_map.setdefault(target, []).append(rel_path)

            for module_name, names in from_imports:
                targets: set[str] = set()
                if names:
                    for name in names:
                        target = _resolve_python_from_import(repo_path, module_name, name, path)
                        if target is not None:
                            targets.add(target)
                else:
                    target = _resolve_python_module(repo_path, module_name, path)
                    if target is not None:
                        targets.add(target)
                for target in sorted(targets):
                    resolved_imports.append(target)
                    edges.append([rel_path, target])
                    imported_by_map.setdefault(target, []).append(rel_path)
        elif language_name == "go":
            raw_imports, functions, classes = _extract_go(tree.root_node, source)
            resolved_imports = []
            for spec in raw_imports:
                for target in _resolve_go_import(repo_path, go_module_prefix, spec):
                    if target == rel_path:
                        continue
                    resolved_imports.append(target)
                    edges.append([rel_path, target])
                    imported_by_map.setdefault(target, []).append(rel_path)
        elif language_name == "rust":
            raw_imports, functions, classes = _extract_rust(tree.root_node, source)
            resolved_imports = []
            if has_rust_crate_root:
                for use_path in raw_imports:
                    target = _resolve_rust_path(repo_path, path, use_path)
                    if target is not None and target != rel_path:
                        resolved_imports.append(target)
                        edges.append([rel_path, target])
                        imported_by_map.setdefault(target, []).append(rel_path)
        elif language_name == "java":
            raw_imports, functions, classes = _extract_java(tree.root_node, source)
            resolved_imports = []
            for dotted, is_static, is_wildcard in raw_imports:
                for target_path in _resolve_java_import(
                    java_source_roots, dotted, is_static, is_wildcard
                ):
                    target = _rel(repo_path, target_path)
                    if target == rel_path:
                        continue
                    resolved_imports.append(target)
                    edges.append([rel_path, target])
                    imported_by_map.setdefault(target, []).append(rel_path)
        else:
            raw_imports, functions, classes = _extract_javascript(tree.root_node, source)
            resolved_imports = []
            for spec in raw_imports:
                target = _resolve_js_import(repo_path, path, spec)
                if target is not None:
                    resolved_imports.append(target)
                    edges.append([rel_path, target])
                    imported_by_map.setdefault(target, []).append(rel_path)

        modules.append(
            {
                "path": rel_path,
                "language": language_name,
                "imports": resolved_imports,
                "imported_by": [],
                "symbols": {"functions": functions, "classes": classes},
            }
        )

    for module in modules:
        module["imported_by"] = sorted(imported_by_map.get(module["path"], []))

    nodes = sorted({m["path"] for m in modules})
    dependency_graph = {"nodes": nodes, "edges": edges}

    return modules, dependency_graph, unparseable
