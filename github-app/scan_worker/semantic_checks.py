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
    __slots__ = ("new_start", "new_end", "removed", "added")

    def __init__(self, new_start: int, new_end: int) -> None:
        self.new_start = new_start
        self.new_end = new_end
        self.removed: list[str] = []
        self.added: list[str] = []

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
        prev_blank = False
        if current_hunk is None or not current_file:
            continue
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
    return [number for number, line in enumerate(source.splitlines(), 1) if needle in line]


def _line_number(source: str, needle: str) -> int | None:
    for number, line in enumerate(source.splitlines(), 1):
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
    """
    lines = source.splitlines()
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
            for line in source.splitlines()[max(0, hunk.new_start - 1 - DIFF_HUNK_TOLERANCE) : hunk.new_end + DIFF_HUNK_TOLERANCE]
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
        window = "\n".join(
            source.splitlines()[
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

            nearby = source.splitlines()[
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


def _swallowed_exception_findings(file: str, source: str, hunks: list[_Hunk]) -> list[dict]:
    findings: list[dict] = []
    for hunk in hunks:
        added = hunk.added
        for idx, line in enumerate(added):
            match = _BROAD_EXCEPT_RE.match(line)
            if not match:
                continue
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

        for name, (_path, dependency) in references.items():
            for call_line in _call_lines(source, name):
                hunk = _nearest_hunk(hunks, call_line)
                if hunk is None:
                    continue
                finding = _check_reference_at_call(file, source, call_line, hunk, name, dependency)
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
        findings.extend(_shell_injection_findings(file, source, hunks))

    unique: list[dict] = []
    seen: set[tuple[str, int, str]] = set()
    for finding in findings:
        key = (finding["file"], finding["line"], finding["issue"])
        if key not in seen:
            seen.add(key)
            unique.append(finding)
    return unique
