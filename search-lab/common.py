"""Shared helpers for the overnight Hallbook test battery. Imported by every stage; not run directly.

Everything the stages need that is easy to get subtly wrong lives here: where the code under test
is, resumable result files, spend metering, calls to the model API, and running an OpenCode agent
session and reading back exactly what it did.

Conventions every stage follows:
  * results are appended to results/<stage>.jsonl, one JSON object per line, each with a unique "key";
    a stage that is run again skips keys it already has (so an interrupted night resumes)
  * a stage writes results/<stage>_summary.json when it finishes (also when it only got partway)
  * `--pilot` runs a tiny subset so the stage can be checked in a minute or two
  * nothing here writes outside this folder, and nothing commits or pushes
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
SCRATCH = Path(os.environ.get("HALLBOOK_SCRATCH", HERE / "scratch"))
RESULTS.mkdir(exist_ok=True)
SCRATCH.mkdir(exist_ok=True)

# The code under test: a clean copy of the committed tree, never the working directory.
SRC = Path(os.environ.get("HALLBOOK_SRC", Path(__file__).resolve().parent.parent / "src")).resolve()
SKILLS_DIR = SRC / "aletheore" / "skills"
PY = sys.executable

IR_BASE = "https://api.indierouter.ai/v1"
IR_MODELS = ["glm-5.3-flash", "deepseek-v4-flash", "deepseek-v4.1-flash"]  # what the plan key can reach
PRICE_IN_INR_PER_M = 7.0  # IndieRouter GLM/DeepSeek Flash class, from the user's dashboard
PRICE_OUT_INR_PER_M = 23.0
BUDGET_INR = float(os.environ.get("HALLBOOK_BUDGET_INR", "650"))

_lock = threading.Lock()


def use_clean_source() -> None:
    """Make `import aletheore` resolve to the clean copy."""
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))


# ---------------------------------------------------------------- result files

def append_jsonl(name: str, row: dict) -> None:
    row = {"t": round(time.time()), **row}
    with _lock, open(RESULTS / f"{name}.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(name: str) -> list[dict]:
    path = RESULTS / f"{name}.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue  # a half-written last line after a crash
    return rows


def done_keys(name: str) -> set[str]:
    return {row["key"] for row in read_jsonl(name) if "key" in row}


def write_summary(stage: str, summary: dict) -> None:
    (RESULTS / f"{stage}_summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------- spend

def _ledger(row: dict) -> None:
    append_jsonl("ledger", row)


def spent_inr() -> float:
    """Estimated spend so far this run, in rupees. Cached input is priced as full input (conservative)."""
    total = 0.0
    for row in read_jsonl("ledger"):
        total += (row.get("input", 0) + row.get("cache_read", 0)) * PRICE_IN_INR_PER_M / 1e6
        total += row.get("output", 0) * PRICE_OUT_INR_PER_M / 1e6
    return round(total, 2)


def budget_left() -> float:
    return round(BUDGET_INR - spent_inr(), 2)


class BudgetExhausted(Exception):
    pass


def require_budget(reserve_inr: float = 1.0) -> None:
    if budget_left() < reserve_inr:
        raise BudgetExhausted(f"spent about Rs {spent_inr()} of the Rs {BUDGET_INR} ceiling")


# ---------------------------------------------------------------- direct model calls

_RATE = re.compile(r"\b429\b|rate.?limit|quota|usage limit|too many requests", re.I)


def chat(prompt: str, model: str = "glm-5.3-flash", max_tokens: int = 300, temperature: float = 0.7,
         system: str | None = None, stage: str = "chat") -> str:
    """One chat completion from the IndieRouter plan key. Retries; waits out rate limits (up to ~30 min)."""
    import httpx

    key = os.environ.get("INDIEROUTER_API_KEY")
    if not key:
        raise RuntimeError("INDIEROUTER_API_KEY is not set")
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
    waited = 0
    for attempt in range(12):
        require_budget()
        try:
            response = httpx.post(f"{IR_BASE}/chat/completions", json=body, timeout=90,
                                  headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError:
            time.sleep(min(20 * (attempt + 1), 120))
            continue
        if response.status_code == 429 or (response.status_code >= 500) or _RATE.search(response.text[:300]):
            wait = min(60 * (attempt + 1), 300)
            waited += wait
            if waited > 1800:
                break
            time.sleep(wait)
            continue
        if response.status_code != 200:
            raise RuntimeError(f"chat failed: HTTP {response.status_code}: {response.text[:200]}")
        data = response.json()
        usage = data.get("usage") or {}
        _ledger({"kind": "chat", "stage": stage, "model": model, "input": usage.get("prompt_tokens", 0),
                 "output": usage.get("completion_tokens", 0), "cache_read": 0})
        return (data["choices"][0]["message"].get("content") or "").strip()
    raise RuntimeError("chat: gave up after repeated failures or rate limits")


# ---------------------------------------------------------------- agent sessions (OpenCode)

def provider_config(mcp_project: Path | None = None) -> dict:
    """OpenCode config: the IndieRouter provider, plus (optionally) the Aletheore MCP server from the clean copy."""
    config = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {
            "indierouter": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "IndieRouter",
                "options": {"baseURL": IR_BASE, "apiKey": "{env:INDIEROUTER_API_KEY}"},
                "models": {name: {"name": name} for name in IR_MODELS},
            }
        },
    }
    if mcp_project is not None:
        config["mcp"] = {
            "aletheore": {
                "type": "local",
                "command": [PY, "-m", "aletheore.cli", "mcp", str(mcp_project), "--no-watch"],
                "environment": {"PYTHONPATH": str(SRC)},
                "enabled": True,
            }
        }
    return config


_FREE_PROBE = RESULTS / "free_probe.json"


def free_models(max_age_s: int = 1800) -> list[str]:
    """Free OpenCode models that answered a one-word probe in the last 30 minutes (probed in parallel, 60 s)."""
    try:
        cached = json.loads(_FREE_PROBE.read_text())
        if time.time() - cached["t"] < max_age_s:
            return cached["ok"]
    except (OSError, ValueError, KeyError):
        pass
    listing = subprocess.run(["opencode", "models", "opencode"], capture_output=True, text=True).stdout
    names = [line.split("/", 1)[1].strip() for line in listing.splitlines() if "free" in line and "/" in line]
    ok: list[str] = []
    lock = threading.Lock()
    probe_dir = SCRATCH / "probe"
    probe_dir.mkdir(exist_ok=True)

    def probe(name: str) -> None:
        try:
            out = subprocess.run(
                ["opencode", "run", "Reply with the single word: ok", "--pure", "-m", f"opencode/{name}", "--format", "json"],
                capture_output=True, text=True, timeout=60, cwd=probe_dir, stdin=subprocess.DEVNULL,
                env={**os.environ, "PWD": str(probe_dir)},
            ).stdout
        except subprocess.TimeoutExpired:
            return
        if '"type": "text"' in out or '"type":"text"' in out:
            with lock:
                ok.append(name)

    threads = [threading.Thread(target=probe, args=(n,)) for n in names]
    [t.start() for t in threads]
    [t.join() for t in threads]
    _FREE_PROBE.write_text(json.dumps({"t": time.time(), "ok": sorted(ok)}))
    return sorted(ok)


def model_arms(include_free: bool = True, max_free: int = 2) -> list[str]:
    """The models a test matrix runs on: always the three IndieRouter models, plus up to max_free
    free models if any are answering right now (free is preferred where it works, never required)."""
    arms = [f"indierouter/{name}" for name in IR_MODELS]
    if include_free:
        arms += [f"opencode/{name}" for name in free_models()[:max_free]]
    return arms


def _summarize_events(stdout: str) -> dict:
    tools, texts = [], []
    tokens = {"input": 0, "output": 0, "cache_read": 0}
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        kind = event.get("type")
        part = event.get("part") or {}
        if kind == "tool_use":
            state = part.get("state") or {}
            tools.append({"name": part.get("tool", ""), "input": state.get("input"),
                          "status": state.get("status"), "output": str(state.get("output", ""))[:4000]})
        elif kind == "text":
            texts.append(part.get("text", ""))
        elif kind == "step_finish":
            step = part.get("tokens") or {}
            tokens["input"] += step.get("input", 0)
            tokens["output"] += step.get("output", 0)
            tokens["cache_read"] += (step.get("cache") or {}).get("read", 0)
    return {"tools": tools, "final_text": "\n".join(texts), "tokens": tokens}


def hallbook_calls(tools: list[dict]) -> list[str]:
    """Names (without the MCP prefix) of the Hallbook tools an agent called, in order."""
    names = []
    for tool in tools:
        match = re.search(r"aletheore_(search_skills|list_skills|get_skill_file|get_skill)$", tool.get("name", ""))
        if match:
            names.append(match.group(1))
    return names


def run_agent(prompt: str, model: str, project_dir: Path, mcp: bool = False, timeout: int = 900,
              stage: str = "agent") -> dict:
    """Run one OpenCode agent session in project_dir and report what it did.

    model is "indierouter/<name>" or "opencode/<free-name>". With mcp=True the Aletheore MCP server (from the
    clean copy, scoped to project_dir) is attached. Returns: rc, secs, timed_out, tools (every tool call with
    its input and output), hallbook (the Hallbook tool calls, in order), final_text, tokens, error.
    Rate limits and outages are retried with waits; an exhausted spend ceiling raises BudgetExhausted.
    """
    require_budget()
    project_dir = Path(project_dir).resolve()
    config_path = SCRATCH / "configs" / f"{uuid.uuid4().hex}.json"
    config_path.parent.mkdir(exist_ok=True)
    config_path.write_text(json.dumps(provider_config(project_dir if mcp else None)))
    env = {**os.environ, "OPENCODE_CONFIG": str(config_path), "PWD": str(project_dir)}
    result: dict = {}
    for attempt in range(6):
        started = time.time()
        try:
            proc = subprocess.run(
                ["opencode", "run", prompt, "--pure", "-m", model, "--dir", str(project_dir), "--format", "json"],
                capture_output=True, text=True, timeout=timeout, cwd=project_dir, stdin=subprocess.DEVNULL, env=env,
            )
            stdout, stderr, rc, timed_out = proc.stdout, proc.stderr, proc.returncode, False
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr, rc, timed_out = "", -1, True
        result = {"rc": rc, "secs": round(time.time() - started, 1), "timed_out": timed_out, "error": None,
                  **_summarize_events(stdout)}
        _ledger({"kind": "agent", "stage": stage, "model": model, **{k: result["tokens"][k] for k in ("input", "output", "cache_read")}})
        result["hallbook"] = hallbook_calls(result["tools"])
        infra = (not result["final_text"] and not result["tools"]) and (_RATE.search(stdout + stderr) or "unavailable" in (stdout + stderr).lower() or "database is locked" in (stdout + stderr))
        if infra and attempt < 5:
            time.sleep(min(120 * (attempt + 1), 600))
            require_budget()
            continue
        if infra:
            result["error"] = "infrastructure failure (rate limit, outage or lock) after retries"
        elif timed_out:
            result["error"] = "timed out"
        break
    try:
        config_path.unlink()
    except OSError:
        pass
    return result


# ---------------------------------------------------------------- small utilities

def pilot_requested() -> bool:
    return "--pilot" in sys.argv


def log(stage: str, message: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {stage}: {message}"
    print(line, flush=True)
    with _lock, open(RESULTS / "run.log", "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
