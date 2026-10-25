# Java command-injection checks

The bundled `java-runtime-exec-command-injection` Semgrep rule reviews variable
commands and executable names passed to `Runtime.exec`, `ProcessBuilder` constructors,
and typed `ProcessBuilder.command` setters. It runs with the other bundled rules
when Semgrep is installed and static analysis is enabled.

It reports a **warning** with **medium confidence**: a review candidate, not proof
that a value is attacker-controlled or that validation is absent.

## Coverage

- Scalar command strings and executable names on locally typed arguments/variables.
- Inline string concatenations used to construct the command or executable.
- `Runtime.exec` overloads, including environment and working-directory arguments.
- Inline command arrays whose first element is variable.
- Whole `String[]` command arrays supplied directly as method parameters.
- Direct `Runtime.getRuntime()` calls and locally typed `Runtime` receivers.

A fixed executable with an ordinary variable argument, such as
`new ProcessBuilder("echo", message)`, is not reported by this rule. The same holds
for fixed first elements in inline arrays and literal command strings. Semgrep's
constant propagation also recognizes the constant-valued variables in the fixtures.
Conventional `test`/`tests` directories and `Test*.java`/`*Test.java`/`*Tests.java` files
are excluded; production directory names such as `contest` remain in scope.

## Limits

This is a structural, single-file check, not general taint analysis. It does not
resolve arbitrary array/list aliases, interprocedural sources, fields with unknown
types, or general sanitization/allowlisting. Local arrays are intentionally not
treated as unsafe merely because an ordinary argument is variable. Variable
arguments to a fixed interpreter (for example `sh -c`) need separate analysis;
absence of this finding does not establish command safety. Constructors/setters
are inspected even when a later `start()` call is not visible.

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
