import re

import httpx

from app_server.http_client import get_generic_http_client

PUSHOVER_API_URL = "https://api.pushover.net/1/messages.json"

# Pushover's emergency priority (2) repeats the notification and requires
# acknowledgement until dismissed or expire seconds pass - the "blaring,
# can't-ignore" behavior a real endpoint-down alert wants. Every other
# alert (recovered, latency, shape-change) stays at normal priority (0):
# a single, non-repeating notification.
EMERGENCY_RETRY_SECONDS = 60
EMERGENCY_EXPIRE_SECONDS = 3600


def _strip_slack_markdown(text: str) -> str:
    # message["text"] is written for Slack/Teams mrkdwn (*bold*, `code`) -
    # a phone push notification has no markdown rendering, so the markers
    # would otherwise show up literally.
    return re.sub(r"[`*]", "", text)


def send_pushover_alert(
    api_token: str,
    user_key: str,
    message: dict,
    http_client: httpx.Client | None = None,
) -> None:
    client = http_client or get_generic_http_client()
    priority = message.get("pushover_priority", 0)
    payload = {
        "token": api_token,
        "user": user_key,
        "message": _strip_slack_markdown(message["text"]),
        "priority": priority,
    }
    if priority == 2:
        payload["retry"] = EMERGENCY_RETRY_SECONDS
        payload["expire"] = EMERGENCY_EXPIRE_SECONDS
    response = client.post(PUSHOVER_API_URL, data=payload, timeout=10)
    response.raise_for_status()
