"""Claude Code (headless) adoption smoke test. Conditions: none, snippet (CLAUDE.md), ask, hook (UserPromptSubmit hint).
Isolated: user settings/plugins are not loaded (--setting-sources project), only the Aletheore MCP server is (--strict-mcp-config).
Usage: python3 claude_smoke.py <out_dir> [task ids...]   env: HB_CONDS, HB_MODEL"""
import json, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TASKS = {t["id"]: t for t in json.loads((HERE / "tasks_usage.json").read_text())}
OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
IDS = sys.argv[2:] or ["t01", "t03", "t07", "t13", "t14", "t22"]
CONDS = tuple(os.environ.get("HB_CONDS", "none,snippet,ask,hook").split(","))
MODEL = os.environ.get("HB_MODEL", "haiku")
SNIPPET = subprocess.run(["aletheore", "hallbook", "snippet"], capture_output=True, text=True, check=True).stdout
ASK = "Use the Aletheore skills available to you for this task.\n\n"
ACLI = shutil.which("aletheore")


def make_repo(cond: str) -> Path:
    d = Path(tempfile.mkdtemp(prefix="cc-run-"))
    (d / "app.py").write_text("def add(a, b):\n    return a + b\n")
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"], ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"]):
        subprocess.run(cmd, cwd=d, check=True)
    (d / "mcp.json").write_text(json.dumps({"mcpServers": {"aletheore": {"command": ACLI, "args": ["mcp", str(d)]}}}))
    if cond == "snippet":
        (d / "CLAUDE.md").write_text(SNIPPET)
    if cond == "hook":
        (d / ".claude").mkdir()
        (d / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": f"{ACLI} hallbook suggest --mode hint"}]}]}}))
    return d


def run(task_id: str, cond: str) -> dict:
    prompt = TASKS[task_id].get("prompt") or TASKS[task_id].get("task")
    if cond == "ask":
        prompt = ASK + prompt
    repo = make_repo(cond)
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    t0 = time.time()
    row = {"task": task_id, "cond": cond, "status": None, "mcp_calls": [], "skill_ids": [], "hook_hint": False,
           "find_skill_confidence": None, "tokens": None, "cost_usd": None, "seconds": None, "answer_chars": 0}
    try:
        cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose", "--model", MODEL,
               "--setting-sources", "project", "--strict-mcp-config", "--mcp-config", "mcp.json",
               "--allowedTools", "mcp__aletheore", "--no-session-persistence", "--max-turns", "10", "--max-budget-usd", "0.60"]
        p = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, timeout=300, env=env, stdin=subprocess.DEVNULL)
        for line in p.stdout.splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            blob = line
            if "Hallbook may have a skill" in blob or "Hallbook has a skill" in blob:
                row["hook_hint"] = True
            if ev.get("type") == "assistant":
                for c in (ev.get("message") or {}).get("content") or []:
                    if c.get("type") == "tool_use" and str(c.get("name", "")).startswith("mcp__aletheore__aletheore_"):
                        short = c["name"].split("__", 2)[2]
                        row["mcp_calls"].append(short)
                        if short == "aletheore_get_skill":
                            row["skill_ids"].append((c.get("input") or {}).get("skill_id"))
            elif ev.get("type") == "user":
                for c in (ev.get("message") or {}).get("content") or []:
                    if isinstance(c, dict) and c.get("type") == "tool_result":
                        txt = json.dumps(c.get("content"))
                        if "confidence: high" in txt: row["find_skill_confidence"] = "high"
                        elif "confidence: low" in txt and row["find_skill_confidence"] is None: row["find_skill_confidence"] = "low"
                        elif "confidence: none" in txt and row["find_skill_confidence"] is None: row["find_skill_confidence"] = "none"
            elif ev.get("type") == "result":
                row["status"] = "SUCCESS" if not ev.get("is_error") else "ERROR:" + str(ev.get("subtype"))
                row["answer"] = ev.get("result"); row["answer_chars"] = len(ev.get("result") or "")
                row["cost_usd"] = ev.get("total_cost_usd")
                u = ev.get("usage") or {}
                row["tokens"] = (u.get("input_tokens") or 0) + (u.get("output_tokens") or 0) + (u.get("cache_read_input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)
        if row["status"] is None:
            row["status"] = "NO_RESULT"; row["stderr"] = p.stderr[-300:]
    except subprocess.TimeoutExpired:
        row["status"] = "TIMEOUT"
    finally:
        shutil.rmtree(repo, ignore_errors=True)
    row["seconds"] = round(time.time() - t0, 1)
    row["used_hallbook"] = any(c in ("aletheore_find_skill", "aletheore_get_skill", "aletheore_search_skills", "aletheore_list_skills") for c in row["mcp_calls"])
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
            print(tid, cond, row["status"], "hallbook" if row["used_hallbook"] else "-", "hint" if row["hook_hint"] else "", row["seconds"], row["cost_usd"], flush=True)
