"""Worker runners: start one headless agent run in a worktree and return its result.

Flags come from council/docs/spike-headless-agents.md (observed on macOS).
Binaries are resolved to absolute paths (never the bare name, which would be
re-resolved against PATH inside an untrusted cwd). Child environments are
built from an allowlist: API keys and other secrets in the parent environment
are not passed on.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


class AgentLaunchError(Exception):
    pass


@dataclass
class RunResult:
    ok: bool
    final_text: str
    exit_code: int
    raw_events: list[dict] = field(default_factory=list)


# The kinds of agent built in (an agent id ends in its kind: 'claude', 'bob-codex', 'alice-opencode'). Claude and Codex have their own runners below;
# the others run through agent_adapters.py, which also reads the user's own agents from agents.json. worker_kind() knows all of them.
WORKER_KINDS = ("claude", "codex", "opencode", "agy")


def worker_kind(agent_id: str) -> str | None:
    """The kind an agent id ends in (the longest match: 'bob-mycodex' is 'mycodex', not 'codex'), or None if no runner exists for it."""
    from aletheore.council.agent_adapters import known_kinds

    matches = [k for k in known_kinds() if agent_id.endswith(k)]
    return max(matches, key=len) if matches else None


class WorkerRunner(Protocol):
    name: str

    def run(self, instruction: str, worktree: Path, mcp_config: Path, identity_env: dict[str, str], timeout_s: int,
            images: tuple[Path, ...] = (), read_only: bool = False) -> RunResult: ...


_ENV_ALLOW = (
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "TERM", "TMPDIR", "TEMP", "TMP",
    "USERPROFILE", "APPDATA", "LOCALAPPDATA", "SYSTEMROOT", "COMSPEC", "PATHEXT", "PROGRAMDATA",
    "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR",
)

DEFAULT_CLAUDE_TOOLS = (
    "Read", "Write", "Edit", "Glob", "Grep",
    "mcp__council__council_status", "mcp__council__council_read", "mcp__council__council_post",
)


def build_child_env(identity_env: dict[str, str]) -> dict[str, str]:
    env = {k: os.environ[k] for k in _ENV_ALLOW if k in os.environ}
    env.update(identity_env)
    return env


def kill_process_tree(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True)
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _real_exe_for_shim(binary: str, shim: str) -> str | None:
    """npm installs agent CLIs as .cmd shims that wrap a real .exe. Find that exe: first `<binary>.exe` on PATH
    (the native installer's location), then inside the shim's own npm folder (never anywhere else)."""
    found = shutil.which(binary + ".exe")
    if found and found.lower().endswith(".exe"):
        return found
    root = Path(shim).parent / "node_modules"
    if root.is_dir():
        for dirpath, dirnames, filenames in os.walk(root):
            if len(Path(dirpath).relative_to(root).parts) > 10:
                dirnames[:] = []
                continue
            if binary + ".exe" in filenames:
                return os.path.join(dirpath, binary + ".exe")
    return None


def _resolve(binary: str) -> str:
    path = shutil.which(binary)
    if not path:
        raise AgentLaunchError(f"{binary} is not installed or not on PATH")
    if path.lower().endswith((".cmd", ".bat")):
        # Python does not escape cmd.exe metacharacters for batch shims, and agent prompts carry other
        # participants' text, so a shim is never run: use the real executable it wraps, or refuse.
        real = _real_exe_for_shim(binary, path)
        if real is None:
            raise AgentLaunchError(f"{path} is a Windows batch shim (.cmd/.bat) and its real executable was not found; point PATH at the real executable")
        path = real
    return os.path.abspath(path)


def _run_process(argv: list[str], cwd: Path, env: dict[str, str], timeout_s: int) -> tuple[int, str, str, bool]:
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(
            argv, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", **kwargs,
        )
    except OSError as exc:  # missing interpreter, permission denied, argument too long
        return -1, "", f"could not start agent: {exc}", False
    try:
        out, err = proc.communicate(timeout=timeout_s)
        return proc.returncode, out, err, False
    except subprocess.TimeoutExpired:
        kill_process_tree(proc.pid)
        out, err = proc.communicate()
        return proc.returncode if proc.returncode is not None else -9, out or "", err or "", True


def _parse_events(stdout: str) -> list[dict]:
    events = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            events.append(obj)
    return events


def _failure(code: int, stderr: str, events: list[dict], timed_out: bool, timeout_s: int) -> RunResult:
    if timed_out:
        return RunResult(False, f"agent run timed out after {timeout_s}s", code, events)
    note = stderr.strip()[-2000:] or f"agent exited with code {code}"
    return RunResult(False, note, code, events)


class ClaudeRunner:
    name = "claude"

    def __init__(self, model: str | None = None, allowed_tools: tuple[str, ...] = DEFAULT_CLAUDE_TOOLS) -> None:
        self.model = model
        self.allowed_tools = allowed_tools

    def run(self, instruction: str, worktree: Path, mcp_config: Path, identity_env: dict[str, str], timeout_s: int,
            images: tuple[Path, ...] = (), read_only: bool = False) -> RunResult:
        try:
            binary = _resolve("claude")
        except AgentLaunchError as exc:
            return RunResult(False, str(exc), -1, [])
        argv = [
            binary, "-p", instruction,
            "--output-format", "stream-json", "--verbose",
            "--strict-mcp-config", "--mcp-config", str(mcp_config),
        ]
        if self.model:
            argv += ["--model", self.model]
        image_dirs = list(dict.fromkeys(str(Path(i).parent) for i in images))
        if image_dirs:
            argv += ["--add-dir", *image_dirs]  # lets the agent's Read tool open the attached images
        tools = [t for t in self.allowed_tools if t not in ("Write", "Edit")] if read_only else list(self.allowed_tools)
        argv += ["--allowedTools", *tools]  # variadic: must come last
        code, out, err, timed_out = _run_process(argv, worktree, build_child_env(identity_env), timeout_s)
        events = _parse_events(out)
        if timed_out or code != 0:
            return _failure(code, err, events, timed_out, timeout_s)
        results = [e for e in events if e.get("type") == "result"]
        if not results:
            return RunResult(False, "agent produced no result event", code, events)
        last = results[-1]
        return RunResult(not last.get("is_error", False), str(last.get("result", "")), code, events)


def _toml_env(env: dict[str, str]) -> str:
    return "{" + ",".join(f"{k}={json.dumps(v)}" for k, v in env.items()) + "}"


class CodexRunner:
    name = "codex"

    def __init__(self, model: str | None = None) -> None:
        self.model = model

    def run(self, instruction: str, worktree: Path, mcp_config: Path, identity_env: dict[str, str], timeout_s: int,
            images: tuple[Path, ...] = (), read_only: bool = False) -> RunResult:
        entry = json.loads(Path(mcp_config).read_text(encoding="utf-8")).get("mcpServers", {}).get("council")
        overrides = []
        if entry:
            mcp_env = {**entry.get("env", {}), **identity_env}
            overrides = [
                f"mcp_servers.council.command={json.dumps(entry['command'])}",
                f"mcp_servers.council.args={json.dumps(list(entry.get('args', [])))}",
                f"mcp_servers.council.env={_toml_env(mcp_env)}",
                'mcp_servers.council.default_tools_approval_mode="approve"',
            ]
        try:
            binary = _resolve("codex")
        except AgentLaunchError as exc:
            return RunResult(False, str(exc), -1, [])
        argv = [binary, "exec"]
        for image in images:  # before the other flags: --image takes several values and must not swallow the prompt
            argv += ["-i", str(image)]
        argv += ["--json", "--sandbox", "read-only" if read_only else "workspace-write", "-C", str(worktree)]
        if self.model:
            argv += ["-m", self.model]
        for o in overrides:
            argv += ["-c", o]
        argv.append(instruction)
        code, out, err, timed_out = _run_process(argv, worktree, build_child_env(identity_env), timeout_s)
        events = _parse_events(out)
        if timed_out or code != 0:
            return _failure(code, err, events, timed_out, timeout_s)
        if any(e.get("type") in ("turn.failed", "error") for e in events):
            msg = next((str(e.get("message") or e.get("error") or "") for e in events if e.get("type") in ("turn.failed", "error")), "")
            return RunResult(False, msg or "codex reported an error", code, events)
        messages = [
            e["item"]["text"] for e in events
            if e.get("type") == "item.completed" and e.get("item", {}).get("type") == "agent_message"
        ]
        if not messages:
            return RunResult(False, "agent produced no final message", code, events)
        return RunResult(True, messages[-1], code, events)


class ScriptedRunner:
    """Test double: records instructions, writes canned files, returns a canned result."""

    name = "scripted"

    def __init__(self, final_text: str = "done", writes: dict[str, str] | None = None, ok: bool = True, exit_code: int = 0, texts: list[str] | None = None) -> None:
        self.final_text = final_text
        self.writes = writes or {}
        self.ok = ok
        self.exit_code = exit_code
        self.texts = list(texts) if texts else None
        self.calls: list[str] = []
        self.image_calls: list[tuple] = []
        self.read_only_calls: list[bool] = []

    def run(self, instruction: str, worktree: Path, mcp_config: Path, identity_env: dict[str, str], timeout_s: int,
            images: tuple[Path, ...] = (), read_only: bool = False) -> RunResult:
        self.calls.append(instruction)
        self.image_calls.append(tuple(images))
        self.read_only_calls.append(read_only)
        for rel, content in ({} if read_only else self.writes).items():
            target = Path(worktree) / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        text = self.texts.pop(0) if self.texts else self.final_text
        return RunResult(ok=self.ok, final_text=text, exit_code=self.exit_code, raw_events=[])
