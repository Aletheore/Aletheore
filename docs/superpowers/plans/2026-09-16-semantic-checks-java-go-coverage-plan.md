# Semantic Checks Java/Go Coverage Implementation Plan

**Goal:** Close the language-coverage gap in `github-app/scan_worker/semantic_checks.py`'s deterministic PR-review checks, so Java and (where the underlying bug shape genuinely exists) Go diffs get the same grounded, non-LLM findings Python diffs already get.

**Architecture:** No new subsystem. Each check already lives as one small, self-contained function called from `find_semantic_regressions()`. The pattern established tonight for the swallowed-exception check (a sibling `_xxx_findings_java` function, called alongside the existing one, sharing the same `_finding()`/`_Hunk` helpers) is reused for every check below rather than rewriting any check to be multi-language internally - keeps each function single-language-simple and each new function independently testable/revertable.

**Tech Stack:** Pure-Python regex/string-scanning, matching this file's existing style exactly (no AST parser, no tree-sitter - deliberate, matches the module's own stated philosophy of narrow, evidence-only, false-positive-averse checks over general static analysis).

**Spec:** None - this is a direct continuation of tonight's session finding (real F1 tests on the Keycloak/Java corpus showed the deterministic layer contributing 0 candidates across 10 real PRs; investigation traced this to `except`/`raise`-anchored regexes that structurally cannot match Java or Go syntax).

## Global Constraints

- Every new check function must have its own test file coverage (positive case, negative/should-not-fire case, and at least one "backs off rather than guess" case), mirroring the existing Python tests in `github-app/tests/test_flash_review.py` and tonight's `github-app/tests/test_semantic_checks.py`.
- No check may regress an existing Python test. Run `pytest tests/test_flash_review.py tests/test_semantic_checks.py -k semantic or swallow or catch` (or the full suite) after every task.
- Where a real corpus case or CVE exists, cite it in a comment exactly like the existing Python checks do. Where none exists (expected for most of these), say so honestly in the comment rather than inventing a citation - same standard applied to tonight's Java empty-catch check.
- Stay conservative: every new check must be willing to return no finding rather than guess when the pattern is ambiguous (nested braces, multi-statement bodies, etc.) - matches this file's documented philosophy throughout.
- `find_semantic_regressions()`'s public signature does not change. New functions are additive call sites only.

---

## Audit: Full Current State of `semantic_checks.py`

