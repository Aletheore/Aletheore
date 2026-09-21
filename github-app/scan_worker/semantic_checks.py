"""High-confidence, evidence-only checks for common PR regressions.

These checks intentionally cover a small set of patterns where the changed
file and a referenced definition prove the behavior. They are not a general
static analyzer and return no finding when the necessary evidence is absent.

Every check is scoped to the diff hunk nearest the call site being examined,
not the whole file. A whole-file scope was tried first and rejected: with
file_contents holding the entire current file and referenced_symbol_context
bundling up to 8 unrelated referenced symbols, a whole-file scan means any
matching keyword anywhere in the file - an unrelated except block, an
unrelated added loop, an unrelated ThreadPoolExecutor three functions away -
satisfies a check's condition regardless of whether it has anything to do
with the call being examined. That is a real false-positive surface on
ordinary PRs that touch more than one thing in the same file, and it also
runs in reverse: an unrelated exception handler elsewhere in the file can
mask a genuine regression at the actual call site. Scoping to the nearest
hunk (DIFF_HUNK_TOLERANCE lines, matching flash_review.py's own
DIFF_LINE_TOLERANCE/LINE_CITATION_CONTEXT_WINDOW convention) keeps a check
from firing on evidence that has no spatial relationship to the change it is
supposedly evidence for.

Two check families need a resolved referenced_symbol_context (a cross-file
dependency the diff calls into) to have anything to reason about: the
exception/iterator/mutation/scaling/concurrency/retry checks in
_check_reference_at_call, and the moved-record-operation check in
_moved_record_findings. The rest - _resource_leak_findings,
_copy_to_alias_findings, _swallowed_exception_findings, and
_shell_injection_findings - read only the diff and the current file, and
never require a referenced symbol at all. This split is deliberate and
measured, not incidental: a corpus evaluation against 18 real historical
bugs (benchmarks/pr-review-benchmark/scripts/evaluate_semantic_checks.py)
found a resolvable in-repo referenced symbol in only 6 of them - most real
diffs call no in-repo symbol at all (a same-function bug, a call into a
third-party package, a call into the standard library). A check that can
only ever run when that narrow condition holds will only ever fire on a
narrow slice of real bugs, however precise its logic is once it does run.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from difflib import SequenceMatcher
from pathlib import Path

from aletheore.static_analysis.bearer_scanner import check_bearer
from aletheore.static_analysis.semgrep_scanner import check_semgrep

_REFERENCE_RE = re.compile(
    r"^--- referenced definition \(not part of this diff\): (?P<path>.+?):(?P<name>[^: ]+) ---\n"
    r"(?P<body>.*?)(?=^--- referenced definition |\Z)",
    re.MULTILINE | re.DOTALL,
)

_FILE_MARKER_RE = re.compile(r"^--- (.+) ---$")
_HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

# A call site counts as "part of this change" if it falls within this many
# lines of some hunk in the same file - generous enough to cover a call a
# few lines inside a function whose body was only partly touched, tight
# enough that an unrelated function living in the same large file does not
# count just because it shares a file with something that did change.
# Matches flash_review.py's DIFF_LINE_TOLERANCE deliberately: one proximity
# rationale, reused rather than re-derived.
DIFF_HUNK_TOLERANCE = 8


class _Hunk:
    __slots__ = ("new_start", "new_end", "removed", "added", "raw_body")

    def __init__(self, new_start: int, new_end: int) -> None:
        self.new_start = new_start
        self.new_end = new_end
        self.removed: list[str] = []
        self.added: list[str] = []
        # Every body line in this hunk, in original diff order, WITH its
        # raw one-character diff tag ("-", "+", or " " for unchanged
        # context) still attached - unlike removed/added, which flatten
        # everything into two untagged, unordered-relative-to-each-other
        # lists. Only _unchanged_except_body_weakened reads this; every
        # other check in this file uses removed/added as before.
        self.raw_body: list[str] = []

    def near(self, line: int) -> bool:
        return (self.new_start - DIFF_HUNK_TOLERANCE) <= line <= (self.new_end + DIFF_HUNK_TOLERANCE)


def _referenced_sources(context: str) -> dict[str, tuple[str, str]]:
    return {
        match.group("name"): (match.group("path"), match.group("body"))
        for match in _REFERENCE_RE.finditer(context)
    }


def _diff_hunks_by_file(diff_text: str) -> dict[str, list[_Hunk]]:
    """Every hunk per file, with its new-file line range and its own
    removed/added lines - not flattened across the whole file's diff."""
    hunks: dict[str, list[_Hunk]] = {}
    current_file = ""
    current_hunk: _Hunk | None = None
    # A removed/added source line that happens to read exactly like a file
    # marker (e.g. a deleted comment "-- text ---", which renders as the raw
    # line "--- text ---" once diffed) is indistinguishable from a real
    # "--- {file} ---" separator without a positional guard - the real
    # separator only ever appears after a blank line (or at the very start
    # of the diff text). Matches flash_review.py's _diff_valid_lines fix for
    # the identical collision; without this, a matching line here resets
    # current_file/current_hunk mid-parse, silently misattributing every
    # subsequent removed/added line to the wrong file.
    prev_blank = True  # start-of-text counts as a boundary
    for line in diff_text.splitlines():
        file_match = _FILE_MARKER_RE.match(line)
        if file_match and prev_blank:
            current_file = file_match.group(1)
            current_hunk = None
            prev_blank = False
            continue
        hunk_match = _HUNK_HEADER_RE.match(line)
        if hunk_match:
            new_start = int(hunk_match.group(1))
            new_count = int(hunk_match.group(2)) if hunk_match.group(2) else 1
            # A pure-deletion hunk reports a 0-count new range; still give
            # it a real single-line anchor so proximity checks below have
            # something to compare against, matching the reasoning in
            # flash_review.py's _diff_valid_lines for the same shape.
            current_hunk = _Hunk(new_start, new_start + max(new_count, 1) - 1)
            hunks.setdefault(current_file, []).append(current_hunk)
            prev_blank = False
            continue
        if line == "":
            prev_blank = True
            continue
        if line == r"\ No newline at end of file":
            # Real bug found via audit: git emits this literal marker line
            # immediately after a +/- line whenever that version of the
            # file has no trailing newline - the identical shape
            # flash_review.py's _patch_valid_lines/_diff_valid_lines
            # already have a dedicated fix and comment for, unfixed here.
            # Its own tag ("\\") matches neither " ", "-", nor "+", so
            # unguarded it was appended to raw_body as a phantom entry.
            # _unchanged_except_body_weakened's forward walk treats that
            # entry's incidental one-space indent (" No newline...") as
            # real body content: whenever it's shallower than or equal to
            # the except header's own indent (true for any except nested
            # inside a function, the overwhelmingly common case), the walk
            # reads it as the dedent marking the end of the block and
            # breaks - before ever reaching the hunk's real "+" content
            # that comes after it. That skips this file's own documented
            # bail-out path (running out of hunk without a real dedent),
            # silently treating an incomplete reconstruction as a complete
            # one instead - confirmed directly, a genuine "except body
            # weakened to bare pass" case went unreported because the walk
            # broke on the marker before ever seeing the new "pass" line.
            prev_blank = False
            continue
        prev_blank = False
        if current_hunk is None or not current_file:
            continue
        current_hunk.raw_body.append(line)
        # Real bug this fixes: once inside a hunk body, past both marker
        # checks above, a line's first character is unambiguously the diff
        # +/- prefix - the file-marker collision this "---"/"+++" exclusion
        # was trying to guard against is already correctly handled by
        # prev_blank gating _FILE_MARKER_RE.match above, the fix for #283/
        # #305. This second, redundant guard didn't just fail to add
        # protection - it actively dropped real content: a genuinely
        # removed source line starting with "--" (diffed as "---...", e.g.
        # a deleted Markdown/YAML divider or SQL/shell comment banner) was
        # silently excluded from hunk.removed entirely, invisible to every
        # check in this file that inspects it. Confirmed directly, not
        # theoretical: a hunk removing "--- old section ---" produced an
        # empty hunk.removed.
        if line.startswith("-"):
            current_hunk.removed.append(line[1:])
        elif line.startswith("+"):
            current_hunk.added.append(line[1:])
    return hunks


def _call_lines(source: str, name: str) -> list[int]:
    needle = f"{name}("
    # split("\n"), never splitlines() - see _line_number_near_hunk's
    # docstring for the full explanation (same bug, same fix, same reason,
    # repeated at every source-content line-splitting site in this file).
    return [number for number, line in enumerate(source.split("\n"), 1) if needle in line]


def _line_number(source: str, needle: str) -> int | None:
    for number, line in enumerate(source.split("\n"), 1):
        if needle in line:
            return number
    return None


