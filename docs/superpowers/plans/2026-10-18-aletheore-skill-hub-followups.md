# Aletheore Skill Hub Follow-Ups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the two gaps explicitly deferred out of the original Skill Hub build (PR #1070): a user can delete a custom skill (locally and server-side, without it reaching already-synced machines), and can genuinely revoke a personal token — either their own current one via `logout`, or every token on the account via `logout --all-devices`.

**Architecture:** Both features are small, additive extensions of what already exists — no new tables, no new sync architecture. Deletion adds one server endpoint, one client sync function, and a small local tombstone file so a machine's own next sync doesn't resurrect what it just deleted. Revocation adds two server endpoints (revoke-self, revoke-all) and wires `logout` to call them before clearing the local credential.

**Tech Stack:** Same as the original plan — Python (`aletheore` package: Typer CLI, MCP server, `httpx`), FastAPI + asyncpg (`github-app/app_server`), pytest, real test Postgres/Redis for integration tests.

**Spec:** `docs/superpowers/specs/2026-10-18-aletheore-skill-hub-design.md`, section "Follow-up: Custom Skill Deletion + Token Revocation" (added for this plan — the rest of that document is the original, already-shipped feature; this plan only implements the new section).

## Global Constraints

- Deletion never reaches a machine that already has its own local copy from a prior sync — only this machine (immediately) and the server (so no *future* sync ever hands it out again). No "delete everywhere" capability.
- `<name>.md.bak` (from any prior overwrite-guard or sync-hardening write) is never touched by `remove` — it's an unrelated, purely local safety net.
- `pull_merge_custom_skills` must never resurrect a name that was deleted locally but whose server-side delete hadn't yet succeeded at the time of `remove` — enforced via the `.pending_deletes.json` tombstone described in the spec, flushed (retried) at the start of every `pull_merge_custom_skills` call before anything else runs.
- The login-time sync disclosure prints every login where a personal token is active — never a one-time/dismissible notice.
- `logout` (plain) revokes only the token that authenticated the call — never infers "the newest" or any other token. `logout --all-devices` revokes every token for the resolved identity.
- A failed server-side revoke call during `logout` must not block the local credential clear or make `logout` report failure — same best-effort philosophy as the rest of this feature's network calls.
- `create_personal_token_within_limit`'s rotate-oldest-on-overflow logic (already shipped) is untouched by this plan — revocation is a separate, additive path, not a replacement.
- Server-side deletion/revocation endpoints are hard operations (real `DELETE`/`revoked_at` writes), never soft no-ops, and every one is scoped by the resolved `personal_identity_id` from the bearer token — never a client-supplied id, matching every existing endpoint in this file.

## Review Focus

- `remove` called on a name that doesn't exist locally (never added, or already removed) — must give a clear result, not a crash, in both the CLI and the MCP tool. (Task 3)
- The pending-deletes tombstone surviving an interpreter crash or a corrupted/partial JSON write — `_load_sync_state`'s existing pattern (treat unreadable as empty) must be matched here too, never a hard failure that blocks `pull_merge_custom_skills` entirely. (Task 2)
- `logout --all-devices` run with no personal token saved at all — must report plainly ("not logged in, nothing to revoke") rather than silently doing nothing or crashing. (Task 6)
- Revoking a token that's already revoked (double-revoke, e.g. a retried request) — must be idempotent, not an error. (Task 5)
- A `DELETE /v1/custom-skills/{name}` or `/v1/personal-tokens` call from one identity must never be able to affect another identity's row, even by guessing a name/path — same tenant-isolation invariant as every existing endpoint. (Task 1, Task 5)

---

## Section A — Custom Skill Deletion

### Task 1: Server-side delete endpoint

**Files:**
- Modify: `github-app/app_server/db.py`
- Modify: `github-app/app_server/skill_hub_api.py`
- Test: `github-app/tests/test_skill_hub_api.py` (append)

**Interfaces:**
- Consumes: `_authenticate_personal_token` (existing, `skill_hub_api.py:86`).
- Produces: `db.delete_custom_skill(pool: asyncpg.Pool, personal_identity_id: int, skill_name: str) -> bool` (`True` if a row was deleted, `False` if no matching row existed for that identity); `DELETE /v1/custom-skills/{name}`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_delete_custom_skill_removes_it(test_app, personal_token_header):
    await test_app.put("/v1/custom-skills/my-review", headers=personal_token_header, json={"content": "x"})
    response = await test_app.delete("/v1/custom-skills/my-review", headers=personal_token_header)
    assert response.status_code == 204
    get_response = await test_app.get("/v1/custom-skills/my-review", headers=personal_token_header)
    assert get_response.status_code == 404

