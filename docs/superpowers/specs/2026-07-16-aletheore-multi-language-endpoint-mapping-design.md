# Aletheore Multi-Language API Endpoint Mapping Design

**Status:** Draft, pending review
**Date:** 2026-07-16

## Problem

The first API endpoint mapping pass (`docs/superpowers/specs/2026-07-16-veridion-api-endpoint-mapping-design.md`,
written under the project's previous name) covered exactly four frameworks across two
languages: Flask, FastAPI, Django (Python), and Express (JavaScript/TypeScript). That spec's own
Non-Goals section explicitly deferred every other framework/language as a separate follow-up.
Aletheore's module graph already understands eleven languages total; only two of them have any
endpoint-mapping coverage. This spec closes that gap for the six languages where it's worth
doing: Go, Rust, Java, Ruby, PHP, and C#.

## Goals

- Add static route extraction for one dominant framework per remaining language, with two
  exceptions where a single choice would misrepresent real-world usage:
  - **Go**: stdlib `net/http`/`gorilla/mux` **and** Gin (no single dominant convention exists).
  - **C#**: ASP.NET Core's attribute-routed Controllers **and** Minimal API (both are current,
    actively used conventions, not a legacy-vs-modern split).
  - Rust (Axum), Java (Spring Boot), Ruby (Rails), PHP (Laravel) each get one extractor —
    each language has one framework used in the overwhelming majority of real code.
