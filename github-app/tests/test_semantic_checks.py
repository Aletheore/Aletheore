from scan_worker.semantic_checks import find_semantic_regressions


def test_java_empty_catch_inline_braces_is_flagged():
    diff = (
        "--- Service.java ---\n@@ -1,3 +1,5 @@\n"
        " void run() {\n"
        "-    doWork();\n"
        "+    try {\n"
        "+        doWork();\n"
        "+    } catch (IOException e) {}\n"
    )
    file_contents = {
        "Service.java": (
            "void run() {\n"
            "    try {\n"
            "        doWork();\n"
            "    } catch (IOException e) {}\n"
        )
    }
    findings = find_semantic_regressions(diff, file_contents, "")
    assert len(findings) == 1
    assert findings[0]["file"] == "Service.java"
    assert "empty body" in findings[0]["issue"]


def test_java_empty_catch_multiline_with_comment_only_body_is_flagged():
    diff = (
        "--- Service.java ---\n@@ -1,2 +1,5 @@\n"
        "+    try {\n"
        "+        doWork();\n"
        "+    } catch (IOException e) {\n"
        "+        // ignore\n"
        "+    }\n"
    )
    file_contents = {
        "Service.java": (
            "void run() {\n"
            "    try {\n"
            "        doWork();\n"
            "    } catch (IOException e) {\n"
            "        // ignore\n"
            "    }\n"
            "}\n"
        )
    }
    findings = find_semantic_regressions(diff, file_contents, "")
    assert len(findings) == 1
    assert "empty body" in findings[0]["issue"]


def test_java_catch_with_real_handling_is_not_flagged():
    diff = (
        "--- Service.java ---\n@@ -1,2 +1,5 @@\n"
        "+    try {\n"
        "+        doWork();\n"
        "+    } catch (IOException e) {\n"
        "+        logger.warn(\"failed\", e);\n"
        "+    }\n"
    )
    file_contents = {
        "Service.java": (
            "void run() {\n"
            "    try {\n"
            "        doWork();\n"
            "    } catch (IOException e) {\n"
            "        logger.warn(\"failed\", e);\n"
            "    }\n"
            "}\n"
        )
    }
    findings = find_semantic_regressions(diff, file_contents, "")
    assert findings == []


def test_java_catch_that_rethrows_on_the_opening_line_is_not_flagged():
    diff = (
        "--- Service.java ---\n@@ -1,1 +1,1 @@\n"
        "+    } catch (IOException e) { throw new RuntimeException(e); }\n"
    )
    file_contents = {
        "Service.java": (
            "void run() {\n"
            "    } catch (IOException e) { throw new RuntimeException(e); }\n"
            "}\n"
        )
    }
    findings = find_semantic_regressions(diff, file_contents, "")
    assert findings == []


def test_java_catch_with_nested_brace_in_body_is_not_flagged():
    # Deliberately conservative: a nested block inside the catch body means
    # this check's simple "no braces at all until the closer" scan can't
    # safely tell where the catch block actually ends, so it backs off
    # rather than risk mis-tracking brace depth into a false positive.
    diff = (
        "--- Service.java ---\n@@ -1,2 +1,6 @@\n"
        "+    try {\n"
        "+        doWork();\n"
        "+    } catch (IOException e) {\n"
        "+        if (retry) {\n"
        "+            doWork();\n"
        "+        }\n"
        "+    }\n"
    )
    file_contents = {
        "Service.java": (
            "void run() {\n"
            "    try {\n"
            "        doWork();\n"
            "    } catch (IOException e) {\n"
            "        if (retry) {\n"
            "            doWork();\n"
            "        }\n"
            "    }\n"
            "}\n"
        )
    }
    findings = find_semantic_regressions(diff, file_contents, "")
    assert findings == []


def test_java_empty_catch_not_flagged_when_line_is_unchanged_context():
    # Only newly-added catch blocks are in scope - an unchanged one already
    # existed before this diff and isn't something the diff introduced.
    diff = (
        "--- Service.java ---\n@@ -1,3 +1,3 @@\n"
        " void run() {\n"
        "     } catch (IOException e) {}\n"
        "-    oldCall();\n"
        "+    newCall();\n"
    )
    file_contents = {
        "Service.java": (
            "void run() {\n"
            "    } catch (IOException e) {}\n"
            "    newCall();\n"
            "}\n"
        )
    }
    findings = find_semantic_regressions(diff, file_contents, "")
    assert findings == []


