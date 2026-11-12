"""Adapters: how Council starts any agent CLI beyond Claude and Codex, and reads its answer.

A worker needs very little from an agent: run headless in a worktree, edit files there, print an answer. The room tools (MCP) are optional,
because a worker's printed answer becomes the room's result and its file changes are committed from the worktree. So an adapter only says
how to build the command line, how to lock the agent down (no shell, no web unless we know how to allow it safely), and how to read the output.

Built in and verified: OpenCode (`opencode`) and AGY / Antigravity CLI (`agy`). Any other CLI can be described in `agents.json` in the Council
folder (see `docs/usage.md`). The command is always a list of arguments, never a shell line: the instruction carries other people's
text and is passed as one argument.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from platformdirs import user_data_dir

from aletheore.council.runners import (
    WORKER_KINDS, AgentLaunchError, RunResult, _failure, _parse_events, _resolve, _run_process, build_child_env,
)

_KIND_RE = re.compile(r"[a-z][a-z0-9]{1,23}")
_OUTPUTS = ("text", "json", "jsonl")


class AdapterError(ValueError):
    pass


@dataclass(frozen=True)
class Spec:
    """Everything an adapter needs to build one run."""
    binary: str            # absolute path of the resolved executable
    instruction: str
    worktree: Path
    mcp_config: Path       # the per-run room tools, in the common {"mcpServers": {"council": {...}}} form
    mcp_entry: dict | None  # that "council" entry with the run's identity merged into its env, or None
    images: tuple
    read_only: bool
    model: str | None


@dataclass(frozen=True)
class Launch:
    argv: list
    env: dict


@dataclass(frozen=True)
class Adapter:
    kind: str
    binary: str
    build: Callable[[Spec], Launch]
    parse: Callable[[str, str, int], RunResult]  # stdout, stderr, exit code
    notes: str = ""


# ---- OpenCode ---------------------------------------------------------------------------------------------------------------------------------
def _opencode_build(s: Spec) -> Launch:
    cfg: dict = {"permission": {"edit": "deny" if s.read_only else "allow", "bash": "deny", "webfetch": "deny"}}
    if s.mcp_entry:
        cfg["mcp"] = {"council": {"type": "local", "command": [s.mcp_entry["command"], *s.mcp_entry.get("args", [])],
                                  "environment": s.mcp_entry.get("env", {}), "enabled": True}}
    argv = [s.binary, "run", "--pure", "--format=json"]
    if s.model:
        argv += ["-m", s.model]
    for image in s.images:  # before "--": -f takes several values
        argv += ["-f", str(image)]
    argv += ["--", s.instruction]  # after "--" the instruction can never be read as a flag
    return Launch(argv, {"OPENCODE_CONFIG_CONTENT": json.dumps(cfg), "OPENCODE_DISABLE_AUTOUPDATE": "1"})


def _opencode_parse(out: str, err: str, code: int) -> RunResult:
    events = _parse_events(out)
    errors = [e for e in events if e.get("type") == "error"]
    if errors:
        detail = errors[-1].get("error") or {}
        msg = (detail.get("data") or {}).get("message") or detail.get("name") or "opencode reported an error"
        return RunResult(False, str(msg), code, events)
    if code != 0:
        return _failure(code, err, events, False, 0)
    texts = [str((e.get("part") or {}).get("text", "")) for e in events if e.get("type") == "text"]
    texts = [t for t in texts if t.strip()]
    if not texts:
        return RunResult(False, "agent produced no final message", code, events)
    return RunResult(True, texts[-1], code, events)


# ---- AGY (Antigravity CLI) --------------------------------------------------------------------------------------------------------------------
_AGY_DENY = ["command(*)", "read_url(*)", "mcp(*)"]  # no shell, no web, no MCP: AGY only takes MCP servers from its global config, not per run


def _agy_build(s: Spec) -> Launch:
    settings = json.dumps({"permissions": {"deny": _AGY_DENY}})
    argv = [s.binary, "--mode", "plan" if s.read_only else "accept-edits", "--output-format", "json", "--settings=" + settings]
    if s.model:
        argv += ["--model", s.model]
    argv.append("--print=" + s.instruction)  # attached to the flag: a leading dash can never be taken for another flag
    return Launch(argv, {})


def _agy_parse(out: str, err: str, code: int) -> RunResult:
    text = out.strip()
    data = None
    for candidate in [text] + [ln for ln in reversed(text.splitlines()) if ln.strip().startswith("{")]:
        try:
            parsed = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            data = parsed
            break
    if data is None:
        return RunResult(False, (text + "\n" + err).strip()[-1500:] or f"agent exited with code {code}", code, [])
    if data.get("status") != "SUCCESS":
        why = data.get("error") or data.get("message") or data.get("status") or "unknown error"
        return RunResult(False, str(why), code, [data])
    response = str(data.get("response", "")).strip()
    if not response:
        return RunResult(False, "agent produced no final message", code, [data])
    return RunResult(True, response, code, [data])


_BUILTIN: dict[str, Adapter] = {
    "opencode": Adapter("opencode", "opencode", _opencode_build, _opencode_parse,
                        "verified on macOS and Windows; edits files, no shell, no web; room tools supported"),
    "agy": Adapter("agy", "agy", _agy_build, _agy_parse,
                   "verified on macOS and Windows; edits files, no shell, no web; no room tools (AGY cannot take them per run); images are not passed"),
}


# ---- agents the user describes ----------------------------------------------------------------------------------------------------------------
def _home() -> Path:
    return Path(os.environ.get("COUNCIL_HOME") or user_data_dir("aletheore-council"))


def _command_list(kind: str, key: str, value, required_placeholder: bool = True) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(a, str) and a for a in value):
        raise AdapterError(f"agent '{kind}': {key} must be a non-empty list of arguments (not a shell line)")
    if required_placeholder and not any("{instruction}" in a for a in value):
        raise AdapterError(f"agent '{kind}': {key} must contain {{instruction}} in one of its arguments")
    return value


def _fill(argv: list[str], s: Spec) -> list[str]:
    out = []
    for i, a in enumerate(argv):
        a = a.replace("{instruction}", s.instruction).replace("{worktree}", str(s.worktree)).replace("{mcp_config}", str(s.mcp_config))
        out.append(s.binary if i == 0 else a)
    return out


def _dig(data, path: str):
    for part in path.split("."):
        if isinstance(data, dict) and part in data:
            data = data[part]
        elif isinstance(data, list) and part.isdigit() and int(part) < len(data):
            data = data[int(part)]
        else:
            return None
    return data


def _user_adapter(kind: str, spec) -> Adapter:
    if kind in _BUILTIN or kind in WORKER_KINDS:
        raise AdapterError(f"agent '{kind}' is built in and cannot be redefined")
    if not isinstance(kind, str) or not _KIND_RE.fullmatch(kind):
        raise AdapterError(f"agent name '{kind}': 2 to 24 lowercase letters and digits, starting with a letter (an agent id ends with it)")
    if not isinstance(spec, dict):
        raise AdapterError(f"agent '{kind}': the definition must be an object")
    command = _command_list(kind, "command", spec.get("command"))
    read_only_command = _command_list(kind, "read_only_command", spec["read_only_command"]) if "read_only_command" in spec else None
    output = spec.get("output", "text")
    if output not in _OUTPUTS:
        raise AdapterError(f"agent '{kind}': output must be one of {', '.join(_OUTPUTS)}")
    field = spec.get("field")
    if output != "text" and not (isinstance(field, str) and field):
        raise AdapterError(f"agent '{kind}': output '{output}' needs a field (the dotted path to the answer, for example result.text)")
    extra_env = spec.get("env", {})
    if not isinstance(extra_env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in extra_env.items()):
        raise AdapterError(f"agent '{kind}': env must map names to text values (put no secrets in it)")

    def build(s: Spec) -> Launch:
        chosen = read_only_command if s.read_only else command
        if chosen is None:
            raise AdapterError(f"agent '{kind}' has no read-only command configured, so it cannot run a read-only (planning) turn")
        return Launch(_fill(chosen, s), dict(extra_env))

    def parse(out: str, err: str, code: int) -> RunResult:
        if code != 0:
            return _failure(code, err, [], False, 0)
        text = out.strip()
        if output == "text":
            return RunResult(True, text, code, []) if text else RunResult(False, f"{kind} produced no output", code, [])
        lines = [ln for ln in text.splitlines() if ln.strip().startswith("{")] if output == "jsonl" else [text]
        try:
            data = json.loads(lines[-1]) if lines else None
        except ValueError:
            data = None
        answer = _dig(data, field) if data is not None else None
        if not isinstance(answer, str) or not answer.strip():
            return RunResult(False, f"{kind} produced no '{field}' in its output", code, [data] if isinstance(data, dict) else [])
        return RunResult(True, answer.strip(), code, [data] if isinstance(data, dict) else [])

    return Adapter(kind, command[0], build, parse, "described in agents.json; not verified by Council")


def load_user_adapters() -> dict[str, Adapter]:
    """Read agents.json from the Council folder: {"agents": {"aider": {"command": [...], "output": "text"}}}. No file is no agents."""
    path = _home() / "agents.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        agents = data["agents"]
        if not isinstance(agents, dict):
            raise TypeError
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise AdapterError(f"{path} is not a valid agents.json (expected {{\"agents\": {{...}}}}): {exc.__class__.__name__}") from None
    return {kind: _user_adapter(kind, spec) for kind, spec in agents.items()}


def known_kinds() -> tuple[str, ...]:
    """Every kind an agent id can end in: Claude, Codex, the built-in adapters, and the user's own. A bad agents.json adds none."""
    try:
        user = tuple(load_user_adapters())
    except AdapterError:
        user = ()
    return tuple(dict.fromkeys((*WORKER_KINDS, *user)))


