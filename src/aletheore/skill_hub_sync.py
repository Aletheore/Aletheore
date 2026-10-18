"""Bridges the local-only custom-skill storage in skill_hub.py to the
server's `/v1/custom-skills` endpoints: a best-effort push whenever a skill
is added locally, and a pull-merge run once at login.

Design note - do not use filesystem mtime for merge decisions. An earlier
draft compared the local file's mtime against the server's `updated_at`.
That breaks under a `git checkout`, a backup restore, or a plain file copy -
all of which change mtime without the content actually changing, and would
cause this module to silently push stale content over a newer server copy
(or the reverse). Instead, this module maintains its own small sidecar
state file (`.sync_state.json`, alongside the custom skill files) recording,
per skill name, the content hash and the server's `updated_at` *as of the
last successful sync*. Comparing against that sidecar, not the filesystem,
is what makes "has this changed since we last synced it" accurate
regardless of what touched the file's mtime.
"""

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path

import httpx

from aletheore import credentials, skill_hub

logger = logging.getLogger(__name__)

DEFAULT_API_BASE_URL = "https://app.aletheore.com"
_SYNC_STATE_FILENAME = ".sync_state.json"
_PENDING_DELETES_FILENAME = ".pending_deletes.json"


def _personal_token() -> str | None:
    if not credentials.has_api_key(
        "ALETHEORE_PERSONAL_TOKEN",
        "aletheore-personal",
        credentials_path=credentials.DEFAULT_CREDENTIALS_PATH,
    ):
        return None
    return credentials.get_api_key(
        "ALETHEORE_PERSONAL_TOKEN",
        "aletheore-personal",
        credentials_path=credentials.DEFAULT_CREDENTIALS_PATH,
        prompt_fn=lambda _msg: "",
    )


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def _sync_state_path() -> Path:
    return skill_hub.custom_skills_dir() / _SYNC_STATE_FILENAME


def _load_sync_state() -> dict:
    """Bookkeeping, not data a user can lose anything real from - a
    missing or corrupt sidecar file just means every skill is treated as
    never-synced-from-this-machine on the next merge, never a crash."""
    path = _sync_state_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        loaded = json.loads(raw)
    except ValueError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _save_sync_state(state: dict) -> None:
    path = _sync_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _record_sync_state(name: str, synced_at, content_hash: str) -> None:
    state = _load_sync_state()
    state[name] = {"synced_at": synced_at, "content_hash": content_hash}
    _save_sync_state(state)


def _pending_deletes_path() -> Path:
    return skill_hub.custom_skills_dir() / _PENDING_DELETES_FILENAME


def _load_pending_deletes() -> set[str]:
    """Bookkeeping, not data a user can lose anything real from - a
    missing or corrupt tombstone file just means nothing is currently
    suppressed from re-pull, never a crash. Mirrors `_load_sync_state`'s
    tolerate-anything pattern exactly."""
    path = _pending_deletes_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return set()
    try:
        loaded = json.loads(raw)
    except ValueError:
        return set()
    return set(loaded) if isinstance(loaded, list) else set()


def _save_pending_deletes(names: set[str]) -> None:
    path = _pending_deletes_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(names), indent=2), encoding="utf-8")


def _record_pending_delete(name: str) -> None:
    names = _load_pending_deletes()
    names.add(name)
    _save_pending_deletes(names)


def _clear_pending_delete(name: str) -> None:
    names = _load_pending_deletes()
    names.discard(name)
    _save_pending_deletes(names)


def _parse_timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_newer(candidate, baseline) -> bool:
    """Whether `candidate` (a server `updated_at`) is strictly newer than
    `baseline` (a stored `synced_at`). Both values originate from the same
    server field, so they share its format; falls back to a plain string
    comparison (still correct for ISO-8601 strings in the same format and
    timezone) only if either value can't be parsed as a timestamp."""
    parsed_candidate = _parse_timestamp(candidate)
    parsed_baseline = _parse_timestamp(baseline)
    if parsed_candidate is not None and parsed_baseline is not None:
        return parsed_candidate > parsed_baseline
    return str(candidate) > str(baseline)


