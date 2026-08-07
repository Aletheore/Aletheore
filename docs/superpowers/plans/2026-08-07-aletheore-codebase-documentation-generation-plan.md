# Grounded Codebase Documentation Generation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract docstrings, comments, return types, and public/private status into AIR's `symbols` data (currently just `name`/`start_line`/`end_line`/`params`), then render that into a real, citeable API reference — via a new `aletheore docs` command, a `query api-reference` kind, and a matching MCP tool — so Aletheore can hand a company grounded documentation of *their own* codebase, not just findings about it.

**Architecture:** Additive fields on the existing per-symbol dict built by `_symbol_entry()` (`src/aletheore/scanner/graph.py`), populated per-language inside each `_extract_*` function during the same tree-sitter walk that already exists — no second parse pass. A new `docs_reference.py` module turns the enriched evidence into markdown, reusing the file:line citation convention every other Aletheore surface already uses. Exposed three ways off one renderer: a bulk-export CLI command, a query kind (so agents/CLI users can ask for one module without a full export), and an MCP tool (registered automatically via the existing `QUERY_FUNCTIONS` registry — see `_register_query_wrapper_tools` in `mcp_server.py`).

**Tech Stack:** tree-sitter (existing per-language grammars, already vendored), Python stdlib, pytest (existing per-language test files: `test_graph.py`, `test_graph_go.py`, `test_graph_java.py`, `test_graph_csharp.py`).

## Global Constraints

- **EVIDENCE_VERSION must bump to `0.2.0`.** `is_evidence_version_compatible()` (`evidence.py:56`) treats any pre-1.0 minor bump as a breaking schema change on purpose — this is correct here, since every existing consumer that reads `symbols.functions[i]` positionally or via fixed keys should not silently get a dict shape it wasn't written against. Every hand-built evidence fixture across `src/tests/` and `github-app/tests/` that already stamps `EVIDENCE_VERSION` (14 files, from the M1 fix earlier this project) keeps working unchanged since it imports the constant — only fixtures that hardcode a literal `"0.1.0"` string would need updating; grep for that before starting Task 1.
- **New fields are additive and always present, never omitted.** `_symbol_entry()` must always emit `docstring: str | None`, `return_type: str | None`, `is_public: bool` — never leave the key out — so every existing reader (`find_symbols`, dashboard, AIRview, MCP `aletheore_symbols`) that does a plain key access doesn't need new `.get()` defensiveness.
- **Do not guess tree-sitter node types.** `_params_text()`'s own docstring states each language's parameter-list field name was "confirmed empirically per language, not assumed." Doc-comment/docstring node types are less standardized across grammars than parameter lists — each per-language task below starts with a throwaway spike script that parses a small fixture and prints the actual node tree before any extraction code is written. Do not copy a node-type string from memory or from another language's grammar.
- **Language scope for this plan: Python, JavaScript/TypeScript, Go, Java, C#.** These five have an unambiguous, near-universal doc-comment convention (Python docstring-as-first-statement; JSDoc/Javadoc `/** */`; Go's contiguous preceding line comments). Rust, Ruby, PHP, C/C++ have weaker or more varied conventions (Rust has three: `///`, `/** */`, `#[doc]`; C/C++ header comments are pure convention with no compiler-enforced marker) and are explicitly **out of scope** — left for a follow-up plan once each has its own spike. `find_symbols` output for those languages is unchanged; `docstring`/`return_type` are `None` and `is_public` still gets computed (it's naming-convention-based, not comment-based, so it works everywhere immediately — see Task 7).
- **`_extract_c_family` does not call `_symbol_entry`** — it builds its function dict manually (`graph.py:845`). Since C/C++ is out of scope for docstrings, only the additive `is_public` field needs to reach it in Task 7; do not add docstring/return-type plumbing there.
- **Never synthesize prose.** If a symbol has no extracted docstring, the renderer prints "Undocumented — no docstring found at {path}:{line}", not an LLM-written guess at what the function does. This is the same grounding contract the audit report and citation verifier already enforce; a documentation feature that hallucinates descriptions would undercut the product's core claim.
- **Rendering is pure evidence transformation, no LLM call.** `docs_reference.py` reads AIR and formats it — it must work identically to `query`/`diff`, with zero network access and zero API key requirement, matching the free-tier positioning in the README ("`scan`, `query`, `diff` ... need no account and no API key").