def test_java_removed_exception_handler_is_flagged():
    diff = (
        "--- Caller.java ---\n@@ -1,2 +1,2 @@\n"
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
        "--- Caller.java ---\n@@ -1,1 +1,1 @@\n"
        "-    try { opOne(key, store); } catch (ErrorA e) { log.warn(\"failed\", e); }\n"
        "+    try { opOne(key, store); } catch (ErrorB e) { log.warn(\"failed\", e); }\n"
    )
    refs = (
        "--- referenced definition (not part of this diff): Callee.java:opOne ---\n"
        "void opOne(String key, Store store) throws ErrorA { ... }"
    )
    file_contents = {
        "Caller.java": (
            "void handler() {\n"
            "    try { opOne(key, store); } catch (ErrorB e) { log.warn(\"failed\", e); }\n"
            "}\n"
        )
    }
    findings = find_semantic_regressions(diff, file_contents, refs)
    assert findings
    assert "catches ErrorB instead" in findings[0]["issue"]


def test_java_unchecked_throw_new_in_body_is_also_detected():
    # No throws clause - unchecked exception, signal comes from a real
    # `throw new X(...)` in the referenced dependency's own body instead.
    diff = (
        "--- Caller.java ---\n@@ -1,1 +1,1 @@\n"
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
        "--- Caller.java ---\n@@ -1,1 +1,1 @@\n"
        "-    try { opOne(key); } catch (ErrorA e) { log.warn(\"a\", e); }\n"
        "+    try { opOne(key); } catch (ErrorA e) { log.warn(\"b\", e); }\n"
    )
    refs = (
        "--- referenced definition (not part of this diff): Callee.java:opOne ---\n"
        "void opOne(String key) throws ErrorA { ... }"
    )
    file_contents = {
        "Caller.java": (
            "void handler() {\n"
            "    try { opOne(key); } catch (ErrorA e) { log.warn(\"b\", e); }\n"
            "}\n"
        )
    }
    findings = find_semantic_regressions(diff, file_contents, refs)
    assert findings == []


def test_java_catch_of_a_broader_supertype_is_not_flagged():
    # Real false positive caught by Aletheore's own Flash Review on the PR
    # that introduced this check (Aletheore/Aletheore#725): catching
    # Exception or Throwable is never wrong, since both are universal
    # supertypes of every exception type - a superclass catch still
    # handles whatever the dependency raises.
    diff = (
        "--- Caller.java ---\n@@ -1,1 +1,1 @@\n"
        "-    try { opOne(key); } catch (IOException e) { log.warn(\"a\", e); }\n"
        "+    try { opOne(key); } catch (Exception e) { log.warn(\"a\", e); }\n"
    )
    refs = (
        "--- referenced definition (not part of this diff): Callee.java:opOne ---\n"
        "void opOne(String key) throws IOException { ... }"
    )
    file_contents = {
        "Caller.java": (
            "void handler() {\n"
            "    try { opOne(key); } catch (Exception e) { log.warn(\"a\", e); }\n"
            "}\n"
        )
    }
    findings = find_semantic_regressions(diff, file_contents, refs)
    assert findings == []


def test_java_removed_handler_detected_when_only_the_catch_line_changes():
    # Real bug found independently by GLM-5.3-Flash reviewing this same PR
    # (Aletheore/Aletheore#725), more severe than it first looked: the
    # overwhelmingly common real diff shape only touches the catch clause
    # itself - `try {` and the try body stay as unchanged context lines,
    # so they never appear in hunk.removed/hunk.added at all. The original
    # _JAVA_CALL_CATCH_RE required a literal `try {...} catch(...)`
    # sequence within the same removed/added text, which this realistic
    # shape can never satisfy regardless of DOTALL - it only ever matched
    # a whole try/catch block removed-and-readded as one unit (rare) or
    # written entirely on one line (unusual Java style). Confirmed
    # directly against this file's own diff parser before fixing.
    diff = (
        "--- Caller.java ---\n@@ -1,5 +1,4 @@\n"
        " void handler() {\n"
        "     try {\n"
        "         opOne(key);\n"
        "-    } catch (ErrorA e) {\n"
        "-        log.warn(\"failed\", e);\n"
        "-    }\n"
        "+    }\n"
    )
    refs = (
        "--- referenced definition (not part of this diff): Callee.java:opOne ---\n"
        "void opOne(String key) throws ErrorA { ... }"
    )
    file_contents = {
        "Caller.java": "void handler() {\n    try {\n        opOne(key);\n    }\n}\n"
    }
    findings = find_semantic_regressions(diff, file_contents, refs)
    assert any("removed its exception handler" in f["issue"] for f in findings)