def _line_number_near_hunk(source: str, needle: str, hunk: "_Hunk") -> int | None:
    """Same as _line_number, but scoped to the window around one hunk
    instead of a whole-file scan for the first match.

    Every finding in this module cites a line via a substring search for
    something like "{var} =" or "except {name}" - for a common name
    (result, count, except ValueError), _line_number's whole-file scan
    returns whichever occurrence happens to appear first in the file, which
    is very often a different, unrelated one from the actual line inside
    the hunk that triggered the check. Confirmed: a variable clamped-then-
    reassigned in a diff hunk, with an earlier unrelated assignment to the
    same name in a different function, cited that earlier line instead of
    the real one. Scoping the search to the same DIFF_HUNK_TOLERANCE window
    every proximity check in this module already uses makes a same-window
    collision the only way to still get this wrong, instead of any same-
    name occurrence anywhere in the file.

    split("\n"), never splitlines() - real bug found in a backward audit:
    splitlines() also breaks on \v, \f, \x1c-\x1e, NEL, LS, and PS, none
    of which GitHub or git treat as a line boundary (they only ever split
    on "\n"). hunk.new_start/new_end are real, \n-based line numbers
    parsed straight from GitHub's own diff headers, so indexing them into
    a splitlines()-produced list silently windows around the WRONG lines
    the moment one of those characters appears anywhere earlier in the
    file - the same bug class already found and fixed in flash_review.py's
    _clickable_suggestion/_line_citation_content_matches and jobs.py's
    _fetch_symbol_source, present here too since every one of them used to
    share the same splitlines()-based line-indexing approach.
    """
    lines = source.split("\n")
    start = max(0, hunk.new_start - 1 - DIFF_HUNK_TOLERANCE)
    end = min(len(lines), hunk.new_end + DIFF_HUNK_TOLERANCE)
    for offset, line in enumerate(lines[start:end]):
        if needle in line:
            return start + offset + 1
    return None


def _nearest_hunk(hunks: list[_Hunk], line: int) -> _Hunk | None:
    candidates = [hunk for hunk in hunks if hunk.near(line)]
    if not candidates:
        return None
    return min(candidates, key=lambda hunk: min(abs(line - hunk.new_start), abs(line - hunk.new_end)))


def _finding(file: str, line: int, issue: str, suggestion: str) -> dict:
    return {"file": file, "line": line, "issue": issue, "suggestion": suggestion}


def _check_reference_at_call(
    file: str,
    source: str,
    call_line: int,
    hunk: _Hunk,
    name: str,
    dependency: str,
) -> dict | None:
    """One reference, one call site, one nearby hunk - every condition
    below is checked against that hunk's own removed/added lines, never the
    whole file's."""
    removed_lines = hunk.removed
    added_lines = hunk.added

    raised = sorted(set(re.findall(r"\braise\s+([A-Za-z_]\w*)", dependency)))
    if raised:
        if any(
            re.search(rf"\bexcept\s+{re.escape(error)}\b", line) for line in removed_lines for error in raised
        ) and not any(
            re.search(rf"\bexcept\s+{re.escape(error)}\b", line)
            # split("\n"), never splitlines() - see _line_number_near_hunk's
            # docstring; same real \n-based hunk line numbers, same bug.
            for line in source.split("\n")[max(0, hunk.new_start - 1 - DIFF_HUNK_TOLERANCE) : hunk.new_end + DIFF_HUNK_TOLERANCE]
            for error in raised
        ):
            return _finding(
                file,
                call_line,
                f"{name} raises {', '.join(raised)}, but the changed code removed its exception handler.",
                f"Restore handling for {raised[0]} or handle the error at this boundary.",
            )

        catches = re.findall(r"\bexcept\s+([A-Za-z_]\w*)", "\n".join(added_lines))
        wrong = [caught for caught in catches if caught not in raised]
        if wrong:
            return _finding(
                file,
                _line_number_near_hunk(source, f"except {wrong[0]}", hunk) or call_line,
                f"{name} raises {', '.join(raised)}, but the changed handler catches {wrong[0]} instead.",
                f"Catch {raised[0]} or translate the dependency error before handling it.",
            )

    if "yield" in dependency:
        # Scoped to the hunk's own nearby window, not the whole file - this
        # docstring's own contract ("every condition below is checked
        # against that hunk's own removed/added lines, never the whole
        # file's") was being violated here specifically. A common variable
        # name (e.g. "results") reused across two genuinely unrelated
        # functions - one already consuming a one-shot iterator correctly
        # elsewhere in the file, another touched by this diff and calling a
        # different yield-based dependency - could trip the len(uses) >= 2
        # whole-file count and fire a false "consumes the iterator twice"
        # finding tied to unrelated code.
        # split("\n"), never splitlines() - see _line_number_near_hunk's
        # docstring; same real \n-based hunk line numbers, same bug.
        window = "\n".join(
            source.split("\n")[
                max(0, hunk.new_start - 1 - DIFF_HUNK_TOLERANCE) : hunk.new_end + DIFF_HUNK_TOLERANCE
            ]
        )
        assignments = re.findall(rf"\b(\w+)\s*=\s*{re.escape(name)}\s*\(", window)
        for variable in assignments:
            uses = re.findall(
                rf"\bfor\s+\w+\s+in\s+{re.escape(variable)}\b|"
                rf"\b(?:sum|list|tuple|set)\([^\n]*\b{re.escape(variable)}\b",
                window,
            )
            if len(uses) >= 2 and any(name in line for line in added_lines):
                return _finding(
                    file,
                    _line_number_near_hunk(source, f"{variable} = {name}(", hunk) or call_line,
                    f"{name} returns a one-shot iterator that the changed code consumes more than once.",
                    f"Materialize {variable} once before performing multiple passes.",
                )

    if re.search(r"\.(?:sort|append|extend|insert|pop|remove|update)\s*\(", dependency):
        copied = re.search(r"\b(\w+)\s*=\s*(?:list|copy)\s*\(\s*(\w+)\s*\)", "\n".join(removed_lines))
        if copied and re.search(rf"\b{re.escape(name)}\s*\(\s*{re.escape(copied.group(2))}\b", "\n".join(added_lines)):
            return _finding(
                file,
                call_line,
                f"{name} mutates its input, but the changed code removed the defensive copy.",
                f"Pass a copy of {copied.group(2)} to {name}.",
            )

    if re.search(r"\*\s*100\b", dependency):
        call_source_line = next((line for line in added_lines if f"{name}(" in line), "")
        if re.search(r"\*\s*100\b", call_source_line):
            return _finding(
                file,
                call_line,
                f"{name} already scales its input by 100, and the changed call scales it again.",
                "Pass the unscaled ratio or remove one of the two percent conversions.",
            )

    if re.search(r"self\.[A-Za-z_]\w*\s*(?:\+=|=)", dependency) and re.search(
        r"(?:ThreadPoolExecutor|pool\.map|Executor|concurrent)", "\n".join(added_lines)
    ):
        return _finding(
            file,
            call_line,
            f"{name} uses shared mutable instance state while the changed code calls it concurrently.",
            "Synchronize the shared state or use an isolated instance per worker.",
        )

    if (
        re.search(r"\b(?:store|cache|db)\s*\[[^\]]+\]\s*=", dependency)
        and any(f"{name}(" in line for line in added_lines)
        and re.search(r"\b(?:for|while)\b", "\n".join(added_lines))
    ):
        return _finding(
            file,
            call_line,
            f"The changed retry loop calls mutating {name} more than once after a failed attempt.",
            "Stop before the extra mutation or make the repeated operation idempotent.",
        )

    return None


# Java sibling of _check_reference_at_call above. Same reasoning shape -
# one reference, one call site, one nearby hunk - but Java has a real
# signal Python lacks: a method's `throws` clause is a formal declaration,
# not just body-scanning. Checked first; falls back to scanning the
# referenced dependency's own body for a real `throw new X(...)` when no
# `throws` clause is present (unchecked exceptions, e.g. RuntimeException
# subclasses, are commonly thrown without one).
_JAVA_THROWS_RE = re.compile(r"\bthrows\s+([\w.]+(?:\s*,\s*[\w.]+)*)")
_JAVA_THROW_NEW_RE = re.compile(r"\bthrow\s+new\s+([\w.]+)\s*\(")
# Deliberately a bare `catch (...)` clause, NOT anchored to a preceding
# `try {...}` on the same removed/added text - a real, more severe defect
# found independently by GLM-5.3-Flash reviewing this same PR (Aletheore/
# Aletheore#725), verified directly against this file's own diff parser:
# the overwhelmingly common real diff shape only touches the catch line
# itself (`try {` and the try body stay as unchanged context, so they
# never appear in hunk.removed/hunk.added at all), which an
# try-block-anchored regex can never match regardless of DOTALL - it only
# ever matched a whole try/catch block removed-and-readded as one unit,
# an unusual diff shape. Confirmed: a synthetic diff changing only
# `catch (ErrorA e)` to `catch (ErrorB e)` produced zero match with the
# try-anchored version and a correct match with this one.
_JAVA_CALL_CATCH_RE = re.compile(
    r"\bcatch\s*\(\s*(?:final\s+)?([\w.]+(?:\s*\|\s*[\w.]+)*)\s+\w+\s*\)"
)
_JAVA_MUTATES_RE = re.compile(r"\.(?:add|addAll|remove|removeAll|set|sort|clear)\s*\(")
_JAVA_CONCURRENCY_RE = re.compile(r"\b(?:ExecutorService|CompletableFuture|Executors\.\w+)\b")
_JAVA_SYNCHRONIZED_RE = re.compile(r"\bsynchronized\b")


def _java_declared_exceptions(dependency: str) -> list[str]:
    throws_match = _JAVA_THROWS_RE.search(dependency)
    if throws_match:
        return [t.strip() for t in throws_match.group(1).split(",")]
    return sorted(set(_JAVA_THROW_NEW_RE.findall(dependency)))