def test_delete_custom_skill_unknown_name_404(test_app, personal_token_header):
    response = await test_app.delete("/v1/custom-skills/never-added", headers=personal_token_header)
    assert response.status_code == 404

async def test_delete_custom_skill_scoped_to_caller_identity(test_app, personal_token_header, other_personal_token_header):
    await test_app.put("/v1/custom-skills/mine", headers=personal_token_header, json={"content": "x"})
    response = await test_app.delete("/v1/custom-skills/mine", headers=other_personal_token_header)
    assert response.status_code == 404
    # Still there for the real owner.
    get_response = await test_app.get("/v1/custom-skills/mine", headers=personal_token_header)
    assert get_response.status_code == 200

async def test_delete_custom_skill_is_idempotent(test_app, personal_token_header):
    await test_app.put("/v1/custom-skills/my-review", headers=personal_token_header, json={"content": "x"})
    await test_app.delete("/v1/custom-skills/my-review", headers=personal_token_header)
    second = await test_app.delete("/v1/custom-skills/my-review", headers=personal_token_header)
    assert second.status_code == 404  # already gone, not a 500
```

Reuse the same `test_app`/`personal_token_header`/`other_personal_token_header` fixtures Task 12's original tests already built in this file — do not invent new ones.

- [ ] **Step 2: Run tests to verify they fail**

Run (from `github-app/`, with the isolated venv): `TEST_DATABASE_URL="postgresql://postgres:test@localhost:55433/aletheore_test" TEST_REDIS_URL="redis://localhost:6379/0" PYTHONPATH=.:../src .venv-skillhub/bin/pytest tests/test_skill_hub_api.py -v -k delete_custom_skill`
Expected: FAIL

- [ ] **Step 3: Add `delete_custom_skill` to `db.py`**

```python
async def delete_custom_skill(pool: asyncpg.Pool, personal_identity_id: int, skill_name: str) -> bool:
    result = await pool.execute(
        "DELETE FROM custom_skills WHERE personal_identity_id = $1 AND skill_name = $2",
        personal_identity_id, skill_name,
    )
    return result != "DELETE 0"
```

Place it next to the other `custom_skills`-table functions (`list_custom_skills`/`get_custom_skill`/`upsert_custom_skill`), same file region.

- [ ] **Step 4: Add the route to `skill_hub_api.py`**

```python
@skill_hub_router.delete("/v1/custom-skills/{name}", status_code=204)
async def delete_custom_skill_route(request: Request, name: str):
    identity = await _authenticate_personal_token(request)
    deleted = await delete_custom_skill(request.app.state.db_pool, identity["id"], name)
    if not deleted:
        raise HTTPException(status_code=404, detail="no custom skill by that name")
```

Import `delete_custom_skill` alongside the other `db` imports at the top of the file. FastAPI returns an empty 204 body automatically for a route with no return value and `status_code=204`.

- [ ] **Step 5: Run tests to verify they pass**

Run the same command as Step 2.
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add github-app/app_server/db.py github-app/app_server/skill_hub_api.py github-app/tests/test_skill_hub_api.py
git commit -m "feat: add DELETE /v1/custom-skills/{name} endpoint"
```

---

### Task 2: Client-side delete sync + pending-deletes tombstone

**Files:**
- Modify: `src/aletheore/skill_hub_sync.py`
- Test: `src/tests/test_skill_hub_sync.py` (append)

**Interfaces:**
- Consumes: `skill_hub.custom_skills_dir`, `sanitize_skill_name` (existing); the new `DELETE /v1/custom-skills/{name}` (Task 1).
- Produces: `delete_custom_skill_best_effort(name: str, timeout: float = 5.0) -> None` (never raises; on success or a 404 — already gone server-side — clears any sync-state entry and any pending-delete entry for `name`; on any other failure, records `name` in the pending-deletes tombstone instead of raising). `pull_merge_custom_skills` (existing, modified) now flushes pending deletes first.

- [ ] **Step 1: Write the failing tests**

