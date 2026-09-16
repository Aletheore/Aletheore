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
    assert any("retry loop" in f["issue"] for f in findings)


def test_java_shell_injection_with_runtime_exec_is_flagged():
    diff = (
        "--- Runner.java ---\n@@ -1,1 +1,1 @@\n"
        "-    // no-op\n"
        "+    Runtime.getRuntime().exec(\"ping \" + host);\n"
    )
    file_contents = {"Runner.java": "void run() {\n    Runtime.getRuntime().exec(\"ping \" + host);\n}\n"}
    findings = find_semantic_regressions(diff, file_contents, "")
    assert any("shell-injection" in f["issue"] for f in findings)


def test_java_process_builder_without_concatenation_is_not_flagged():
    diff = (
        "--- Runner.java ---\n@@ -1,1 +1,1 @@\n"
        "-    // no-op\n"
        "+    new ProcessBuilder(\"ping\", host).start();\n"
    )
    file_contents = {"Runner.java": "void run() {\n    new ProcessBuilder(\"ping\", host).start();\n}\n"}
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
