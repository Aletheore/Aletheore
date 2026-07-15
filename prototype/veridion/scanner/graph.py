from pathlib import Path

import tree_sitter_javascript as tsjavascript
import tree_sitter_python as tspython
import tree_sitter_typescript as tstypescript
from tree_sitter import Language, Node, Parser

from veridion.scanner.detect import IGNORED_DIRS

PY_LANGUAGE = Language(tspython.language())
JS_LANGUAGE = Language(tsjavascript.language())
TS_LANGUAGE = Language(tstypescript.language_typescript())
TSX_LANGUAGE = Language(tstypescript.language_tsx())

LANGUAGE_BY_EXTENSION = {
    ".py": ("python", PY_LANGUAGE),
    ".js": ("javascript", JS_LANGUAGE),
    ".jsx": ("javascript", JS_LANGUAGE),
    ".ts": ("typescript", TS_LANGUAGE),
    ".tsx": ("typescript", TSX_LANGUAGE),
}

# Extensions that are recognizable programming languages we don't yet have a grammar
# for. Only these count as "unparseable" coverage gaps. Everything else (assets, docs,
# configs, lock files, tool caches not already excluded by IGNORED_DIRS) was never
# source code and is skipped silently rather than reported as a gap - otherwise
# unparseable_files balloons with noise (a real repo scan turned up 19k+ .json files
# from an untracked cache directory before IGNORED_DIRS was widened, none of which
# were ever "unparseable source").
KNOWN_SOURCE_EXTENSIONS_WITHOUT_GRAMMAR = {
    ".swift", ".go", ".rs", ".java", ".rb", ".c", ".cpp", ".cc", ".h", ".hpp",
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


def _resolve_python_module(repo_path: Path, dotted: str) -> str | None:
    if not dotted:
        return None
    as_path = Path(*dotted.split("."))
    candidate_module = repo_path / (as_path.as_posix() + ".py")
    candidate_package = repo_path / as_path / "__init__.py"
    if candidate_module.exists():
        return _rel(repo_path, candidate_module)
    if candidate_package.exists():
        return _rel(repo_path, candidate_package)
    return None


def _resolve_python_from_import(repo_path: Path, module_name: str, imported_name: str) -> str | None:
    submodule_dotted = f"{module_name}.{imported_name}" if module_name else imported_name
    target = _resolve_python_module(repo_path, submodule_dotted)
    if target is not None:
        return target
    return _resolve_python_module(repo_path, module_name)


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
                target = _resolve_python_module(repo_path, dotted)
                if target is not None:
                    resolved_imports.append(target)
                    edges.append([rel_path, target])
                    imported_by_map.setdefault(target, []).append(rel_path)

            for module_name, names in from_imports:
                targets: set[str] = set()
                if names:
                    for name in names:
                        target = _resolve_python_from_import(repo_path, module_name, name)
                        if target is not None:
                            targets.add(target)
                else:
                    target = _resolve_python_module(repo_path, module_name)
                    if target is not None:
                        targets.add(target)
                for target in sorted(targets):
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