```python
def test_delete_best_effort_removes_sync_state_on_success(tmp_path, monkeypatch, recording_http_client_for_delete):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    monkeypatch.setattr(skill_hub_sync, "_personal_token", lambda: "tok")
    skill_hub_sync._record_sync_state("my-review", "2026-01-01T00:00:00Z", "somehash")
    skill_hub_sync.delete_custom_skill_best_effort("my-review")
    assert "my-review" not in skill_hub_sync._load_sync_state()
    assert "my-review" not in skill_hub_sync._load_pending_deletes()

def test_delete_best_effort_records_pending_on_network_failure(tmp_path, monkeypatch, http_client_that_raises):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    monkeypatch.setattr(skill_hub_sync, "_personal_token", lambda: "tok")
    skill_hub_sync.delete_custom_skill_best_effort("my-review")  # must not raise
    assert "my-review" in skill_hub_sync._load_pending_deletes()

def test_delete_best_effort_is_noop_with_no_personal_token(monkeypatch, http_client_that_raises_if_called):
    monkeypatch.setattr(skill_hub_sync, "_personal_token", lambda: None)
    skill_hub_sync.delete_custom_skill_best_effort("my-review")  # no-op, no http call, no raise

def test_pull_merge_flushes_pending_delete_before_syncing(tmp_path, monkeypatch, fake_server_that_accepts_delete):
    # A name is pending-delete from an earlier offline remove, and still
    # exists server-side. pull_merge's flush step must retry the DELETE;
    # once it succeeds, branch 1 ("local missing -> pull") must NOT fire
    # for that name even though the server still listed it before the flush.
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    monkeypatch.setattr(skill_hub_sync, "_personal_token", lambda: "tok")
    skill_hub_sync._record_pending_delete("server-still-has-this")
    skill_hub_sync.pull_merge_custom_skills()
    assert skill_hub.read_custom_skill_file("server-still-has-this") is None
    assert "server-still-has-this" not in skill_hub_sync._load_pending_deletes()

def test_pull_merge_skips_resurrection_when_flush_still_fails(tmp_path, monkeypatch, fake_server_that_rejects_delete):
    # The retry itself fails again (still offline) - the name must stay
    # pending and branch 1 must still be suppressed for it this run, not
    # silently pull it back down.
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    monkeypatch.setattr(skill_hub_sync, "_personal_token", lambda: "tok")
    skill_hub_sync._record_pending_delete("still-pending")
    skill_hub_sync.pull_merge_custom_skills()  # must not raise
    assert skill_hub.read_custom_skill_file("still-pending") is None
    assert "still-pending" in skill_hub_sync._load_pending_deletes()

def test_load_pending_deletes_tolerates_corrupt_file(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    (tmp_path / ".pending_deletes.json").write_text("not valid json")
    assert skill_hub_sync._load_pending_deletes() == set()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest src/tests/test_skill_hub_sync.py -v -k "delete_best_effort or pending_delete or flushes_pending"`
Expected: FAIL

- [ ] **Step 3: Implement in `src/aletheore/skill_hub_sync.py`**

- `_pending_deletes_path() -> Path` = `skill_hub.custom_skills_dir() / ".pending_deletes.json"`.
- `_load_pending_deletes() -> set[str]`: read and `json.loads` as a list, return as a `set`; on any missing file, read error, or malformed JSON, return `set()` — mirror `_load_sync_state`'s existing tolerate-anything pattern exactly, don't raise.
- `_save_pending_deletes(names: set[str]) -> None`: write `sorted(names)` as a JSON list.
- `_record_pending_delete(name: str) -> None`: load, add, save.
- `_clear_pending_delete(name: str) -> None`: load, discard (no-op if absent), save.
- `delete_custom_skill_best_effort(name, timeout=5.0)`: if `_personal_token()` is `None`, return immediately (no-op, matching `push_custom_skill_best_effort`'s own early-return). Otherwise `try`/`except Exception` around `httpx.delete(f"{DEFAULT_API_BASE_URL}/v1/custom-skills/{name}", headers=..., timeout=timeout)`. A `204` or a `404` (already gone server-side — not an error from this function's point of view) both count as "server no longer has it": remove any sync-state entry for `name` (pop the key if present, save) and `_clear_pending_delete(name)`. Any other status code or exception: `_record_pending_delete(name)` and log a warning — never raise.
- `pull_merge_custom_skills`: at the very start of the function body, right after resolving `_personal_token()` and before the existing `GET /v1/custom-skills` call, add a flush step: for each name currently in `_load_pending_deletes()`, retry `delete_custom_skill_best_effort(name, timeout=timeout)` (reusing the function above — it already does the right thing on both success and repeated failure). After the flush attempt, re-load `_load_pending_deletes()` once (some may have cleared, some may not) and skip branch 1 ("local content missing → pull") for any name still in that set, for the rest of this call — a name still pending after the flush attempt means its delete still hasn't reached the server, so this run must not resurrect it locally even though the server's list still contains it.

- [ ] **Step 4: Run tests to verify they pass**

