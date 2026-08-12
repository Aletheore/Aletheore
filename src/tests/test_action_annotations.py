import json
import subprocess
import sys
import textwrap
from pathlib import Path


def _annotate_new_secrets_script() -> str:
    action_yml = Path(__file__).resolve().parents[2] / "action.yml"
    text = action_yml.read_text()
    section_start = text.index("- name: Annotate new secrets")
    heredoc_start = text.index("python3 - <<'PYEOF'", section_start)
    script_start = text.index("\n", heredoc_start) + 1
    script_end = text.index("\n        PYEOF", script_start)
    return textwrap.dedent(text[script_start:script_end])


def test_action_secret_annotation_escapes_workflow_command_file_property(tmp_path):
    script = _annotate_new_secrets_script()
    (tmp_path / "annotate.py").write_text(script)
    hostile_path = "src/a,b\nc:d%file.py"
    diff = {
        "secrets": {
            "new": [
                {
                    "path": hostile_path,
                    "line": 7,
                    "pattern": "API key",
                }
            ]
        }
    }
    (tmp_path / "diff-output.json").write_text(json.dumps(diff))

    result = subprocess.run(
        [sys.executable, "annotate.py"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.splitlines() == [
        "::error file=src/a%2Cb%0Ac%3Ad%25file.py,line=7::"
        "New secret detected (API key). Rotate it and remove it from source."
    ]