def _java_simple_name(qualified: str) -> str:
    # Real gap found auditing this check: a `throws java.io.IOException`
    # (fully qualified, as a referenced-definition snippet may render it)
    # never string-equals a `catch (IOException e)` (unqualified, as real
    # Java code overwhelmingly writes it via an import) - exact matching
    # silently missed a genuinely removed/mismatched handler. Comparing by
    # simple name instead; a same-simple-name collision across two
    # different packages at one call site is rare enough that this
    # conservative check accepts the risk rather than require real type
    # resolution it doesn't have.
    return qualified.rsplit(".", 1)[-1]


def _check_reference_at_call_java(
    file: str,
    source: str,
    call_line: int,
    hunk: _Hunk,
    name: str,
    dependency: str,
) -> dict | None:
    removed_text = "\n".join(hunk.removed)
    added_text = "\n".join(hunk.added)

    raised = _java_declared_exceptions(dependency)
    if raised:
        raised_simple = {_java_simple_name(t) for t in raised}
        removed_catch = _JAVA_CALL_CATCH_RE.search(removed_text)
        added_catch = _JAVA_CALL_CATCH_RE.search(added_text)

        if removed_catch:
            removed_types = [t.strip() for t in removed_catch.group(1).split("|")]
            if any(_java_simple_name(t) in raised_simple for t in removed_types):
                if not added_catch:
                    return _finding(
                        file, call_line,
                        f"{name} throws {', '.join(raised)}, but the changed code removed its "
                        "exception handler.",
                        f"Restore a catch for {raised[0]} or declare it on the enclosing method.",
                    )
                caught = [t.strip() for t in added_catch.group(1).split("|")]
                # A multi-catch (`catch (A | B e)`) or a broader supertype
                # (Exception/Throwable - universal supertypes of every
                # exception type, checked or unchecked) still handles
                # whatever the dependency raises as long as ANY caught type
                # covers it - matching the same `any(...)` semantics already
                # used above for the removed side. The original version here
                # flagged whenever ANY caught type wasn't in `raised`,
                # which is the wrong direction: two real false positives
                # found independently on the PR that introduced this check
                # (Aletheore/Aletheore#725) - Aletheore's own Flash Review
                # caught the Exception/Throwable case, GLM-5.3-Flash caught
                # the multi-catch case (`catch (IOException | SQLException
                # e)` replacing `catch (IOException e)` was flagged as
                # "catches SQLException instead" even though IOException is
                # still handled). No attempt to recognize other real
                # supertype relationships (e.g. a custom exception
                # hierarchy) without real type information - that would be
                # guessing, not evidence.
                covers_raised = any(
                    _java_simple_name(c) in raised_simple or c in ("Exception", "Throwable") for c in caught
                )
                if not covers_raised:
                    return _finding(
                        file,
                        _line_number_near_hunk(source, f"catch ({caught[0]}", hunk) or call_line,
                        f"{name} throws {', '.join(raised)}, but the changed handler catches "
                        f"{', '.join(caught)} instead.",
                        f"Catch {raised[0]} instead of {', '.join(caught)}.",
                    )

    if _JAVA_MUTATES_RE.search(dependency):
        copied = re.search(r"\b(\w+)\s*=\s*new\s+ArrayList<>\s*\(\s*(\w+)\s*\)", removed_text)
        # Real false positive found auditing this check: it only verified a
        # copy assignment was removed and the same raw variable now reaches
        # the call - never that the removed code actually PASSED the copy
        # to this call. An unrelated removed copy (e.g. one kept for a
        # separate audit log) whose source variable happens to match the
        # call's argument was flagged as a lost defensive copy even though
        # the call never used the copy in the first place.
        if (
            copied
            and re.search(rf"\b{re.escape(name)}\s*\(\s*{re.escape(copied.group(1))}\b", removed_text)
            and re.search(rf"\b{re.escape(name)}\s*\(\s*{re.escape(copied.group(2))}\b", added_text)
        ):
            return _finding(
                file, call_line,
                f"{name} mutates its input, but the changed code removed the defensive copy.",
                f"Pass `new ArrayList<>({copied.group(2)})` instead of {copied.group(2)} directly.",
            )

    # Real bug found independently by GLM-5.3-Flash reviewing this same PR
    # (Aletheore/Aletheore#725), confirmed directly: bare `=` matches the
    # first `=` of `==`, so a dependency body that only COMPARES instance
    # state (`if (this.count == expected)`) satisfied this "mutates shared
    # instance state" premise. `=(?!=)` keeps `+=` and assignment `=`
    # while excluding equality.
    if (
        re.search(r"\bthis\.[A-Za-z_]\w*\s*(?:\+=|=(?!=))", dependency)
        and _JAVA_CONCURRENCY_RE.search(added_text)
        and not _JAVA_SYNCHRONIZED_RE.search(added_text)
    ):
        return _finding(
            file, call_line,
            f"{name} mutates shared instance state while the changed code calls it concurrently.",
            "Synchronize the shared state or use an isolated instance per task.",
        )

    if (
        re.search(r"\b(?:store|cache|db)\s*\.\s*put\s*\(", dependency)
        and f"{name}(" in added_text
        and re.search(r"\b(?:for|while)\b", added_text)
    ):
        # Message softened from the original's flat assertion, per a fair
        # precision critique from GLM-5.3-Flash reviewing this same PR
        # (Aletheore/Aletheore#725): the trigger conditions (a mutating
        # dependency call inside any added loop) don't actually establish
        # a retry-after-failure scenario - an ordinary batch loop over
        # unrelated items matches just as well - so the message should not
        # assert that specific narrative as fact.
        return _finding(
            file, call_line,
            f"{name} mutates a store/cache inside the changed loop - if this loop can retry the "
            "same key after a failure, that mutation could run more than once.",
            "Verify whether this loop can re-run for the same key; if so, make the mutation "
            "idempotent or stop before repeating it.",
        )

    return None


def _moved_record_findings(
    file: str,
    source: str,
    hunks: list[_Hunk],
    references: dict[str, tuple[str, str]],
) -> list[dict]:
    findings: list[dict] = []
    for hunk in hunks:
        for removed_line in hunk.removed:
            if not removed_line.strip():
                continue
            if not re.search(r"\b(?:log|record|audit)\b|\.append\s*\(", removed_line):
                continue
            if removed_line.strip() not in {line.strip() for line in hunk.added}:
                continue
            moved = removed_line.strip()
            moved_line = _line_number_near_hunk(source, moved, hunk)
            if moved_line is None:
                continue
            for name, (_path, dependency) in references.items():
                if name not in source or not re.search(r"\b(?:raise|pop|append|update)\b", dependency):
                    continue
                for call_line in _call_lines(source, name):
                    if call_line < moved_line and hunk.near(call_line):
                        findings.append(
                            _finding(
                                file,
                                call_line,
                                f"{name} runs before the moved side-effecting log/record operation, "
                                "so a failure can skip that record.",
                                "Perform the required record operation before the fallible call.",
                            )
                        )
                        break
                else:
                    continue
                break
    return findings


_RESOURCE_CLOSE_RE = re.compile(r"\bdefer\s+(\w+)\.Close\s*\(\s*\)|\b(\w+)\.close\s*\(\s*\)")
_RESOURCE_OPEN_RE = re.compile(r"\b(\w+)\s*:?=\s*(?:os\.NewFile|os\.Open|open)\s*\(")


def _resource_leak_findings(file: str, source: str, hunks: list[_Hunk]) -> list[dict]:
    """A close() call on a file/resource handle was removed, with nothing
    nearby still closing that same variable - evidence-only in the same
    sense as every other check here: this only fires when the diff itself
    shows the close disappearing, never from inferring that a resource
    ought to have been closed in the first place."""
    findings: list[dict] = []
    for hunk in hunks:
        for removed_line in hunk.removed:
            match = _RESOURCE_CLOSE_RE.search(removed_line)
            if not match:
                continue
            var = match.group(1) or match.group(2)

            if any(re.search(rf"\b{re.escape(var)}\.[Cc]lose\s*\(", added) for added in hunk.added):
                continue  # still closed somewhere in this same hunk

            # split("\n"), never splitlines() - see _line_number_near_hunk's
            # docstring; same real \n-based hunk line numbers, same bug.
            nearby = source.split("\n")[
                max(0, hunk.new_start - 1 - DIFF_HUNK_TOLERANCE) : hunk.new_end + DIFF_HUNK_TOLERANCE
            ]
            if any(re.search(rf"\b{re.escape(var)}\.[Cc]lose\s*\(", line) for line in nearby):
                continue  # a close for this variable survives nearby

            # Cite the hunk itself, not open_line - a resource is
            # frequently opened near the top of a function and closed near
            # the bottom, and the downstream grounding filter
            # (flash_review.py's _validate_findings) drops any finding
            # whose cited line lands more than DIFF_LINE_TOLERANCE (8)
            # lines from a diff-touched line. hunk.new_start is always
            # inside the diff by construction, so this survives grounding
            # regardless of how far the open() call is from the removed
            # Close(). Where the resource was opened is still useful
            # context for a human - included in the issue text, just not
            # used as the finding's own citation, and its lookup stays a
            # whole-file search since the open call itself can legitimately
            # be anywhere in the file.
            open_match = next(
                (m for m in _RESOURCE_OPEN_RE.finditer(source) if m.group(1) == var), None
            )
            open_line = _line_number(source, open_match.group(0)) if open_match else None
            opened_at = f" ({var} was opened at line {open_line})" if open_line else ""

            findings.append(
                _finding(
                    file,
                    hunk.new_start,
                    f"The changed code removed {var}'s Close() call{opened_at} - this resource now leaks.",
                    f"Restore closing {var} (e.g. `defer {var}.Close()`), including on early-return paths.",
                )
            )
    return findings


