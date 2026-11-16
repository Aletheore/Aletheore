"""One replicate of the review-gain experiment, with the real Claude Code and Codex CLIs.

    python run_gain.py CORPUS_DIR OUT_DIR REPLICATE [condition ...]      conditions: claude-alone codex-alone split council (default: all four)

The same task in every condition: review 12 networkx modules that hold 24 injected bugs, report at most 25 findings (a cap, so spraying guesses does not pay).
  claude-alone / codex-alone  one agent, one read-only turn, all 12 files
  split                       three agents in parallel, four files each (a fixed split, no planner, no talking), their reports joined; at most 8 findings each
  council                     the real thing: a planner (Claude) is asked once; it splits the files across three executors (Codex, Claude, Codex) by mentioning
                              them, they work in parallel, each reports back with a mention, and the planner writes the final review. No person after the first message.
Each review is scored by score.py against manifest.json. Also scored: each executor's own report, to see whether the planner dropped correct findings."""
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from aletheore.council.bundle_store import LocalBundleStore
from aletheore.council.daemon import WorkerDaemon
from aletheore.council.protocol import EventDraft, EventType
from aletheore.council.room import Room
from aletheore.council.runners import ClaudeRunner, CodexRunner
from aletheore.council.sqlite_store import SqliteRoomStore
from aletheore.council.state import Area
from aletheore.council.store import read_all
from score import found_ids

CORPUS, OUT, REP = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), int(sys.argv[3])
CONDITIONS = sys.argv[4:] or ["claude-alone", "codex-alone", "split", "council"]
MANIFEST = json.load(open(CORPUS.parent / "manifest.json"))["bugs"]
FILES = sorted({b["file"] for b in MANIFEST})
LINES = {f: len((CORPUS / f).read_text().split("\n")) for f in FILES}
TURN_TIMEOUT = 1500
CAP_ALONE, CAP_EXECUTOR = 25, 8
EXECUTORS = [("alice-codex", "alice", CodexRunner), ("carol-claude", "carol", ClaudeRunner), ("dave-codex", "dave", CodexRunner)]
PLANNER = ("bob-claude", "bob", ClaudeRunner)

TASK = ("Review the networkx modules listed below for bugs: code that does not do what its name, docstring or the surrounding logic says it should. Some of "
        "these files were changed recently and the changes introduced subtle bugs (a wrong operator, a flipped condition, an off-by-one, a wrong constant). Read the "
        "code carefully; do not run it. Report at most {cap} findings, most confident first, as a numbered list. Each finding: the file, the function, the line "
        "number, and what is wrong and what it should be. Real defects only: no style remarks.\n\nFiles:\n{files}")


def groups():
    """Three groups of about equal line count (greedy, biggest first)."""
    g = [[], [], []]
    sizes = [0, 0, 0]
    for f in sorted(FILES, key=lambda f: -LINES[f]):
        i = sizes.index(min(sizes))
        g[i].append(f)
        sizes[i] += LINES[f]
    return g


def clone(dest):
    subprocess.run(["git", "clone", "-q", str(CORPUS), str(dest)], check=True)
    return dest


def empty_mcp(d, name="mcp.json"):
    p = Path(d) / name
    p.write_text('{"mcpServers": {}}')   # one file per caller: three threads writing and reading one file raced (an empty file was read mid-write)
    return p


def run_alone(kind):
    base = Path(tempfile.mkdtemp(prefix=f"gain-{kind}-"))
    wt = clone(base / "repo")
    runner = {"claude": ClaudeRunner, "codex": CodexRunner}[kind]()
    t0 = time.time()
    r = runner.run(TASK.format(cap=CAP_ALONE, files="\n".join(f"- {f}" for f in FILES)), wt, empty_mcp(base), {}, TURN_TIMEOUT, read_only=True)
    return {"text": r.final_text, "secs": round(time.time() - t0), "ok": r.ok}


