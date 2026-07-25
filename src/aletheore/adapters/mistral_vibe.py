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
