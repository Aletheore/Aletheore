# Java session trust-boundary check

The bundled `java-session-trust-boundary` Semgrep rule is a taint rule for CWE-501. It reports
an HTTP request value that reaches `HttpSession` storage, either as the attribute **name** or as
its **value**, through `setAttribute` or `putValue`. It runs with the other bundled rules when
Semgrep is installed and static analysis is enabled.

It reports a **warning** with **medium confidence**: a review candidate, not proof that the input
is attacker-controlled or that validation is absent.

## Coverage

- Sources: `HttpServletRequest` `getParameter`, `getParameterValues`, `getParameterMap`,
  `getHeader`, `getHeaders`, `getQueryString` and `getCookies`, `Cookie.getValue`, and Spring
  parameters annotated `@RequestParam`, `@PathVariable`, `@RequestHeader`, `@CookieValue` or
  `@RequestBody`. Imported type names, and fully qualified `javax.servlet.http.` and `jakarta.servlet.http.`
  names, are recognized.
- Sinks: `HttpSession.setAttribute` and `HttpSession.putValue`, on a typed session variable or
  directly on `request.getSession(...)`, for either argument.
- Constants, values overwritten before use, unannotated method parameters, and `setAttribute`
  methods on unrelated types are not reported.
- Conventional `test`/`tests` directories and `Test*.java`/`*Test.java`/`*Tests.java` files are
  excluded; production directory names such as `contest` remain in scope.

## Why it exists

On the OWASP Benchmark for Java (83 vulnerable and 120 safe trust-boundary tests) Semgrep's
registry rules found 52% of the vulnerable cases, and Bearer's trust-boundary rule found 94% but
also flagged 91% of the safe ones. With this rule added to the registry rules, recall is 78% at
72% precision, and the true-positive-minus-false-positive score rises from 9.9 to 20.2 points
(2.2 with Bearer). The measurement setup is in `benchmarks/deterministic-scan-benchmark`.

## Limits

This is a single-file check. Semgrep's taint tracking does not follow values across methods or
files, and it treats a collection as tainted when any element is, so a value a program stores in a
map or list and then deliberately does not read back is still reported. There are no sanitizers:
validation or canonicalization of the value is not recognized, so a validated value is still
reported. Session writes through wrappers and framework session abstractions other than `HttpSession`
are out of scope.

## Regression tests

With `semgrep` on `PATH`, run:

```bash
python src/tests/semgrep_rules/test_java_session_trust_boundary.py
```

The suite invokes the actual engine against positive and negative Java fixtures, compares exact
finding locations, checks for parse errors, and tests path exclusions. It uses only this local
YAML rule, with registry configuration, metrics and version checks disabled.