def run_split():
    g = groups()
    base = Path(tempfile.mkdtemp(prefix="gain-split-"))
    t0 = time.time()

    def one(i):
        wt = clone(base / f"repo{i}")
        runner = EXECUTORS[i][2]()
        r = runner.run(TASK.format(cap=CAP_EXECUTOR, files="\n".join(f"- {f}" for f in g[i])), wt, empty_mcp(base, f"mcp{i}.json"), {}, TURN_TIMEOUT, read_only=True)
        return r.final_text, r.ok, round(time.time() - t0)

    with ThreadPoolExecutor(3) as ex:
        res = list(ex.map(one, range(3)))
    return {"text": "\n\n".join(t for t, _, _ in res), "secs": round(time.time() - t0), "ok": all(ok for _, ok, _ in res), "parts": [t for t, _, _ in res],
            "part_secs": [s for _, _, s in res]}


def run_council():
    g = groups()
    base = Path(tempfile.mkdtemp(prefix="gain-council-"))
    store = SqliteRoomStore(base / "room.db")
    people = [PLANNER] + [(a, p, k) for a, p, k in EXECUTORS]
    host_repo = clone(base / "alice-repo")
    host = Room.create(store, host_repo, "alice", base / "state-alice")
    for agent, person, _ in people:
        host.define_area(Area(f"area-{person}", ["**"], person, agent))
    rooms = {"alice": host}
    for _, person, _ in people:
        if person != "alice":
            rooms[person] = Room.open_remote(store, host.room_id, clone(base / f"{person}-repo"), base / f"state-{person}")
    rid = host.room_id
    bundles = LocalBundleStore(base / "bundles")
    wake_from = {"alice": {"bob"}, "bob": {"carol", "dave"}, "carol": {"bob"}, "dave": {"bob"}}
    daemons = {}
    for agent, person, kind in people:
        daemons[person] = WorkerDaemon(store, rooms[person], person, {agent: kind()}, bundles=bundles, task_timeout_s=TURN_TIMEOUT, claim_timeout_s=TURN_TIMEOUT + 900,
                                       wake_cooldown_s=0.0, max_wake_depth=4, wake_from=wake_from[person], poll_interval_s=0.5)
    pid, p_person = PLANNER[0], PLANNER[1]
    ex_ids = [a for a, _, _ in EXECUTORS]

    def job(agent, text):
        store.append(EventDraft(rid, f"job:{agent}", "room", EventType.SYSTEM, "alice", None, None, {"subtype": "standing_job", "agent": agent, "text": text}))

    job(pid, f"You coordinate a code review done by three executor agents: {', '.join(ex_ids)}. When you are asked to review, do not read the code yourself first. Split the "
             "files into three groups of roughly equal size and send ONE message that mentions all three executors (write each as @name), gives each its own group of files "
             f"and tells each to report at most {CAP_EXECUTOR} findings (file, function, line, what is wrong and what it should be; real defects only). Executors answer one at a time "
             "and each answer wakes you: until all three have answered, reply with one short line saying who is still missing, and mention nobody. When all three have "
             f"answered, write the final review: a numbered list of at most {CAP_ALONE} findings, most confident first, combining the three reports (you may read the code to check "
             "a finding before you include it, and drop one that is wrong), each with file, function, line and what is wrong. The last line must be exactly: REVIEW COMPLETE. "
             "The final review must not mention anyone with an @.")
    for a in ex_ids:
        job(a, f"You are a code reviewer. The message that woke you names three reviewers, each with its own files: review ONLY the files listed for you ({a}). Real defects only, at "
               f"most {CAP_EXECUTOR}, each with file, function, line, and what is wrong and what it should be. End your reply with one line that mentions @{pid} so the planner sees it.")

    stop = threading.Event()
    threads = [threading.Thread(target=d.run_forever, args=(stop,), daemon=True) for d in daemons.values()]
    for t in threads:
        t.start()
    files_text = "; ".join(f"{', '.join(Path(f).name for f in grp)}" for grp in g)
    t0 = time.time()
    store.append(EventDraft(rid, "human:1", "room", EventType.QUESTION, "alice", None, None,
                            {"kind": "chat", "text": f"@{pid} review the networkx modules in this repo: {', '.join(FILES)}. Some were changed recently and have subtle bugs. Your three "
                                                     f"executors are {', '.join(ex_ids)}. I will not answer questions; work it out between you."}))

    def state():
        ev = read_all(store, rid)
        ex_answers = {e.sender_agent: e for e in ev if e.sender_agent in ex_ids and e.body.get("wake_for") and e.type is EventType.QUESTION}
        first_all = max((e.seq for e in ex_answers.values()), default=None) if len(ex_answers) == 3 else None
        final = next((e for e in ev if e.sender_agent == pid and first_all and e.seq > first_all and "REVIEW COMPLETE" in str(e.body.get("text", ""))), None)
        return ev, ex_answers, final

    last_n, last_change = 0, time.time()
    while time.time() - t0 < 50 * 60:
        ev, ex_answers, final = state()
        if final is not None:
            break
        if len(ev) != last_n:
            last_n, last_change = len(ev), time.time()
        elif time.time() - last_change > 20 * 60:
            break
        time.sleep(2)
    secs = round(time.time() - t0)
    ev, ex_answers, final = state()
    stop.set()
    for t in threads:
        t.join(5)
    for r in rooms.values():
        r.close_brokers()
    notices = [e.body.get("note") for e in ev if e.body.get("subtype") in ("wake_skipped", "wake_failed", "worker_error")]
    planner_msgs = [str(e.body.get("text")) for e in ev if e.sender_agent == pid and e.body.get("wake_for")]
    return {"text": str(final.body["text"]) if final else "", "secs": secs, "ok": final is not None, "executor_reports": {a: str(e.body.get("text")) for a, e in ex_answers.items()},
            "planner_messages": planner_msgs, "notices": notices, "person_messages": len([e for e in ev if e.type is EventType.QUESTION and e.sender_agent is None and e.body.get("kind") == "chat"]),
            "planner_wakes": len([e for e in ev if e.body.get("subtype") == "wake_claimed" and e.sender_agent == pid]), "files_by_group": g}


