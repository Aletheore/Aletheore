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
        try:
            # --auto-approve, like codex's --sandbox workspace-write, lets
            # this write .aletheore/audit-report.md itself per the manual's
            # instruction - see AgentAdapter.requires_consent's docstring
            # for why that's unrelated to this adapter's requires_consent=False.
            result = subprocess.run(
                ["mistral-vibe", "--prompt", instruction, "--auto-approve", "--output", "text"],
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
