# MCP Client Config Install Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the real zero-friction-install gap in the local MCP server - today a user has to hand-write their coding tool's MCP config to ever get `aletheore mcp` running automatically. `aletheore mcp-install` writes it for them.

**Architecture:** A new `mcp-install` CLI command writes the project-scoped MCP registration file for one or more coding tools (Claude Code, Cursor, VS Code, Kiro), each pointing at `aletheore mcp <absolute-repo-path>`. Config formats differ per tool (`mcpServers` vs `servers` top-level key, `"type": "stdio"` required or not) - these were verified against each tool's current published documentation, not assumed. Writing merges into any existing file rather than overwriting it, so a user's other configured MCP servers are never destroyed.

**Tech Stack:** Python 3.12, `typer` (already the CLI framework), stdlib `json`/`pathlib` only - no new dependency.

## Global Constraints

- Never overwrite an existing config file wholesale - always parse, merge the `"aletheore"` entry into the appropriate top-level key, and write the whole (merged) object back. Every other server entry in that file must survive untouched.
- If an existing file isn't valid JSON, or its top-level shape doesn't match what's expected (not an object, or the `mcpServers`/`servers` key exists but isn't itself an object), skip that one target with a clear message rather than crashing or corrupting it. A per-target skip must not fail the whole command - other targets still get written.
- The registered command always uses an absolute, resolved repository path in `args` (`aletheore mcp <abs-path>`) - never a relative `.` - because the config file gets committed and opened from different working directories by the host tool, and stdio server startup should not depend on guessing the host's CWD.
- Config formats are per verified documentation, not memory: Claude Code and VS Code use `"type": "stdio"` in their example configs; Cursor and Kiro's documented examples omit it. Match that exactly per target - do not add or remove it uniformly across all four.

---

### Task 1: Config-writing core (pure functions)

**Files:**
- Modify: `prototype/aletheore/cli.py`
- Test: `prototype/tests/test_cli.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `_MCP_CLIENT_CONFIGS: dict[str, tuple[str, str, bool]]` (target name -> (relative config path, top-level key, whether to include `"type": "stdio"`)), `_server_entry(repo_path: Path, include_type: bool) -> dict`, `_write_mcp_client_config(config_path: Path, top_level_key: str, entry: dict) -> str` (returns a human-readable status message) - all consumed by Task 2's `_mcp_install`.

- [ ] **Step 1: Write the failing tests**

Add to `prototype/tests/test_cli.py`, near the existing `init` tests:

```python
from aletheore.cli import _MCP_CLIENT_CONFIGS, _server_entry, _write_mcp_client_config


def test_mcp_client_configs_cover_the_four_known_targets():
    assert set(_MCP_CLIENT_CONFIGS.keys()) == {"claude-code", "cursor", "vscode", "kiro"}


def test_server_entry_includes_type_stdio_only_where_documented():
    entry_with_type = _server_entry(Path("/repo"), include_type=True)
    entry_without_type = _server_entry(Path("/repo"), include_type=False)

    assert entry_with_type == {"type": "stdio", "command": "aletheore", "args": ["mcp", "/repo"]}
    assert entry_without_type == {"command": "aletheore", "args": ["mcp", "/repo"]}


def test_write_mcp_client_config_creates_new_file(tmp_path):
    config_path = tmp_path / ".mcp.json"
    entry = {"command": "aletheore", "args": ["mcp", str(tmp_path)]}

    message = _write_mcp_client_config(config_path, "mcpServers", entry)

    assert "wrote" in message
    data = json.loads(config_path.read_text())
    assert data == {"mcpServers": {"aletheore": entry}}


def test_write_mcp_client_config_creates_parent_directories(tmp_path):
    config_path = tmp_path / ".vscode" / "mcp.json"
    entry = {"type": "stdio", "command": "aletheore", "args": ["mcp", str(tmp_path)]}

    _write_mcp_client_config(config_path, "servers", entry)

    assert config_path.exists()
    assert json.loads(config_path.read_text()) == {"servers": {"aletheore": entry}}