def main():
    out = OUT / f"rep{REP}"
    out.mkdir(parents=True, exist_ok=True)
    judge = ClaudeRunner()
    for cond in CONDITIONS:
        print(f"[rep {REP}] {cond} ...", flush=True)
        res = {"claude-alone": lambda: run_alone("claude"), "codex-alone": lambda: run_alone("codex"), "split": run_split, "council": run_council}[cond]()
        found = found_ids(res["text"], MANIFEST, judge) if res["text"].strip() else []
        record = {"condition": cond, "rep": REP, "secs": res["secs"], "ok": res["ok"], "found": found, "n_found": len(found)}
        (out / f"{cond}.txt").write_text(res["text"])
        if cond == "council":
            parts = {a: found_ids(t, MANIFEST, judge) for a, t in res["executor_reports"].items()}
            record.update({"executor_found": parts, "executor_union": sorted({i for v in parts.values() for i in v}), "notices": res["notices"],
                           "person_messages": res["person_messages"], "planner_wakes": res["planner_wakes"], "files_by_group": res["files_by_group"]})
            (out / "council-planner-messages.txt").write_text("\n\n=====\n\n".join(res["planner_messages"]))
            for a, t in res["executor_reports"].items():
                (out / f"council-executor-{a}.txt").write_text(t)
        if cond == "split":
            parts = [found_ids(t, MANIFEST, judge) for t in res["parts"]]
            record.update({"part_found": parts, "part_secs": res["part_secs"]})
        print(f"[rep {REP}] {cond}: {record['n_found']}/{len(MANIFEST)} in {record['secs']}s ok={record['ok']}", flush=True)
        with open(out / "results.jsonl", "a") as f:
            f.write(json.dumps(record) + "\n")


if __name__ == "__main__":
    main()