---

## Task 1: Schema — extend `_symbol_entry` and bump `EVIDENCE_VERSION`

**Files:**
- Modify: `src/aletheore/evidence.py`, `src/aletheore/scanner/graph.py`
- Test: `src/tests/test_graph.py`, `src/tests/test_evidence.py`

**Interfaces:**
- Produces: `_symbol_entry(source, name_node, enclosing_node, docstring=None, return_type=None, is_public=True)` — three new optional kwargs, defaulted so every existing call site keeps compiling unchanged until Tasks 2-6 wire real values through.

- [ ] **Step 1: Write the failing tests**

Append to `src/tests/test_evidence.py`:

```python
def test_evidence_version_is_0_2_0():
    assert EVIDENCE_VERSION == "0.2.0"
```

Append to `src/tests/test_graph.py`:

```python
def test_symbol_entry_always_includes_docstring_return_type_and_is_public_keys(tmp_path):
    (tmp_path / "a.py").write_text("def f():\n    pass\n")
    modules, _, _ = build_module_graph(tmp_path)
    func = modules[0]["symbols"]["functions"][0]
    assert set(func) == {"name", "start_line", "end_line", "params", "docstring", "return_type", "is_public"}
    assert func["docstring"] is None
    assert func["return_type"] is None
    assert func["is_public"] is True
```

- [ ] **Step 2: Run to verify failure**

Run: `cd src && python -m pytest tests/test_evidence.py tests/test_graph.py -k "0_2_0 or always_includes" -v`
Expected: FAIL — `EVIDENCE_VERSION` is still `"0.1.0"`; `_symbol_entry` dict has only 4 keys.

- [ ] **Step 3: Implement**

In `evidence.py`: change `EVIDENCE_VERSION = "0.1.0"` to `EVIDENCE_VERSION = "0.2.0"`.

In `graph.py`, change `_symbol_entry`:

```python
def _symbol_entry(
    source: bytes,
    name_node: Node,
    enclosing_node: Node,
    docstring: str | None = None,
    return_type: str | None = None,
    is_public: bool = True,
) -> dict:
    return {
        "name": source[name_node.start_byte:name_node.end_byte].decode(),
        "start_line": enclosing_node.start_point[0] + 1,
        "end_line": enclosing_node.end_point[0] + 1,
        "params": _params_text(source, enclosing_node),
        "docstring": docstring,
        "return_type": return_type,
        "is_public": is_public,
    }
```

Grep every hand-built evidence fixture in `src/tests/` and `github-app/tests/` for a literal `"0.1.0"` string (not the `EVIDENCE_VERSION` import) and update it — these are cache/history-snapshot fixtures written before the M1 version-check work, distinct from the 14 files that already import the constant.

- [ ] **Step 4: Verify tests pass**

Run: `cd src && python -m pytest tests/test_evidence.py tests/test_graph.py -v`
Expected: PASS. Also run the full suite once (`python -m pytest`) to catch any fixture still hardcoding `0.1.0` — expect a batch of `IncompatibleEvidenceVersionError` failures if any were missed, same failure mode fixed project-wide in the M1 task.

---

## Task 2: Docstring/return-type extraction — Python

**Files:**
- Modify: `src/aletheore/scanner/graph.py` (`_extract_python`)
- Test: `src/tests/test_graph.py`

**Interfaces:**
- Consumes: Task 1's extended `_symbol_entry`.
- Produces: `_python_docstring(source, enclosing_node) -> str | None`, `_python_return_type(source, enclosing_node) -> str | None`.

- [ ] **Step 1: Spike the actual node shapes (throwaway, not committed)**

