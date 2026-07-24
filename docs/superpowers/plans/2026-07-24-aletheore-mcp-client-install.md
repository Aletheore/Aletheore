# MCP Client Config Install Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the real zero-friction-install gap in the local MCP server - today a user has to hand-write their coding tool's MCP config to ever get `aletheore mcp` running automatically. `aletheore mcp-install` writes it for every tool where that's actually safe to script, and gives clear manual guidance for the rest.

**Architecture:** A new `mcp-install` CLI command writes the project-scoped MCP registration file for six coding tools across two file formats - five JSON-based (Claude Code, Cursor, VS Code, Kiro, Opencode) sharing one merge-safe JSON writer, plus one TOML-based (OpenAI Codex CLI) needing its own reader/writer since Python's stdlib can parse TOML but not emit it. Two more tool families are deliberately *not* auto-written, with the reasoning documented inline: PyCharm/JetBrains IDEs (no single stable, publicly-documented file schema to safely script against - the primary supported path is a Settings-UI dialog with an "import a Claude MCP config" button, which this command's Claude Code output feeds directly) and the vim/Neovim/Emacs/micro family (no native MCP client exists in any of them - support is entirely dependent on whichever third-party plugin a given user has installed, each with its own non-JSON config).

**Tech Stack:** Python 3.12 (package floor 3.11), `typer` (existing CLI framework), stdlib `json`/`tomllib`/`pathlib`, `tomli-w` (new dependency, TOML *writing* - stdlib `tomllib` only reads).

## Global Constraints

- Never overwrite an existing config file wholesale - always parse, merge the `"aletheore"` entry into the appropriate table/key, and write the whole (merged) structure back. Every other server entry already in that file must survive untouched.
- If an existing file isn't valid JSON/TOML, or its relevant section isn't itself an object/table, skip that one target with a clear message rather than crashing or corrupting it. A per-target skip must never fail the whole command - other targets still get written.
- The registered command always uses an absolute, resolved repository path (`aletheore mcp <abs-path>`) - never a relative `.` - because config files get committed and opened from different working directories by the host tool.
- Every config format and file location in this plan was verified against that tool's current published documentation before being written into a task - not assumed from memory. Where verification came back genuinely ambiguous or unconfirmed (PyCharm's `.idea/mcp.json`), the plan does not auto-write it and says so explicitly, rather than guessing at a schema that could produce an invalid file.
- Writing to `.codex/config.toml` reformats the whole file (comments are not preserved - `tomllib`/`tomli-w` round-trip data only, not formatting). This is a real, unavoidable limitation of the stdlib-plus-`tomli-w` approach and must be stated in both the console output and the README, not hidden.

---

### Task 1: JSON config-writing core (5 targets)

**Files:**
- Modify: `prototype/aletheore/cli.py`
- Test: `prototype/tests/test_cli.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `_MCP_CLIENT_CONFIGS: dict[str, tuple[str, str, Callable[[Path], dict]]]` (target name -> (relative config path, top-level key, entry-builder function)), `_stdio_entry(repo_path: Path, include_type: bool) -> dict`, `_opencode_entry(repo_path: Path) -> dict`, `_write_json_mcp_client_config(config_path: Path, top_level_key: str, entry: dict) -> str` (returns a human-readable status message) - all consumed by Task 2's `_mcp_install`.

- [ ] **Step 1: Write the failing tests**

Add to `prototype/tests/test_cli.py`, near the existing `init` tests:

```python
from aletheore.cli import _MCP_CLIENT_CONFIGS, _opencode_entry, _stdio_entry, _write_json_mcp_client_config


def test_mcp_client_configs_cover_the_five_json_targets():
    assert set(_MCP_CLIENT_CONFIGS.keys()) == {"claude-code", "cursor", "vscode", "kiro", "opencode"}


def test_stdio_entry_includes_type_only_when_asked():
    entry_with_type = _stdio_entry(Path("/repo"), include_type=True)
    entry_without_type = _stdio_entry(Path("/repo"), include_type=False)

    assert entry_with_type == {"type": "stdio", "command": "aletheore", "args": ["mcp", "/repo"]}
    assert entry_without_type == {"command": "aletheore", "args": ["mcp", "/repo"]}


def test_opencode_entry_uses_single_command_array_not_command_plus_args():
    entry = _opencode_entry(Path("/repo"))

    assert entry == {"type": "local", "command": ["aletheore", "mcp", "/repo"], "enabled": True}