def test_java_multicatch_that_still_covers_the_raised_exception_is_not_flagged():
    # Real false positive found independently by GLM-5.3-Flash reviewing
    # this same PR (Aletheore/Aletheore#725): replacing `catch
    # (IOException e)` with the multi-catch `catch (IOException |
    # SQLException e)` was flagged as "catches SQLException instead" even
    # though IOException is still handled - the added-handler check used
    # to require EVERY caught type to be in `raised`, instead of the
    # correct "does ANY caught type cover it" test already used on the
    # removed side.
    diff = (
        "--- Caller.java ---\n@@ -1,1 +1,1 @@\n"
        "-    try { opOne(key); } catch (IOException e) { log.warn(\"a\", e); }\n"
        "+    try { opOne(key); } catch (IOException | SQLException e) { log.warn(\"a\", e); }\n"
    )
    refs = (
        "--- referenced definition (not part of this diff): Callee.java:opOne ---\n"
        "void opOne(String key) throws IOException { ... }"
    )
    file_contents = {
        "Caller.java": (
            "void handler() {\n"
            "    try { opOne(key); } catch (IOException | SQLException e) { log.warn(\"a\", e); }\n"
            "}\n"
        )
    }
    findings = find_semantic_regressions(diff, file_contents, refs)
    assert findings == []


def test_java_equality_comparison_on_instance_field_is_not_flagged_as_mutation():
    # Real bug found independently by GLM-5.3-Flash reviewing this same PR
    # (Aletheore/Aletheore#725), confirmed directly: bare `=` in the old
    # regex matched the first `=` of `==`, so a dependency body that only
    # COMPARES instance state satisfied the "mutates shared instance
    # state" premise.
    diff = (
        "--- Caller.java ---\n@@ -1,1 +1,2 @@\n"
        "-    worker(x);\n"
        "+    ExecutorService pool = Executors.newFixedThreadPool(4);\n"
        "+    pool.submit(() -> worker(x));\n"
    )
    refs = "--- referenced definition (not part of this diff): Worker.java:worker ---\nif (this.count == expected) { return; }"
    file_contents = {
        "Caller.java": (
            "void run() {\n"
            "    ExecutorService pool = Executors.newFixedThreadPool(4);\n"
            "    pool.submit(() -> worker(x));\n"
            "}\n"
        )
    }
    findings = find_semantic_regressions(diff, file_contents, refs)
    assert findings == []


def test_java_mutates_input_with_removed_defensive_copy_is_flagged():
    diff = (
        "--- Caller.java ---\n@@ -1,1 +1,1 @@\n"
        "-    List<Item> working = new ArrayList<>(raw);\n"
        "+    sortItems(raw);\n"
    )
    refs = (
        "--- referenced definition (not part of this diff): Callee.java:sortItems ---\n"
        "void sortItems(List<Item> items) { items.sort(...); }"
    )
    file_contents = {"Caller.java": "void run() {\n    sortItems(raw);\n}\n"}
    findings = find_semantic_regressions(diff, file_contents, refs)
    assert any("removed the defensive copy" in f["issue"] for f in findings)


def test_java_defensive_copy_that_remains_is_not_flagged():
    diff = (
        "--- Caller.java ---\n@@ -1,2 +1,2 @@\n"
        "+    List<Item> working = new ArrayList<>(raw);\n"
        "+    sortItems(working);\n"
    )
    refs = (
        "--- referenced definition (not part of this diff): Callee.java:sortItems ---\n"
        "void sortItems(List<Item> items) { items.sort(...); }"
    )
    file_contents = {
        "Caller.java": "void run() {\n    List<Item> working = new ArrayList<>(raw);\n    sortItems(working);\n}\n"
    }
    findings = find_semantic_regressions(diff, file_contents, refs)
    assert findings == []


def test_java_shared_state_under_concurrency_is_flagged():
    diff = (
        "--- Caller.java ---\n@@ -1,1 +1,2 @@\n"
        "-    worker(x);\n"
        "+    ExecutorService pool = Executors.newFixedThreadPool(4);\n"
        "+    pool.submit(() -> worker(x));\n"
    )
    refs = "--- referenced definition (not part of this diff): Worker.java:worker ---\nthis.cache = x;"
    file_contents = {
        "Caller.java": (
            "void run() {\n"
            "    ExecutorService pool = Executors.newFixedThreadPool(4);\n"
            "    pool.submit(() -> worker(x));\n"
            "}\n"
        )
    }
    findings = find_semantic_regressions(diff, file_contents, refs)
    assert any("concurrently" in f["issue"] for f in findings)