- Establish one governing rule for the "prefix/mount not composed into the literal path"
  problem, which recurs in nearly every one of these frameworks, rather than re-deciding it
  once per language: **record the most-local literal path as written. When something composes
  a prefix or expands a routing shorthand that this scanner doesn't resolve, either record it as
  an `unresolved: true` entry (a distinct mount/include-style call, matching the existing Django
  `include()`/Express `app.use()` precedent) or attach a `note` to the entry (a same-file prefix
  that isn't composed in). Never silently resolve it, never silently drop it.**
- Keep every new framework's output flowing through the exact same evidence block, query kind,
  MCP tool, and diff tracking that already exist — no new top-level structure.

## Non-Goals

- **C/C++.** Web frameworks in C/C++ (Crow, Drogon, Pistache) are a minority convention with no
  single dominant choice, unlike every other language in scope here. Explicitly excluded from
  this pass; revisit only if a real need for it shows up later.
- **Full resolution of composed prefixes and expanded shorthands.** This spec deliberately does
  not attempt to compose Spring Boot's class-level `@RequestMapping` prefix into its method-level
  paths, does not expand Rails' `resources :name` into its seven conventional routes, does not
  trace into Laravel's `Route::group()` closures for their prefix, does not resolve ASP.NET's
  `[Route("api/[controller]")]` template, and does not trace into Axum's `.nest(...)` or Go's
  `.PathPrefix().Subrouter()`. Each of these is its own, meaningfully-sized sub-problem (tracing
  calls across scope boundaries, expanding a documented convention into multiple synthetic
  routes) — recording them as `unresolved`/`note`d is the same choice already made for Django/
  Express in the first pass, generalized rather than re-litigated.
- **Exact `framework` tag string values.** The design intent for each extractor is specified
  below; the exact tag strings (e.g. whether Go's stdlib and gorilla/mux calls get one shared
  honest-ambiguity tag, mirroring `flask_or_fastapi`'s precedent, or two separate tags) is a
  plan-time detail, decided once the real tree-sitter AST shapes are verified live — the same
  discipline every previous language addition in this project has used, not guessed upfront.
- **Changes to Phase 2 (`run_healthcheck`).** Every new framework's path-parameter syntax —
  Go/Spring/Laravel/ASP.NET's `{id}`, Rails/Gin/Axum's `:id` — already collapses into the three
  regex patterns `_substitute_path_params` handles today. Confirmed by inspection, not assumed:
  no framework introduces a fourth placeholder syntax. Phase 2 needs zero code changes.
- **A dashboard UI treatment.** Same deferred call made for licenses and the first endpoint-
  mapping pass — the data is reachable via `/api/evidence` the moment it exists.

## Schema Change: `note` field on endpoint entries

The current endpoint entry shape (from the first pass) is:

```python
{
    "method": str | None,
    "path": str,
    "framework": str,
    "file": str,
    "line": int,
    "handler": str,
    "unresolved": bool,
}
```

This spec adds one field:

```python
"note": str | None,   # populated only for same-file, un-composed prefix cases
```

`note` defaults to `None` for every entry from every framework, including the four already
shipped (Flask/FastAPI/Django/Express never populate it). This is additive and backward-
compatible in behavior, but it changes the literal dict shape every existing test asserts
against — every Phase 1 test that does `entries == [{...}]` needs `"note": None` added to its
expected dict. This is a small, mechanical, cross-cutting update, called out explicitly here so
it's planned for rather than discovered mid-implementation.

## Per-Language Design

### Go — stdlib `net/http`/`gorilla/mux`, and Gin

**Scope:** all `.go` files, no filename restriction (same precedent as Flask/FastAPI — Go has
no single conventional routes-file name).

**Patterns:**
- `http.HandleFunc("/path", handler)` — package-qualified call (object identifier literally
  `http`) — stdlib, method-agnostic (`"ANY"`) **unless** the path string uses Go 1.22+'s combined
  pattern syntax (`"GET /users/{id}"`), which embeds the method directly in the string and must
  be parsed out.
- Any other `<var>.HandleFunc(...)` / `<var>.Handle(...)` — assumed gorilla/mux-style router,
  method-agnostic unless followed by a chained `.Methods("GET", "POST")` call, which narrows it
  (the same shape as Flask's `methods=[...]` keyword argument, just expressed as a method chain
  instead of a keyword).
- Gin: `router.GET/POST/PUT/DELETE/PATCH("/path", handler)` (verb-specific), `router.Any(...)`
  (`"ANY"`).

**Unresolved case:** gorilla/mux's `.PathPrefix("/api").Subrouter()` — a distinct sub-router
mount, recorded as `unresolved: true`, same treatment as Express's `app.use()`.

### Rust — Axum

**Scope:** all `.rs` files.

**Patterns:** `Router::new().route("/path", get(handler))` — method combinator functions
(`get`/`post`/`put`/`delete`/`patch`); a chain like `.route("/path", get(h1).post(h2))` produces
two entries for the one path. `any(handler)` → `"ANY"`.

**Unresolved case:** `.nest("/api", nested_router)` — a nested sub-router, recorded as
`unresolved: true`.

### Java — Spring Boot

**Scope:** all `.java` files. A `@RestController`/`@Controller` class-level annotation is not
required for detection — a method-level mapping annotation is signal enough on its own, the same
posture Flask/FastAPI's detection already takes (no verification that the decorated object is
really a `Flask()`/`APIRouter()` instance).

**Patterns:** `@GetMapping/@PostMapping/@PutMapping/@DeleteMapping/@PatchMapping("/path")`
(dedicated per-verb annotations); generic `@RequestMapping("/path")` with no `method=` attribute
→ `"ANY"`, with `method = RequestMethod.GET` → that specific verb.

**Note case:** a class-level `@RequestMapping("/prefix")` composing with a method-level
`@GetMapping("/path")` — the method-level path is recorded literally, with
`"note": "class-level @RequestMapping prefix present, not composed into this path"`.

### Ruby — Rails

**Scope:** only files named `routes.rb` (Rails' routing convention is even more singular than
Django's `urls.py` — every real Rails app has exactly one `config/routes.rb`).

**Patterns:** `get/post/put/patch/delete 'path', to: 'controller#action'` — plain Ruby DSL method
calls inside a `Rails.application.routes.draw do ... end` block. `root to: 'controller#action'`
is recorded as a real `GET /` entry. The `to:` string value is used directly as the `handler`
field — no separate identifier resolution needed, unlike Django where the view is a bare
identifier/attribute.

**Unresolved case:** `resources :name` — Rails' RESTful-routes shorthand, which expands into
seven conventional routes (index/show/new/create/edit/update/destroy) via convention rather than
literal code. Recorded as a single `unresolved: true` entry rather than expanded, the same
treatment as Django's `include()`.

### PHP — Laravel

**Scope:** any `.php` file under a `routes/` directory (Laravel's own convention — typically
`routes/web.php` and `routes/api.php`; `routes/console.php` uses Artisan console syntax, not
`Route::` calls, so it simply won't match anything and needs no special-casing).

**Patterns:** `Route::get/post/put/delete/patch('/path', [Controller::class, 'method'])`;
`Route::any('/path', ...)` → `"ANY"`; `Route::match(['get', 'post'], '/path', ...)` → explicit
multi-method, the same shape as Flask's `methods=[...]` list. An inline closure handler
(`function () {...}`) is recorded as `"<inline handler>"`, matching Express's precedent.

**Note case:** `Route::group(['prefix' => 'admin'], function () {...})` — routes declared
inside the closure are still found (the extractor walks every descendant node regardless of
nesting), just without the outer prefix composed in — each gets
`"note": "declared inside a Route::group() prefix, not composed into this path"`.

### C# — ASP.NET Core (two extractors)

**Scope:** all `.cs` files.

**Attribute routing:** `[HttpGet("path")]`/`[HttpPost]`/`[HttpPut]`/`[HttpDelete]`/`[HttpPatch]`
attributes on methods inside a class. **Note case:** a class-level `[Route("api/[controller]")]`
template composing with a method-level route — recorded with a note, same pattern as Spring
Boot's class-level prefix.

**Minimal API:** `app.MapGet/MapPost/MapPut/MapDelete/MapPatch("/path", handler)`;
`app.MapMethods("/path", new[] {"GET", "POST"}, handler)` → explicit multi-method. **Unresolved
case:** `app.MapGroup("/api")` — a route group mount, recorded as `unresolved: true`, the same
treatment as Express's `app.use()`.

## Testing Strategy

The same discipline as every prior language and framework addition in this project: a real,
minimal, actually-running (or actually-compiled, where a full run isn't practical in a test
harness) application per framework, verified against real code, not hand-written fixtures alone.
The base language toolchains (Go, Rust/cargo, Java/JDK, Ruby, PHP, .NET SDK) are already
installed from the original seven-language module-graph work; the framework-level pieces
(Spring Boot via a minimal Maven project, the `rails` gem, Laravel via `composer create-project`,
`gin`/`axum` as Cargo/Go dependencies) are fresh installs on top of those, the same one-off dev-
environment setup pattern used throughout this project (installed live, confirmed working,
never added as a project dependency of Aletheore itself).

Each language's tests cover: a plain route, a route with a path parameter, a route with an
explicit multi-method declaration (`methods=[...]`-equivalent), and the one indirection case
identified for that language (subrouter/`.nest`/route group/`resources`/class-level prefix),
confirming it's recorded as `unresolved`/`note`d rather than silently resolved or dropped.

## Success Criteria

1. Each of the 6 new frameworks (Go ×2, Rust, Java, Ruby, PHP, C# ×2 — 8 extractors total),
   run against a real compiled/running instance, produces entries matching its actual routes
   exactly.
2. Each language's identified indirection case (subrouter/`.nest`/route group/`resources`/
   class-level prefix) triggers `unresolved: true` or a populated `note`, never a silently wrong
   composed path and never a silently dropped entry.
3. Every Phase 1 test (Flask/FastAPI/Django/Express) still passes, with only the additive
   `"note": None` key added to their expected entry dicts — no behavioral change.
4. `aletheore query endpoints`, the `aletheore_endpoints` MCP tool, `aletheore diff`'s endpoint
   tracking, and `aletheore healthcheck` all continue working unchanged against the enlarged
   endpoint set, confirming the framework-agnostic design from the first pass holds under real
   scale-out.