def test_write_json_mcp_client_config_creates_new_file(tmp_path):
    config_path = tmp_path / ".mcp.json"
    entry = {"command": "aletheore", "args": ["mcp", str(tmp_path)]}

    message = _write_json_mcp_client_config(config_path, "mcpServers", entry)

    assert "wrote" in message
    assert json.loads(config_path.read_text()) == {"mcpServers": {"aletheore": entry}}


def test_write_json_mcp_client_config_creates_parent_directories(tmp_path):
    config_path = tmp_path / ".vscode" / "mcp.json"
    entry = {"type": "stdio", "command": "aletheore", "args": ["mcp", str(tmp_path)]}

    _write_json_mcp_client_config(config_path, "servers", entry)

    assert json.loads(config_path.read_text()) == {"servers": {"aletheore": entry}}


def test_write_json_mcp_client_config_preserves_other_servers(tmp_path):
    config_path = tmp_path / ".mcp.json"
    config_path.write_text(
        json.dumps({"mcpServers": {"other-tool": {"command": "npx", "args": ["-y", "other"]}}})
    )
    entry = {"command": "aletheore", "args": ["mcp", str(tmp_path)]}

    _write_json_mcp_client_config(config_path, "mcpServers", entry)

    data = json.loads(config_path.read_text())
    assert data["mcpServers"]["other-tool"] == {"command": "npx", "args": ["-y", "other"]}
    assert data["mcpServers"]["aletheore"] == entry


def test_write_json_mcp_client_config_updates_existing_aletheore_entry(tmp_path):
    config_path = tmp_path / ".mcp.json"
    config_path.write_text(json.dumps({"mcpServers": {"aletheore": {"command": "aletheore", "args": ["mcp", "/old"]}}}))
    new_entry = {"command": "aletheore", "args": ["mcp", str(tmp_path)]}

    message = _write_json_mcp_client_config(config_path, "mcpServers", new_entry)

    assert "updated" in message
    data = json.loads(config_path.read_text())
    assert data["mcpServers"]["aletheore"] == new_entry
    assert len(data["mcpServers"]) == 1


def test_write_json_mcp_client_config_skips_invalid_json_without_crashing(tmp_path):
    config_path = tmp_path / ".mcp.json"
    config_path.write_text("{not valid json")

    message = _write_json_mcp_client_config(
        config_path, "mcpServers", {"command": "aletheore", "args": ["mcp", str(tmp_path)]}
    )

    assert "skipped" in message
    assert config_path.read_text() == "{not valid json"


def test_write_json_mcp_client_config_skips_when_top_level_key_is_not_an_object(tmp_path):
    config_path = tmp_path / ".mcp.json"
    config_path.write_text(json.dumps({"mcpServers": "not-an-object"}))

    message = _write_json_mcp_client_config(
        config_path, "mcpServers", {"command": "aletheore", "args": ["mcp", str(tmp_path)]}
    )

    assert "skipped" in message
    assert json.loads(config_path.read_text()) == {"mcpServers": "not-an-object"}
```

Confirm `import json` and `from pathlib import Path` are already imported at the top of `prototype/tests/test_cli.py` (they are, used by the existing `init` tests).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd prototype && python -m pytest tests/test_cli.py -v -k "mcp_client_config or stdio_entry or opencode_entry or write_json_mcp"`
Expected: FAIL with `ImportError: cannot import name '_MCP_CLIENT_CONFIGS'`.

- [ ] **Step 3: Implement the core functions**

Add `from collections.abc import Callable` to the imports at the top of `prototype/aletheore/cli.py` if not already present (check first - this file already has substantial imports).

Add to `prototype/aletheore/cli.py`, right before the `def init(` function:

```python
def _stdio_entry(repo_path: Path, include_type: bool) -> dict:
    entry: dict = {"command": "aletheore", "args": ["mcp", str(repo_path)]}
    if include_type:
        entry = {"type": "stdio", **entry}
    return entry


def _opencode_entry(repo_path: Path) -> dict:
    # Opencode's schema folds the command and its arguments into one array
    # rather than separate "command"/"args" fields - a real, verified
    # difference from the other four JSON targets, not an oversight.
    return {"type": "local", "command": ["aletheore", "mcp", str(repo_path)], "enabled": True}


_MCP_CLIENT_CONFIGS: dict[str, tuple[str, str, Callable[[Path], dict]]] = {
    "claude-code": (".mcp.json", "mcpServers", lambda p: _stdio_entry(p, include_type=True)),
    "cursor": (".cursor/mcp.json", "mcpServers", lambda p: _stdio_entry(p, include_type=False)),
    "vscode": (".vscode/mcp.json", "servers", lambda p: _stdio_entry(p, include_type=True)),
    "kiro": (".kiro/settings/mcp.json", "mcpServers", lambda p: _stdio_entry(p, include_type=False)),
    "opencode": ("opencode.json", "mcp", _opencode_entry),
}


def _write_json_mcp_client_config(config_path: Path, top_level_key: str, entry: dict) -> str:
    if config_path.exists():
        try:
            data = json.loads(config_path.read_text())
        except json.JSONDecodeError:
            return f"skipped (existing file is not valid JSON): {config_path}"
        if not isinstance(data, dict):
            return f"skipped (existing file's top level is not a JSON object): {config_path}"
    else:
        data = {}

    servers = data.get(top_level_key, {})
    if not isinstance(servers, dict):
        return f"skipped (existing '{top_level_key}' is not a JSON object): {config_path}"

    already_present = "aletheore" in servers
    servers["aletheore"] = entry
    data[top_level_key] = servers

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(data, indent=2) + "\n")
    return f"{'updated' if already_present else 'wrote'} {config_path}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd prototype && python -m pytest tests/test_cli.py -v -k "mcp_client_config or stdio_entry or opencode_entry or write_json_mcp"`
Expected: all PASS.

- [ ] **Step 5: Run the full prototype suite to check for regressions**

Run: `cd prototype && python -m pytest -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add prototype/aletheore/cli.py prototype/tests/test_cli.py
git commit -m "feat: add merge-safe JSON MCP client config writer for 5 targets"
```

---

### Task 2: `aletheore mcp-install` command (JSON targets + non-scriptable guidance)

**Files:**
- Modify: `prototype/aletheore/cli.py`
- Modify: `prototype/README.md`
- Test: `prototype/tests/test_cli.py`

**Interfaces:**
- Consumes: `_MCP_CLIENT_CONFIGS`, `_write_json_mcp_client_config` from Task 1.
- Produces: `aletheore mcp-install [path] [--target ...]` CLI command, printing guidance for PyCharm and terminal editors alongside the file writes. Task 3 extends this same command with a 6th (TOML) target.

- [ ] **Step 1: Write the failing tests**

Add to `prototype/tests/test_cli.py`:

```python
def test_mcp_install_writes_all_json_targets_by_default(tmp_path):
    result = runner.invoke(app, ["mcp-install", str(tmp_path)])

    assert result.exit_code == 0
    assert (tmp_path / ".mcp.json").exists()
    assert (tmp_path / ".cursor" / "mcp.json").exists()
    assert (tmp_path / ".vscode" / "mcp.json").exists()
    assert (tmp_path / ".kiro" / "settings" / "mcp.json").exists()
    assert (tmp_path / "opencode.json").exists()


def test_mcp_install_respects_target_flag(tmp_path):
    result = runner.invoke(app, ["mcp-install", str(tmp_path), "--target", "cursor"])

    assert result.exit_code == 0
    assert (tmp_path / ".cursor" / "mcp.json").exists()
    assert not (tmp_path / ".mcp.json").exists()
    assert not (tmp_path / "opencode.json").exists()


def test_mcp_install_accepts_multiple_target_flags(tmp_path):
    result = runner.invoke(
        app, ["mcp-install", str(tmp_path), "--target", "cursor", "--target", "opencode"]
    )

    assert result.exit_code == 0
    assert (tmp_path / ".cursor" / "mcp.json").exists()
    assert (tmp_path / "opencode.json").exists()
    assert not (tmp_path / ".mcp.json").exists()


def test_mcp_install_rejects_unknown_target(tmp_path):
    result = runner.invoke(app, ["mcp-install", str(tmp_path), "--target", "notatool"])

    assert result.exit_code == 1
    assert "notatool" in result.stdout


def test_mcp_install_written_entry_points_at_the_resolved_repo_path(tmp_path):
    result = runner.invoke(app, ["mcp-install", str(tmp_path), "--target", "claude-code"])

    assert result.exit_code == 0
    entry = json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]["aletheore"]
    assert entry["args"] == ["mcp", str(tmp_path.resolve())]


def test_mcp_install_opencode_entry_uses_command_array(tmp_path):
    runner.invoke(app, ["mcp-install", str(tmp_path), "--target", "opencode"])

    entry = json.loads((tmp_path / "opencode.json").read_text())["mcp"]["aletheore"]
    assert entry["command"] == ["aletheore", "mcp", str(tmp_path.resolve())]
    assert "args" not in entry


def test_mcp_install_is_idempotent_and_does_not_duplicate_entries(tmp_path):
    runner.invoke(app, ["mcp-install", str(tmp_path), "--target", "cursor"])
    result = runner.invoke(app, ["mcp-install", str(tmp_path), "--target", "cursor"])

    assert result.exit_code == 0
    data = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
    assert list(data["mcpServers"].keys()) == ["aletheore"]


def test_mcp_install_preserves_other_servers_already_in_the_file(tmp_path):
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "mcp.json").write_text(
        json.dumps({"mcpServers": {"other-tool": {"command": "npx", "args": ["-y", "other"]}}})
    )

    result = runner.invoke(app, ["mcp-install", str(tmp_path), "--target", "cursor"])

    assert result.exit_code == 0
    data = json.loads((cursor_dir / "mcp.json").read_text())
    assert "other-tool" in data["mcpServers"]
    assert "aletheore" in data["mcpServers"]


def test_mcp_install_prints_pycharm_and_terminal_editor_guidance(tmp_path):
    result = runner.invoke(app, ["mcp-install", str(tmp_path)])

    assert "PyCharm" in result.stdout
    assert "Import a Claude MCP config" in result.stdout
    assert "avante.nvim" in result.stdout or "no native MCP" in result.stdout
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd prototype && python -m pytest tests/test_cli.py -v -k mcp_install`
Expected: FAIL - `aletheore mcp-install` doesn't exist yet.

- [ ] **Step 3: Implement `_mcp_install` and the command**

Add to `prototype/aletheore/cli.py`, right after `_write_json_mcp_client_config`:

```python
def _mcp_install(path: str, targets: list[str]) -> int:
    repo_path = Path(path).resolve()
    all_targets = list(_MCP_CLIENT_CONFIGS.keys())
    selected = targets or all_targets
    unknown = [t for t in selected if t not in _MCP_CLIENT_CONFIGS]
    if unknown:
        console.print(
            f"[bold red]error:[/bold red] unknown target(s): {', '.join(unknown)}. "
            f"Valid targets: {', '.join(all_targets)}"
        )
        return 1

    for target in selected:
        relative_path, top_level_key, entry_builder = _MCP_CLIENT_CONFIGS[target]
        config_path = repo_path / relative_path
        entry = entry_builder(repo_path)
        message = _write_json_mcp_client_config(config_path, top_level_key, entry)
        console.print(f"[bold green]{target}[/bold green]: {message}")

    console.print(
        "\nRestart or reload your coding tool so it picks up the new MCP server - "
        "Aletheore's tools will then be available without running 'aletheore mcp' yourself."
    )
    console.print(
        "\n[bold]PyCharm / other JetBrains IDEs:[/bold] not auto-configured - there's no single "
        "stable, documented file format to script against yet. Instead: open Settings | Tools | "
        "AI Assistant | Model Context Protocol, and use \"Import a Claude MCP config\", pointing "
        "at the .mcp.json written above."
    )
    console.print(
        "[bold]vim / Neovim / Emacs / other terminal editors:[/bold] no native MCP client exists "
        "in any of them - support depends entirely on whichever AI plugin you have installed "
        "(e.g. avante.nvim, codecompanion.nvim). Point that plugin's own MCP config at: "
        f"aletheore mcp {repo_path}"
    )
    return 0


@app.command(name="mcp-install", help="write MCP client config so a coding agent auto-launches this repo's MCP server")
def mcp_install(
    path: str = typer.Argument(".", help="repository path"),
    target: list[str] = typer.Option(
        [],
        "--target",
        help="which client(s) to configure (default: all). See --help for the full list.",
    ),
) -> None:
    raise typer.Exit(code=_mcp_install(path, target))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd prototype && python -m pytest tests/test_cli.py -v -k mcp_install`