def test_java_shared_state_under_synchronized_block_is_not_flagged():
    diff = (
        "--- Caller.java ---\n@@ -1,1 +1,3 @@\n"
        "-    worker(x);\n"
        "+    ExecutorService pool = Executors.newFixedThreadPool(4);\n"
        "+    synchronized (this) {\n"
        "+        pool.submit(() -> worker(x));\n"
        "+    }\n"
    )
    refs = "--- referenced definition (not part of this diff): Worker.java:worker ---\nthis.cache = x;"
    file_contents = {
        "Caller.java": (
            "void run() {\n"
            "    ExecutorService pool = Executors.newFixedThreadPool(4);\n"
            "    synchronized (this) {\n"
            "        pool.submit(() -> worker(x));\n"
            "    }\n"
            "}\n"
        )
    }
    findings = find_semantic_regressions(diff, file_contents, refs)
    assert findings == []


def test_java_retry_loop_mutation_is_flagged():
    diff = (
        "--- Caller.java ---\n@@ -1,1 +1,2 @@\n"
        "-    writeRecord(key, value);\n"
        "+    for (int i = 0; i < retries; i++) {\n"
        "+        writeRecord(key, value);\n"
        "+    }\n"
    )
    refs = "--- referenced definition (not part of this diff): Store.java:writeRecord ---\nstore.put(key, value);"
    file_contents = {
        "Caller.java": (
            "void run() {\n"
            "    for (int i = 0; i < retries; i++) {\n"
            "        writeRecord(key, value);\n"
            "    }\n"
            "}\n"
        )
    }
    findings = find_semantic_regressions(diff, file_contents, refs)
    assert any("could run more than once" in f["issue"] for f in findings)


def test_java_runtime_exec_with_concatenation_is_flagged():
    diff = (
        "--- Runner.java ---\n@@ -1,1 +1,1 @@\n"
        "-    // no-op\n"
        "+    Runtime.getRuntime().exec(\"ping \" + host);\n"
    )
    file_contents = {"Runner.java": "void run() {\n    Runtime.getRuntime().exec(\"ping \" + host);\n}\n"}
    findings = find_semantic_regressions(diff, file_contents, "")
    assert any("inject extra arguments" in f["issue"] for f in findings)
    # Real false positive caught by Aletheore's own Flash Review on the PR
    # that introduced this check (Aletheore/Aletheore#725): Runtime.exec
    # never invokes a shell, so the finding must not claim shell injection
    # or shell metacharacters - it may still correctly say it does NOT
    # invoke a shell, which is why this checks the specific wrong claim
    # rather than the bare word "shell".
    assert not any("shell injection" in f["issue"].lower() or "shell metacharacter" in f["issue"].lower() for f in findings)


def test_java_process_builder_is_never_flagged_even_with_concatenation():
    # Real false positive caught by Aletheore's own Flash Review on the PR
    # that introduced this check (Aletheore/Aletheore#725): ProcessBuilder
    # never invokes a shell, and passing one concatenated string as its
    # sole argument doesn't correspond to a real exploitable shape at all
    # (it just names one literal program with a space in it) - dropped
    # from this check entirely rather than kept with a corrected message.
    diff = (
        "--- Runner.java ---\n@@ -1,1 +1,1 @@\n"
        "-    // no-op\n"
        "+    new ProcessBuilder(\"ping \" + host).start();\n"
    )
    file_contents = {"Runner.java": "void run() {\n    new ProcessBuilder(\"ping \" + host).start();\n}\n"}
    findings = find_semantic_regressions(diff, file_contents, "")
    assert findings == []


def test_go_shell_injection_with_sh_c_is_flagged():
    diff = (
        "--- runner.go ---\n@@ -1,1 +1,1 @@\n"
        "-\t// no-op\n"
        '+\texec.Command("sh", "-c", "ping " + host)\n'
    )
    file_contents = {"runner.go": 'func run() {\n\texec.Command("sh", "-c", "ping " + host)\n}\n'}
    findings = find_semantic_regressions(diff, file_contents, "")
    assert any("shell-injection" in f["issue"] for f in findings)


def test_go_direct_exec_command_is_not_flagged():
    diff = (
        "--- runner.go ---\n@@ -1,1 +1,1 @@\n"
        "-\t// no-op\n"
        '+\texec.Command("ping", host)\n'
    )
    file_contents = {"runner.go": 'func run() {\n\texec.Command("ping", host)\n}\n'}
    findings = find_semantic_regressions(diff, file_contents, "")
    assert findings == []
