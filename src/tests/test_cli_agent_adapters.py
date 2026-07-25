import subprocess
from unittest.mock import MagicMock, patch

import pytest

from aletheore.adapters.base import AdapterInvocationError
from aletheore.adapters.codex_cli import CodexCliAdapter
from aletheore.adapters.gemini_cli import GeminiCliAdapter
from aletheore.adapters.grok_build import GrokBuildAdapter
from aletheore.adapters.mistral_vibe import MistralVibeAdapter


@pytest.mark.parametrize(
    ("adapter_cls", "name", "binary", "expected_command"),
    [
        (
            CodexCliAdapter,
            "codex",
            "codex",
            ["codex", "exec", "--sandbox", "workspace-write", "-C", "/some/repo", "do the audit"],
        ),
        (GeminiCliAdapter, "gemini-cli", "gemini", ["gemini", "-p", "do the audit"]),
        (
            MistralVibeAdapter,
            "mistral-vibe",
            "mistral-vibe",
            ["mistral-vibe", "--prompt", "do the audit", "--auto-approve", "--output", "text"],
        ),
        (GrokBuildAdapter, "grok-build", "grok", ["grok", "-p", "do the audit"]),
    ],
)
def test_cli_adapter_contract(adapter_cls, name, binary, expected_command):
    adapter = adapter_cls()
    assert adapter.name == name
    assert adapter.requires_consent is False

    module_name = adapter_cls.__module__
    with patch(f"{module_name}.shutil.which", return_value=f"/usr/local/bin/{binary}") as which:
        assert adapter.is_available() is True
    which.assert_called_once_with(binary)

    with patch(f"{module_name}.shutil.which", return_value=None):
        assert adapter.is_available() is False

    with patch(f"{module_name}.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout="report text", stderr="")
        assert adapter.invoke("do the audit", cwd="/some/repo") == "report text"
    args, kwargs = run.call_args
    assert args[0] == expected_command
    assert kwargs["cwd"] == "/some/repo"

    with patch(f"{module_name}.subprocess.run") as run:
        run.return_value = MagicMock(returncode=1, stdout="", stderr="boom")
        with pytest.raises(AdapterInvocationError, match="boom"):
            adapter.invoke("do the audit", cwd="/some/repo")

    with patch(f"{module_name}.subprocess.run") as run:
        run.side_effect = subprocess.TimeoutExpired(cmd=binary, timeout=600)
        with pytest.raises(AdapterInvocationError, match="timed out"):
            adapter.invoke("do the audit", cwd="/some/repo")