Expected: all PASS.

- [ ] **Step 5: Add `mcp-install` to the command description list**

Find the tuple list near the top of `prototype/aletheore/cli.py` that includes `("mcp", "run an MCP server so an agent can query a repo directly")`, and add right after it:

```python
    ("mcp-install", "write MCP client config for Claude Code, Cursor, VS Code, Kiro, Opencode, or Codex CLI"),
```

- [ ] **Step 6: Document the command in the README**

In `prototype/README.md`, right after the existing `### \`aletheore mcp [path]\`` section, add:

```markdown
### `aletheore mcp-install [path]`

Writes the MCP server registration into your coding tool's own config, so it launches
`aletheore mcp` for you automatically instead of you running it by hand. By default writes for
every scriptable target: Claude Code (`.mcp.json`), Cursor (`.cursor/mcp.json`), VS Code
(`.vscode/mcp.json`), Kiro (`.kiro/settings/mcp.json`), Opencode (`opencode.json`), and OpenAI
Codex CLI (`.codex/config.toml`). Use `--target` to restrict to one or more
(e.g. `--target cursor --target vscode`). Safe to re-run - it merges into any existing config
file rather than overwriting it, so other MCP servers you've already configured are left alone.

```bash
aletheore mcp-install .
```

**PyCharm / other JetBrains IDEs:** not auto-configured. There's no single stable, publicly
documented file format to safely script against - the supported path is Settings | Tools | AI
Assistant | Model Context Protocol, using "Import a Claude MCP config" against the `.mcp.json`
this command already wrote.

**vim, Neovim, Emacs, and other terminal editors:** none of these have a native MCP client -
support depends entirely on whichever AI plugin you've installed (e.g. `avante.nvim`,
`codecompanion.nvim`), each with its own config. Point that plugin at `aletheore mcp <path>`.
```

- [ ] **Step 7: Run the full prototype suite to check for regressions**

Run: `cd prototype && python -m pytest -q`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add prototype/aletheore/cli.py prototype/README.md prototype/tests/test_cli.py
git commit -m "feat: add aletheore mcp-install for 5 JSON-based MCP clients"
```

---

### Task 3: OpenAI Codex CLI support (TOML, 6th target)

**Files:**
- Modify: `prototype/pyproject.toml` (new `tomli-w` dependency)
- Modify: `prototype/aletheore/cli.py`
- Modify: `prototype/README.md`
- Test: `prototype/tests/test_cli.py`

**Interfaces:**
- Consumes: nothing new from other tasks (parallels Task 1's JSON writer, but for TOML).
- Produces: `_write_toml_mcp_client_config(config_path: Path, top_level_key: str, entry: dict) -> str`. Registers `"codex-cli"` as a 6th entry read by `_mcp_install`'s existing loop.

Codex CLI's format is confirmed from its own docs: `~/.codex/config.toml` (user-level) or a project-scoped `.codex/config.toml` (Codex only trusts project-scoped config for projects it considers "trusted" - a gate this command cannot control, only the file it writes). Server entries are TOML tables: `[mcp_servers.<name>]` with `command` (string) and `args` (array of strings) - the same shape as the JSON targets' `command`+`args`, just serialized differently.

- [ ] **Step 1: Add the `tomli-w` dependency**

In `prototype/pyproject.toml`, add to the `dependencies` list (alphabetically, after `"rich>=13.0"` or wherever it sorts - this file's list isn't strictly alphabetical, so just add it at the end):

```
    "tomli-w>=1.0,<2.0",
```

Run: `cd prototype && pip install -e .`
Expected: installs cleanly, `tomli_w` importable.

- [ ] **Step 2: Write the failing tests**

Add to `prototype/tests/test_cli.py`:

```python
import tomllib

from aletheore.cli import _write_toml_mcp_client_config


def test_write_toml_mcp_client_config_creates_new_file(tmp_path):
    config_path = tmp_path / ".codex" / "config.toml"
    entry = {"command": "aletheore", "args": ["mcp", str(tmp_path)]}

    message = _write_toml_mcp_client_config(config_path, "mcp_servers", entry)

    assert "wrote" in message
    data = tomllib.loads(config_path.read_text())
    assert data == {"mcp_servers": {"aletheore": entry}}


