import os
import shutil
import subprocess

from aletheore.adapters.base import AdapterInvocationError, AgentAdapter

INVOCATION_TIMEOUT_SECONDS = 600


class GeminiCliAdapter(AgentAdapter):
    name = "gemini-cli"
    requires_consent = False

    def is_available(self) -> bool:
        return shutil.which("gemini") is not None

    def invoke(self, instruction: str, cwd: str) -> str:
        # Resolved to an absolute path, not the bare "gemini" name: cwd below
        # is the repository being analyzed, which this adapter's own callers
        # (e.g. report.py's managed-audit flow) can point at untrusted
        # third-party code. A bare command name re-resolves PATH at exec
        # time relative to the NEW cwd, so a relative PATH entry would let
        # that repository shadow it with its own same-named executable
        # (Strix security review, 2026-10-15; same class as jobs.py's
        # _git_path() and the scanner wrappers' shutil.which() fixes).
        gemini_path = shutil.which("gemini")
        gemini_path = os.path.abspath(gemini_path) if gemini_path else "gemini"
        try:
            result = subprocess.run(
                [gemini_path, "-p", instruction],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=INVOCATION_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise AdapterInvocationError(
                f"gemini invocation timed out after {INVOCATION_TIMEOUT_SECONDS}s"
            ) from exc

        if result.returncode != 0:
            raise AdapterInvocationError(
                f"gemini invocation failed (exit {result.returncode}): {result.stderr}"
            )

        return result.stdout