# ---- the runner -------------------------------------------------------------------------------------------------------------------------------
class AdapterRunner:
    def __init__(self, adapter: Adapter, model: str | None = None) -> None:
        self.adapter = adapter
        self.name = adapter.kind
        self.model = model

    def run(self, instruction: str, worktree: Path, mcp_config: Path, identity_env: dict[str, str], timeout_s: int,
            images: tuple[Path, ...] = (), read_only: bool = False) -> RunResult:
        try:
            binary = _resolve(self.adapter.binary)
        except AgentLaunchError as exc:
            return RunResult(False, str(exc), -1, [])
        entry = None
        try:
            found = json.loads(Path(mcp_config).read_text(encoding="utf-8")).get("mcpServers", {}).get("council")
            if found:
                entry = {**found, "env": {**found.get("env", {}), **identity_env}}
        except (OSError, ValueError, AttributeError):
            entry = None
        try:
            launch = self.adapter.build(Spec(binary, instruction, Path(worktree), Path(mcp_config), entry, tuple(images), read_only, self.model))
        except AdapterError as exc:
            return RunResult(False, str(exc), -1, [])
        env = {**build_child_env(identity_env), **launch.env}
        code, out, err, timed_out = _run_process(launch.argv, Path(worktree), env, timeout_s)
        if timed_out:
            return _failure(code, err, [], True, timeout_s)
        return self.adapter.parse(out, err, code)


def adapter_runner(kind: str) -> AdapterRunner:
    adapter = _BUILTIN.get(kind) or load_user_adapters().get(kind)
    if adapter is None:
        raise AdapterError(f"no agent kind '{kind}'; the kinds are: {', '.join(known_kinds())}")
    return AdapterRunner(adapter, os.environ.get(f"COUNCIL_{kind.upper()}_MODEL") or None)
