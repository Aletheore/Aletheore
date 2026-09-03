import requests

GITHUB_TOKEN = "__BENCHMARK_PLACEHOLDER_GITHUB_TOKEN__"

response = requests.get(
    "https://api.github.com/user",
    headers={"Authorization": f"token {GITHUB_TOKEN}"},
)
