import requests

SLACK_BOT_TOKEN = "__BENCHMARK_PLACEHOLDER_SLACK_TOKEN__"


def post_message(channel, text):
    return requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
        json={"channel": channel, "text": text},
    )
