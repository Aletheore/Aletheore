# Scanner language coverage

Every language the scanner claims to support, verified with controlled two-file
fixtures (each defining a function, a class, a module-level constant, and
importing across files) plus eight real repositories. Deterministic — no LLM.

Reproduce: `python3 scripts/lang_coverage_matrix.py`

## Current state — all 11 languages

| language | imports | functions | classes | constants |
|---|---|---|---|---|
| python | ✅ | ✅ | ✅ | ✅ |
| javascript (ESM) | ✅ | ✅ | ✅ | ✅ |
| javascript (CommonJS) | ✅ | ✅ | ✅ | ✅ |
| typescript | ✅ | ✅ | ✅ | ✅ |
| go | ✅ | ✅ | ✅ | ✅ |
| rust | ✅ | ✅ | ✅ | ✅ |
| java | ✅ | ✅ | ✅ | ✅ |
| ruby | ✅ | ✅ | ✅ | ✅ |
| php | ✅ | ✅ | ✅ | ✅ |
| c | ✅ | ✅ | n/a | ✅ |
| cpp | ✅ | ✅ | ✅ | ✅ |
| csharp | ✅ | ✅ | ✅ | ✅ |

Before this work three languages resolved **no imports at all** and ten
recorded **no constants**. Everything downstream — clustering, subsystem
naming, importance ranking, AIRview, layer violations — consumes the import
graph, so those languages produced output that was structurally wrong while
looking normal.

## What was broken, measured on real repositories

### CommonJS: empty dependency graph

`_extract_javascript` handled only ESM `import_statement`, never `require()`.

| expressjs/express | before | after |
|---|---|---|
| modules with resolved imports | **0 / 141** | 125 / 141 |
| import edges | **0** | 159 |
| clusters | 141 | 27 |
| singleton clusters | **141 / 141** | 4 / 27 |

A full AIRview build would have made 141 LLM calls to describe 141 one-file
"subsystems".

### JavaScript: assigned function expressions were not symbols

Only `function f(){}` and `class C{}` counted, but Express defines its entire
surface as `app.use = function use(fn) {...}`.

| expressjs/express | before | after |
|---|---|---|
| files with zero symbols | 102 / 141 | 86 / 141 |
| functions extracted | ~0 in lib/ | 231 |

Now covers `const f = () => {}`, `exports.f = function(){}` and
`Foo.prototype.bar = function(){}`.

### Rust: two silent failures

`serde-rs/serde` scanned as **208 modules, 0 import edges, 208 singleton clusters.**

1. `_rust_crate_root` only checked `<repo>/src/lib.rs`, so **Cargo workspaces** —
   serde, tokio, rust-analyzer, most large Rust projects — resolved nothing.
2. `mod foo;` was not treated as an edge, though it is how a crate declares its
   module tree. A crate whose `lib.rs` is all `mod` statements had no edges.

After: **25 of 33 crate-source files resolve imports.** The remaining 151 files
are integration tests that `use serde::` across a crate boundary, where zero is
correct.

### C#: flat projects resolved nothing

`_csharp_prefix_and_root_for` required at least one trailing namespace segment
to match a real directory, so a project whose namespace comes entirely from
`<RootNamespace>` with no mirroring folders contributed nothing to the prefix
map. Now falls back to treating the whole namespace as an implicit prefix rooted
at the file's own directory.

### Module-level constants: Python only

A file can export a substantial public API without a single function or class —
Flask's `signals.py` is ten assignments exporting ten public signals, and was
invisible to every consumer of the evidence. The same shape is everywhere:
`export const`, Go `const` blocks, `pub const`, `public static final`,
`#define`, C# `const`. Now extracted in all 11.

| repo | modules with zero symbols, before → after |
|---|---|
| axios | 131 → 76 |
| gin (go) | 2 → 1 |
| express | 102 → 86 |

## Not a defect

- **C reports no classes.** C has none.
- **PHP requires a PSR-4-compliant layout** (one class per file, named after the
  class). An earlier draft of this register listed PHP as broken; that was a
  non-compliant fixture, not a scanner bug — corrected after verification.
- **Header declarations are not definitions**, so header-only C/C++ libraries
  under-report functions.

## Still open

- Clusters that are entirely tests or docs still get a subsystem and an LLM call
  (7 of 12 on Flask, 150 of 208 on serde).
- Singleton clusters remain common in repos whose test files import nothing.
- Retrieval and wiki quality have been **measured only on Flask**. The scores in
  METHODOLOGY.md are a property of that one Python repo, not of the product, and
  must not be quoted as cross-language numbers.
