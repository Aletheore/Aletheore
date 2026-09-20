import asyncio
import logging

from app_server.config import get_settings
from app_server.db import get_installation, hide_repo, purge_installation_data, unhide_repo, upsert_installation
from app_server.error_alerts import send_error_alert
from app_server.github_auth import generate_app_jwt, get_installation_token
from app_server.github_pagination import fetch_paginated_github_collection
from app_server.http_client import get_github_api_client
from app_server.paddle_client import PaddleAPIError, PaddleAPINotConfigured
from app_server.paddle_client import cancel_subscription as cancel_paddle_subscription

logger = logging.getLogger(__name__)


def _enqueue_checkout_purge(installation_id: int, redis_url: str, queue=None) -> None:
    """purge_installation_data (app_server/db.py) is SQL-only - app-server
    has no filesystem access to the persistent-checkout volume that only
    scan-worker mounts (see scan_worker.jobs._ensure_persistent_checkout),
    so the on-disk deletion has to happen there instead."""
    if queue is None:
        from redis import Redis
        from rq import Queue

        queue = Queue("scans", connection=Redis.from_url(redis_url))
    queue.enqueue(
        "scan_worker.jobs.purge_persistent_checkouts_job",
        job_timeout=120,
        installation_id=installation_id,
    )


def _fetch_installation_repos_sync(installation_id: int, app_jwt: str) -> list[str]:
    token = get_installation_token(installation_id, app_jwt)
    repositories = fetch_paginated_github_collection(
        get_github_api_client(),
        "/installation/repositories",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
        collection_key="repositories",
        require_total_count_match=True,
    )
    return [repo["full_name"] for repo in repositories]


async def _fetch_all_installation_repo_full_names(installation_id: int) -> list[str]:
    settings = get_settings()
    app_jwt = generate_app_jwt(settings.github_app_id, settings.github_app_private_key)
    return await asyncio.to_thread(_fetch_installation_repos_sync, installation_id, app_jwt)


async def handle_installation_event(
    event_name: str, payload: dict, pool, redis_url: str, queue=None
) -> None:
    action = payload.get("action")
    installation = payload["installation"]
    installation_id = installation["id"]
    account_login = installation["account"]["login"]

    if event_name == "installation" and action == "deleted":
        # Uninstalling is a deletion request like any other - it goes
        # through the same purge as the dashboard button so it clears the
        # user-scoped email/session rows too, and lands in the same audit
        # log. A bare DELETE here would leave those behind.
        sender = payload.get("sender") or {}
        actor = sender.get("login") or "github:installation.deleted"

        # Cancel the real Paddle subscription BEFORE purge deletes the row
        # it lives on - see delete_all_data's identical comment in
        # admin.py. Unlike that route, there's no live user here to retry
        # for: GitHub already completed the uninstall on its side, so this
        # can't block or fail the webhook. Best-effort cancel, then alert
        # loudly on failure so a human follows up in the Paddle dashboard
        # instead of the customer silently being billed forever.
        existing = await get_installation(pool, installation_id)
        subscription_id = existing.get("paddle_subscription_id") if existing else None
        if subscription_id:
            settings = get_settings()
            try:
                await asyncio.to_thread(
                    cancel_paddle_subscription, settings.paddle_api_key, subscription_id
                )
            except PaddleAPINotConfigured:
                pass
            except PaddleAPIError as exc:
                logger.error(
                    "could not cancel Paddle subscription %s for installation %s on uninstall: %s",
                    subscription_id, installation_id, exc,
                )
                # send_error_alert does a real blocking HTTP call (Resend)
                # under the hood - off the event loop the same way the
                # Paddle cancel call above is, or it stalls every other
                # webhook this process is handling concurrently for the
                # duration of that request.
                await asyncio.to_thread(
                    send_error_alert,
                    "installation_webhook",
                    exc,
                    context=f"uninstall for installation {installation_id} ({account_login}) - "
                    f"Paddle subscription {subscription_id} NOT canceled, customer will keep being billed",
                )

        await purge_installation_data(pool, installation_id, actor)
        _enqueue_checkout_purge(installation_id, redis_url, queue)
        return

    await upsert_installation(pool, installation_id, account_login)

    if event_name == "installation_repositories" and action == "removed":
        # Deselecting a repo from an existing installation, distinct from
        # uninstalling the whole app (handled above) - GitHub revokes the
        # app's access to it, but the customer didn't ask us to forget it.
        # Soft-hide rather than purge: gone from the dashboard and a no-op
        # for any new scan/review trigger (see is_repo_hidden's call
        # sites), reversible if they reselect it later. upsert_installation
        # runs first (just above) so hidden_repos' FK to installations is
        # always satisfied, even for a "removed" event somehow arriving
        # before this installation's own "created" event was processed.
        for repo in payload.get("repositories_removed", []):
            await hide_repo(pool, installation_id, repo["full_name"])
        return

    # Without this, a repo with no open pull requests never gets scanned
    # at all - run_pr_scan_job is the only other thing that writes a
    # repo_history row, and it only fires on a PR event. A freshly
    # connected repo would otherwise sit "Initialization required" on
    # the dashboard forever, with no feedback or path forward.
    repo_full_names: list[str] = []
    if event_name == "installation" and action == "created":
        # The payload's own `repositories` field is only reliable for
        # repository_selection == "selected" - fetching the live list
        # covers "all" too, and matches what the "Your repositories" page
        # already uses as its source of truth.
        try:
            repo_full_names = await _fetch_all_installation_repo_full_names(installation_id)
        except Exception:
            logger.warning(
                "failed to enumerate repos for new installation %s", installation_id, exc_info=True
            )
    elif event_name == "installation_repositories" and action == "added":
        repo_full_names = [
            repo["full_name"] for repo in payload.get("repositories_added", [])
        ]
        # A repo can only be re-selected here if it was previously
        # deselected under this same installation (a brand new repo was
        # never hidden) - unhide is a no-op DELETE otherwise.
        for repo_full_name in repo_full_names:
            await unhide_repo(pool, installation_id, repo_full_name)

    if not repo_full_names:
        return

    if queue is None:
        from redis import Redis
        from rq import Queue

        queue = Queue("scans", connection=Redis.from_url(redis_url))
    for repo_full_name in repo_full_names:
        queue.enqueue(
            "scan_worker.jobs.run_initial_scan_job",
            job_timeout=300,
            installation_id=installation_id,
            repo_full_name=repo_full_name,
        )
