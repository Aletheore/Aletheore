import json, os, sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _bench
sys.path.insert(0, os.environ.get("GITHUB_APP_PATH", os.path.join(_bench.ROOT, "..", "..", "github-app")))

from scan_worker.live_wiki import (
    attach_file_pages,
    generate_file_pages,
    generate_overview,
    generate_subsystems,
    select_file_page_paths,
)
from aletheore.adapters.openai_compatible import OpenAICompatibleAdapter

REPO = Path(_bench.FLASK)
evidence = json.loads((REPO / ".aletheore" / "air.json").read_text())

USAGE = {"in": 0, "out": 0, "calls": 0}


def _u(i, o):
    USAGE["in"] += i
    USAGE["out"] += o
    USAGE["calls"] += 1


def make_adapter():
    return OpenAICompatibleAdapter(
        name="deepseek",
        base_url="https://api.deepseek.com/v1",
        api_key_env_var="DEEPSEEK_API_KEY",
        model=os.environ.get("AIRVIEW_MODEL", "deepseek-chat"),
        requires_consent=False,
        on_usage=_u,
    )


def fetch_line_count(path: str):
    try:
        return sum(1 for _ in (REPO / path).open(errors="ignore"))
    except Exception:
        return None


naming, writing = make_adapter(), make_adapter()

print("subsystems...", file=sys.stderr)
subs = generate_subsystems(evidence, naming, writing, model_used="deepseek-chat",
                           fetch_line_count=fetch_line_count)
print(f"  {len(subs)} subsystems", file=sys.stderr)

subsystem_by_path = {f["path"]: s["name"] for s in subs for f in (s.get("files") or [])}
planned = select_file_page_paths(evidence)
print(f"file pages ({len(planned)} planned)...", file=sys.stderr)
pages = generate_file_pages(evidence, writing, paths=planned,
                            subsystem_by_path=subsystem_by_path,
                            fetch_line_count=fetch_line_count)
print(f"  {len(pages)} pages verified & kept", file=sys.stderr)
attach_file_pages(subs, pages)

print("overview...", file=sys.stderr)
ov = generate_overview(evidence, subs, writing, fetch_line_count=fetch_line_count)

json.dump({"subsystems": subs, "overview": ov, "file_pages": pages},
          open(os.path.join(_bench.OUT,"airview_v3.json"), "w"), indent=2, default=str)
print(f"TOKENS in={USAGE['in']} out={USAGE['out']} calls={USAGE['calls']}", file=sys.stderr)