# Go-specific: `dst := make([]T, n); copy(dst, src)` is the standard way to
# defensively copy a slice before mutating it, because a bare re-slice
# (`dst := src[:n]`) still shares src's backing array - the case this
# guards is exactly case 009 of this project's own PR-review benchmark
# corpus (spf13/cobra#2257): a defensive copy replaced with a bare re-slice
# let a later append corrupt the caller's original slice (ultimately
# os.Args). Scoped to Go's copy() builtin specifically rather than
# generalized to every language's copy idiom (list()/.copy()/slice() in
# Python/JS) because this is the only real evidence this check is built
# from; broadening it without a second real example to verify against
# would be guessing, not evidence.
_GO_COPY_CALL_RE = re.compile(r"\bcopy\s*\(\s*(\w+)\s*,\s*(\w+)\b")


def _copy_to_alias_findings(file: str, source: str, hunks: list[_Hunk]) -> list[dict]:
    findings: list[dict] = []
    for hunk in hunks:
        removed_text = "\n".join(hunk.removed)
        added_text = "\n".join(hunk.added)
        copy_call = _GO_COPY_CALL_RE.search(removed_text)
        if not copy_call:
            continue
        dest, src = copy_call.group(1), copy_call.group(2)

        alias = re.search(rf"\b{re.escape(dest)}\s*:?=\s*({re.escape(src)}\b[^\n]*)", added_text)
        if not alias:
            continue
        if re.search(r"\bcopy\s*\(|\.copy\s*\(|\bmake\s*\(", alias.group(0)):
            continue  # the replacement still copies - not the bug this guards

        line = _line_number_near_hunk(source, f"{dest} ", hunk) or hunk.new_start
        findings.append(
            _finding(
                file,
                line,
                f"{dest} used to be a defensive copy of {src}; the changed code aliases it directly "
                f"instead, so a later mutation of {dest} can corrupt {src}'s backing array.",
                f"Restore the copy (allocate {dest} and `copy({dest}, {src})`) before mutating {dest}.",
            )
        )
    return findings


# A value clamped to a floor (or ceiling) via a max()/min() call wrapping
# its whole assignment, then reassigned in the same hunk without that
# wrapper: the inner expression is unchanged, only the bound disappeared.
# Deliberately loose across languages - Math.max (JS/Java), math.max (Go),
# a bare max() (Python) - because the pattern being matched is the call
# shape itself, not language-specific syntax the way the Go copy() check
# above necessarily is. Matches axios#6807 (benchmark case 005): `Math.max(0,
# total != null ? Math.min(rawLoaded, total) : rawLoaded)` lost its outer
# Math.max(0, ...), letting a computed byte count go negative.
_CLAMP_REMOVED_RE = re.compile(r"\b(\w+)\s*=\s*(?:Math\.max|math\.max|\bmax)\s*\(\s*0(?:\.0)?\s*,")
_STILL_CLAMPED_RE = re.compile(r"(?:Math\.max|math\.max|\bmax)\s*\(\s*0")


def _removed_bounds_clamp_findings(file: str, source: str, hunks: list[_Hunk]) -> list[dict]:
    findings: list[dict] = []
    for hunk in hunks:
        for removed_line in hunk.removed:
            match = _CLAMP_REMOVED_RE.search(removed_line)
            if not match:
                continue
            var = match.group(1)

            # The same variable must still be assigned in this same hunk,
            # without the clamp - otherwise the clamp just moved elsewhere
            # in the diff (this module's existing "moved" reasoning), which
            # is not a regression.
            still_assigned = next(
                (added for added in hunk.added if re.search(rf"\b{re.escape(var)}\s*=", added)),
                None,
            )
            if still_assigned is None or _STILL_CLAMPED_RE.search(still_assigned):
                continue

            findings.append(
                _finding(
                    file,
                    _line_number_near_hunk(source, f"{var} =", hunk) or hunk.new_start,
                    f"{var} used to be clamped to a bound; the changed code removed it, "
                    f"so {var} can now fall outside that bound.",
                    f"Restore the bound (e.g. `max(0, ...)`) around {var}'s assignment.",
                )
            )
    return findings


# A newly-added bare/broad Python except clause whose entire body is
# `pass` - the classic swallowed-exception anti-pattern, where a failure is
# discarded with no logging and no re-raise, leaving callers with no way to
# know something went wrong. Python-only, not generalized to other
# languages' broad-catch syntax (JS bare `catch {}`, Go's blanket `if err
# != nil { return }` shape are structurally different and have no real
# example in this corpus to verify a pattern against). Matches this
# project's own PR-review benchmark case 021 (requests):
# `except Exception: pass` added around Session.close_quietly()'s per-
# adapter v.close() call, silently discarding any close failure.
_BROAD_EXCEPT_RE = re.compile(
    r"^(?P<indent>\s*)except(?:\s+\(?\s*(?:Exception|BaseException)\s*\)?(?:\s+as\s+\w+)?)?\s*:(?P<rest>.*)$"
)
_LOG_OR_RERAISE_RE = re.compile(
    r"\braise\b|\blog(?:ger|ging)?\.\w+\s*\(|\bwarnings\.warn\s*\(|\bprint\s*\("
)


def _unchanged_except_body_weakened(file: str, source: str, hunk: _Hunk) -> dict | None:
    """A sibling gap to the main loop below: this check only ever looked
    for the `except` line itself inside hunk.added - a PR that replaces
    an EXISTING except block's body with a bare `pass`, without touching
    the except line's own text, is invisible to that path entirely.
    _diff_hunks_by_file never records unchanged context lines into
    hunk.added/removed (only +/- lines), so an unchanged except header
    sitting just above the hunk's real change has nowhere to be matched.

    Real, previously-identified-but-deferred gap: commit bad16b7's own
    message documented this as a second, real (not theoretical) finding
    from the same review that fixed the comment-scoping bug, and said it
    "needs new detection logic... tracked separately" - that follow-up
    was never implemented until now.

    Real Flash Review finding on this check's first version: gating on
    the WHOLE hunk's added/removed content (was the hunk's real added
    content exactly `pass`, was its real removed content more than
    `pass`) doesn't prove the removed content actually belonged to the
    except block a match happened to find nearby - an unchanged handler
    that already legitimately contains `pass` could be falsely reported
    when an unrelated change elsewhere in the SAME hunk replaces real
    code with `pass`. Scoped instead to hunk.raw_body - every body line
    in original diff order with its real "-"/"+"/" " tag still attached
    - walking forward from the except header's own position and
    reconstructing the OLD (context + removed) and NEW (context + added)
    body text separately, stopping at the first line back at or above
    the except's own indentation. Only that block's own old/new content
    is compared, the same reconstruction technique github_api.py's
    _trim_patch_context uses for old/new hunk text generally.
    """
    for idx, raw_line in enumerate(hunk.raw_body):
        if raw_line[:1] != " ":
            continue  # an except header that's itself newly added is
            # already handled by the main loop below - this path is only
            # for one sitting in the diff as unchanged context.
        match = _BROAD_EXCEPT_RE.match(raw_line[1:])
        if match is None:
            continue
        except_indent = len(match.group("indent"))
        inline_rest = re.sub(r"#.*$", "", match.group("rest")).strip()

        if inline_rest:
            old_body = new_body = [inline_rest]
        else:
            old_body, new_body = [], []
            for later in hunk.raw_body[idx + 1 :]:
                tag, text = later[:1], later[1:]
                stripped = text.strip()
                if not stripped:
                    continue
                indent = len(text) - len(text.lstrip())
                if indent <= except_indent:
                    break
                if tag in (" ", "-"):
                    old_body.append(stripped)
                if tag in (" ", "+"):
                    new_body.append(stripped)
            else:
                # Real gap found via audit: the hunk's own context window
                # (a unified diff only shows a few lines around each real
                # change) can run out before we ever see a dedent back to
                # the except header's own indent - that isn't proof the
                # block ended, only that the diff stopped showing it.
                # Reporting on an incomplete reconstruction here could
                # flag a handler as reduced to bare `pass` even though
                # real, unchanged handling continues past the hunk's
                # cutoff - e.g. only the FIRST statement of a multi-
                # statement body was replaced with `pass`, and the rest
                # of the block (still real handling) simply isn't in this
                # hunk. Bail rather than guess - matches this file's own
                # fail-closed philosophy elsewhere (an unrepresentable
                # case degrades to no finding, not a wrong one).
                continue

        non_comment_old = [b for b in old_body if not b.startswith("#")]
        non_comment_new = [b for b in new_body if not b.startswith("#")]
        if not non_comment_old or non_comment_old == ["pass"]:
            continue  # this block's own old body had no real handling to lose
        if non_comment_new != ["pass"]:
            continue  # this block's own new body isn't reduced to bare pass

        return _finding(
            file,
            _line_number_near_hunk(source, raw_line[1:].strip(), hunk) or hunk.new_start,
            "This except block's body was replaced with a bare `pass`, discarding real error "
            "handling that used to run here - no logging, no re-raise. If the wrapped call ever "
            "fails, the failure is now silently swallowed and there's no way to diagnose what went "
            "wrong.",
            "Restore logging (e.g. `logger.warning(...)`) or re-raising instead of silently passing.",
        )
    return None


