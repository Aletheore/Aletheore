import os
import shutil
import subprocess

from aletheore.adapters.base import AdapterInvocationError, AgentAdapter
from aletheore.subprocess_utils import command_argv

INVOCATION_TIMEOUT_SECONDS = 600


class OpenCodeAdapter(AgentAdapter):
    name = "opencode"

    def is_available(self) -> bool:
        return shutil.which("opencode") is not None

    def invoke(self, instruction: str, cwd: str) -> str:
        # Resolved to an absolute path, not the bare "opencode" name: cwd
        # below is the repository being analyzed, which this adapter's own
        # callers (e.g. report.py's managed-audit flow) can point at
        # untrusted third-party code. A bare command name re-resolves PATH
        # at exec time relative to the NEW cwd, so a relative PATH entry
        # would let that repository shadow it with its own same-named
        # executable (Strix security review, 2026-10-15; same class as
        # jobs.py's _git_path() and the scanner wrappers' shutil.which()
        # fixes).
        opencode_path = shutil.which("opencode")
        if not opencode_path:
            # Never fall back to the bare name: it would be re-resolved against PATH inside the untrusted cwd.
            raise AdapterInvocationError("opencode is not installed or not on PATH")
        opencode_path = os.path.abspath(opencode_path)
        try:
            result = subprocess.run(  # nosec B603 - absolute tool, literal argv; batch shims resolved directly to Node
                command_argv(opencode_path, "run", instruction),
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
                f"opencode invocation timed out after {INVOCATION_TIMEOUT_SECONDS}s"
            ) from exc

        except OSError as exc:
            raise AdapterInvocationError(f"{self.name} could not start: {exc}") from exc

        if result.returncode != 0:
            raise AdapterInvocationError(
                f"opencode invocation failed (exit {result.returncode}): {result.stderr}"
            )

        return result.stdout
