"""Real-engine regression tests; also runnable without installing Aletheore.

CI invokes this file directly after installing the pinned Semgrep environment.
The ordinary pytest suite skips these tests when the optional scanner is absent.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


RULE_ID = "java-runtime-exec-command-injection"
SRC = Path(__file__).resolve().parents[2]
RULE = SRC / "aletheore" / "static_analysis" / "semgrep_rules" / f"{RULE_ID}.yaml"
FIXTURE = SRC / "tests" / "fixtures" / "semgrep" / f"{RULE_ID}.java"


@unittest.skipUnless(shutil.which("semgrep"), "optional Semgrep binary is not installed")
class JavaCommandInjectionRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="aletheore-java-rule-")
        cls.addClassCleanup(cls.temp.cleanup)
        root = Path(cls.temp.name)
        cls.scan_root = root
        cls.repo = root / "repo"
        # Include a production directory containing "test" as a substring:
        # exclusions must not accidentally hide application code in contest/.
        for rel in ["production/Commands.java", "contest/Commands.java",
                    "production/CommandsTest.java", "production/TestCommands.java",
                    "production/CommandsTests.java", "test/Commands.java", "tests/Commands.java"]:
            target = cls.repo / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(FIXTURE, target)
        env = dict(os.environ)
        env.pop("SEMGREP_APP_TOKEN", None)
        env.update({
            "SEMGREP_ENABLE_VERSION_CHECK": "0",
            "SEMGREP_LOG_FILE": str(root / "semgrep.log"),
            "SEMGREP_SETTINGS_FILE": str(root / "settings.yml"),
            "XDG_CACHE_HOME": str(root / "cache"),
        })
        # Semgrep's OCaml entry point also initializes its CA store for local
        # scans. Respect an explicit bundle; otherwise use its certifi dependency.
        if "SSL_CERT_FILE" not in env:
            import certifi
            env["SSL_CERT_FILE"] = certifi.where()
        binary = str(Path(shutil.which("semgrep")).absolute())
        result = subprocess.run(
            [binary, "scan", "--config", str(RULE), "--metrics=off",
             "--disable-version-check", "--strict", "--json", str(cls.repo)],
            cwd=root, env=env, capture_output=True, text=True, timeout=90,
        )
        if result.returncode != 0:
            raise AssertionError(f"Semgrep failed ({result.returncode}): {result.stderr}\n{result.stdout}")
        cls.payload = json.loads(result.stdout)

    def _relative_path(self, value):
        path = Path(value)
        if not path.is_absolute():
            path = self.scan_root / path
        return path.resolve().relative_to(self.repo.resolve()).as_posix()

    def test_exact_positive_and_negative_examples(self):
        expected_lines = {
            index + 2 for index, line in enumerate(FIXTURE.read_text(encoding="utf-8").splitlines())
            if f"// ruleid: {RULE_ID}" in line
        }
        expected = {
            (path, line) for path in ["production/Commands.java", "contest/Commands.java"]
            for line in expected_lines
        }
        actual = [
            (self._relative_path(item["path"]), item["start"]["line"])
            for item in self.payload["results"]
        ]
        self.assertTrue(expected_lines, "fixture must exercise positive matches")
        self.assertCountEqual(actual, expected)
        self.assertTrue(all(item["check_id"].endswith(RULE_ID) for item in self.payload["results"]))

    def test_supported_java_files_parse_without_errors(self):
        self.assertEqual(self.payload["errors"], [])
        scanned = {
            self._relative_path(path)
            for path in self.payload["paths"]["scanned"]
        }
        self.assertEqual(scanned, {"production/Commands.java", "contest/Commands.java"})


if __name__ == "__main__":
    if shutil.which("semgrep") is None:
        raise SystemExit("Install Semgrep before running the rule regression suite")
    unittest.main()