def _swallowed_exception_findings(file: str, source: str, hunks: list[_Hunk]) -> list[dict]:
    findings: list[dict] = []
    for hunk in hunks:
        added = hunk.added
        found_except_in_added = False
        for idx, line in enumerate(added):
            match = _BROAD_EXCEPT_RE.match(line)
            if not match:
                continue
            found_except_in_added = True
            except_indent = len(match.group("indent"))

            # A common idiom the old regex's "colon, then only whitespace or
            # a comment" anchor missed entirely: `except Exception: pass` on
            # one line never matches it at all (real, verified false
            # negative - the pattern this check exists to catch, invisible
            # to it whenever the body isn't given its own line). Anything
            # after the colon is now captured instead of gating the match,
            # so an inline body is judged directly - only a bare `pass`
            # (optionally trailing a comment) counts as a swallow; any other
            # inline statement (e.g. `except Exception: raise`) is real
            # handling and correctly falls through as not a swallow.
            inline_rest = re.sub(r"#.*$", "", match.group("rest")).strip()
            if inline_rest:
                if inline_rest != "pass":
                    continue
                body = ["pass"]
            else:
                # Collect this except block's body: contiguous added lines
                # indented deeper than the except itself, stopping at the
                # first line back at or above that indentation (end of the
                # block) or the end of this hunk's added lines.
                body = []
                for later in added[idx + 1 :]:
                    stripped = later.strip()
                    if not stripped:
                        continue
                    indent = len(later) - len(later.lstrip())
                    if indent <= except_indent:
                        break
                    body.append(stripped)

                if not body:
                    continue  # the except body isn't in this hunk at all - nothing to judge
            non_comment = [b for b in body if not b.startswith("#")]
            if non_comment != ["pass"]:
                continue  # body has real handling (or more than just pass) - not a swallow
            # Scoped to non_comment, not body: a comment merely mentioning
            # "raise"/"log"/"warn" ("# used to raise, now silently
            # ignored") is commentary, not real handling, and must not
            # suppress a genuine swallow - confirmed as a real false
            # negative, not theoretical, by a standalone before/after
            # check against this exact shape.
            if any(_LOG_OR_RERAISE_RE.search(b) for b in non_comment):
                continue  # defensive, in case a future body shape sneaks a log/raise into real code

            findings.append(
                _finding(
                    file,
                    _line_number_near_hunk(source, line.strip(), hunk) or hunk.new_start,
                    "This newly-added except block discards the error with a bare `pass` - no "
                    "logging, no re-raise. If the wrapped call ever fails, the failure is silently "
                    "swallowed and there's no way to diagnose what went wrong.",
                    "Log the exception (e.g. `logger.warning(...)`) or re-raise it instead of "
                    "silently passing.",
                )
            )
        if not found_except_in_added:
            finding = _unchanged_except_body_weakened(file, source, hunk)
            if finding is not None:
                findings.append(finding)
    return findings


# A newly-added Java catch block whose body is empty (or only a comment) -
# the direct structural analog of the Python swallowed-exception check
# above (same "newly-added block, no logging, no re-raise" semantics),
# just Java's brace-delimited catch instead of Python's colon-indented
# except. CWE-390 (Detection of Error Condition Without Action) names this
# exact anti-pattern; unlike this file's other checks, there is no real
# corpus case or CVE citation backing this one yet - it ships on the
# pattern being unambiguous and the underlying "empty handler swallows the
# error" logic already being proven by the Python check it mirrors, not on
# a verified real-bug example. Replace this comment with a real citation
# if/when one turns up (none of this project's own 25 benchmark cases or
# the Keycloak martian corpus had this exact shape as of 2026-09-16).
#
# Deliberately conservative like every other check here: only recognizes a
# catch body with no nested braces at all (an inline `{}`, or a run of
# blank/comment-only lines between the opening `{` and a lone closing `}`)
# - any real statement, or any nested block, and the check backs off
# rather than risk a false positive from mis-tracked brace depth.
_JAVA_CATCH_OPEN_RE = re.compile(
    r"\bcatch\s*\(\s*(?:final\s+)?[\w.]+(?:\s*\|\s*[\w.]+)*\s+\w+\s*\)\s*\{(?P<inline>.*)$"
)
_JAVA_RETHROW_OR_LOG_RE = re.compile(
    r"\bthrow\b|\b(?:log|logger)\.\w+\s*\(|\bSystem\.(?:out|err)\.print\w*\s*\("
)


def _swallowed_exception_findings_java(file: str, source: str, hunks: list[_Hunk]) -> list[dict]:
    findings: list[dict] = []
    for hunk in hunks:
        added = hunk.added
        for idx, line in enumerate(added):
            match = _JAVA_CATCH_OPEN_RE.search(line)
            if not match:
                continue

            # Real false-negative gap found independently by GLM-5.3-Flash
            # reviewing PR #725 with PR-Agent's own prompt structure: only
            # `//` line comments were stripped, so a catch body containing
            # only a `/* ... */` block comment was misjudged as real
            # content and missed. Strip both comment styles the same way
            # before judging emptiness.
            inline = re.sub(r"/\*.*?\*/", "", match.group("inline"))
            inline = re.sub(r"//.*$", "", inline).strip()
            if inline == "}":
                body_empty = True
            elif inline == "":
                # Multi-line block: collect contiguous blank/comment-only
                # lines until a lone closing brace - any nested brace or
                # real statement bails out with no finding (see module
                # comment above - conservative on purpose).
                #
                # in_block_comment tracks a /* ... */ that genuinely spans
                # multiple lines - a real gap Aletheore's own Flash Review
                # found on the single-line-only version of this fix (PR
                # #726): matching only `^/\*.*\*/$` left a comment's own
                # opening ("/* explanation") and closing ("more text */")
                # lines unrecognized, so a real multi-line block comment
                # left the body looking non-empty. While inside one, a
                # line's content (including a stray brace character in the
                # comment's own text) is never treated as real code or as
                # the catch's closing brace - only "*/" ends the tracked
                # state.
                body_lines: list[str] = []
                closed = False
                in_block_comment = False
                for later in added[idx + 1 :]:
                    stripped = later.strip()

                    if in_block_comment:
                        if "*/" not in stripped:
                            body_lines.append("")
                            continue
                        # Real gap found auditing this check: the comment's
                        # closing "*/" can share a line with the catch's own
                        # closing "}" (e.g. "explanation. */ }"). Falling
                        # through to re-examine whatever follows "*/" - rather
                        # than unconditionally consuming the whole line - so
                        # that trailing "}" still closes the catch instead of
                        # silently dropping the finding.
                        in_block_comment = False
                        stripped = stripped.split("*/", 1)[1].strip()
                        if stripped == "":
                            body_lines.append("")
                            continue
                    if stripped == "}":
                        closed = True
                        break
                    if stripped.startswith("/*"):
                        if "*/" not in stripped[2:]:
                            in_block_comment = True
                            body_lines.append("")
                            continue
                        # Sibling gap to the in_block_comment branch's own
                        # fix above: a block comment that opens AND closes
                        # on this same line (e.g. "/* note */ recover();")
                        # was unconditionally discarding whatever real code
                        # follows "*/" - misjudging a genuinely-handled
                        # catch (even one that logs the exception) as
                        # empty/swallowed. Fall through to re-examine the
                        # remainder instead of consuming the whole line.
                        stripped = stripped.split("*/", 1)[1].strip()
                        if stripped == "":
                            body_lines.append("")
                            continue
                        if stripped == "}":
                            closed = True
                            break
                    if stripped.startswith("//"):
                        body_lines.append("")
                        continue
                    if "{" in stripped or "}" in stripped:
                        break  # nested brace - outside this check's scope
                    body_lines.append(stripped)
                if not closed:
                    continue  # closing brace isn't in this hunk - nothing to judge
                body_empty = not any(body_lines)
            else:
                body_empty = False  # real inline statement

            if not body_empty:
                continue
            if _JAVA_RETHROW_OR_LOG_RE.search(line):
                continue  # defensive - the opening line itself logs/rethrows

            findings.append(
                _finding(
                    file,
                    _line_number_near_hunk(source, line.strip(), hunk) or hunk.new_start,
                    "This newly-added catch block discards the exception with an empty body - no "
                    "logging, no re-throw. If the wrapped call ever fails, the failure is silently "
                    "swallowed and there's no way to diagnose what went wrong.",
                    "Log the exception (e.g. `logger.warn(...)`) or re-throw it instead of leaving "
                    "the catch block empty.",
                )
            )
    return findings


# os.system() always runs through a shell; subprocess.{call,run,Popen,
# check_call,check_output}() only does when explicitly given shell=True -
# hence the two-branch pattern rather than one. Same STRING_CONCAT_RE
# reused from the SQL check above: the risk shape is identical (a bare
# variable concatenated directly into the command text), just a different
# sink. No case in this project's own PR-review benchmark corpus has this
# exact shape - grounded instead in a real, verified external example:
# CVE-2024-29189 (ansys-geometry-core), a subprocess call with shell=True
# flagged as a real command-injection risk. Only the + concatenation shape
# is covered, matching the SQL check's own scope limit - f-strings and
# %-formatting building a shell command are a different syntactic shape
# with no real example yet to verify a pattern against.
_SHELL_CALL_RE = re.compile(
    r"\bos\.system\s*\(|\bsubprocess\.(?:call|run|Popen|check_call|check_output)\s*\([^\n]*\bshell\s*=\s*True\b"
)


