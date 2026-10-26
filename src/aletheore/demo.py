"""An explicit synthetic example, scanned locally with the real engine."""

# cspell:ignore NOSYSTEM gpgsign

import os
import shutil
import subprocess  # nosec B404 - fixed Git argument lists, never a shell
import tempfile
from pathlib import Path

from aletheore.evidence import scan_repository, write_evidence
from aletheore.git_exec import git_executable

DEMO_MARKER = "synthetic-checkout-v1\n"

_FILES = {
    "app.py": "from service import checkout\n\ndef main():\n    return checkout([12, 8])\n",
    "service.py": "from pricing import total\n\ndef checkout(items):\n    return {'total': total(items)}\n",
    "pricing.py": "def total(items):\n    return sum(items)\n",
    "test_service.py": "from service import checkout\n\ndef test_checkout():\n    assert checkout([12, 8]) == {'total': 20}\n",
}


def create_demo() -> tuple[Path, dict]:
    """Keep the fresh temporary repo so follow-up queries and dashboard work."""
    repo = Path(tempfile.mkdtemp(prefix="aletheore-demo-"))
    try:
        executable = git_executable()
        # Ignore user/system git configuration: a demo must not invoke signing,
        # hooks, credential helpers or filters installed by a user's git setup.
        env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        hooks = repo / ".demo-hooks"
        hooks.mkdir()

        def git(*args: str) -> None:
            subprocess.run(  # nosec B603 - absolute Git executable, fixed demo args, shell disabled
                [executable, "-c", f"core.hooksPath={hooks}", "-c", "commit.gpgsign=false",
                 "-c", "user.name=Aletheore Demo", "-c", "user.email=demo@example.invalid", *args],
                cwd=repo, env=env, check=True, capture_output=True, text=True, timeout=30, shell=False,
            )

        git("init", "--quiet", "--template=")
        for name, source in _FILES.items():
            (repo / name).write_text(source, encoding="utf-8")
        git("add", "--", *_FILES)
        git("commit", "--quiet", "-m", "Create synthetic checkout example")
        # Real local commits make the hotspot query meaningful without downloading
        # a corpus or pretending the example represents production findings.
        for revision in (1, 2):
            with (repo / "pricing.py").open("a", encoding="utf-8") as stream:
                stream.write(f"\n# Demo pricing revision {revision}\n")
            git("add", "pricing.py")
            git("commit", "--quiet", "-m", f"Demo pricing revision {revision}")
        evidence = scan_repository(
            repo, check_vulnerabilities=False, check_licenses=False,
            check_static_analysis=False, scan_git_history=False,
            map_endpoints=False, map_schema=False,
        )
        write_evidence(evidence, repo)
        (repo / ".aletheore" / "demo").write_text(DEMO_MARKER, encoding="utf-8")
        return repo, evidence
    except BaseException:
        shutil.rmtree(repo, ignore_errors=True)
        raise
