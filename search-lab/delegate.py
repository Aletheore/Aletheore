"""Run one builder agent on one brief. Usage: python3 delegate.py <name> <brief-file-in-briefs/> [model]

The agent works inside this folder with the rules in briefs/CONTRACT.md. Its final report is written to
scratch/delegate_<name>.txt and the full event record to scratch/delegate_<name>.json.
"""
import json, sys
import common

name, brief = sys.argv[1], sys.argv[2]
model = sys.argv[3] if len(sys.argv) > 3 else "indierouter/deepseek-v4-flash"
prompt = (f"Read briefs/CONTRACT.md and briefs/{brief} in full, then carry out the brief completely, following its rules "
          f"exactly. Work only inside the current folder (search-lab). Some of the files may already exist from an earlier attempt that was cut short: check what is there, keep what is correct, finish what is missing, and verify with the commands the brief gives. Do not stop early or ask questions. When you are done, reply with the report the brief asks for.")
result = common.run_agent(prompt, model, common.HERE, mcp=False, timeout=3300, stage=f"build-{name}")
(common.SCRATCH / f"delegate_{name}.json").write_text(json.dumps(result, indent=1, default=str))
(common.SCRATCH / f"delegate_{name}.txt").write_text(result["final_text"])
print(name, "rc", result["rc"], "secs", result["secs"], "tools", len(result["tools"]), "tokens", result["tokens"], "error", result["error"])
