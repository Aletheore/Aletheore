import os
import shutil
import subprocess

from aletheore.adapters.base import AdapterInvocationError, AgentAdapter

INVOCATION_TIMEOUT_SECONDS = 600


class GrokBuildAdapter(AgentAdapter):
    name = "grok-build"
    requires_consent = False

    def is_available(self) -> bool:
        return shutil.which("grok") is not None

    def invoke(self, instruction: str, cwd: str) -> str:
        # Resolved to an absolute path, not the bare "grok" name: cwd below
        # is the repository being analyzed, which this adapter's own callers
        # (e.g. report.py's managed-audit flow) can point at untrusted
        # third-party code. A bare command name re-resolves PATH at exec
        # time relative to the NEW cwd, so a relative PATH entry would let
        # that repository shadow it with its own same-named executable
        # (Strix security review, 2026-10-15; same class as jobs.py's
        # _git_path() and the scanner wrappers' shutil.which() fixes).
        grok_path = shutil.which("grok")
        grok_path = os.path.abspath(grok_path) if grok_path else "grok"
        try:
            result = subprocess.run(
                [grok_path, "-p", instruction],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=INVOCATION_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise AdapterInvocationError(
                f"grok invocation timed out after {INVOCATION_TIMEOUT_SECONDS}s"
            ) from exc

        if result.returncode != 0:
            raise AdapterInvocationError(
                f"grok invocation failed (exit {result.returncode}): {result.stderr}"
            )

        return result.stdout