def _shell_injection_findings(file: str, source: str, hunks: list[_Hunk]) -> list[dict]:
    findings: list[dict] = []
    for hunk in hunks:
        for added_line in hunk.added:
            if not _SHELL_CALL_RE.search(added_line):
                continue
            if not _STRING_CONCAT_RE.search(added_line):
                continue
            findings.append(
                _finding(
                    file,
                    _line_number_near_hunk(source, added_line.strip(), hunk) or hunk.new_start,
                    "This runs a shell command built by concatenating a variable directly into the "
                    "command text - a shell-injection risk if that value can be influenced by a caller.",
                    "Avoid shell=True/os.system with concatenated input - pass arguments as a list "
                    "(e.g. subprocess.run([...], shell=False)) instead.",
                )
            )
    return findings


# Java sibling of _shell_injection_findings above - same risk shape (a
# variable concatenated directly into a command string), but the real
# risk here is narrower and differently shaped than the Python/Go
# versions - a genuine false positive caught by Aletheore's own Flash
# Review on the PR that introduced this check
# (github.com/Aletheore/Aletheore/pull/725). Runtime.exec(String) and
# ProcessBuilder never invoke a shell at all (unlike os.system/subprocess
# with shell=True, or Go's exec.Command("sh", "-c", ...)) - so "shell
# injection"/"shell metacharacters" is factually wrong for either. Two
# real consequences: (1) ProcessBuilder is dropped from this check
# entirely - passing one concatenated string as its sole argument doesn't
# correspond to a real exploitable shape at all (it names one literal
# program with a space in it, which just fails to launch, since
# ProcessBuilder never shell-splits); (2) Runtime.exec(String command)
# does have a real, different, narrower risk worth flagging - it
# naively splits the command on whitespace and executes the pieces
# directly (no shell metacharacter interpretation), so a value that adds
# extra whitespace-separated tokens can inject additional arguments
# (CWE-88, argument injection) even though it can't inject a `;`/`|`/
# backtick shell command the way the Python/Go checks' targets can. No
# real corpus case or CVE citation for this exact Java shape yet (same
# honesty note as the Java empty-catch check).
_JAVA_EXEC_CALL_RE = re.compile(r"\bRuntime\.getRuntime\(\)\.exec\s*\(")


def _shell_injection_findings_java(file: str, source: str, hunks: list[_Hunk]) -> list[dict]:
    findings: list[dict] = []
    for hunk in hunks:
        for added_line in hunk.added:
            if not _JAVA_EXEC_CALL_RE.search(added_line):
                continue
            if not _STRING_CONCAT_RE.search(added_line):
                continue
            findings.append(
                _finding(
                    file,
                    _line_number_near_hunk(source, added_line.strip(), hunk) or hunk.new_start,
                    "Runtime.exec(String) splits this concatenated command on whitespace and runs "
                    "the pieces directly (it does not invoke a shell) - a value that can be "
                    "influenced by a caller could inject extra arguments this way.",
                    "Use Runtime.exec(String[]) or ProcessBuilder with separate arguments instead of "
                    "one command string.",
                )
            )
    return findings


# Go sibling of the same check. Go has no generic "shell=True" flag - the
# risk is only present when the command being run is itself a shell
# (`sh -c`/`bash -c`), with the actual work passed as a string argument
# built from a variable. Different enough from the Python/Java shape
# (there is no equivalent of subprocess's shell= kwarg to gate on) that it
# needs its own regex rather than sharing _SHELL_CALL_RE/_JAVA_SHELL_CALL_RE.
#
# Real gap found in a reverse-audit of #725/#726: the bare "sh"/"bash"
# literal never matched exec.Command("/bin/sh", "-c", ...) or
# exec.Command("/bin/bash", "-c", ...) - real Go code invokes the shell by
# full path at least as often as by bare name (os/exec resolves a bare
# name via PATH at call time, which many callers avoid pinning down
# explicitly). Allowing an optional path prefix before the sh/bash
# basename closes that false negative without widening the match to
# unrelated binaries whose name merely contains "sh" or "bash".
_GO_SHELL_CALL_RE = re.compile(
    r'\bexec\.Command\s*\(\s*"(?:[\w./-]*/)?(?:sh|bash)"\s*,\s*"-c"\s*,'
)


def _shell_injection_findings_go(file: str, source: str, hunks: list[_Hunk]) -> list[dict]:
    findings: list[dict] = []
    for hunk in hunks:
        for added_line in hunk.added:
            if not _GO_SHELL_CALL_RE.search(added_line):
                continue
            if not (_STRING_CONCAT_RE.search(added_line) or "fmt.Sprintf" in added_line):
                continue
            findings.append(
                _finding(
                    file,
                    _line_number_near_hunk(source, added_line.strip(), hunk) or hunk.new_start,
                    "This runs `sh -c`/`bash -c` with a command string built from a variable - a "
                    "shell-injection risk if that value can be influenced by a caller.",
                    "Call the target binary directly via exec.Command with separate arguments "
                    "instead of building a shell command string.",
                )
            )
    return findings


# Asymmetric cache trust: a new security/permission check that guards two
# different cache lookups before deciding an outcome, but doesn't give them
# the same trust - one returns unconditionally on a cache hit (Go's comma-ok
# idiom, `if _, ok := X.Get(...); ok { ... return }`), the other - a helper
# wrapping a cache read, `v, err := someCachedThing(...); if err == nil {
# ... }` - branches internally before deciding whether to return, so at
# least one of its outcomes falls through instead of short-circuiting. If
# both guard the same underlying decision, a cache hit for one outcome can
# end up trusted while the other is always re-verified (or vice versa),
# letting a stale cached result outlive the state change that should have
# invalidated it.
#
# Real, validated example: grafana/grafana#103633 - a new permDenialCache
# hit returns immediately, but a cache hit via getCachedIdentityPermissions
# only short-circuits when the cached result is a grant; a cached denial
# still falls through to a fresh DB lookup, which is itself the SAME shape
# as the real bug (grants trusted, denials re-derived - the golden finding's
# actual asymmetry direction). Confirmed no LLM caught this across a full
# night of testing (2026-09-20/21): GLM-5.3-Flash, DeepSeek-V4-Flash,
# gpt-5.6-luna, gpt-4.1-mini, o4-mini, and three open-weight models, across
# a plain prompt, a targeted rewritten safety rule specifically naming this
# pattern, and full tool-executing agent mode with real grep/read access to
# the complete repo, all missed it - this check exists because that gap is
# real and repeatable, not hypothetical. Validated zero false positives
# across the other 14 real diffs in this session's own benchmark corpus
# (13 PR-review-benchmark cases plus 2 additional real security-tagged
# cases) - a single true-positive example, so treat the false-positive rate
# on genuinely novel code as unproven beyond that, not as a large-sample
# guarantee.
#
# Intentionally narrow and Go-only (gated by is_go at the call site, same
# convention as _shell_injection_findings_go): flags the STRUCTURAL
# asymmetry as worth a look, not a proven bug - a legitimate reason for two
# cache guards to have different trust levels can exist, and only
# Aletheore's LLM review layer (which sees this finding alongside the full
# diff) can tell a real bug from an intentional design choice.
_GO_CACHE_GETOK_RE = re.compile(
    r'\bif\s+[^{;]{0,60}?:=\s*(?:\w+\.)?(\w*[Cc]ache\w*)\s*\.\s*(?:Get|Fetch|Lookup)\s*\([^{;]{0,160}?;\s*'
    r'(?:ok|found|hit)\s*\{',
)
_GO_CACHE_ERR_ASSIGN_RE = re.compile(
    r'\b\w+,\s*err\s*:=\s*\w*\.?\s*(\w*[Cc]ached\w*|\w*[Cc]ache\w*)\s*\([^)]{0,160}?\)',
)
_ERR_NIL_GUARD_RE = re.compile(r'\bif\s+err\s*==\s*nil\s*\{')
_ASYMMETRIC_CACHE_CLASSIFY_WINDOW_CHARS = 300


def _go_cache_guards(added_text: str) -> list[tuple[str, int]]:
    guards: list[tuple[str, int]] = []
    for match in _GO_CACHE_GETOK_RE.finditer(added_text):
        guards.append((match.group(1), match.end()))
    for match in _GO_CACHE_ERR_ASSIGN_RE.finditer(added_text):
        tail = added_text[match.end():match.end() + 40]
        nil_check = _ERR_NIL_GUARD_RE.search(tail)
        if nil_check:
            guards.append((match.group(1), match.end() + nil_check.end()))
    return guards


def _returns_unconditionally_on_hit(added_text: str, guard_end_pos: int) -> bool | None:
    # Deliberately crude proxy for real control-flow analysis, matching this
    # file's existing pragmatic style (see _off_by_one_loop_findings):
    # scans lines in order from the guard's opening brace, returns True the
    # moment `return` is seen first, False the moment `if` is seen first
    # (branching before any return - the partial-trust shape), None if the
    # block's own closing brace is reached before either - an ambiguous
    # shape this check should not guess about.
    block = added_text[guard_end_pos:guard_end_pos + _ASYMMETRIC_CACHE_CLASSIFY_WINDOW_CHARS]
    for line in block.split("\n"):
        stripped = line.strip()
        if stripped == "}":
            return None
        if re.match(r"\bif\b", stripped):
            return False
        if re.search(r"\breturn\b", stripped):
            return True
    return None