def push_custom_skill_best_effort(name: str, content: str, timeout: float = 5.0) -> None:
    """Fire-and-forget push of one custom skill, called right after `add`.
    Never raises: a token-less machine, an offline machine, and a server
    error are all just "didn't sync this time", not a failure the caller
    needs to handle."""
    token = _personal_token()
    if token is None:
        return
    try:
        response = httpx.put(
            f"{DEFAULT_API_BASE_URL}/v1/custom-skills/{name}",
            json={"content": content},
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
        if response.status_code // 100 != 2:
            logger.warning(
                "push of custom skill '%s' failed: server returned %s",
                name,
                response.status_code,
            )
            return
        updated_at = response.json().get("updated_at")
        _record_sync_state(name, updated_at, _content_hash(content))
    except Exception:
        logger.warning("push of custom skill '%s' failed", name, exc_info=True)


def delete_custom_skill_best_effort(name: str, timeout: float = 5.0) -> None:
    """Fire-and-forget delete of one custom skill, called right after a
    local `remove`. Never raises: a token-less machine, an offline
    machine, and a server error are all just "didn't sync this time", not
    a failure the caller needs to handle.

    A 204 and a 404 both mean the server no longer has it (a 404 is just
    "already gone" from this function's point of view, not an error) -
    either way, clear any sync-state entry for `name` so a future
    pull-merge doesn't think it's still tracking a synced copy, and clear
    any pending-delete tombstone entry. Any other outcome (network
    exception, 500, etc.) means the delete didn't reach the server: record
    `name` in the pending-deletes tombstone instead, so the next
    pull-merge retries it and - critically - doesn't resurrect it locally
    in the meantime."""
    token = _personal_token()
    if token is None:
        return
    try:
        response = httpx.delete(
            f"{DEFAULT_API_BASE_URL}/v1/custom-skills/{name}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
        if response.status_code == 204 or response.status_code == 404:
            sync_state = _load_sync_state()
            if sync_state.pop(name, None) is not None:
                _save_sync_state(sync_state)
            _clear_pending_delete(name)
            return
        logger.warning(
            "delete of custom skill '%s' failed: server returned %s",
            name,
            response.status_code,
        )
        _record_pending_delete(name)
    except Exception:
        logger.warning("delete of custom skill '%s' failed", name, exc_info=True)
        _record_pending_delete(name)


def _fetch_skill_content(name: str, headers: dict, timeout: float) -> str:
    response = httpx.get(
        f"{DEFAULT_API_BASE_URL}/v1/custom-skills/{name}", headers=headers, timeout=timeout
    )
    response.raise_for_status()
    return response.json()["content"]


def _push_and_record(name: str, content: str, headers: dict, timeout: float, sync_state: dict) -> None:
    response = httpx.put(
        f"{DEFAULT_API_BASE_URL}/v1/custom-skills/{name}",
        json={"content": content},
        headers=headers,
        timeout=timeout,
    )
    response.raise_for_status()
    updated_at = response.json().get("updated_at")
    sync_state[name] = {"synced_at": updated_at, "content_hash": _content_hash(content)}
    _save_sync_state(sync_state)


def pull_merge_custom_skills(timeout: float = 10.0) -> None:
    """Run once at login. Requires a saved personal token - returns
    immediately (no network call) without one, since login's caller only
    calls this once it has minted/loaded a token, but this stays safe
    either way. Never raises: a network failure anywhere in here is just
    "didn't merge this time", logged and swallowed, not something login
    should fail over."""
    token = _personal_token()
    if token is None:
        return
    try:
        # Flush pending deletes first, before even listing the server's
        # skills - a name that still fails to delete here must not have
        # branch 1 below pull it back down this run, even though the
        # server's list (fetched next) still contains it.
        for pending_name in _load_pending_deletes():
            delete_custom_skill_best_effort(pending_name, timeout=timeout)
        still_pending = _load_pending_deletes()

        headers = {"Authorization": f"Bearer {token}"}
        response = httpx.get(
            f"{DEFAULT_API_BASE_URL}/v1/custom-skills", headers=headers, timeout=timeout
        )
        response.raise_for_status()
        server_skills = response.json().get("skills", [])
        sync_state = _load_sync_state()
        seen_names = set()

        for entry in server_skills:
            name = entry["name"]
            try:
                skill_hub.sanitize_skill_name(name)
            except ValueError:
                logger.warning("skill hub sync: ignoring unsafe skill name from server: %r", name)
                continue
            seen_names.add(name)
            server_updated_at = entry.get("updated_at")
            local_content = skill_hub.read_custom_skill_file(name)
            state_entry = sync_state.get(name)

            if local_content is None:
                if name in still_pending:
                    # This machine deleted it locally and the delete still
                    # hasn't reached the server (the flush above just
                    # retried and failed again) - the server's list still
                    # contains it, but pulling it back down now would
                    # silently undo the user's own `remove`. Skip branch 1
                    # for this name for the rest of this run.
                    continue
                # Branch 1: local content missing entirely - pull.
                content = _fetch_skill_content(name, headers, timeout)
                skill_hub.write_custom_skill_file(name, content)
                sync_state[name] = {
                    "synced_at": server_updated_at,
                    "content_hash": _content_hash(content),
                }
                _save_sync_state(sync_state)
                continue

            if state_entry is None:
                # Branch 2: present locally but never synced from this
                # machine before - local predates any sync relationship,
                # so it wins.
                _push_and_record(name, local_content, headers, timeout, sync_state)
                continue

            if _content_hash(local_content) != state_entry.get("content_hash"):
                # Branch 3: local was edited since the last sync (by `add`,
                # by hand, whatever) - the content-hash check, not a
                # timestamp comparison, is what decides this: local wins
                # and gets pushed regardless of what the server's
                # updated_at says.
                _push_and_record(name, local_content, headers, timeout, sync_state)
                continue

            if _is_newer(server_updated_at, state_entry.get("synced_at")):
                # Branch 4: the server changed since we last synced and we
                # haven't touched our copy - pull. This is the one branch
                # that overwrites existing local content with something
                # that arrived over the network, so harden it: back up the
                # current content first and log a visible notice.
                backup_path = skill_hub.custom_skills_dir() / f"{name}.md.bak"
                backup_path.write_text(local_content, encoding="utf-8")
                logger.warning(
                    "custom skill '%s' was updated from your account (synced from "
                    "another machine) - previous local content saved to %s.md.bak",
                    name,
                    name,
                )
                content = _fetch_skill_content(name, headers, timeout)
                skill_hub.write_custom_skill_file(name, content)
                sync_state[name] = {
                    "synced_at": server_updated_at,
                    "content_hash": _content_hash(content),
                }
                _save_sync_state(sync_state)
                continue

            # Branch 5: hash matches and the server isn't newer - already
            # in sync, no-op.

        # Any local custom skill the server's list didn't contain at all -
        # added locally while fully offline, never pushed. Same
        # push-and-record-state logic as the "never synced" branch above.
        for name in skill_hub.list_custom_skill_names():
            if name in seen_names:
                continue
            local_content = skill_hub.read_custom_skill_file(name)
            if local_content is None:
                continue
            _push_and_record(name, local_content, headers, timeout, sync_state)
    except Exception:
        logger.warning("skill hub pull-merge failed", exc_info=True)
