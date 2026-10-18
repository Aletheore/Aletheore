import os
import shutil
import subprocess

from aletheore.adapters.base import AdapterInvocationError, AgentAdapter

INVOCATION_TIMEOUT_SECONDS = 600


class CodexCliAdapter(AgentAdapter):
    name = "codex"
    requires_consent = False

    def is_available(self) -> bool:
        return shutil.which("codex") is not None

    def invoke(self, instruction: str, cwd: str) -> str:
        # Resolved to an absolute path, not the bare "codex" name: cwd below
        # is the repository being analyzed, which this adapter's own callers
        # (e.g. report.py's managed-audit flow) can point at untrusted
        # third-party code. A bare command name re-resolves PATH at exec
        # time relative to the NEW cwd, so a relative PATH entry would let
        # that repository shadow it with its own same-named executable
        # (Strix security review, 2026-10-15; same class as jobs.py's
        # _git_path() and the scanner wrappers' shutil.which() fixes).
        codex_path = shutil.which("codex")
        if not codex_path:
            # Never fall back to the bare name: it would be re-resolved against PATH inside the untrusted cwd.
            raise AdapterInvocationError("codex is not installed or not on PATH")
        codex_path = os.path.abspath(codex_path)
        try:
            # workspace-write (not full/unrestricted) scopes codex's write
            # access to cwd - it's how codex writes .aletheore/audit-report.md
            # itself per the manual's instruction, not a gap: see
            # AgentAdapter.requires_consent's docstring for why this is
            # unrelated to that flag.
            result = subprocess.run(
                [codex_path, "exec", "--sandbox", "workspace-write", "-C", cwd, instruction],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=INVOCATION_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise AdapterInvocationError(
                f"codex invocation timed out after {INVOCATION_TIMEOUT_SECONDS}s"
            ) from exc

        if result.returncode != 0:
            raise AdapterInvocationError(
                f"codex invocation failed (exit {result.returncode}): {result.stderr}"
            )

        return result.stdout