def _asymmetric_cache_trust_findings_go(file: str, source: str, hunks: list[_Hunk]) -> list[dict]:
    findings: list[dict] = []
    for hunk in hunks:
        added_text = "\n".join(hunk.added)
        guards = _go_cache_guards(added_text)

        by_name: dict[str, list[bool]] = {}
        for name, end_pos in guards:
            classification = _returns_unconditionally_on_hit(added_text, end_pos)
            if classification is None:
                continue
            by_name.setdefault(name, []).append(classification)

        if len(by_name) < 2:
            continue

        unconditional_names = [name for name, classifications in by_name.items() if any(classifications)]
        conditional_names = [name for name, classifications in by_name.items() if not any(classifications)]
        if not unconditional_names or not conditional_names:
            continue

        findings.append(
            _finding(
                file,
                hunk.new_start,
                f"This new code guards two different cache lookups ({unconditional_names[0]!r} and "
                f"{conditional_names[0]!r}) before making a decision, but they don't get the same "
                f"trust: {unconditional_names[0]!r} returns immediately on a cache hit, while "
                f"{conditional_names[0]!r}'s hit branches internally and doesn't return for at "
                "least one of its outcomes.",
                "Confirm this asymmetry is intentional; if not, make both branches return "
                "immediately on a hit (or neither does) so a cached result isn't trusted "
                "differently depending on which outcome it represents.",
            )
        )
    return findings


# A C-style counted loop indexing a collection by its own length/size using
# <= instead of < is an off-by-one that reads or writes one element past
# the end. The comparison syntax (`i <= x.length`, `i <= x.size()`,
# `i <= x.Length`) is shared across Java/JS/C#/C/C++; Go's len(x) uses
# function-call rather than property syntax, matched separately. Not
# claimed for every language's loop idiom (Python's `range(len(x) + 1)`
# is a structurally different shape with no real example in this
# project's own PR-review benchmark corpus to verify against) - only
# for the syntax this check has real evidence for. Matches
# apache/commons-lang#1247 (benchmark case 017): `for (int i = 0; i <=
# array.length; i++) { last = array[i]; }`.
_OFF_BY_ONE_PROPERTY_RE = re.compile(
    r"for\s*\(?[^;]*?\b(\w+)\s*=\s*0\s*;\s*\1\s*<=\s*([\w.]+?)\s*(?:\.\s*length\b|\.\s*size\s*\(\s*\)|\.\s*Length\b)\s*;"
)
_OFF_BY_ONE_LEN_CALL_RE = re.compile(
    # No `\(` requirement, unlike the property-syntax pattern above: Go's
    # for statement (the only real evidence for this len()-call variant)
    # omits the parentheses entirely - `for i := 0; i <= len(x); i++ {`.
    r"for\s+[^;]*?\b(\w+)\s*(?::?=)\s*0\s*;\s*\1\s*<=\s*len\s*\(\s*([\w.]+?)\s*\)\s*;"
)


def _off_by_one_loop_findings(file: str, source: str, hunks: list[_Hunk]) -> list[dict]:
    findings: list[dict] = []
    for hunk in hunks:
        added_text = "\n".join(hunk.added)
        match = _OFF_BY_ONE_PROPERTY_RE.search(added_text) or _OFF_BY_ONE_LEN_CALL_RE.search(added_text)
        if not match:
            continue
        loop_var, collection = match.group(1), match.group(2)

        indexed = re.search(
            rf"\b{re.escape(collection)}\s*\[\s*{re.escape(loop_var)}\s*\]|"
            rf"\b{re.escape(collection)}\s*\.\s*get\s*\(\s*{re.escape(loop_var)}\s*\)",
            added_text,
        )
        if not indexed:
            continue  # the loop bound is suspicious, but nothing in this hunk actually indexes with it

        line = _line_number_near_hunk(source, match.group(0), hunk) or hunk.new_start
        findings.append(
            _finding(
                file,
                line,
                f"This loop bounds {loop_var} with <= against {collection}'s length/size, "
                f"then indexes {collection}[{loop_var}] - the last iteration reads one past the end.",
                f"Use `{loop_var} < {collection}.length` (or the equivalent size/len bound) instead of <=.",
            )
        )
    return findings


# A SQL statement keyword-pair (SELECT...FROM, INSERT INTO, UPDATE...SET,
# DELETE FROM) inside a string literal, concatenated directly with a bare
# variable via +. The keyword *pair* is deliberate, not a single keyword -
# "select" and "update" are also ordinary English words, and a bare
# single-keyword match would false-positive on log/UI strings like "Update
# your settings" or "Select an option". Requiring the SQL-shaped pair
# together with direct string+variable concatenation is a much rarer
# coincidence. Matches flask's own build_user_lookup_query() (this
# project's own PR-review benchmark case 016): `"SELECT id, username,
# email FROM users WHERE username = '" + username + "'"`. Only the +
# concatenation shape is covered - f-strings, %-formatting and .format()
# calls building SQL are a different syntactic shape with no real example
# in this corpus to verify a pattern against.
_SQL_SHAPE_RE = re.compile(
    r"\bSELECT\b.{0,120}?\bFROM\b|\bINSERT\s+INTO\b|\bUPDATE\b.{0,80}?\bSET\b|\bDELETE\s+FROM\b",
    re.IGNORECASE,
)
_STRING_CONCAT_RE = re.compile(r"[\"']\s*\+\s*\w+|\w+\s*\+\s*[\"']")


def _sql_injection_findings(file: str, source: str, hunks: list[_Hunk]) -> list[dict]:
    findings: list[dict] = []
    for hunk in hunks:
        for added_line in hunk.added:
            if not _SQL_SHAPE_RE.search(added_line):
                continue
            if not _STRING_CONCAT_RE.search(added_line):
                continue
            findings.append(
                _finding(
                    file,
                    _line_number_near_hunk(source, added_line.strip(), hunk) or hunk.new_start,
                    "This builds a SQL query by concatenating a variable directly into the query "
                    "text - a SQL-injection risk if that value can be influenced by a caller.",
                    "Use a parameterized query (a placeholder plus a bound parameter) instead of "
                    "string concatenation.",
                )
            )
    return findings


# A double-quote-delimited pair, content bounded to a realistic nested-
# phrase length (not a whole paragraph) so this can't accidentally span
# across what are really two separate phrases on the same line.
_QUOTED_PHRASE_RE = re.compile(r'"([^"\n]{1,80})"')

# Below this, two texts that just happen to share a quoted phrase for
# unrelated reasons (a whole different string swapped in, coincidentally
# reusing the same short flag name) aren't good evidence of anything - this
# gate is what keeps the check to "the same string, edited" rather than
# "any two strings that happen to mention the same phrase".
_BROKEN_QUOTED_PHRASE_SIMILARITY_THRESHOLD = 0.6


def _broken_quoted_phrase_findings(file: str, source: str, hunks: list[_Hunk]) -> list[dict]:
    """A hunk that edits a string literal's own content (not adds or
    removes a whole one) sometimes drops one closing double-quote off a
    nested quoted phrase inside it - real example: pallets/flask PR #5344
    (this project's own benchmark case 001), where a multi-line error-
    message call got reformatted onto one line and `"--key"` lost its
    closing `"` along the way, producing the malformed literal text
    `"--key is not used.` instead of `"--key" is not used.`. The string
    itself still parses fine either way - the OUTER Python/Go/JS string
    delimiter is untouched, only the message's own nested quoted phrase
    breaks - so nothing about this is a syntax error a linter or the
    language's own parser would catch. It also survived three different
    LLM prompt variants tried against this exact case, including an
    explicit character-by-character quote-check instruction (see PR #716's
    "Known open gap" and case 001's own scoring history) - the miss isn't
    a prompt-wording problem, it's a case for exactly this kind of
    deterministic, code-verified check.

    Deliberately scoped to a real EDIT (both hunk.removed and hunk.added
    non-empty) with high textual similarity between the two sides - a hunk
    that adds a brand new string or deletes one wholesale is a different
    situation this isn't evidence about.

    Real bug found and fixed via this project's own Flash Review dogfood
    review of this exact PR (#717), twice over:

    1. The first version counted raw `"` characters across the WHOLE hunk
       and compared even/odd parity - so an edit to one string's content
       could get smeared together with an unrelated quote-containing
       change elsewhere in the same hunk (a separate string, a trailing
       comment with a stray `"`), flipping the aggregate parity for a
       reason that had nothing to do with any actual phrase losing its
       closing quote.
    2. A line-level SequenceMatcher.get_opcodes() re-scoping (tried next,
       during this same fix) only separates edits when an EXACTLY
       matching line sits between them in the diff - two adjacent but
       unrelated changed lines with nothing identical between them still
       land in one merged "replace" block, so it didn't actually close
       the gap in the general case.

    Fixed properly by checking PHRASE PRESENCE instead of counting
    anything: for each complete `"phrase"` in the hunk's removed text,
    check whether that exact phrase's opening quote survives in the added
    text without its closing quote following it (a negative lookahead
    excluding another quote or a word character, so `"foo"`
    edited into `"foobar"` - a real, harmless content change, not a
    broken phrase - correctly does NOT match, since "foo" is immediately
    followed by the word character "b" in "foobar"). This is immune to
    unrelated quotes anywhere else in the hunk by construction: it never
    counts or aggregates anything, it only asks "did THIS specific phrase
    that used to be complete lose its closing quote", which no unrelated
    string or comment elsewhere can answer differently.
    """
    findings: list[dict] = []
    for hunk in hunks:
        if not hunk.removed or not hunk.added:
            continue
        removed_text = "\n".join(hunk.removed)
        added_text = "\n".join(hunk.added)
        if SequenceMatcher(None, removed_text, added_text).ratio() < _BROKEN_QUOTED_PHRASE_SIMILARITY_THRESHOLD:
            continue
        # dict.fromkeys: dedupe while preserving first-seen order, so a
        # phrase repeated verbatim in removed_text isn't checked twice.
        for phrase in dict.fromkeys(_QUOTED_PHRASE_RE.findall(removed_text)):
            if f'"{phrase}"' in added_text:
                continue  # still complete somewhere in the added text - not broken
            broken_re = re.compile(r'"' + re.escape(phrase) + r'(?!["\w])')
            broken_line = next((line for line in hunk.added if broken_re.search(line)), None)
            if broken_line is None:
                continue
            findings.append(
                _finding(
                    file,
                    _line_number_near_hunk(source, broken_line.strip(), hunk) or hunk.new_start,
                    f'The quoted phrase "{phrase}" appears in the removed text but its closing quote '
                    "is missing from the corresponding added text - a nested quoted phrase inside "
                    "this string likely lost its closing quote during the edit.",
                    f'Restore the closing double-quote after "{phrase}".',
                )
            )
            break  # one finding per hunk is enough; a message with several broken phrases is one bug
    return findings


