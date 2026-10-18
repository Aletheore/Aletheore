import os
import shutil
import subprocess

from aletheore.adapters.base import AdapterInvocationError, AgentAdapter

INVOCATION_TIMEOUT_SECONDS = 600


class MistralVibeAdapter(AgentAdapter):
    name = "mistral-vibe"
    requires_consent = False

    def is_available(self) -> bool:
        return shutil.which("mistral-vibe") is not None

    def invoke(self, instruction: str, cwd: str) -> str:
        # Resolved to an absolute path, not the bare "mistral-vibe" name: cwd
        # below is the repository being analyzed, which this adapter's own
        # callers (e.g. report.py's managed-audit flow) can point at
        # untrusted third-party code. A bare command name re-resolves PATH
        # at exec time relative to the NEW cwd, so a relative PATH entry
        # would let that repository shadow it with its own same-named
        # executable (Strix security review, 2026-10-15; same class as
        # jobs.py's _git_path() and the scanner wrappers' shutil.which()
        # fixes).
        mistral_vibe_path = shutil.which("mistral-vibe")
        if not mistral_vibe_path:
            # Never fall back to the bare name: it would be re-resolved against PATH inside the untrusted cwd.
            raise AdapterInvocationError("mistral-vibe is not installed or not on PATH")
        mistral_vibe_path = os.path.abspath(mistral_vibe_path)
        try:
            # --auto-approve, like codex's --sandbox workspace-write, lets
            # this write .aletheore/audit-report.md itself per the manual's
            # instruction - see AgentAdapter.requires_consent's docstring
            # for why that's unrelated to this adapter's requires_consent=False.
            result = subprocess.run(
                [mistral_vibe_path, "--prompt", instruction, "--auto-approve", "--output", "text"],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=INVOCATION_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise AdapterInvocationError(
                f"mistral-vibe invocation timed out after {INVOCATION_TIMEOUT_SECONDS}s"
            ) from exc

        if result.returncode != 0:
            raise AdapterInvocationError(
                f"mistral-vibe invocation failed (exit {result.returncode}): {result.stderr}"
            )

        return result.stdout
