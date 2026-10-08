"""Shared comment-body parsing for the ChatOps-style commands triggered by a
GitHub comment reply (issue_comment.py's "/aletheore audit",
pull_request_review_comment.py's "/dismiss").

Both callers need the exact same two things: a non-bare-prefix command
match, and a way to skip lines inside a fenced or indented code block so a
maintainer documenting the command in a reply doesn't fire it for real.
This used to be two independent copies of the same logic in the two
webhook handlers - one copy was fixed for fence-awareness, the other
(pull_request_review_comment.py's /dismiss gate) was not, and stayed
vulnerable to the identical bug for a long time after the fix landed.
Shared here so a fix to either half applies to every caller at once.
"""


def matches_command(line: str, command: str) -> bool:
    """True if `line` (already stripped) IS `command`, or starts with
    `command` followed by whitespace - not a bare string-prefix check.

    A bare `line.startswith(command)` also matches an ordinary English
    word sharing the same stem - "/aletheore auditing this PR now" or
    "/dismissed this already" both satisfy that check, on a thread whose
    entire subject is auditing/dismissing, exactly the conversational
    context where this is a real risk, not a hypothetical one.
    """
    if line == command:
        return True
    return line.startswith(command) and line[len(command) : len(command) + 1].isspace()


def _fence_marker(stripped: str) -> tuple[str, int] | None:
    """Return (fence_char, run_length) if `stripped` is a fence marker line
    (a run of 3+ backticks or tildes), else None."""
    if not stripped:
        return None
    char = stripped[0]
    if char not in ("`", "~"):
        return None
    length = len(stripped) - len(stripped.lstrip(char))
    if length < 3:
        return None
    return char, length


def command_candidate_lines(body: str):
    """Lines of a comment that could be a real command invocation: not inside
    a fenced code block and not an indented (4 spaces / tab) code block, so a
    maintainer documenting the command doesn't fire a real action.

    Compares the full fence-marker run length, not just the first 3
    characters, to decide whether a line closes an open fence - a closing
    marker SHORTER than the opening one (e.g. a literal ``` line
    documented inside a ````-fenced block) must not end tracking early,
    per GitHub's own CommonMark/GFM rendering (a closing fence must be
    >= the opening fence's length). A fence-shaped line that doesn't
    close the open fence (too short, wrong character, or has trailing
    content) is literal content inside the fence, not a real close.
    """
    fence_char: str | None = None
    fence_len = 0
    for raw in body.splitlines():
        stripped = raw.strip()
        marker = _fence_marker(stripped)
        if marker is not None:
            char, length = marker
            if fence_char is None:
                fence_char, fence_len = char, length
                continue
            if char == fence_char and length >= fence_len and stripped[length:].strip() == "":
                fence_char = None
                fence_len = 0
                continue
        if fence_char is not None:
            continue
        if raw.startswith(("    ", "\t")):
            continue
        yield stripped