Run the same command as Step 2.
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/aletheore/skill_hub_sync.py src/tests/test_skill_hub_sync.py
git commit -m "feat: add best-effort custom-skill deletion with a pending-delete tombstone"
```

---

### Task 3: `skill_hub.remove_custom_skill` + CLI `skill-hub remove` + MCP `aletheore_remove_custom_skill`

**Files:**
- Modify: `src/aletheore/skill_hub.py`
- Modify: `src/aletheore/cli.py`
- Modify: `src/aletheore/mcp_server.py`
- Test: `src/tests/test_skill_hub.py`, `src/tests/test_cli.py`, `src/tests/test_mcp_server.py` (all append)

**Interfaces:**
- Consumes: `skill_hub_sync.delete_custom_skill_best_effort` (Task 2); `custom_skills_dir`, `sanitize_skill_name`, `read_custom_skill_file` (existing).
- Produces: `skill_hub.remove_custom_skill(name: str) -> bool` (raises `ValueError` on an unsafe name via `sanitize_skill_name`; returns `True` if a local file was actually deleted, `False` if there was nothing to remove — never raises `SkillNotFoundError`, since "already gone" is a valid outcome for a delete, not an error); `aletheore skill-hub remove <name>` CLI command; `aletheore_remove_custom_skill(name: str) -> str` MCP tool.

- [ ] **Step 1: Write the failing tests**

```python
# src/tests/test_skill_hub.py (append)
def test_remove_custom_skill_deletes_local_file_and_pushes(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    calls = []
    monkeypatch.setattr(skill_hub_sync, "delete_custom_skill_best_effort", lambda name, **kw: calls.append(name))
    skill_hub.add_custom_skill("my-review", "x")
    assert skill_hub.remove_custom_skill("my-review") is True
    assert skill_hub.read_custom_skill_file("my-review") is None
    assert calls == ["my-review"]

def test_remove_custom_skill_already_gone_returns_false_not_error(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    assert skill_hub.remove_custom_skill("never-existed") is False

def test_remove_custom_skill_rejects_unsafe_name():
    with pytest.raises(ValueError):
        skill_hub.remove_custom_skill("../escape")

def test_remove_custom_skill_leaves_bak_file_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    monkeypatch.setattr(skill_hub_sync, "delete_custom_skill_best_effort", lambda name, **kw: None)
    skill_hub.add_custom_skill("my-review", "v1")
    skill_hub.add_custom_skill("my-review", "v2")  # creates my-review.md.bak with "v1"
    skill_hub.remove_custom_skill("my-review")
    assert (tmp_path / "my-review.md.bak").read_text() == "v1"
```

```python
# src/tests/test_cli.py (append) - use the module-level `runner`, no fixture
def test_skill_hub_remove_deletes_and_reports(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    monkeypatch.setattr(skill_hub_sync, "delete_custom_skill_best_effort", lambda name, **kw: None)
    skill_file = tmp_path / "input.md"
    skill_file.write_text("x")
    runner.invoke(app, ["skill-hub", "add", "my-review", str(skill_file)])
    result = runner.invoke(app, ["skill-hub", "remove", "my-review"])
    assert result.exit_code == 0
    assert skill_hub.read_custom_skill_file("my-review") is None

def test_skill_hub_remove_unknown_name_clean_message_not_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    result = runner.invoke(app, ["skill-hub", "remove", "never-existed"])
    assert result.exit_code == 0  # not an error - "already gone" is a fine outcome for a delete
```

```python
# src/tests/test_mcp_server.py (append) - match this file's existing
# fixture/helper names for building a server instance and invoking a tool,
# the same way the add/get/list skill tool tests already do.
def test_aletheore_remove_custom_skill_tool(mcp_server_fixture, tmp_path, monkeypatch):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    monkeypatch.setattr(skill_hub_sync, "delete_custom_skill_best_effort", lambda name, **kw: None)
    invoke_tool(mcp_server_fixture, "aletheore_add_custom_skill", {"name": "my-review", "content": "x"})
    result = invoke_tool(mcp_server_fixture, "aletheore_remove_custom_skill", {"name": "my-review"})
    assert "removed" in result.lower() or "deleted" in result.lower()
    assert skill_hub.read_custom_skill_file("my-review") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest src/tests/test_skill_hub.py src/tests/test_cli.py src/tests/test_mcp_server.py -v -k remove`
Expected: FAIL

- [ ] **Step 3: Implement `remove_custom_skill` in `skill_hub.py`**

```python
def remove_custom_skill(name: str) -> bool:
    safe = sanitize_skill_name(name)
    path = custom_skills_dir() / f"{safe}.md"
    existed = path.is_file()
    if existed:
        path.unlink()
    from aletheore import skill_hub_sync
    skill_hub_sync.delete_custom_skill_best_effort(name)
    return existed
```

Same local-import-to-avoid-circularity pattern `add_custom_skill` already uses (`skill_hub.py:248`). Calling `delete_custom_skill_best_effort` unconditionally (even when `existed` is `False`) is deliberate: a name that was deleted on this machine in a previous, now-forgotten session could still exist server-side, and a repeated `remove` should keep trying to clear it there — the sync function itself already handles "nothing to do" cheaply if the server also has nothing.

- [ ] **Step 4: Add the CLI command to `cli.py`**

```python
@skill_hub_app.command("remove", help="remove a custom skill")
def skill_hub_remove(name: str) -> None:
    try:
        existed = skill_hub.remove_custom_skill(name)
    except ValueError as exc:
        console.print(f"[bold red]error:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc
    if existed:
        console.print(f"[bold green]Removed custom skill[/bold green] custom/{name}")
    else:
        console.print(f"custom/{name} was not present - nothing to remove.")
```

- [ ] **Step 5: Add the MCP tool to `mcp_server.py`**

New `_register_remove_custom_skill_tool(mcp_instance: MCPServer, repo_path: Path) -> None`, same shape and same gating as `_register_add_custom_skill_tool` (`mcp_server.py:948`) — this tool also writes locally and may make a network call, so gate it the same way: add `"aletheore_remove_custom_skill": frozenset({EFFECT_WRITE, EFFECT_NETWORK})` to `TOOL_REQUIRED_EFFECTS` (next to the `aletheore_add_custom_skill` entry, `mcp_server.py:1494`), and add `if permitted("aletheore_remove_custom_skill"): _register_remove_custom_skill_tool(mcp_instance, repo_path)` next to the `aletheore_add_custom_skill` registration call (`mcp_server.py:1630-1631`).

```python
def aletheore_remove_custom_skill(name: str) -> str:
    try:
        existed = skill_hub.remove_custom_skill(name)
    except ValueError as exc:
        return str(exc)
    if existed:
        return f"Removed custom skill custom/{name}."
    return f"custom/{name} was not present - nothing to remove."
```

- [ ] **Step 6: Run tests to verify they pass**

Run the same command as Step 2.
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/aletheore/skill_hub.py src/aletheore/cli.py src/aletheore/mcp_server.py src/tests/test_skill_hub.py src/tests/test_cli.py src/tests/test_mcp_server.py
git commit -m "feat: add skill-hub remove command and MCP tool for custom skills"
```

---

### Task 4: Login-time sync disclosure

**Files:**
- Modify: `src/aletheore/cli.py`
- Test: `src/tests/test_cli.py` (append)

**Interfaces:**
- Consumes: nothing new — this is a `console.print` added to the existing `login()` control flow, right where the personal token is successfully saved (the same unconditional step Fix 2 of the final-review fix wave added — find it by searching for where `save_api_token("aletheore-personal", ...)` is called in `login()`).

- [ ] **Step 1: Write the failing test**

```python
def test_login_prints_sync_disclosure_when_personal_token_saved(monkeypatch):
    # ...stub request_device_code/poll_for_access_token/mint_personal_token
    # per this file's existing login tests...
    result = runner.invoke(app, ["login"])
    assert "sync to your Aletheore account" in result.stdout

def test_login_does_not_print_disclosure_when_personal_token_mint_fails(monkeypatch):
    # ...stub mint_personal_token to raise, per the existing
    # test_login_succeeds_for_paid_user_when_personal_token_mint_fails test...
    result = runner.invoke(app, ["login"])
    assert "sync to your Aletheore account" not in result.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest src/tests/test_cli.py -v -k sync_disclosure`
Expected: FAIL

- [ ] **Step 3: Add the disclosure line to `login()`**

Immediately after the successful `save_api_token("aletheore-personal", personal_token)` call (inside the same success path Fix 2 already guards with its own try/except — the disclosure must only print when a personal token was actually saved this run, not when the mint failed and was skipped):

```python
console.print(
    "[dim]Custom skills you add sync to your Aletheore account and are available on your other machines.[/dim]"
)
```

- [ ] **Step 4: Run test to verify it passes**

Run the same command as Step 2.
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/aletheore/cli.py src/tests/test_cli.py
git commit -m "feat: disclose at login that custom skills sync to the user's account"
```

---

## Section B — Token Revocation

### Task 5: Server-side revoke endpoints

**Files:**
- Modify: `github-app/app_server/db.py`
- Modify: `github-app/app_server/skill_hub_api.py`
- Test: `github-app/tests/test_skill_hub_api.py` (append)

**Interfaces:**
- Consumes: nothing new from other tasks in this plan.
- Produces: `db.revoke_personal_token(pool: asyncpg.Pool, token_hash: str) -> bool` (`True` if a non-revoked row with that hash existed and was revoked, `False` if it didn't exist or was already revoked — idempotent, never an error either way); `db.revoke_all_personal_tokens(pool: asyncpg.Pool, personal_identity_id: int) -> int` (count of rows revoked, `0` if none were active); `skill_hub_api._bearer_token_hash(request: Request) -> str` (extracts and hashes the bearer token — same logic `_authenticate_personal_token` already does inline, pulled out so the revoke-self endpoint can reuse it without changing `_authenticate_personal_token`'s existing return contract, which three already-shipped routes depend on); `DELETE /v1/personal-tokens`; `DELETE /v1/personal-tokens/all`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_revoke_own_token_then_it_cannot_authenticate(test_app, personal_token_header):
    response = await test_app.delete("/v1/personal-tokens", headers=personal_token_header)
    assert response.status_code == 204
    list_response = await test_app.get("/v1/custom-skills", headers=personal_token_header)
    assert list_response.status_code == 401

async def test_revoke_own_token_is_idempotent(test_app, personal_token_header):
    await test_app.delete("/v1/personal-tokens", headers=personal_token_header)
    second = await test_app.delete("/v1/personal-tokens", headers=personal_token_header)
    assert second.status_code == 401  # the token used to authenticate this call is already revoked

async def test_revoke_all_invalidates_every_token_for_identity(test_app, personal_token_header, second_personal_token_header_same_identity):
    # second_personal_token_header_same_identity: mint a second token for the
    # SAME github_user_id as personal_token_header (same pattern Task 12's
    # two-distinct-tokens tests already use for a DIFFERENT identity -
    # mirror it, but keep the mocked GitHub user id the same across both mints).
    response = await test_app.delete("/v1/personal-tokens/all", headers=personal_token_header)
    assert response.status_code == 204
    r1 = await test_app.get("/v1/custom-skills", headers=personal_token_header)
    r2 = await test_app.get("/v1/custom-skills", headers=second_personal_token_header_same_identity)
    assert r1.status_code == 401
    assert r2.status_code == 401

async def test_revoke_all_does_not_affect_other_identity(test_app, personal_token_header, other_personal_token_header):
    await test_app.delete("/v1/personal-tokens/all", headers=personal_token_header)
    response = await test_app.get("/v1/custom-skills", headers=other_personal_token_header)
    assert response.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `TEST_DATABASE_URL="postgresql://postgres:test@localhost:55433/aletheore_test" TEST_REDIS_URL="redis://localhost:6379/0" PYTHONPATH=.:../src .venv-skillhub/bin/pytest tests/test_skill_hub_api.py -v -k revoke`
Expected: FAIL

- [ ] **Step 3: Add the two db.py functions**

```python
async def revoke_personal_token(pool: asyncpg.Pool, token_hash: str) -> bool:
    result = await pool.execute(
        "UPDATE personal_api_tokens SET revoked_at = now() WHERE token_hash = $1 AND revoked_at IS NULL",
        token_hash,
    )
    return result != "UPDATE 0"

async def revoke_all_personal_tokens(pool: asyncpg.Pool, personal_identity_id: int) -> int:
    result = await pool.execute(
        "UPDATE personal_api_tokens SET revoked_at = now() WHERE personal_identity_id = $1 AND revoked_at IS NULL",
        personal_identity_id,
    )
    # asyncpg execute() returns a string like "UPDATE 3"; the count is the
    # second token, same shape already relied on by revoke_personal_token's
    # own "UPDATE 0" check above.
    return int(result.split()[-1])
```

Place both next to `create_personal_token_within_limit`/`get_personal_identity_by_token_hash`.

- [ ] **Step 4: Add `_bearer_token_hash` and the two routes to `skill_hub_api.py`**

```python
def _bearer_token_hash(request: Request) -> str:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    raw_token = auth_header.removeprefix("Bearer ")
    return hashlib.sha256(raw_token.encode()).hexdigest()


@skill_hub_router.delete("/v1/personal-tokens", status_code=204)
async def revoke_own_personal_token(request: Request):
    await _authenticate_personal_token(request)  # 401s on an already-invalid token before we touch anything
    token_hash = _bearer_token_hash(request)
    await revoke_personal_token(request.app.state.db_pool, token_hash)


@skill_hub_router.delete("/v1/personal-tokens/all", status_code=204)
async def revoke_all_personal_tokens_route(request: Request):
    identity = await _authenticate_personal_token(request)
    await revoke_all_personal_tokens(request.app.state.db_pool, identity["id"])
```

`revoke_own_personal_token` deliberately doesn't branch on `revoke_personal_token`'s return value — whether the token was still active or got revoked by a concurrent/retried call a moment earlier, the caller's desired end state (this token no longer works) is the same either way, which is exactly the idempotency the tests require.

- [ ] **Step 5: Run tests to verify they pass**

Run the same command as Step 2.
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add github-app/app_server/db.py github-app/app_server/skill_hub_api.py github-app/tests/test_skill_hub_api.py
git commit -m "feat: add personal-token revocation endpoints (self and all-devices)"
```

---

### Task 6: `logout` revokes server-side; `--all-devices` flag

**Files:**
- Modify: `src/aletheore/device_auth.py`
- Modify: `src/aletheore/cli.py`
- Test: `src/tests/test_device_auth.py`, `src/tests/test_cli.py` (both append)

**Interfaces:**
- Consumes: `DELETE /v1/personal-tokens` and `DELETE /v1/personal-tokens/all` (Task 5); existing `credentials.has_api_key`/`get_api_key`/`clear_api_key`.
- Produces: `device_auth.revoke_personal_token(personal_token: str, all_devices: bool = False, api_base_url: str = "https://app.aletheore.com", http_client: httpx.Client | None = None) -> None` (raises `httpx.HTTPError` on a genuine failure — unlike the server-sync functions in `skill_hub_sync.py`, this one is allowed to signal failure to its caller, since `logout()` decides for itself whether to treat that as fatal or just a warning, per the brief below); modified `logout()` CLI command with a new `--all-devices` flag.

- [ ] **Step 1: Write the failing tests**

```python
# src/tests/test_device_auth.py (append) - mirror mint_personal_token's
# own httpx.MockTransport test pattern exactly.
def test_revoke_personal_token_calls_delete_personal_tokens():
    seen = {}
    def handler(request):
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(204)
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://app.aletheore.com")
    device_auth.revoke_personal_token("my-token", http_client=client)
    assert seen == {"method": "DELETE", "path": "/v1/personal-tokens", "auth": "Bearer my-token"}

def test_revoke_personal_token_all_devices_hits_the_all_path():
    seen = {}
    def handler(request):
        seen["path"] = request.url.path
        return httpx.Response(204)
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://app.aletheore.com")
    device_auth.revoke_personal_token("my-token", all_devices=True, http_client=client)
    assert seen["path"] == "/v1/personal-tokens/all"

def test_revoke_personal_token_raises_on_server_error():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)), base_url="https://app.aletheore.com")
    with pytest.raises(httpx.HTTPStatusError):
        device_auth.revoke_personal_token("my-token", http_client=client)
```

```python
# src/tests/test_cli.py (append) - module-level `runner`
def test_logout_revokes_personal_token_then_clears_both_slots(monkeypatch, tmp_credentials_path):
    credentials.save_api_token("aletheore-managed-audit", "tok1", credentials_path=tmp_credentials_path)
    credentials.save_api_token("aletheore-personal", "tok2", credentials_path=tmp_credentials_path)
    calls = []
    monkeypatch.setattr(device_auth, "revoke_personal_token", lambda token, **kw: calls.append((token, kw.get("all_devices", False))))
    result = runner.invoke(app, ["logout"])
    assert result.exit_code == 0
    assert calls == [("tok2", False)]
    assert not credentials.has_api_key("ALETHEORE_PERSONAL_TOKEN", "aletheore-personal", credentials_path=tmp_credentials_path)

def test_logout_still_clears_locally_when_revoke_fails(monkeypatch, tmp_credentials_path):
    credentials.save_api_token("aletheore-personal", "tok2", credentials_path=tmp_credentials_path)
    monkeypatch.setattr(device_auth, "revoke_personal_token", lambda token, **kw: (_ for _ in ()).throw(httpx.ConnectError("down")))
    result = runner.invoke(app, ["logout"])
    assert result.exit_code == 0  # best-effort: offline logout still logs out locally
    assert not credentials.has_api_key("ALETHEORE_PERSONAL_TOKEN", "aletheore-personal", credentials_path=tmp_credentials_path)

def test_logout_all_devices_passes_the_flag_through(monkeypatch, tmp_credentials_path):
    credentials.save_api_token("aletheore-personal", "tok2", credentials_path=tmp_credentials_path)
    calls = []
    monkeypatch.setattr(device_auth, "revoke_personal_token", lambda token, **kw: calls.append(kw.get("all_devices", False)))
    runner.invoke(app, ["logout", "--all-devices"])
    assert calls == [True]

def test_logout_all_devices_with_no_token_reports_plainly(tmp_credentials_path):
    result = runner.invoke(app, ["logout", "--all-devices"])
    assert result.exit_code == 0
    assert "not logged in" in result.stdout.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest src/tests/test_device_auth.py src/tests/test_cli.py -v -k "revoke or logout"`
Expected: FAIL

- [ ] **Step 3: Implement `revoke_personal_token` in `device_auth.py`**

```python
def revoke_personal_token(
    personal_token: str,
    all_devices: bool = False,
    api_base_url: str = "https://app.aletheore.com",
    http_client: httpx.Client | None = None,
) -> None:
    client = http_client or httpx.Client(base_url=api_base_url)
    path = "/v1/personal-tokens/all" if all_devices else "/v1/personal-tokens"
    response = client.delete(path, headers={"Authorization": f"Bearer {personal_token}"})
    response.raise_for_status()
```

Mirrors `mint_personal_token`'s exact shape, minus the JSON body (a `DELETE` here has none) and minus a return value (nothing to extract from a 204).

- [ ] **Step 4: Modify `logout()` in `cli.py`**

Add a `--all-devices` typer option. Before the existing loop that clears both credential slots: if `"aletheore-personal"` currently has a saved token, read it (`credentials.get_api_key(...)` with a non-prompting `prompt_fn`, same pattern `status()` already uses), then call `device_auth.revoke_personal_token(personal_token, all_devices=all_devices)` inside a `try`/`except Exception` — log/print a short warning on failure, never let it stop the local clear that follows. If `all_devices` is `True` and there is no saved personal token to authenticate the all-devices revoke with, print `"Not logged in - nothing to revoke."` and return before attempting the (impossible) revoke-all call, rather than silently doing nothing.

- [ ] **Step 5: Run tests to verify they pass**

Run the same command as Step 2.
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/aletheore/device_auth.py src/aletheore/cli.py src/tests/test_device_auth.py src/tests/test_cli.py
git commit -m "feat: logout revokes the personal token server-side; add --all-devices"
```

---

## Section C — End-to-End Verification

### Task 7: Integration test for deletion-survives-own-resync and revocation

**Files:**
- Modify: `github-app/tests/test_skill_hub_cross_machine_integration.py` (append — reuse the existing `live_skill_hub_server` fixture and real-identity-mocking setup this file already built for the original cross-machine test; do not duplicate that machinery in a new file)

**Interfaces:**
- Consumes: everything from Tasks 1-6, plus the existing `live_skill_hub_server` fixture and identity-mocking helpers already in this file.

- [ ] **Step 1: Write the failing tests**

```python
def test_add_remove_then_relogin_does_not_resurrect_the_skill(live_skill_hub_server, tmp_path_factory, monkeypatch):
    # One "machine": add a custom skill, confirm it synced (same pattern the
    # existing cross-machine test uses to confirm a push landed server-side),
    # then remove it. Force the delete call to fail once (simulate offline)
    # so the pending-delete tombstone path is actually exercised, then run
    # pull_merge_custom_skills again (as login would) and confirm the skill
    # is still absent locally - not resurrected by branch 1.
    ...

def test_logout_revoked_token_cannot_be_used_again(live_skill_hub_server, tmp_path_factory, monkeypatch):
    # Mint a real personal token via the real login flow, confirm it works
    # (e.g. a GET /v1/custom-skills succeeds), run `logout`, then confirm
    # the same raw token now gets a 401 from the real live server.
    ...

def test_logout_all_devices_invalidates_a_second_machines_token(live_skill_hub_server, tmp_path_factory, monkeypatch):
    # Two "machines" (two separate local config dirs), same fake GitHub
    # identity, each with its own real minted token (mirrors the original
    # cross-machine test's two-machine setup). Run `logout --all-devices`
    # on machine A's token; confirm machine B's token also now 401s.
    ...
```

Write these against the real `live_skill_hub_server` fixture and real Postgres, following this file's own established conventions (real sockets, real db queries to confirm server-side state, network disabled only where a prior task's tests already proved the read-path-stays-local claim — this task's job is the NEW behaviors, not re-proving the old ones).

- [ ] **Step 2: Run tests to verify they fail**

Run: `TEST_DATABASE_URL="postgresql://postgres:test@localhost:55433/aletheore_test" TEST_REDIS_URL="redis://localhost:6379/0" PYTHONPATH=.:../src .venv-skillhub/bin/pytest tests/test_skill_hub_cross_machine_integration.py -v -k "resurrect or revoked or all_devices"`
Expected: FAIL

- [ ] **Step 3: Nothing to implement**

Every piece this test exercises was already built in Tasks 1-6. If a failure here reveals a real gap in an earlier task, fix it in that task's own files, not by adding workaround logic to this test.

- [ ] **Step 4: Run tests to verify they pass**

Run the same command as Step 2, then the full suite for both halves of the repo once (`pytest src/tests/ -v` and the equivalent `github-app` invocation) to confirm no regressions across the whole follow-up plan.
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add github-app/tests/test_skill_hub_cross_machine_integration.py
git commit -m "test: verify deletion survives own resync and revocation actually invalidates tokens"
```
