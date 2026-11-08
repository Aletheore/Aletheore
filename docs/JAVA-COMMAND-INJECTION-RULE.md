# Java command-injection checks

The bundled `java-runtime-exec-command-injection` Semgrep rule is a taint rule. It reports a
command or executable passed to `Runtime.exec`, a `ProcessBuilder` constructor, or a typed
`ProcessBuilder.command` setter when that value can come from an HTTP request or from a
`String`/`String[]` parameter of the enclosing method. It runs with the other bundled rules when
Semgrep is installed and static analysis is enabled.

It reports a **warning** with **medium confidence**: a review candidate, not proof that a value is
attacker-controlled or that validation is absent.

## Coverage

- Sources: `HttpServletRequest` `getParameter`, `getParameterValues`, `getParameterMap`,
  `getHeader`, `getHeaders`, `getQueryString`, `getInputStream` and `getCookies`, `Cookie.getValue`
  (imported or fully qualified `javax.`/`jakarta.` types), and any `String` or `String[]` parameter of the enclosing method (including `java.lang.`
  qualified types).
- Sinks: only the command or executable position of `Runtime.exec` (all overloads, direct
  `Runtime.getRuntime()` calls and typed `Runtime` receivers), `new ProcessBuilder(...)` and typed
  `ProcessBuilder.command(...)`.
- A fixed executable with a variable argument, such as `new ProcessBuilder("echo", message)`, is
  not reported. The same holds for arrays and lists whose first element is a literal, for literal
  command strings, and for constant-valued variables.
- Conventional `test`/`tests` directories and `Test*.java`/`*Test.java`/`*Tests.java` files are
  excluded; production directory names such as `contest` remain in scope.

## Why it is a taint rule

The first version was a structural pattern that flagged every call whose command was any variable.
Run over the OWASP Benchmark for Java (2,740 tests) it flagged 95 tests, 46 vulnerable and 49 safe,
and together with Semgrep's registry rules it lowered the command-injection score (true-positive
rate minus false-positive rate) from +5.7 points to -1.6. The taint version keeps every case in the
fixture, including the original motivating one (a `String` parameter passed straight to `exec`),
flags 38 of those tests with 16 false positives, and adds no false positive on top of the registry
rules. The measurement setup is in `benchmarks/deterministic-scan-benchmark`.

## Limits

This is a single-file check. Semgrep's taint tracking does not follow values across methods or
files, and it treats a collection as tainted when any element is. A local array or list is not
unsafe merely because an ordinary argument is variable, and an array declared from a literal-first
initializer is treated as having a fixed executable. Fields with unknown types, sanitization and
allowlisting beyond that, and variable arguments to a fixed interpreter (for example `sh -c`) need
separate analysis; absence of this finding does not establish command safety. Constructors and
setters are inspected even when a later `start()` call is not visible.

The type restrictions follow Semgrep's
[typed-metavariable syntax and inference limits](https://docs.semgrep.dev/writing-rules/pattern-syntax#typed-metavariables).

## Regression tests

With `semgrep` on `PATH`, run:

```bash
python src/tests/semgrep_rules/test_java_command_injection.py
```

The suite invokes the actual engine against positive/negative Java fixtures,
compares exact finding locations, checks for parse errors, and tests path exclusions.
It uses only this local YAML rule, with registry configuration, metrics and version
checks disabled. Temporary log/settings/cache paths keep it isolated from user state.
The ordinary pytest suite skips this optional-engine test if Semgrep is absent;
the dedicated CI job installs the pinned scanner and requires it to run.