| Check | Current coverage | Gap? | Priority |
|---|---|---|---|
| Removed exception handler (`_check_reference_at_call`, `raised` branch) | Python (`except`/`raise`) only | **Yes - biggest gap** | P0 |
| Wrong exception caught (same function) | Python only | **Yes** | P0 |
| One-shot iterator reused (`yield` branch) | Python only (generator semantics) | Yes, but hardest to generalize correctly | P2 |
| Mutates input / defensive copy removed | Python only (`.append/.sort`, `list()/.copy()`) | **Yes** | P1 |
| Scales by 100 twice | Already language-agnostic (bare `* 100`) | No | - |
| Shared mutable state + concurrency | Python only (`self.`, `ThreadPoolExecutor`) | **Yes** | P1 |
| Retry loop mutation | Partial (`store[key]=` works in JS/Python; not Java's `.put(`) | **Yes** | P1 |
| Moved record/log operation (`_moved_record_findings`) | Partial (`.append(` is Python/JS only) | **Yes** | P2 |
| Resource leak (`_resource_leak_findings`) | Close-detection already Java-compatible; open-detection is Go/Python only | **Partial** | P2 |
| Copy-to-alias (`_copy_to_alias_findings`) | Go only, by design (no equivalent Java bug shape found) | No action - correctly scoped | - |
| Removed bounds clamp | Already covers Python/Java/Go (`max`/`Math.max`/`math.max`) | No | - |
| Swallowed exception (Python) | Python only | Done - Java sibling shipped tonight | - |
| Shell injection | Python only (`os.system`/`subprocess`) | **Yes** | P1 |
| Off-by-one loop bound | Already covers Java/JS/C#/Go | No | - |
| SQL injection | Already language-agnostic | No | - |

**Broader codebase check:** searched `src/aletheore/vulnerabilities.py` (the dependency/OSV vulnerability scanner) for the same kind of language lock-in - it already covers Java (Maven `pom.xml`/Gradle) and Go (`go.mod`) via manifest parsing, so this specific gap is confined to `semantic_checks.py`'s in-diff pattern checks, not a wider pattern across the codebase.

**Scope for this plan:** P0 and P1 rows only (5 checks: removed/wrong exception handler, mutates-input, shared-state-concurrency, retry-loop-mutation, shell-injection). P2 rows (one-shot-iterator, moved-record, resource-leak-open-detection) are real but lower-value/higher-risk gaps, called out at the end as follow-up, not built in this pass.

---

## Task 1: Java exception-handler checks (removed handler + wrong exception caught)

**Files:**
- Modify: `github-app/scan_worker/semantic_checks.py`
- Test: `github-app/tests/test_semantic_checks.py`

**Interfaces:**
- Consumes: `_Hunk`, `_finding()`, `_line_number_near_hunk()`, `_nearest_hunk()`, `_call_lines()`, `_referenced_sources()` - all existing helpers, unchanged.
- Produces: `_check_reference_at_call_java(file, source, call_line, hunk, name, dependency) -> dict | None`, called from `find_semantic_regressions()` alongside the existing Python-only `_check_reference_at_call`.

**Design:** The Python version reasons about a referenced dependency's *declared* exception behavior two ways: (1) it scans the dependency's own body text for `raise ErrorType` statements (Python has no formal checked-exception declaration, so the body is the only signal), (2) it checks whether the diff removed a matching `except ErrorType` or added a mismatched one.

Java has a real, stronger signal Python lacks: a method's `throws` clause is a formal declaration, not just body-scanning. So the Java version should look for `throws\s+([\w.]+(?:\s*,\s*[\w.]+)*)` in the referenced dependency's signature line *first*, falling back to scanning the body for `throw new ErrorType(` if no `throws` clause is present (unchecked exceptions, e.g. `RuntimeException` subclasses, are commonly thrown without a `throws` declaration).

- [ ] **Step 1: Write the failing tests**

```python
def test_java_removed_exception_handler_is_flagged():
    diff = (
        "--- Caller.java ---\n@@ -1,3 +1,2 @@\n"
        " void handler() {\n"
        "-    try { opOne(key, store); } catch (ErrorA e) { log.warn(\"failed\", e); }\n"
        "+    opOne(key, store);\n"
    )
    refs = (
        "--- referenced definition (not part of this diff): Callee.java:opOne ---\n"
        "void opOne(String key, Store store) throws ErrorA { ... }"
    )
    file_contents = {"Caller.java": "void handler() {\n    opOne(key, store);\n}\n"}
    findings = find_semantic_regressions(diff, file_contents, refs)
    assert findings
    assert "removed its exception handler" in findings[0]["issue"]


def test_java_wrong_exception_caught_is_flagged():
    diff = (
        "--- Caller.java ---\n@@ -1,2 +1,2 @@\n"
        "-    try { opOne(key, store); } catch (ErrorA e) { log.warn(\"failed\", e); }\n"
        "+    try { opOne(key, store); } catch (ErrorB e) { log.warn(\"failed\", e); }\n"
    )
    refs = (
        "--- referenced definition (not part of this diff): Callee.java:opOne ---\n"
        "void opOne(String key, Store store) throws ErrorA { ... }"
    )
    file_contents = {
        "Caller.java": "void handler() {\n    try { opOne(key, store); } catch (ErrorB e) { log.warn(\"failed\", e); }\n}\n"
    }
    findings = find_semantic_regressions(diff, file_contents, refs)
    assert findings
    assert "catches ErrorB instead" in findings[0]["issue"]


def test_java_unchecked_throw_new_in_body_is_also_detected():
    # No throws clause - unchecked exception, signal comes from a real
    # `throw new X(...)` in the referenced dependency's own body instead.
    diff = (
        "--- Caller.java ---\n@@ -1,2 +1,1 @@\n"
        "-    try { opOne(key); } catch (IllegalStateException e) { log.warn(\"failed\", e); }\n"
        "+    opOne(key);\n"
    )
    refs = (
        "--- referenced definition (not part of this diff): Callee.java:opOne ---\n"
        "void opOne(String key) { if (key == null) throw new IllegalStateException(\"bad key\"); }"
    )
    file_contents = {"Caller.java": "void handler() {\n    opOne(key);\n}\n"}
    findings = find_semantic_regressions(diff, file_contents, refs)
    assert findings
    assert "removed its exception handler" in findings[0]["issue"]


def test_java_exception_handler_that_still_matches_is_not_flagged():
    diff = (
        "--- Caller.java ---\n@@ -1,2 +1,2 @@\n"
        "-    try { opOne(key); } catch (ErrorA e) { log.warn(\"a\", e); }\n"
        "+    try { opOne(key); } catch (ErrorA e) { log.warn(\"b\", e); }\n"
    )
    refs = (
        "--- referenced definition (not part of this diff): Callee.java:opOne ---\n"
        "void opOne(String key) throws ErrorA { ... }"
    )
    file_contents = {
        "Caller.java": "void handler() {\n    try { opOne(key); } catch (ErrorA e) { log.warn(\"b\", e); }\n}\n"
    }
    findings = find_semantic_regressions(diff, file_contents, refs)
    assert findings == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd github-app && python3 -m pytest tests/test_semantic_checks.py -k java_removed_exception -v`
Expected: FAIL (function doesn't exist yet / not wired into `find_semantic_regressions`).

- [ ] **Step 3: Implement `_check_reference_at_call_java`**

Add above `_moved_record_findings` in `semantic_checks.py`:

```python
_JAVA_THROWS_RE = re.compile(r"\bthrows\s+([\w.]+(?:\s*,\s*[\w.]+)*)")
_JAVA_THROW_NEW_RE = re.compile(r"\bthrow\s+new\s+([\w.]+)\s*\(")
_JAVA_CALL_CATCH_RE = re.compile(
    r"\btry\s*\{.*?\}\s*catch\s*\(\s*(?:final\s+)?([\w.]+(?:\s*\|\s*[\w.]+)*)\s+\w+\s*\)"
)


def _java_declared_exceptions(dependency: str) -> list[str]:
    throws_match = _JAVA_THROWS_RE.search(dependency)
    if throws_match:
        return [t.strip() for t in throws_match.group(1).split(",")]
    return sorted(set(_JAVA_THROW_NEW_RE.findall(dependency)))


def _check_reference_at_call_java(
    file: str,
    source: str,
    call_line: int,
    hunk: "_Hunk",
    name: str,
    dependency: str,
) -> dict | None:
    raised = _java_declared_exceptions(dependency)
    if not raised:
        return None

    removed_text = "\n".join(hunk.removed)
    added_text = "\n".join(hunk.added)

    removed_catch = _JAVA_CALL_CATCH_RE.search(removed_text)
    if removed_catch:
        removed_types = [t.strip() for t in removed_catch.group(1).split("|")]
        still_present = _JAVA_CALL_CATCH_RE.search(added_text)
        if any(t in raised for t in removed_types) and not still_present:
            return _finding(
                file, call_line,
                f"{name} throws {', '.join(raised)}, but the changed code removed its exception handler.",
                f"Restore a catch for {raised[0]} or declare it on the enclosing method.",
            )

    added_catch = _JAVA_CALL_CATCH_RE.search(added_text)
    if added_catch:
        caught = [t.strip() for t in added_catch.group(1).split("|")]
        wrong = [c for c in caught if c not in raised]
        if wrong and removed_catch:
            removed_types = [t.strip() for t in removed_catch.group(1).split("|")]
            if any(t in raised for t in removed_types):
                return _finding(
                    file,
                    _line_number_near_hunk(source, f"catch ({wrong[0]}", hunk) or call_line,
                    f"{name} throws {', '.join(raised)}, but the changed handler catches {wrong[0]} instead.",
                    f"Catch {raised[0]} instead of {wrong[0]}.",
                )

    return None
```

Wire into `find_semantic_regressions()`, inside the existing `for name, (_path, dependency) in references.items():` loop, right after the existing Python call:

```python
                finding = _check_reference_at_call(file, source, call_line, hunk, name, dependency)
                if finding is None:
                    finding = _check_reference_at_call_java(file, source, call_line, hunk, name, dependency)
                if finding is not None:
                    findings.append(finding)
                    break
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd github-app && python3 -m pytest tests/test_semantic_checks.py -v`
Expected: all PASS, including the 6 from tonight's earlier Java empty-catch work.

- [ ] **Step 5: Run the full existing suite to confirm no regressions**

Run: `cd github-app && python3 -m pytest tests/test_flash_review.py -k semantic -v`
Expected: all 47 existing tests still PASS.

- [ ] **Step 6: Commit**

```bash
git add github-app/scan_worker/semantic_checks.py github-app/tests/test_semantic_checks.py
git commit -m "feat: add Java exception-handler checks to semantic_checks.py

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 2: Java mutates-input / defensive-copy-removed check

**Files:**
- Modify: `github-app/scan_worker/semantic_checks.py`
- Test: `github-app/tests/test_semantic_checks.py`

**Interfaces:**
- Consumes: same helpers as Task 1.
- Produces: extends `_check_reference_at_call_java` with one more branch (mirrors the Python version's `.sort/append/extend/insert/pop/remove/update` + `list()/.copy()` branch).

**Design:** Java's mutating collection methods are `.add/.remove/.set/.sort/.clear/.addAll/.removeAll`. Java's defensive-copy idiom is `new ArrayList<>(x)` (or `List.copyOf(x)`, but `copyOf` returns an immutable list so a mutation downstream would throw, not silently corrupt - only `new ArrayList<>(x)` is the relevant "looks copied but isn't" case worth checking).

- [ ] **Step 1: Write the failing tests**

```python
def test_java_mutates_input_with_removed_defensive_copy_is_flagged():
    diff = (
        "--- Caller.java ---\n@@ -1,2 +1,2 @@\n"
        "-    List<Item> working = new ArrayList<>(raw);\n"
        "+    List<Item> working = raw;\n"
    )
    refs = "--- referenced definition (not part of this diff): Callee.java:sortItems ---\nvoid sortItems(List<Item> items) { items.sort(...); }"
    file_contents = {"Caller.java": "void run() {\n    List<Item> working = raw;\n    sortItems(working);\n}\n"}
    # sortItems must actually be called in this file for _call_lines to find it
    file_contents["Caller.java"] += "    sortItems(working);\n"
    findings = find_semantic_regressions(diff, file_contents, refs)
    assert any("removed the defensive copy" in f["issue"] for f in findings)


def test_java_defensive_copy_that_remains_is_not_flagged():
    diff = (
        "--- Caller.java ---\n@@ -1,2 +1,2 @@\n"
        "-    List<Item> working = new ArrayList<>(raw);\n"
        "+    List<Item> working = new ArrayList<>(raw); // renamed comment\n"
    )
    refs = "--- referenced definition (not part of this diff): Callee.java:sortItems ---\nvoid sortItems(List<Item> items) { items.sort(...); }"
    file_contents = {
        "Caller.java": "void run() {\n    List<Item> working = new ArrayList<>(raw);\n    sortItems(working);\n}\n"
    }
    findings = find_semantic_regressions(diff, file_contents, refs)
    assert findings == []
```

- [ ] **Step 2: Run to confirm failure**, same command pattern as Task 1.

- [ ] **Step 3: Implement**

Add a `_JAVA_MUTATES_RE = re.compile(r"\.(?:add|addAll|remove|removeAll|set|sort|clear)\s*\(")` and a defensive-copy branch inside `_check_reference_at_call_java`, mirroring the Python version's structure exactly:

```python
    if _JAVA_MUTATES_RE.search(dependency):
        copied = re.search(r"\b(\w+)\s*=\s*new\s+ArrayList<>\s*\(\s*(\w+)\s*\)", removed_text)
        if copied and re.search(rf"\b{re.escape(name)}\s*\(\s*{re.escape(copied.group(2))}\b", added_text):
            return _finding(
                file, call_line,
                f"{name} mutates its input, but the changed code removed the defensive copy.",
                f"Pass `new ArrayList<>({copied.group(2)})` instead of {copied.group(2)} directly.",
            )
```

Note this branch checks `dependency` (the *referenced* method body) for mutation evidence, same as Python - not `added_text`. Place it after the exception-handling return in `_check_reference_at_call_java` so it only runs when no exception-handling finding already fired for this call site.

- [ ] **Step 4-6:** same run/verify/commit pattern as Task 1.

---

## Task 3: Java shared-mutable-state-under-concurrency check

**Files:** same two files as above.

**Design:** Python version keys on `self.attr (+=|=)` plus `ThreadPoolExecutor|pool.map|Executor|concurrent` in the added lines. Java equivalent: `this.field (+=|=)` plus `ExecutorService|CompletableFuture|Executors\.|synchronized\b` (note: presence of `synchronized` should actually suppress the finding, not trigger it - it signals the mutation *is* guarded). Structure:

```python
_JAVA_CONCURRENCY_RE = re.compile(r"\b(?:ExecutorService|CompletableFuture|Executors\.\w+)\b")
_JAVA_SYNCHRONIZED_RE = re.compile(r"\bsynchronized\b")

    if (
        re.search(r"\bthis\.[A-Za-z_]\w*\s*(?:\+=|=)", dependency)
        and _JAVA_CONCURRENCY_RE.search(added_text)
        and not _JAVA_SYNCHRONIZED_RE.search(added_text)
    ):
        return _finding(
            file, call_line,
            f"{name} mutates shared instance state while the changed code calls it concurrently.",
            "Synchronize the shared state or use an isolated instance per task.",
        )
```

- [ ] Write failing test (concurrency-added case fires; a `synchronized` block does not; unrelated `ExecutorService` usage elsewhere in the file does not - reuse the existing Python test's "unrelated to call site" pattern from `test_semantic_checker_does_not_flag_concurrency_unrelated_to_the_call_site`).
- [ ] Verify failure, implement, verify pass, run full suite, commit - same pattern as Task 1.

---

## Task 4: Java retry-loop-mutation check

**Files:** same two files.

**Design:** Python version keys on `store[key]=`/`cache[...]=`/`db[...]=` plus a `for`/`while` in added lines. Java's equivalent mutation idiom is `.put(` on a `Map` (not bracket indexing - Java has no `[]` assignment on objects). Add a parallel branch matching `\b(?:store|cache|db)\.put\s*\(` in `dependency`, with the same "called more than once inside a loop" added-lines check as the Python version.

- [ ] Failing test, implement, verify, full suite, commit.

---

## Task 5: Shell-injection - Java and Go variants

**Files:**
- Modify: `github-app/scan_worker/semantic_checks.py`
- Test: `github-app/tests/test_semantic_checks.py`

**Design:** This one is a standalone check (not part of `_check_reference_at_call`), same shape as tonight's Java empty-catch work - add sibling functions, not a rewrite.

Java: `Runtime.getRuntime().exec(...)` or `new ProcessBuilder(...)` where the command string is built via `+` concatenation (mirrors the existing `_STRING_CONCAT_RE`, which is already language-agnostic and can be reused as-is).

Go: `exec.Command("sh", "-c", ...)` or `exec.Command("bash", "-c", ...)` where the final argument is built via `+` or `fmt.Sprintf` with a variable. Go's idiom is different enough (no generic "shell=True" flag - the shell-invocation risk is *only* present when `sh -c`/`bash -c` is the literal command) that it needs its own regex, not a shared one with Java.

```python
_JAVA_SHELL_CALL_RE = re.compile(
    r"\bRuntime\.getRuntime\(\)\.exec\s*\(|\bnew\s+ProcessBuilder\s*\("
)

def _shell_injection_findings_java(file: str, source: str, hunks: list[_Hunk]) -> list[dict]:
    findings: list[dict] = []
    for hunk in hunks:
        for added_line in hunk.added:
            if not _JAVA_SHELL_CALL_RE.search(added_line):
                continue
            if not _STRING_CONCAT_RE.search(added_line):
                continue
            findings.append(_finding(
                file,
                _line_number_near_hunk(source, added_line.strip(), hunk) or hunk.new_start,
                "This runs a shell command built by concatenating a variable directly into the "
                "command text - a shell-injection risk if that value can be influenced by a caller.",
                "Avoid Runtime.exec/ProcessBuilder with concatenated input - pass arguments as a "
                "String[] (or List<String>) instead of building one command string.",
            ))
    return findings


_GO_SHELL_CALL_RE = re.compile(
    r'\bexec\.Command\s*\(\s*"(?:sh|bash)"\s*,\s*"-c"\s*,'
)


def _shell_injection_findings_go(file: str, source: str, hunks: list[_Hunk]) -> list[dict]:
    findings: list[dict] = []
    for hunk in hunks:
        for added_line in hunk.added:
            if not _GO_SHELL_CALL_RE.search(added_line):
                continue
            if not (_STRING_CONCAT_RE.search(added_line) or "fmt.Sprintf" in added_line):
                continue
            findings.append(_finding(
                file,
                _line_number_near_hunk(source, added_line.strip(), hunk) or hunk.new_start,
                "This runs `sh -c`/`bash -c` with a command string built from a variable - a "
                "shell-injection risk if that value can be influenced by a caller.",
                "Call the target binary directly via exec.Command with separate arguments instead "
                "of building a shell command string.",
            ))
    return findings
```

Wire both into `find_semantic_regressions()` next to the existing `_shell_injection_findings` call.

- [ ] Failing tests for both (Java: `Runtime.getRuntime().exec("sh -c " + cmd)`; Go: `exec.Command("sh", "-c", "echo " + input)`; plus one negative case each - Java with a `String[]` args array, Go with a literal fixed command).
- [ ] Verify failure, implement, verify pass, full suite, commit.

---

## Final Step: Verify against the real Keycloak corpus

After all 5 tasks land:

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
set -a && source github-app/.env && source .env && set +a
python3 /private/tmp/claude-501/-Users-arihantkaul-Documents-GitHub-Veridion/c7ddc4c6-0a76-4909-9958-1aa98bb379d7/scratchpad/pr_agent_prompt_deterministic_glm_keycloak.py
```

Report the real "Deterministic candidates contributed" count and F1 - this is the actual proof the work paid off (or the honest finding that this specific 10-PR sample still doesn't happen to contain any of these bug shapes, same caveat as the Java empty-catch check).

## Follow-up (not in this plan's scope)

- **One-shot iterator reused (Java/Go):** genuinely valuable (Java `Stream`s are single-use; Go channels are consume-once) but the pattern-matching shape differs enough from Python's generator-reuse regex that it needs its own design pass, not a quick sibling function. Revisit separately.
- **Moved-record-findings Java variant:** swap `.append(` for `.add(` - small, but lower value than the P0/P1 items above; a two-line follow-up once this plan's tasks are in.
- **Resource-leak Java open-detection:** add `new File(Input|Output)Stream\(|new (File)?Reader\(|new (File)?Writer\(` to `_RESOURCE_OPEN_RE` so the "opened at line X" message works for Java too - the close-detection half already works, this is a small quality-of-message improvement, not a missed-finding bug.
