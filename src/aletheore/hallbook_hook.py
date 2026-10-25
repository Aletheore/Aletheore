"""Fast entry point for the Claude Code UserPromptSubmit hook.

The hook runs on every user prompt, so this module deliberately imports only the
standard library and `aletheore.skill_hub` (whose heavy imports are lazy). The
command-line path in `aletheore.cli` and this module both call `main`, so there is
one implementation of reading stdin, printing a suggestion and staying silent.

    python -m aletheore.hallbook_hook --mode hint
    python -m aletheore.hallbook_hook --mode inject
"""
from __future__ import annotations

import json
import sys


def _read_prompt() -> str:
    raw = sys.stdin.buffer.read()
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return raw.decode("utf-8", "replace")
    prompt = payload.get("prompt") if isinstance(payload, dict) else payload
    if isinstance(prompt, str):
        return prompt
    return "" if prompt is None else str(prompt)


def main(argv: list[str] | None = None) -> int:
    """Read the hook JSON from stdin, print a suggestion and always exit 0."""
    try:
        args = list(sys.argv[1:] if argv is None else argv)
        mode = "hint"
        for index, arg in enumerate(args):
            if arg == "--mode" and index + 1 < len(args):
                mode = args[index + 1]
            elif arg.startswith("--mode="):
                mode = arg.split("=", 1)[1]
        from aletheore.skill_hub import HOOK_MODES, hook_suggestion

        if mode not in HOOK_MODES:
            # The hook must never break a prompt, so this stays exit 0 with nothing on stdout.
            sys.stderr.write(f"aletheore hallbook suggest: unknown mode {mode!r}; use one of: {', '.join(HOOK_MODES)}\n")
            return 0

        suggestion = hook_suggestion(_read_prompt(), mode=mode)
        if suggestion:
            sys.stdout.write(suggestion + "\n")
            sys.stdout.flush()
    except BaseException:
        # A hook must never break the prompt or print a traceback. Exit 0 silently.
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
