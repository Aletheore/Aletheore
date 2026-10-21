"""Codex CLI smoke test (adapted from agy_smoke.py); Antigravity CLI (agy) smoke test: does the agent find and use Hallbook, per delivery channel?

Conditions: none (MCP connected only), snippet (the one-line instruction in AGENTS.md, which agy reads), ask (prompt says to use the skills).
Each run gets a fresh copy of a tiny scratch repo and a fresh conversation. Output: results.jsonl (one row per run).
Usage: python3 agy_smoke.py <out_dir> [task ids...]
"""
import json, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TASKS = {t["id"]: t for t in json.loads((HERE / "tasks_usage.json").read_text())}
OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
IDS = sys.argv[2:] or ["t01", "t03", "t07", "t13", "t14", "t22"]
SNIPPET = subprocess.run(["aletheore", "hallbook", "snippet"], capture_output=True, text=True, check=True).stdout
SNIPPET += os.environ.get("HB_SNIPPET_EXTRA", "")
CONDS = tuple(os.environ.get("HB_CONDS", "none,snippet,ask").split(","))
ASK = "Use the Aletheore skills available to you for this task.\n\n"


def make_repo(snippet: bool) -> Path:
    d = Path(tempfile.mkdtemp(prefix="agy-run-"))
    (d / "app.py").write_text("def add(a, b):\n    return a + b\n")
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    subprocess.run(["git", "add", "-A"], cwd=d, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"], cwd=d, check=True)
    if snippet:
        (d / "AGENTS.md").write_text(SNIPPET)
    return d


def run(task_id: str, cond: str) -> dict:
    prompt = TASKS[task_id].get("prompt") or TASKS[task_id].get("task")
    if cond == "ask":
        prompt = ASK + prompt
    repo = make_repo(cond == "snippet")
    # the Aletheore MCP server for this scratch repo
    t0 = time.time()
    row = {"task": task_id, "cond": cond, "status": None, "mcp_calls": [], "skill_ids": [], "find_skill_confidence": None,
           "tokens": None, "seconds": None, "answer_chars": 0}
    try:
        p = subprocess.run(["codex", "exec", "--json", "-s", "read-only", "--skip-git-repo-check", prompt],
                           cwd=repo, capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
        row["status"] = "NO_RESULT"
        types = []
        for line in p.stdout.splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            it = ev.get("item") or {}
            if ev.get("type") == "item.completed":
                types.append(it.get("type"))
                blob = json.dumps(it)
                if it.get("type") != "agent_message":
                    for name in ("aletheore_find_skill", "aletheore_get_skill_file", "aletheore_get_skill", "aletheore_search_skills", "aletheore_list_skills"):
                        if name in blob:
                            row["mcp_calls"].append(name)
                            if name == "aletheore_get_skill":
                                try:
                                    row["skill_ids"].append(json.loads(json.dumps(it)).get("arguments", {}).get("skill_id"))
                                except Exception:
                                    pass
                            break
                    if "confidence: high" in blob or "\"confidence\": \"high\"" in blob: row["find_skill_confidence"] = "high"
                    elif ("confidence: low" in blob or "\"confidence\": \"low\"" in blob) and row["find_skill_confidence"] is None: row["find_skill_confidence"] = "low"
                if it.get("type") == "agent_message":
                    row["answer"] = it.get("text"); row["answer_chars"] = len(it.get("text") or "")
            if ev.get("type") == "turn.completed":
                row["status"] = "SUCCESS"
                u = ev.get("usage") or {}
                row["tokens"] = (u.get("input_tokens") or 0) + (u.get("output_tokens") or 0)
        row["item_types"] = types
    except subprocess.TimeoutExpired:
        row["status"] = "TIMEOUT"
    finally:
        shutil.rmtree(repo, ignore_errors=True)
    row["seconds"] = round(time.time() - t0, 1)
    row["used_hallbook"] = any(c and c.startswith("aletheore_") and c in ("aletheore_find_skill", "aletheore_get_skill", "aletheore_search_skills", "aletheore_list_skills") for c in row["mcp_calls"])
    return row


if __name__ == "__main__":
    out = OUT / "results.jsonl"
    done = {(json.loads(l)["task"], json.loads(l)["cond"]) for l in out.read_text().splitlines()} if out.exists() else set()
    for tid in IDS:
        for cond in CONDS:
            if (tid, cond) in done:
                continue
            row = run(tid, cond)
            with out.open("a") as fh:
                fh.write(json.dumps(row) + "\n")
            print(tid, cond, row["status"], "hallbook" if row["used_hallbook"] else "-", row["seconds"], flush=True)