def test_write_mcp_client_config_preserves_other_servers(tmp_path):
    config_path = tmp_path / ".mcp.json"
    config_path.write_text(
        json.dumps({"mcpServers": {"other-tool": {"command": "npx", "args": ["-y", "other"]}}})
    )
    entry = {"command": "aletheore", "args": ["mcp", str(tmp_path)]}

    _write_mcp_client_config(config_path, "mcpServers", entry)

    data = json.loads(config_path.read_text())
    assert data["mcpServers"]["other-tool"] == {"command": "npx", "args": ["-y", "other"]}
    assert data["mcpServers"]["aletheore"] == entry


def test_write_mcp_client_config_updates_existing_aletheore_entry(tmp_path):
    config_path = tmp_path / ".mcp.json"
    old_entry = {"command": "aletheore", "args": ["mcp", "/old/path"]}
    config_path.write_text(json.dumps({"mcpServers": {"aletheore": old_entry}}))
    new_entry = {"command": "aletheore", "args": ["mcp", str(tmp_path)]}

    message = _write_mcp_client_config(config_path, "mcpServers", new_entry)

    assert "updated" in message
    data = json.loads(config_path.read_text())
    assert data["mcpServers"]["aletheore"] == new_entry
    assert len(data["mcpServers"]) == 1


def test_write_mcp_client_config_skips_invalid_json_without_crashing(tmp_path):
    config_path = tmp_path / ".mcp.json"
    config_path.write_text("{not valid json")

    message = _write_mcp_client_config(
        config_path, "mcpServers", {"command": "aletheore", "args": ["mcp", str(tmp_path)]}
    )

    assert "skipped" in message
    assert config_path.read_text() == "{not valid json"


def test_write_mcp_client_config_skips_when_top_level_key_is_not_an_object(tmp_path):
    config_path = tmp_path / ".mcp.json"
    config_path.write_text(json.dumps({"mcpServers": "not-an-object"}))

    message = _write_mcp_client_config(
        config_path, "mcpServers", {"command": "aletheore", "args": ["mcp", str(tmp_path)]}
    )

    assert "skipped" in message
    assert json.loads(config_path.read_text()) == {"mcpServers": "not-an-object"}
```

Confirm `import json` and `from pathlib import Path` are already imported at the top of `prototype/tests/test_cli.py` (they are, used by the existing `init` tests) - no new imports needed there beyond the new `from aletheore.cli import ...` line above.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd prototype && python -m pytest tests/test_cli.py -v -k "mcp_client_config or server_entry or write_mcp"`
Expected: FAIL with `ImportError: cannot import name '_MCP_CLIENT_CONFIGS'`.

- [ ] **Step 3: Implement the core functions**

Add to `prototype/aletheore/cli.py`, right before the `def init(` function:

```python
_MCP_CLIENT_CONFIGS: dict[str, tuple[str, str, bool]] = {
    "claude-code": (".mcp.json", "mcpServers", True),
    "cursor": (".cursor/mcp.json", "mcpServers", False),
    "vscode": (".vscode/mcp.json", "servers", True),
    "kiro": (".kiro/settings/mcp.json", "mcpServers", False),
}


def _server_entry(repo_path: Path, include_type: bool) -> dict:
    entry: dict = {"command": "aletheore", "args": ["mcp", str(repo_path)]}
    if include_type:
        entry = {"type": "stdio", **entry}
    return entry


def _write_mcp_client_config(config_path: Path, top_level_key: str, entry: dict) -> str:
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

Run: `cd prototype && python -m pytest tests/test_cli.py -v -k "mcp_client_config or server_entry or write_mcp"`
Expected: all PASS.

- [ ] **Step 5: Run the full prototype suite to check for regressions**

Run: `cd prototype && python -m pytest -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add prototype/aletheore/cli.py prototype/tests/test_cli.py
git commit -m "feat: add merge-safe MCP client config writer functions"
```

---

### Task 2: `aletheore mcp-install` command

**Files:**
- Modify: `prototype/aletheore/cli.py`
- Modify: `prototype/README.md`
- Test: `prototype/tests/test_cli.py`

**Interfaces:**
- Consumes: `_MCP_CLIENT_CONFIGS`, `_server_entry`, `_write_mcp_client_config` from Task 1.
- Produces: `aletheore mcp-install [path] [--target claude-code|cursor|vscode|kiro ...]` CLI command. Nothing else depends on this.

- [ ] **Step 1: Write the failing tests**

Add to `prototype/tests/test_cli.py`:

```python
def test_mcp_install_writes_all_four_targets_by_default(tmp_path):
    result = runner.invoke(app, ["mcp-install", str(tmp_path)])

    assert result.exit_code == 0
    assert (tmp_path / ".mcp.json").exists()
    assert (tmp_path / ".cursor" / "mcp.json").exists()
    assert (tmp_path / ".vscode" / "mcp.json").exists()
    assert (tmp_path / ".kiro" / "settings" / "mcp.json").exists()


