import os
import shutil
import subprocess

from aletheore.adapters.base import AdapterInvocationError, AgentAdapter
from aletheore.subprocess_utils import command_argv

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
        if not gemini_path:
            # Never fall back to the bare name: it would be re-resolved against PATH inside the untrusted cwd.
            raise AdapterInvocationError("gemini is not installed or not on PATH")
        gemini_path = os.path.abspath(gemini_path)
        try:
            result = subprocess.run(  # nosec B603 - absolute tool, literal argv; batch shims resolved directly to Node
                command_argv(gemini_path, "-p", instruction),
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdin=subprocess.DEVNULL,
                timeout=INVOCATION_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise AdapterInvocationError(
                f"gemini invocation timed out after {INVOCATION_TIMEOUT_SECONDS}s"
            ) from exc

        except OSError as exc:
            raise AdapterInvocationError(f"{self.name} could not start: {exc}") from exc

        if result.returncode != 0:
            raise AdapterInvocationError(
                f"gemini invocation failed (exit {result.returncode}): {result.stderr}"
            )

        return result.stdout
