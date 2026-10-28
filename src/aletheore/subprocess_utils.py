"""Resolve Windows tool launchers without passing arguments through cmd.exe."""

import os
from pathlib import Path
import re
import shutil
import sys


def command_argv(executable: str, *args: str, cwd: Path | None = None) -> list[str]:
    if sys.platform != "win32" or Path(executable).suffix.lower() not in (".cmd", ".bat"):
        return [executable, *args]
    shim = Path(executable)
    java = _java_scanner_argv(shim, args, cwd)
    if java is not None:
        return java
    try:
        text = shim.read_text(encoding="utf-8-sig")
    except UnicodeError as exc:
        raise OSError(f"unsupported encoding in batch launcher {shim}; use a UTF-8 launcher or the tool's native executable") from exc
    # npm's cmd-shim uses %dp0% or %~dp0 to locate its JS entry point. Run
    # Node directly: cmd.exe otherwise expands metacharacters and truncates
    # multiline prompts, even when subprocess was called with shell=False.
    if re.search(r"\bnode(?:\.exe)?\b", text, re.I):
        for relative in re.findall(r'"(?:%~dp0|%dp0%)[\\/]([^"\r\n]+)"', text, re.I):
            script = shim.parent.joinpath(*relative.replace("\\", "/").split("/"))
            if not script.is_file() or script.suffix.lower() == ".exe":
                continue
            if script.suffix.lower() not in (".js", ".cjs", ".mjs"):
                with script.open(encoding="utf-8", errors="replace") as stream:
                    first_line = stream.readline(4096)
                if not (first_line.startswith("#!") and "node" in first_line):
                    continue
            sibling = shim.parent / "node.exe"
            node = str(sibling) if sibling.is_file() else shutil.which("node")
            if not node:
                raise FileNotFoundError("Node.js is required to run this npm-installed tool")
            return [os.path.abspath(node), str(script.resolve()), *args]
    raise OSError(f"unsupported batch launcher {shim}; use the tool's native executable or standard npm installation")


def _java_scanner_argv(shim: Path, args: tuple[str, ...], cwd: Path | None) -> list[str] | None:
    """Run standard PMD/Sonar distributions through Java instead of cmd.exe.

    These classpaths and main classes mirror the upstream Windows launchers.
    GUI/designer flags are irrelevant to the scanner's check-only invocation.
    """
    spec = {
        "pmd": ("pmd-cli-*.jar", "net.sourceforge.pmd.cli.PmdCli", ("PMD_JAVA_OPTS",)),
        "sonar-scanner": ("sonar-scanner-cli-*.jar", "org.sonarsource.scanner.cli.Main",
                          ("SONAR_SCANNER_OPTS", "SONAR_SCANNER_DEBUG_OPTS")),
    }.get(shim.stem.lower())
    if spec is None:
        return None
    home = shim.resolve().parent.parent
    if not any(p.is_file() for p in (home / "lib").glob(spec[0])):
        return None
    if any(os.environ.get(name) for name in spec[2]):
        raise OSError("use JAVA_TOOL_OPTIONS for Java VM settings when running a Windows scanner directly")
    candidates = [home / "jre" / "bin" / "java.exe"]
    if os.environ.get("JAVA_HOME"):
        candidates.append(Path(os.environ["JAVA_HOME"]) / "bin" / "java.exe")
    java = next((str(p) for p in candidates if p.is_file()), None) or shutil.which("java")
    if not java or Path(java).suffix.lower() in (".bat", ".cmd"):
        raise FileNotFoundError("a native Java executable is required to run this scanner")
    if shim.stem.lower() == "pmd":
        # This integration selects bundled rules only. A global CLASSPATH
        # (especially '.') must not load classes from the scanned repository.
        classpath = f"{home / 'conf'};{home / 'lib' / '*'}"
        return [os.path.abspath(java), "-cp", classpath, spec[1], *args]
    return [os.path.abspath(java), "-Djava.awt.headless=true", "-Djdk.http.auth.tunneling.disabledSchemes=",
            "-cp", str(home / "lib" / "*"), f"-Dscanner.home={home}",
            f"-Dproject.home={(cwd or Path.cwd()).resolve()}", spec[1], *args]
