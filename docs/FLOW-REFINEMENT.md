# Flow analysis (refinement and discovery)

Aletheore's Semgrep findings for data-flow weaknesses (SQL, command, path, LDAP, XPath injection,
XSS, trust-boundary violations) are checked by a small abstract interpreter before they are
reported. It asks one question about each finding: **can the value that reaches the sink be proven
not attacker controlled?** If yes, the finding is left out of `findings` and recorded under
`security.static_analysis.suppressed_findings` with a reason. If the engine cannot prove it, the
finding stays. Currently Java only.

The same engine also runs in **discover mode**: it follows request data through the code and
reports a finding where it reaches a SQL, command, file, output, LDAP, XPath or session sink without
being validated or encoded for that sink. This finds flows Semgrep's open-source engine misses, because
it follows calls into other methods and files and tracks values through local lists, maps and
`StringBuilder`s. These findings carry the tool name `aletheore-flow` and a `flow.java.<class>` rule
id; one that Semgrep already reported at the same place is dropped in favor of Semgrep's.
`ALETHEORE_DISABLE_FLOW_DISCOVERY=1` turns discover mode off.

Sinks are matched by what the call does, not by how the value is built: constructing a `File` or a
`Path` is not a finding, opening, reading, writing or deleting it is; only the path arguments of
`Files.*` count, not the content; LDAP and plain `execute` calls need a known receiver type; and the
trust-boundary class needs a tainted string, not a form-bound object.

Python discover mode follows request data from Flask, Django, FastAPI and similar entry points
(route parameters, `request.*`, `sys.argv`, `input()`) through functions, classes and imported modules
to SQL, command, path, XSS, LDAP, XPath, session, code-injection (`eval`/`exec`), deserialization
(`pickle`, `yaml.load`), request-forgery (`requests.get`) and redirect sinks. It understands f-strings
and `%`/`.format` formatting, containers, tuple unpacking, and recognizes the usual validations:
`if name in ALLOWED`, `name == "x"`, `name.isalnum()`, the early-exit form
`if not name.isalnum(): return`, `"".join(c for c in name if c.isalnum())` and `re.sub("[^a-z0-9_-]", "", name)`.
The same specificity rules apply: `send_from_directory` is not a sink (it is the safe way to serve a
file), a framework response builder such as `jsonify` or `render_template` is not a tainted string, and
`str.replace` is not `Path.replace`.

JavaScript and TypeScript discover mode follows request data from Express, Koa and Fastify-style
handlers (`req.query`, `req.body`, `req.params`, `ctx`, `process.argv`) through functions, closures,
constructor functions and `require`/`import`ed modules to SQL, NoSQL (`$where`), command, path, XSS,
`eval`/`Function`, redirect, request-forgery, deserialization and session sinks. Handlers are found where
they are registered (`app.get("/x", ...)`), where they are assigned (`this.handle = (req, res) => ...`)
and wherever a function takes a request-like parameter, typed or not. Callbacks are run with the values
they capture. A response with an explicit non-HTML `Content-Type` is not reported as XSS, and a file read
through a tainted path is a traversal finding but its contents are not treated as request data.

PHP follows the superglobals (`$_GET`, `$_POST`, `$_REQUEST`, `$_COOKIE`, `$_FILES`, tainted `$_SERVER`
keys, `php://input`) through functions, classes and `include`/`require`d files to SQL, command, path,
output (`echo`, `print`, `die`), file-inclusion, `eval`, deserialization, request-forgery, redirect, LDAP,
XPath and session sinks, and it also checks Semgrep's PHP findings. An `include` with a literal path, or a
path built from a small set of constants (a `switch` choosing a per-level file), is followed, and a path whose
leading directory is an unevaluable constant is resolved by its known ending. Guards are tracked per value:
`is_numeric($octet[0])`, `in_array`, `=== 'const'`, `strtolower($ext) == 'jpg'`, `ctype_*`, anchored
`preg_match` whitelists and the early-exit forms mark that exact value safe, and so is anything derived only
from validated values (while the unvalidated parent it came from stays tainted). Casts and hashes are safe,
`$_FILES[...]['tmp_name']` is server generated, and escaping helpers are recognized per weakness class, by
their standard names and by convention (`quoteString`, `backquote`, `esc_html`).

