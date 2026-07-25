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
        try:
            result = subprocess.run(
                ["grok", "-p", instruction],
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