def find_semantic_regressions(
    diff_text: str,
    file_contents: dict[str, str] | None,
    referenced_symbol_context: str,
) -> list[dict]:
    """Return only regressions directly supported by current source and refs.

    referenced_symbol_context is optional evidence, not a precondition:
    checks anchored on a cross-file dependency's own behavior (raises,
    yields, mutates) genuinely need it and are skipped without it, but
    resource-leak and copy-to-alias detection read only the diff and the
    current file, and running only when a referenced symbol happens to be
    resolvable would skip them on the large majority of real diffs, where
    the changed code calls no in-repo symbol at all (a same-function bug,
    a call into a third-party package, a call into the standard library).
    """
    if not file_contents:
        return []

    references = _referenced_sources(referenced_symbol_context) if referenced_symbol_context else {}
    hunks_by_file = _diff_hunks_by_file(diff_text)
    findings: list[dict] = []

    for file, source in file_contents.items():
        hunks = hunks_by_file.get(file, [])
        if not hunks:
            continue

        # Real false positive found independently by GLM-5.3-Flash reviewing
        # PR #725 with PR-Agent's own prompt structure (a different prompt
        # from Aletheore's, catching a different bug class than the
        # Aletheore-prompt run did): none of the Java/Go-specific checks
        # below were gated by file extension, so their regexes could match
        # Java/Go-shaped text sitting in a comment, docstring, or string
        # literal inside an unrelated Python/JS file. Confirmed directly: a
        # Python docstring showing a Java code example (`try { ... } catch
        # (IOException e) {}`) produced a real "empty catch block" finding
        # on a .py file. The pre-existing Python checks below have the same
        # theoretical gap (not something introduced here, not fixed here -
        # out of scope for this pass) but Python's `except`/`raise` keyword
        # syntax is far less likely to collide with example code sitting in
        # a Java/Go/JS file than Java/Go's brace-and-paren syntax is with
        # exactly this kind of cross-language documentation.
        is_java = file.endswith(".java")
        is_go = file.endswith(".go")

        for name, (_path, dependency) in references.items():
            for call_line in _call_lines(source, name):
                hunk = _nearest_hunk(hunks, call_line)
                if hunk is None:
                    continue
                finding = _check_reference_at_call(file, source, call_line, hunk, name, dependency)
                if finding is None and is_java:
                    finding = _check_reference_at_call_java(file, source, call_line, hunk, name, dependency)
                if finding is not None:
                    findings.append(finding)
                    break

        findings.extend(_moved_record_findings(file, source, hunks, references))
        findings.extend(_resource_leak_findings(file, source, hunks))
        findings.extend(_copy_to_alias_findings(file, source, hunks))
        findings.extend(_removed_bounds_clamp_findings(file, source, hunks))
        findings.extend(_off_by_one_loop_findings(file, source, hunks))
        findings.extend(_sql_injection_findings(file, source, hunks))
        findings.extend(_swallowed_exception_findings(file, source, hunks))
        if is_java:
            findings.extend(_swallowed_exception_findings_java(file, source, hunks))
            findings.extend(_shell_injection_findings_java(file, source, hunks))
        if is_go:
            findings.extend(_shell_injection_findings_go(file, source, hunks))
            findings.extend(_asymmetric_cache_trust_findings_go(file, source, hunks))
        findings.extend(_shell_injection_findings(file, source, hunks))
        findings.extend(_broken_quoted_phrase_findings(file, source, hunks))

    unique: list[dict] = []
    seen: set[tuple[str, int, str]] = set()
    for finding in findings:
        key = (finding["file"], finding["line"], finding["issue"])
        if key not in seen:
            seen.add(key)
            unique.append(finding)
    return unique


_STATIC_ANALYSIS_TOOLS = (check_semgrep, check_bearer)


def find_static_analysis_regressions(diff_text: str, file_contents: dict[str, str] | None) -> list[dict]:
    """Semgrep/Bearer, run fresh and scoped to just this diff's changed
    files - real precedent from the integration scope doc's own per-tool
    cost split: both are cheap stateless CLI subprocess calls (seconds),
    unlike SonarQube, which is deliberately NOT run here - it's too slow
    for any realistic PR-review latency budget and instead feeds
    AIRview/MCP from the last full `aletheore scan` (see
    static_analysis/sonarqube_scanner.py and the scope doc's "PR review
    timing" section).

    review_diff never has a real on-disk checkout - only diff text and
    fetched file blobs (file_contents) - so this materializes just the
    changed files into a throwaway temp directory, runs the two scanners
    against that, and discards it. A finding whose tool/rule already
    exists is intentionally not deduplicated against find_semantic_
    regressions' own findings here - _merge_semantic_findings at the
    call site already handles cross-source dedup by (file, line, issue).
    """
    if not file_contents:
        return []

    hunks_by_file = _diff_hunks_by_file(diff_text)
    relevant_files = {file: content for file, content in file_contents.items() if file in hunks_by_file}
    if not relevant_files:
        return []

    findings: list[dict] = []
    try:
        with tempfile.TemporaryDirectory(prefix="aletheore-pr-static-analysis-") as tmp:
            tmp_path = Path(tmp)
            for file, content in relevant_files.items():
                # file_contents' keys are diff-derived paths, not raw user
                # input, but still checked before writing - a path
                # escaping the temp checkout (a literal ".." component)
                # must never be allowed to write outside the directory
                # this function creates and tears down.
                dest = (tmp_path / file).resolve()
                if tmp_path.resolve() not in dest.parents:
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content, encoding="utf-8", errors="ignore")

            # Bearer requires a git-tracked working tree (real requirement
            # confirmed live: static_analysis/bearer_scanner.py) - a fresh
            # init+commit of just these files satisfies that without
            # needing the PR's real git history, which this function never
            # has access to either way.
            subprocess.run(["git", "init", "-q"], cwd=tmp_path, capture_output=True, timeout=10)
            subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True, timeout=10)
            subprocess.run(
                [
                    "git", "-c", "user.email=aletheore@local", "-c", "user.name=aletheore",
                    "commit", "-q", "-m", "diff-scoped scan",
                ],
                cwd=tmp_path, capture_output=True, timeout=10,
            )

            for scanner in _STATIC_ANALYSIS_TOOLS:
                result = scanner(tmp_path)
                if not result["checked"]:
                    continue
                for finding in result["findings"]:
                    hunks = hunks_by_file.get(finding["path"], [])
                    line = finding["line"]
                    # Tight to the hunk's actual new-line range, not the
                    # padded DIFF_HUNK_TOLERANCE used elsewhere in this
                    # file for call-site proximity - Semgrep/Bearer
                    # analyze the WHOLE materialized file, so without this
                    # a finding on unrelated, unchanged code sitting in the
                    # same file would be misreported as part of this diff.
                    if not any(hunk.new_start <= line <= hunk.new_end for hunk in hunks):
                        continue
                    # Presented as an Aletheore finding, not a
                    # third-party-tool one - which underlying scanner/rule
                    # produced it (finding["tool"]/finding["rule_id"])
                    # stays available in air.json's security.static_analysis
                    # for internal attribution/debugging, but is deliberately
                    # not surfaced in PR-facing text.
                    findings.append(
                        _finding(
                            finding["path"],
                            line,
                            finding["message"],
                            "Review this finding and address it if it applies to the change.",
                        )
                    )
    except OSError:
        # Materializing/scanning a throwaway temp checkout must never take
        # down the rest of PR review - find_semantic_regressions' own
        # findings still run and merge normally even if this fails.
        return []

    return findings
