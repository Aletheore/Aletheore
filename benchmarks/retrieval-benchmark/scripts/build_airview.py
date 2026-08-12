import json, os, sys
from pathlib import Path

sys.path.insert(0, "/Users/arihantkaul/Documents/GitHub/Veridion/github-app")

from scan_worker.live_wiki import generate_subsystems, generate_overview
from aletheore.adapters.openai_compatible import OpenAICompatibleAdapter

REPO = Path("/private/tmp/bench-flask")
evidence = json.loads((REPO / ".aletheore" / "air.json").read_text())


def make_adapter():
    return OpenAICompatibleAdapter(
        name="deepseek",
        base_url="https://api.deepseek.com/v1",
        api_key_env_var="DEEPSEEK_API_KEY",
        model=os.environ.get("AIRVIEW_MODEL", "deepseek-chat"),
        requires_consent=False,
    )


def fetch_line_count(path: str):
    p = REPO / path
    try:
        return sum(1 for _ in p.open(errors="ignore"))
    except Exception:
        return None


naming = make_adapter()
writing = make_adapter()

print("generating subsystems...", file=sys.stderr)
subs = generate_subsystems(
    evidence, naming, writing, model_used="deepseek-chat",
    fetch_line_count=fetch_line_count,
)
print(f"subsystems produced: {len(subs)}", file=sys.stderr)

print("generating overview...", file=sys.stderr)
ov = generate_overview(evidence, subs, writing, fetch_line_count=fetch_line_count)
print("overview:", "ok" if ov else "REJECTED", file=sys.stderr)

json.dump({"subsystems": subs, "overview": ov},
          open("/private/tmp/bench/airview.json", "w"), indent=2, default=str)
print("wrote airview.json", file=sys.stderr)