def test_mcp_install_respects_target_flag(tmp_path):
    result = runner.invoke(app, ["mcp-install", str(tmp_path), "--target", "cursor"])

    assert result.exit_code == 0
    assert (tmp_path / ".cursor" / "mcp.json").exists()
    assert not (tmp_path / ".mcp.json").exists()
    assert not (tmp_path / ".vscode" / "mcp.json").exists()
    assert not (tmp_path / ".kiro" / "settings" / "mcp.json").exists()


def test_mcp_install_accepts_multiple_target_flags(tmp_path):
    result = runner.invoke(
        app, ["mcp-install", str(tmp_path), "--target", "cursor", "--target", "vscode"]
    )

    assert result.exit_code == 0
    assert (tmp_path / ".cursor" / "mcp.json").exists()
    assert (tmp_path / ".vscode" / "mcp.json").exists()
    assert not (tmp_path / ".mcp.json").exists()


def test_mcp_install_rejects_unknown_target(tmp_path):
    result = runner.invoke(app, ["mcp-install", str(tmp_path), "--target", "notatool"])

    assert result.exit_code == 1
    assert "notatool" in result.stdout


def test_mcp_install_written_entry_points_at_the_resolved_repo_path(tmp_path):
    result = runner.invoke(app, ["mcp-install", str(tmp_path), "--target", "claude-code"])

    assert result.exit_code == 0
    data = json.loads((tmp_path / ".mcp.json").read_text())
    entry = data["mcpServers"]["aletheore"]
    assert entry["command"] == "aletheore"
    assert entry["args"] == ["mcp", str(tmp_path.resolve())]
    assert entry["type"] == "stdio"


def test_mcp_install_vscode_entry_has_type_stdio_cursor_does_not(tmp_path):
    runner.invoke(app, ["mcp-install", str(tmp_path)])

    vscode_entry = json.loads((tmp_path / ".vscode" / "mcp.json").read_text())["servers"]["aletheore"]
    cursor_entry = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())["mcpServers"]["aletheore"]

    assert vscode_entry["type"] == "stdio"
    assert "type" not in cursor_entry


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd prototype && python -m pytest tests/test_cli.py -v -k mcp_install`
Expected: FAIL - `aletheore mcp-install` doesn't exist yet (Typer reports it as an unknown command, non-zero exit).

- [ ] **Step 3: Implement `_mcp_install` and the command**

Add to `prototype/aletheore/cli.py`, right after the `_write_mcp_client_config` function added in Task 1:

```python
def _mcp_install(path: str, targets: list[str]) -> int:
    repo_path = Path(path).resolve()
    selected = targets or list(_MCP_CLIENT_CONFIGS.keys())
    unknown = [t for t in selected if t not in _MCP_CLIENT_CONFIGS]
    if unknown:
        console.print(
            f"[bold red]error:[/bold red] unknown target(s): {', '.join(unknown)}. "
            f"Valid targets: {', '.join(_MCP_CLIENT_CONFIGS)}"
        )
        return 1

    for target in selected:
        relative_path, top_level_key, include_type = _MCP_CLIENT_CONFIGS[target]
        config_path = repo_path / relative_path
        entry = _server_entry(repo_path, include_type)
        message = _write_mcp_client_config(config_path, top_level_key, entry)
        console.print(f"[bold green]{target}[/bold green]: {message}")

    console.print(
        "\nRestart or reload your coding tool so it picks up the new MCP server - "
        "Aletheore's tools will then be available without running 'aletheore mcp' yourself."
    )
    return 0