```python
import tree_sitter_python as tspython
from tree_sitter import Language, Parser
parser = Parser(Language(tspython.language()))
src = b'def f(x: int) -> str:\n    """Doc."""\n    return "x"\n'
tree = parser.parse(src)
print(tree.root_node.sexp())
```
Confirm: function body is a `block` field; docstring is `block.children[0]` when that child is an `expression_statement` wrapping a `string` node; return type is a `return_type` field directly on `function_definition` (verify the field name empirically, do not assume it matches `parameters`'s pattern).

- [ ] **Step 2: Write the failing tests**

Append to `src/tests/test_graph.py`:

```python
def test_python_extracts_docstring_and_return_type(tmp_path):
    (tmp_path / "a.py").write_text(
        'def greet(name: str) -> str:\n    """Return a greeting."""\n    return f"hi {name}"\n'
    )
    modules, _, _ = build_module_graph(tmp_path)
    func = modules[0]["symbols"]["functions"][0]
    assert func["docstring"] == "Return a greeting."
    assert func["return_type"] == "str"


def test_python_function_with_no_docstring_or_annotation_gets_none(tmp_path):
    (tmp_path / "a.py").write_text("def f(x):\n    return x\n")
    modules, _, _ = build_module_graph(tmp_path)
    func = modules[0]["symbols"]["functions"][0]
    assert func["docstring"] is None
    assert func["return_type"] is None
```

- [ ] **Step 3: Run to verify failure**

Run: `cd src && python -m pytest tests/test_graph.py -k python_extracts_docstring -v`
Expected: FAIL — both fields still `None`.

- [ ] **Step 4: Implement**

Add the two helpers (using the field/node names confirmed in Step 1) and pass their results into both `_symbol_entry(...)` calls inside `_extract_python`. Strip docstring quote characters and leading/trailing whitespace; collapse internal blank-line-only whitespace but preserve the raw text otherwise (no paraphrasing — this is quoted evidence, not a summary).

- [ ] **Step 5: Verify tests pass**

Run: `cd src && python -m pytest tests/test_graph.py -v`
Expected: PASS, including all pre-existing Python graph tests (docstring extraction must not change `params`, `imports`, or line numbers for any existing fixture).

---

## Task 3: Docstring/return-type extraction — JavaScript/TypeScript

**Files:**
- Modify: `src/aletheore/scanner/graph.py` (`_extract_javascript`)
- Test: `src/tests/test_graph.py`

**Interfaces:** `_leading_jsdoc(source, enclosing_node) -> str | None`, `_ts_return_type(source, enclosing_node) -> str | None` (TS-only; plain JS has no annotation, stays `None`).

- [ ] **Step 1: Spike node shapes** for a `/** ... */` block immediately preceding a `function_declaration`/`class_declaration` as a previous sibling in the parent's `children` list (confirm this holds when the function is exported — `export function f() {}` may nest differently than a bare `function f() {}`; verify both). For TS, confirm the `return_type` field name on `function_declaration`.

- [ ] **Step 2: Write the failing tests**

```python
def test_javascript_extracts_jsdoc_comment(tmp_path):
    (tmp_path / "a.js").write_text(
        "/**\n * Adds two numbers.\n */\nfunction add(a, b) {\n  return a + b;\n}\n"
    )
    modules, _, _ = build_module_graph(tmp_path)
    func = modules[0]["symbols"]["functions"][0]
    assert "Adds two numbers." in func["docstring"]


def test_javascript_function_with_no_leading_comment_gets_none(tmp_path):
    (tmp_path / "a.js").write_text("function add(a, b) {\n  return a + b;\n}\n")
    modules, _, _ = build_module_graph(tmp_path)
    assert modules[0]["symbols"]["functions"][0]["docstring"] is None


def test_typescript_extracts_return_type(tmp_path):
    (tmp_path / "a.ts").write_text("function add(a: number, b: number): number {\n  return a + b;\n}\n")
    modules, _, _ = build_module_graph(tmp_path)
    assert modules[0]["symbols"]["functions"][0]["return_type"] == "number"
```

- [ ] **Step 3: Run to verify failure** — `cd src && python -m pytest tests/test_graph.py -k "jsdoc or javascript_function or typescript_extracts" -v`

- [ ] **Step 4: Implement** the two helpers and wire into both `_symbol_entry` calls in `_extract_javascript`. Strip `/**`, `*/`, and leading `* ` per line.

- [ ] **Step 5: Verify** — `cd src && python -m pytest tests/test_graph.py -v` passes, existing JS/TS fixtures unaffected.

---

## Task 4: Docstring extraction — Go

**Files:**
- Modify: `src/aletheore/scanner/graph.py` (`_extract_go`)
- Test: `src/tests/test_graph_go.py`

**Interfaces:** `_leading_go_doc_comment(source, enclosing_node) -> str | None`.

- [ ] **Step 1: Spike node shapes.** Go doc comments are `//` line comments immediately above the declaration with no intervening blank line — confirm whether tree-sitter-go exposes contiguous line comments as separate sibling `comment` nodes (requiring you to walk backward collecting contiguous ones and stop at the first blank-line gap) or as one merged node. Go has no return-type annotation to extract here — `_params_text`'s existing raw-text approach already covers Go signatures well enough via `params`; do not add a separate return-type field for Go in this task (Go's `result` field can be a multi-value tuple, not worth the complexity for this plan's scope).

- [ ] **Step 2: Write the failing tests**

```python
def test_go_extracts_leading_doc_comment(tmp_path):
    (tmp_path / "a.go").write_text(
        "package main\n\n// Add returns the sum of two integers.\nfunc Add(a, b int) int {\n\treturn a + b\n}\n"
    )
    modules, _, _ = build_module_graph(tmp_path)
    func = modules[0]["symbols"]["functions"][0]
    assert func["docstring"] == "Add returns the sum of two integers."


def test_go_function_with_blank_line_before_comment_gets_no_docstring(tmp_path):
    (tmp_path / "a.go").write_text(
        "package main\n\n// Unrelated comment.\n\nfunc Add(a, b int) int {\n\treturn a + b\n}\n"
    )
    modules, _, _ = build_module_graph(tmp_path)
    assert modules[0]["symbols"]["functions"][0]["docstring"] is None
```

- [ ] **Step 3: Run to verify failure** — `cd src && python -m pytest tests/test_graph_go.py -k doc_comment -v`

- [ ] **Step 4: Implement**, wire into `_extract_go`'s `_symbol_entry` calls for both functions and types.

- [ ] **Step 5: Verify** — `cd src && python -m pytest tests/test_graph_go.py -v` passes.

---

## Task 5: Docstring/return-type extraction — Java

**Files:**
- Modify: `src/aletheore/scanner/graph.py` (`_extract_java`)
- Test: `src/tests/test_graph_java.py`

**Interfaces:** `_leading_javadoc(source, enclosing_node) -> str | None` (same `/** */` shape as JSDoc — reuse Task 3's `_leading_jsdoc` helper if the node-tree shape matches on spike; only write a second function if it doesn't), plus a return-type field read directly from `method_declaration`'s `type` field.

- [ ] **Step 1: Spike node shapes** — confirm whether the same generic "immediately preceding block comment" walk from Task 3 works unmodified for `tree-sitter-java`, or whether Java's grammar nests differently (e.g. behind a `modifiers` node that itself precedes the method). Confirm the return-type field name on `method_declaration`.

- [ ] **Step 2: Write the failing tests**

```python
def test_java_extracts_javadoc_and_return_type(tmp_path):
    (tmp_path / "A.java").write_text(
        "public class A {\n"
        "  /**\n   * Adds two numbers.\n   */\n"
        "  public int add(int a, int b) {\n    return a + b;\n  }\n"
        "}\n"
    )
    modules, _, _ = build_module_graph(tmp_path)
    func = modules[0]["symbols"]["functions"][0]
    assert "Adds two numbers." in func["docstring"]
    assert func["return_type"] == "int"
```

- [ ] **Step 3: Run to verify failure** — `cd src && python -m pytest tests/test_graph_java.py -k javadoc -v`

- [ ] **Step 4: Implement**, wire into `_extract_java`.

- [ ] **Step 5: Verify** — `cd src && python -m pytest tests/test_graph_java.py -v` passes.

---

## Task 6: Docstring/return-type extraction — C#

**Files:**
- Modify: `src/aletheore/scanner/graph.py` (`_extract_csharp`)
- Test: `src/tests/test_graph_csharp.py`

**Interfaces:** `_leading_xmldoc(source, enclosing_node) -> str | None` — C# doc comments are `///` line-triples (XML doc comments), not `/** */`, so this needs its own extractor even though the surface intent matches Java/JS. Extract the raw text between `<summary>` and `</summary>` if present; fall back to the raw concatenated `///` lines if no XML tags are used.

- [ ] **Step 1: Spike node shapes** for contiguous `///` line comments preceding a method, and confirm `method_declaration`'s return-type field name.

- [ ] **Step 2: Write the failing tests**

```python
def test_csharp_extracts_summary_from_xmldoc(tmp_path):
    (tmp_path / "A.cs").write_text(
        "public class A {\n"
        "  /// <summary>\n  /// Adds two numbers.\n  /// </summary>\n"
        "  public int Add(int a, int b) {\n    return a + b;\n  }\n"
        "}\n"
    )
    modules, _, _ = build_module_graph(tmp_path)
    func = modules[0]["symbols"]["functions"][0]
    assert func["docstring"] == "Adds two numbers."
    assert func["return_type"] == "int"
```

- [ ] **Step 3: Run to verify failure** — `cd src && python -m pytest tests/test_graph_csharp.py -k xmldoc -v`

- [ ] **Step 4: Implement**, wire into `_extract_csharp`.

- [ ] **Step 5: Verify** — `cd src && python -m pytest tests/test_graph_csharp.py -v` passes.

---

## Task 7: Public/private surface detection — all languages including out-of-scope ones

**Files:**
- Modify: `src/aletheore/scanner/graph.py` (all nine `_extract_*` functions, plus `_extract_c_family`'s manual dict)
- Test: `src/tests/test_graph.py`, `test_graph_go.py`, `test_graph_java.py`, `test_graph_csharp.py`, `test_graph_rust.py`, `test_graph_ruby.py`, `test_graph_php.py`, `test_graph_cpp.py`

**Interfaces:** `_is_public_symbol(name: str, language: str) -> bool` — pure string/naming-convention logic, no tree-sitter node needed, so this task covers every language including the four out-of-scope-for-docstrings ones.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("name,language,expected", [
    ("get_user", "python", True),
    ("_internal_helper", "python", False),
    ("__dunder__", "python", True),  # dunder methods are part of the public protocol
    ("GetUser", "go", True),
    ("getUser", "go", False),
    ("getUser", "javascript", True),   # JS has no enforced convention - default public
    ("PublicMethod", "csharp", True),
    ("privateMethod", "csharp", True),  # C# visibility is a modifier keyword, not name-based - see Task 7 note
])
def test_is_public_symbol(name, language, expected):
    assert _is_public_symbol(name, language) is expected
```

- [ ] **Step 2: Run to verify failure** — `cd src && python -m pytest tests/test_graph.py -k is_public_symbol -v`
Expected: FAIL — function doesn't exist yet.

- [ ] **Step 3: Implement**

```python
def _is_public_symbol(name: str, language: str) -> bool:
    """Best-effort public/private classification from naming convention alone.

    Deliberately conservative: languages whose visibility is a keyword
    modifier (private/public in Java, C#, C++) rather than a naming
    convention are NOT classified from the name - _symbol_entry has no
    modifier text to inspect without a second AST lookup per symbol, out
    of scope for this pass. Those languages default every symbol to
    public; a later pass could read the modifier node directly.
    """
    if language == "go":
        return name[:1].isupper()
    if language in ("python", "ruby"):
        return not name.startswith("_")
    return True
```

Wire a `is_public=_is_public_symbol(name, language_name)` argument into every `_symbol_entry(...)` call site across all nine extractors (the `language_name` variable is already in scope at every call site inside `build_module_graph`'s per-file dispatch), and into `_extract_c_family`'s manual dict construction.

- [ ] **Step 4: Verify tests pass** — `cd src && python -m pytest tests/test_graph.py tests/test_graph_go.py tests/test_graph_java.py tests/test_graph_csharp.py tests/test_graph_rust.py tests/test_graph_ruby.py tests/test_graph_php.py tests/test_graph_cpp.py -v`

---

## Task 8: `docs_reference.py` — grounded markdown renderer

**Files:**
- Create: `src/aletheore/docs_reference.py`
- Test: `src/tests/test_docs_reference.py`

**Interfaces:**
- Produces: `build_module_reference(evidence: dict, module_path: str) -> str` (markdown for one module), `build_api_reference(evidence: dict) -> dict[str, str]` (module path → markdown, public symbols only, across the whole repo).
- Consumes: `evidence["repository"]["modules"]` from AIR (post Task 1-7 schema).

- [ ] **Step 1: Write the failing tests**

```python
def test_build_module_reference_includes_public_symbol_with_docstring_and_citation():
    evidence = {"repository": {"modules": [{
        "path": "src/greet.py", "language": "python", "imports": [], "imported_by": [],
        "symbols": {"functions": [{
            "name": "greet", "start_line": 3, "end_line": 5, "params": "(name: str)",
            "docstring": "Return a greeting.", "return_type": "str", "is_public": True,
        }], "classes": []},
    }]}}
    md = build_module_reference(evidence, "src/greet.py")
    assert "greet(name: str) -> str" in md
    assert "Return a greeting." in md
    assert "src/greet.py:3" in md


def test_build_module_reference_marks_missing_docstring_as_undocumented_not_invented():
    evidence = {"repository": {"modules": [{
        "path": "src/a.py", "language": "python", "imports": [], "imported_by": [],
        "symbols": {"functions": [{
            "name": "f", "start_line": 1, "end_line": 2, "params": "()",
            "docstring": None, "return_type": None, "is_public": True,
        }], "classes": []},
    }]}}
    md = build_module_reference(evidence, "src/a.py")
    assert "Undocumented" in md


def test_build_module_reference_excludes_private_symbols():
    evidence = {"repository": {"modules": [{
        "path": "src/a.py", "language": "python", "imports": [], "imported_by": [],
        "symbols": {"functions": [{
            "name": "_helper", "start_line": 1, "end_line": 2, "params": "()",
            "docstring": None, "return_type": None, "is_public": False,
        }], "classes": []},
    }]}}
    md = build_module_reference(evidence, "src/a.py")
    assert "_helper" not in md


def test_build_api_reference_returns_one_entry_per_module_with_at_least_one_public_symbol():
    evidence = {"repository": {"modules": [
        {"path": "src/a.py", "language": "python", "imports": [], "imported_by": [],
         "symbols": {"functions": [{"name": "f", "start_line": 1, "end_line": 2, "params": "()",
                                     "docstring": None, "return_type": None, "is_public": True}], "classes": []}},
        {"path": "src/empty.py", "language": "python", "imports": [], "imported_by": [],
         "symbols": {"functions": [], "classes": []}},
    ]}}
    refs = build_api_reference(evidence)
    assert set(refs) == {"src/a.py"}
```

- [ ] **Step 2: Run to verify failure** — `cd src && python -m pytest tests/test_docs_reference.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement.** `build_module_reference` iterates `module["symbols"]["functions"]` and `["classes"]`, skips `is_public is False`, renders each as a markdown heading with a fenced signature line (`name(params) -> return_type` when `return_type` is set, else `name(params)`), the docstring or the literal string `"*Undocumented — no docstring found.*"`, and a citation line `` `{path}:{start_line}` ``. `build_api_reference` calls it per module in `evidence["repository"]["modules"]`, skipping modules whose rendered body has zero public symbols, and returns `{module_path: markdown}`.

- [ ] **Step 4: Verify tests pass** — `cd src && python -m pytest tests/test_docs_reference.py -v`

---

## Task 9: Wire into the query/MCP registry

**Files:**
- Modify: `src/aletheore/query.py`, `src/aletheore/mcp_server.py`
- Test: `src/tests/test_query.py`, `src/tests/test_mcp_server.py`

**Interfaces:**
- Produces: `find_api_reference(evidence: dict, target: str | None) -> str` in `query.py` (wraps `build_module_reference`, requires a target module path — errors clearly if the path isn't in `modules`), registered as `"api-reference": (find_api_reference, True)` in `QUERY_FUNCTIONS`.
- The MCP tool `aletheore_api_reference` is generated automatically by `_register_query_wrapper_tools` once the query kind exists — add its description to `_QUERY_TOOL_DESCRIPTIONS` and its name mapping to `_TOOL_NAME_TO_QUERY_KIND` (both in `mcp_server.py`, following the exact pattern every other dynamic query tool already uses — this is the same registry work done for the FR-era MCP tools, e.g. task #81 "Give each dynamic MCP query tool its own description").

- [ ] **Step 1: Write the failing tests**

```python
# test_query.py
def test_find_api_reference_renders_the_named_module():
    evidence = {"repository": {"modules": [{
        "path": "src/greet.py", "language": "python", "imports": [], "imported_by": [],
        "symbols": {"functions": [{"name": "greet", "start_line": 1, "end_line": 2, "params": "()",
                                    "docstring": "Hi.", "return_type": None, "is_public": True}], "classes": []},
    }]}}
    result = find_api_reference(evidence, "src/greet.py")
    assert "greet" in result and "Hi." in result


def test_find_api_reference_raises_clearly_for_unknown_module():
    evidence = {"repository": {"modules": []}}
    with pytest.raises(ValueError, match="src/missing.py"):
        find_api_reference(evidence, "src/missing.py")
```

```python
# test_mcp_server.py — add to whatever test already parametrizes over every registered tool name,
# asserting aletheore_api_reference is present and callable exactly like aletheore_symbol_source.
```

- [ ] **Step 2: Run to verify failure** — `cd src && python -m pytest tests/test_query.py -k api_reference -v`

- [ ] **Step 3: Implement.** Add `find_api_reference` to `query.py`, register in `QUERY_FUNCTIONS`. Add the CLI-facing kind name `"api-reference"` to `cli.py`'s query-kind choice list (wherever `QUERY_FUNCTIONS.keys()` or the hardcoded kind list feeds the typer `Choice`/help text — check both, per the LOW fix "README cleanup - stale query kinds list" earlier in this project, which found these can drift). Add the MCP wiring described above.

- [ ] **Step 4: Verify tests pass** — `cd src && python -m pytest tests/test_query.py tests/test_mcp_server.py tests/test_cli.py -v`. Manually smoke-test: `aletheore query api-reference src/aletheore/evidence.py` against this repo itself.

---

## Task 10: `aletheore docs` CLI command — bulk export

**Files:**
- Modify: `src/aletheore/cli.py`
- Test: `src/tests/test_cli.py`

**Interfaces:** `aletheore docs [PATH] [--out .aletheore/docs]` — loads evidence via the existing `load_evidence` helper (same version-checked path every other command uses), calls `build_api_reference`, writes one `.md` file per module path (mirroring the module's own relative path under `--out`, `.py`→`.md` etc.), prints a summary count, exits non-zero with a clear message if no evidence exists yet (same `FileNotFoundError` handling pattern already used by `_query`/`_diff`).

- [ ] **Step 1: Write the failing tests**

```python
def test_docs_command_writes_one_markdown_file_per_documented_module(tmp_path, runner):
    # reuse this file's existing make_evidence_file() helper, extended with
    # one module that has a public, docstring'd function
    ...
    result = runner.invoke(app, ["docs", str(tmp_path), "--out", str(tmp_path / "docs-out")])
    assert result.exit_code == 0
    assert (tmp_path / "docs-out" / "src" / "greet.md").exists()


def test_docs_command_errors_clearly_with_no_scan(tmp_path, runner):
    result = runner.invoke(app, ["docs", str(tmp_path)])
    assert result.exit_code != 0
    assert "aletheore scan" in result.output
```

- [ ] **Step 2: Run to verify failure** — `cd src && python -m pytest tests/test_cli.py -k "docs_command" -v`

- [ ] **Step 3: Implement** the `docs` typer command in `cli.py`, following the exact structure of the existing `_dashboard`/`_index` commands (progress messages, `load_evidence` try/except for `(FileNotFoundError, IncompatibleEvidenceVersionError)`).

- [ ] **Step 4: Verify tests pass** — `cd src && python -m pytest tests/test_cli.py -v`

---

## Task 11: Documentation

**Files:**
- Modify: `src/README.md`, `README.md`, `website/developers.html`

- [ ] Add `aletheore docs` to `src/README.md`'s full command reference (matching the existing format for `scan`/`audit`/`query`).
- [ ] Add one line to the root `README.md`'s "What's actually shipped" list.
- [ ] Add `aletheore_api_reference` to `website/developers.html`'s `.mcp-tool-list` (bumping the "28 tools" count in the accompanying `<p class="mcp-tool-note">` — this file already has a pattern for exactly this kind of footnote, added for `aletheore_answer`).
- [ ] Run `cd src && python -m pytest` (full suite) and `cd github-app && python -m pytest` (full suite, isolated venv per this project's established verification pattern) once at the end, to catch any evidence-fixture straggler still on `"0.1.0"`.

---

## Explicitly out of scope (follow-up plans)

- **Rust, Ruby, PHP, C/C++ docstring extraction** — deferred pending their own node-shape spikes (see Global Constraints).
- **Structured parameter parsing** (`params` as `[{name, type, default}]` instead of raw text) — the raw text already renders a readable signature; not worth the per-language parsing cost for this pass.
- **Test-to-symbol coverage linkage** ("is this public function tested") — a real differentiator, but a separate data source (coverage.xml / test file parsing) and deserves its own plan.
- **Endpoint API reference (OpenAPI-style) generation** — `api_endpoints` evidence already exists and is a much smaller lift than symbol docs since it doesn't need new extraction, just a renderer; good candidate for the very next plan after this one ships.