def test_write_toml_mcp_client_config_preserves_other_servers(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text('[mcp_servers.other-tool]\ncommand = "uvx"\nargs = ["other"]\n')
    entry = {"command": "aletheore", "args": ["mcp", str(tmp_path)]}

    _write_toml_mcp_client_config(config_path, "mcp_servers", entry)

    data = tomllib.loads(config_path.read_text())
    assert data["mcp_servers"]["other-tool"] == {"command": "uvx", "args": ["other"]}
    assert data["mcp_servers"]["aletheore"] == entry


def test_write_toml_mcp_client_config_updates_existing_aletheore_entry(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text('[mcp_servers.aletheore]\ncommand = "aletheore"\nargs = ["mcp", "/old"]\n')
    new_entry = {"command": "aletheore", "args": ["mcp", str(tmp_path)]}

    message = _write_toml_mcp_client_config(config_path, "mcp_servers", new_entry)

    assert "updated" in message
    data = tomllib.loads(config_path.read_text())
    assert data["mcp_servers"]["aletheore"] == new_entry
    assert len(data["mcp_servers"]) == 1


def test_write_toml_mcp_client_config_skips_invalid_toml_without_crashing(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text("not [ valid toml")

    message = _write_toml_mcp_client_config(
        config_path, "mcp_servers", {"command": "aletheore", "args": ["mcp", str(tmp_path)]}
    )

    assert "skipped" in message
    assert config_path.read_text() == "not [ valid toml"


def test_mcp_install_writes_codex_cli_target(tmp_path):
    result = runner.invoke(app, ["mcp-install", str(tmp_path), "--target", "codex-cli"])

    assert result.exit_code == 0
    config_path = tmp_path / ".codex" / "config.toml"
    assert config_path.exists()
    entry = tomllib.loads(config_path.read_text())["mcp_servers"]["aletheore"]
    assert entry == {"command": "aletheore", "args": ["mcp", str(tmp_path.resolve())]}


def test_mcp_install_default_now_includes_codex_cli(tmp_path):
    result = runner.invoke(app, ["mcp-install", str(tmp_path)])

    assert result.exit_code == 0
    assert (tmp_path / ".codex" / "config.toml").exists()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd prototype && python -m pytest tests/test_cli.py -v -k "toml_mcp or codex_cli"`
Expected: FAIL - `ImportError: cannot import name '_write_toml_mcp_client_config'`, and `codex-cli` isn't a recognized target yet.

- [ ] **Step 4: Implement TOML support and register the target**

Add `import tomllib` and `import tomli_w` to the imports at the top of `prototype/aletheore/cli.py`.

Add right after `_write_json_mcp_client_config`:

```python
def _write_toml_mcp_client_config(config_path: Path, top_level_key: str, entry: dict) -> str:
    if config_path.exists():
        try:
            data = tomllib.loads(config_path.read_text())
        except tomllib.TOMLDecodeError:
            return f"skipped (existing file is not valid TOML): {config_path}"
        if not isinstance(data, dict):
            return f"skipped (existing file's top level is not a TOML table): {config_path}"
    else:
        data = {}

    servers = data.get(top_level_key, {})
    if not isinstance(servers, dict):
        return f"skipped (existing '{top_level_key}' is not a TOML table): {config_path}"

    already_present = "aletheore" in servers
    servers["aletheore"] = entry
    data[top_level_key] = servers

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(tomli_w.dumps(data))
    return f"{'updated' if already_present else 'wrote'} {config_path}"
```

Change `_mcp_install`'s loop from:

```python
    for target in selected:
        relative_path, top_level_key, entry_builder = _MCP_CLIENT_CONFIGS[target]
        config_path = repo_path / relative_path
        entry = entry_builder(repo_path)
        message = _write_json_mcp_client_config(config_path, top_level_key, entry)
        console.print(f"[bold green]{target}[/bold green]: {message}")
```

to:

```python
    for target in selected:
        if target == "codex-cli":
            config_path = repo_path / ".codex" / "config.toml"
            entry = {"command": "aletheore", "args": ["mcp", str(repo_path)]}
            message = _write_toml_mcp_client_config(config_path, "mcp_servers", entry)
        else:
            relative_path, top_level_key, entry_builder = _MCP_CLIENT_CONFIGS[target]
            config_path = repo_path / relative_path
            entry = entry_builder(repo_path)
            message = _write_json_mcp_client_config(config_path, top_level_key, entry)
        console.print(f"[bold green]{target}[/bold green]: {message}")
```

And change the target-validation and default-list logic from:

```python
    repo_path = Path(path).resolve()
    all_targets = list(_MCP_CLIENT_CONFIGS.keys())
    selected = targets or all_targets
    unknown = [t for t in selected if t not in _MCP_CLIENT_CONFIGS]
```

to:

```python
    repo_path = Path(path).resolve()
    all_targets = [*_MCP_CLIENT_CONFIGS.keys(), "codex-cli"]
    selected = targets or all_targets
    unknown = [t for t in selected if t not in all_targets]
```

Add a note about the "trusted projects" caveat to the guidance block printed at the end of `_mcp_install` - right after the existing `vim / Neovim / Emacs` `console.print`, add:

```python
    console.print(
        "[bold]OpenAI Codex CLI:[/bold] wrote .codex/config.toml, but Codex only reads "
        "project-scoped MCP config for projects it already trusts - if the tools don't show up, "
        "check Codex's own trust prompt for this directory. Also note: writing this file "
        "reformats it - any hand-written comments in an existing config.toml are not preserved."
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd prototype && python -m pytest tests/test_cli.py -v`
Expected: all PASS - including every earlier `mcp_install`/JSON test from Tasks 1-2, since `codex-cli` is additive and every prior test either targets specific tools explicitly or already expects "all" to include whatever the default list currently contains at test-run time.

- [ ] **Step 6: Update the README**

In `prototype/README.md`, the `aletheore mcp-install` section written in Task 2 already lists `.codex/config.toml` as one of the default targets - add one more paragraph right after the "vim, Neovim, Emacs" paragraph:

```markdown
**OpenAI Codex CLI:** writes `.codex/config.toml`, but Codex only reads project-scoped MCP
config for projects it already trusts - check Codex's own trust prompt if the tools don't
appear. Writing this file reformats it; hand-written comments in an existing `config.toml` are
not preserved.
```

- [ ] **Step 7: Run the full prototype suite to check for regressions**

Run: `cd prototype && python -m pytest -q`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add prototype/pyproject.toml prototype/aletheore/cli.py prototype/README.md prototype/tests/test_cli.py
git commit -m "feat: add OpenAI Codex CLI (TOML) as a 6th mcp-install target"
```

---

## Self-Review

**Spec coverage:**
- Claude Code, Cursor, VS Code, Kiro (original 4, verified formats) → Task 1/2. ✅
- Opencode (verified: `"mcp"` key, command-as-array, `"type": "local"`) → Task 1/2. ✅
- OpenAI Codex CLI (verified: TOML, `.codex/config.toml`, `[mcp_servers.name]`) → Task 3, with the "trusted projects" and comment-loss caveats surfaced in both console output and README rather than hidden. ✅
- PyCharm/JetBrains → deliberately not auto-written, with the reasoning (no confirmed stable schema) stated in Global Constraints and surfaced to the user as actionable guidance (import the Claude Code file) rather than silence. ✅
- vim/Neovim/Emacs/micro family → deliberately not auto-written (no native MCP client in any of them), with plugin-pointer guidance instead. ✅

**Placeholder scan:** No "TBD"/"TODO" in any task; every step shows complete code.

**Type consistency:** `_MCP_CLIENT_CONFIGS: dict[str, tuple[str, str, Callable[[Path], dict]]]` (Task 1) is unpacked identically in `_mcp_install` (Task 2, then modified consistently in Task 3's loop change) via `relative_path, top_level_key, entry_builder = _MCP_CLIENT_CONFIGS[target]`. `_write_json_mcp_client_config` and `_write_toml_mcp_client_config` share an identical signature `(config_path: Path, top_level_key: str, entry: dict) -> str` and identical merge semantics, differing only in their parse/serialize calls - verified by writing Task 3's function as a structural mirror of Task 1's rather than inventing new logic.

**Scope check:** Three tasks, each independently shippable - Task 1 is pure functions with no CLI surface yet; Task 2 adds the command for the 5 JSON targets; Task 3 is purely additive (one more target, one new file-format branch) and doesn't modify Task 2's JSON path at all. This plan still does not cover a hosted/remote MCP endpoint for the GitHub App's paid tiers - that remains a separate, larger, not-yet-designed feature direction, flagged previously and intentionally excluded here.