@app.command(name="mcp-install", help="write MCP client config so a coding agent auto-launches this repo's MCP server")
def mcp_install(
    path: str = typer.Argument(".", help="repository path"),
    target: list[str] = typer.Option(
        [],
        "--target",
        help="which client(s) to configure: claude-code, cursor, vscode, kiro (default: all four)",
    ),
) -> None:
    raise typer.Exit(code=_mcp_install(path, target))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd prototype && python -m pytest tests/test_cli.py -v -k mcp_install`
Expected: all PASS.

- [ ] **Step 5: Add `mcp-install` to the command description list**

Find the tuple list near the top of `prototype/aletheore/cli.py` that includes `("mcp", "run an MCP server so an agent can query a repo directly")`, and add a new entry right after it:

```python
    ("mcp-install", "write MCP client config for Claude Code, Cursor, VS Code, or Kiro"),
```

- [ ] **Step 6: Document the command in the README**

In `prototype/README.md`, right after the existing `### \`aletheore mcp [path]\`` section (the one ending with the `aletheore mcp .` example), add:

```markdown
### `aletheore mcp-install [path]`

Writes the MCP server registration into your coding tool's own config, so it launches
`aletheore mcp` for you automatically instead of you running it by hand. Supports Claude Code
(`.mcp.json`), Cursor (`.cursor/mcp.json`), VS Code (`.vscode/mcp.json`), and Kiro
(`.kiro/settings/mcp.json`) - by default it writes all four; use `--target` to restrict to one
or more (e.g. `--target cursor --target vscode`). Safe to re-run: it merges into any existing
config file rather than overwriting it, so other MCP servers you've already configured are left
alone.

```bash
aletheore mcp-install .
```
```

- [ ] **Step 7: Run the full prototype suite to check for regressions**

Run: `cd prototype && python -m pytest -q`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add prototype/aletheore/cli.py prototype/README.md prototype/tests/test_cli.py
git commit -m "feat: add aletheore mcp-install for zero-friction MCP client setup"
```

---

## Self-Review

**Spec coverage:**
- Zero-friction MCP install gap (identified: no `.mcp.json`/equivalent is ever generated, users must hand-write config) → Task 2's `mcp-install` command. ✅
- Multi-client support, with real per-tool schema differences respected rather than assumed → Task 1's `_MCP_CLIENT_CONFIGS` table, sourced against each tool's current documentation before writing (Claude Code and VS Code use `"type": "stdio"` in their docs' examples; Cursor and Kiro's don't). ✅
- Never destroy a user's existing MCP config for other servers → Task 1's merge-not-overwrite logic, with dedicated tests for preservation and graceful skip on malformed/unexpected existing content. ✅

**Placeholder scan:** No "TBD"/"TODO" in any task; every step shows complete code.

**Type consistency:** `_MCP_CLIENT_CONFIGS: dict[str, tuple[str, str, bool]]` (Task 1) is iterated identically in `_mcp_install` (Task 2) via `relative_path, top_level_key, include_type = _MCP_CLIENT_CONFIGS[target]` - the tuple order matches exactly. `_write_mcp_client_config(config_path: Path, top_level_key: str, entry: dict) -> str` is called with the same three positional argument types in both its Task 1 tests and its Task 2 call site.

**Scope check:** This plan covers only the local CLI's zero-friction client registration. It does not cover a hosted/remote MCP endpoint for the GitHub App's paid tiers (a real, separate, and substantially larger gap - no route exists in `app_server` today, and it would need its own transport (streamable HTTP, not stdio), its own auth model reusing the existing bearer-token scheme, and a scoping decision about which tools make sense to expose remotely for a paid installation's evidence). That gap is deliberately not planned here - it's a new feature direction, not a fix to what already exists, and deserves its own design conversation before a plan gets written for it.