Go follows `*http.Request` (`FormValue`, `URL.Query().Get`, `Header.Get`, `Cookie`, `Body`) and gin, echo and fiber
contexts through functions, struct methods, closures registered with `http.HandleFunc` and packages resolved
through their imports to SQL, command, path, output, redirect, request-forgery, LDAP, XPath, code-loading,
deserialization and session sinks. A prepared statement's `Query`/`Exec` arguments are bound parameters, not SQL
text; `exec.Command("sh", "-c", ...)` is judged on the whole argument list; an explicit non-HTML `Content-Type`
set through `w.Header().Set` is respected; `os.Stat` is an existence check, not file access; and a method on a
record fetched by a tainted key (a database lookup) does not return a tainted string. Command-line arguments
(`os.Args`, `sys.argv`, `process.argv`, `$argv`) and `input()` are not treated as attacker input in any language:
they are the invoking user's own authority and produced noise on every real project tried.

## What it models

- Constants and arithmetic, and string and `char` operations on constants.
- `if`, ternary and `switch` with constant conditions: a branch that can never run is pruned.
- Loops (one pass, merged with "no pass"), `try`/`catch`, early `return`.
- Local lists, maps and `StringBuilder`s, including `add`, `remove` and `get` by constant index or key.
- Object creation, fields, and calls to methods defined in the repository, followed across files and
  nested classes to a bounded depth. A cast to a repository interface resolves to its implementations.
- Context encoders matched to the weakness (an HTML encoder does not sanitize a SQL sink).
- Request objects are sources; method parameters and fields are unknown, so a method that simply
  forwards its parameter to a sink is never suppressed.

## Why it cannot hide a real vulnerability

The engine only ever proves safety. Anything it cannot model becomes "unknown", which keeps the
finding: unresolved calls, unmodeled library methods, containers handed to unknown code, and
mutations under a condition that may not hold. A library call counts as pure only if the class is on
an allowlist, so a chain such as a thread-local request holder is never mistaken for a constant. Taint
that passes through any unmodeled call stays tainted. Every failure inside the engine keeps the
finding. `src/tests/test_flow_java.py` holds the adversarial cases: tainted flows that must never be
dropped, next to safe ones that must.

## Controls

- `ALETHEORE_DISABLE_FLOW_REFINEMENT=1` turns it off.
- `aletheore scan` prints how many findings it suppressed.
- Limits: at most 20,000 findings and 120 seconds per scan for refinement and 150 seconds per language for
discovery; beyond that findings are kept unexamined. A weakness class is skipped entirely when none of its sink
names appears anywhere in the repository.

## Measured effect

On the OWASP Benchmark for Java (2,740 tests), the scan with this engine reaches a true-positive
rate of 96% (88% with Semgrep alone) and a false-positive rate of 10% (42%); discover mode alone has
no false positives on that benchmark. No vulnerable test lost its finding to refinement. The
benchmark's safe tests are built from exactly the constructs this engine models (dead branches,
constant switches, list and map juggling, helper classes), so the real-world improvement is expected
to be smaller.

On real Python projects (vulpy, pygoat) discover mode reported 15 findings, 10 of them already in
Semgrep's output, and the other five were mostly real (an `eval` of a user expression, a documented
path traversal, an unvalidated command argument). Semgrep's registry reports almost no data-flow
findings for Python (12 of 45 on vulpy, 7 of 199 on pygoat), so discovery is where Python gains come from.

On NodeGoat, a deliberately vulnerable Node app, Semgrep's registry reported one data-flow finding;
discover mode found six, every one a documented vulnerability (three `eval(req.body.*)`, an open redirect,
SSRF, and the NoSQL injection that reaches `$where` through a DAO in another file). On Juice Shop it
found twelve, mostly documented challenges (the two `$where` injections, `eval` in the profile route,
SSRF in the image-URL upload, path traversal through the code-snippet key).

On DVWA, which ships each vulnerability at four security levels, discover mode reports nothing in the
`impossible` (secure) files and finds the vulnerable ones (SQL injection, command injection, file inclusion,
open redirect, upload path traversal, XSS at the shared output sink); refinement removed the secure-level
false positives Semgrep reports without touching a vulnerable level. On phpMyAdmin, a mature codebase, discovery
reports 15 findings, mostly queries the tool builds from user-supplied SQL by design.

On real Java projects the picture is more modest and more useful: refinement suppressed 8 findings across
seven projects (all checked by hand, all correct apart from one XXE configuration finding that led to
narrowing the eligible weakness classes), and discover mode reported 17 findings Semgrep had not,
about 15 of them clearly real (a remote-code-execution filter, SQL injection through helper methods,
zip-slip, path traversal through uploaded file names). The setup is in
`benchmarks/deterministic-scan-benchmark`.

## Not covered yet

Go, C#, PHP, Ruby, Kotlin and Rust (their tree-sitter grammars are present; the interpreters are not
written), refinement of Semgrep findings for Python and JavaScript, browser-side (DOM) code, arrow-form `switch`,
lambdas and anonymous classes, and values that flow through fields set in another method.
