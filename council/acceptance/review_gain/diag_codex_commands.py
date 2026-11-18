import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, "/Users/arihantkaul/Aletheore-council-autonomy/council/acceptance/review_gain")
from aletheore.council.runners import CodexRunner
man = json.load(open(Path.home() / "dev/council-gain-data/v2-60files/manifest.json"))["bugs"]
files = sorted({b["file"] for b in man})
task = ("Review the networkx modules listed below for bugs: code that does not do what its name, docstring or the surrounding logic says it should. Some of these files "
        "were changed recently and the changes introduced subtle bugs. Read the code carefully; do not run it. Report at most 90 findings, most confident first, as a numbered list. "
        "Each finding: the file, the function, the line number, and what is wrong and what it should be. Real defects only: no style remarks.\n\nFiles:\n" + "\n".join(f"- {f}" for f in files))
d = Path(tempfile.mkdtemp()); cfg = d / "m.json"; cfg.write_text('{"mcpServers": {}}')
r = CodexRunner().run(task, Path("repo").resolve(), cfg, {}, 1500, read_only=True)
print("ok", r.ok, "chars", len(r.final_text))
cmds = []
for e in r.raw_events:
    it = e.get("item", {})
    if e.get("type") == "item.completed" and it.get("type") == "command_execution":
        cmds.append(it.get("command", ""))
print(len(cmds), "commands run")
for c in cmds[:40]: print("  $", c[:230])
json.dump(cmds, open("cmds.json", "w"))
