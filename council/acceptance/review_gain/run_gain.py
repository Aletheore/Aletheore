"""One replicate of the review-gain experiment, with the real Claude Code and Codex CLIs.

    python run_gain.py CORPUS_DIR OUT_DIR REPLICATE [condition ...]      conditions: claude-alone codex-alone split council (default: all four)

The same task in every condition: review 12 networkx modules that hold 24 injected bugs, report at most 25 findings (a cap, so spraying guesses does not pay).
  claude-alone / codex-alone  one agent, one read-only turn, all 12 files
  split                       three agents in parallel, four files each (a fixed split, no planner, no talking), their reports joined; at most 8 findings each
  council                     the real thing: a planner (Claude) is asked once; it splits the files across three executors (Codex, Claude, Codex) by mentioning
                              them, they work in parallel, each reports back with a mention, and the planner writes the final review. No person after the first message.
Settings come from the environment: GAIN_EXECUTORS (3 by default, up to 6) and GAIN_CAPS ("fixed": 25 findings for one agent and 8 per executor, the first run; "per-file":
1.5 findings per file everywhere, so the executors' caps add up to the single agent's, the large run). A finished condition is not run again if the script is started again;
the review text of every condition is saved before it is scored, so a judge failure costs a re-score, not a re-run.
Each review is scored by score.py against manifest.json. Also scored: each executor's own report, to see whether the planner dropped correct findings."""
import json
import os
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
import gainlib
from score import JudgeFailed, found_ids

CORPUS, OUT, REP = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), int(sys.argv[3])
CONDITIONS = sys.argv[4:] or ["claude-alone", "codex-alone", "split", "council"]
def _key():
    """The key to the encrypted answer key: the environment (GAIN_KEY), else the macOS keychain item gain-key. Never a file next to the corpus."""
    if os.environ.get("GAIN_KEY"):
        return os.environ["GAIN_KEY"]
    r = subprocess.run(["security", "find-generic-password", "-s", "gain-key", "-w"], capture_output=True, text=True)
    return r.stdout.strip() or None


MANIFEST = gainlib.load_manifest(CORPUS.parent, _key())["bugs"]
LOG: list = []   # (agent id, commands) for every agent run in the condition being run; audited and saved after it
FILES = sorted({b["file"] for b in MANIFEST})
LINES = {f: len((CORPUS / f).read_text().split("\n")) for f in FILES}
K = int(os.environ.get("GAIN_EXECUTORS", "3"))
CAPS = os.environ.get("GAIN_CAPS", "fixed")
TURN_TIMEOUT = int(os.environ.get("GAIN_TURN_TIMEOUT", "3000"))
CAP_ALONE = gainlib.cap_for(len(FILES), CAPS, 25)
ALL_EXECUTORS = [("alice-codex", "alice", CodexRunner), ("carol-claude", "carol", ClaudeRunner), ("dave-codex", "dave", CodexRunner),
                 ("erin-claude", "erin", ClaudeRunner), ("finn-codex", "finn", CodexRunner), ("gwen-claude", "gwen", ClaudeRunner)]
EXECUTORS = ALL_EXECUTORS[:K]
PLANNER = ("bob-claude", "bob", ClaudeRunner)


def cap_executor(n_files):
    return gainlib.cap_for(n_files, "per-file") if CAPS == "per-file" else 8


TASK = ("Review the networkx modules listed below for bugs: code that does not do what its name, docstring or the surrounding logic says it should. Some of "
        "these files were changed recently and the changes introduced subtle bugs (a wrong operator, a flipped condition, an off-by-one, a wrong constant). Read the "
        "code carefully; do not run it. Report at most {cap} findings, most confident first, as a numbered list. Each finding: the file, the function, the line "
        "number, and what is wrong and what it should be. Real defects only: no style remarks.\n\nFiles:\n{files}")


def groups():
    return gainlib.groups(FILES, LINES, K)


def clone(dest):
    subprocess.run(["git", "clone", "-q", str(CORPUS), str(dest)], check=True)
    subprocess.run(["git", "remote", "remove", "origin"], cwd=dest, check=True)   # a clone must not say where it came from: the folder beside the corpus held the answer key
    return dest


def empty_mcp(d, name="mcp.json"):
    p = Path(d) / name
    p.write_text('{"mcpServers": {}}')   # one file per caller: three threads writing and reading one file raced (an empty file was read mid-write)
    return p


def run_alone(kind):
    base = Path(tempfile.mkdtemp(prefix=f"gain-{kind}-"))
    wt = clone(base / "repo")
    runner = gainlib.Recording({"claude": ClaudeRunner, "codex": CodexRunner}[kind](), LOG, f"{kind}-alone")
    t0 = time.time()
    r = runner.run(TASK.format(cap=CAP_ALONE, files="\n".join(f"- {f}" for f in FILES)), wt, empty_mcp(base), {}, TURN_TIMEOUT, read_only=True)
    return {"text": r.final_text, "secs": round(time.time() - t0), "ok": r.ok}


def run_split():
    g = groups()
    base = Path(tempfile.mkdtemp(prefix="gain-split-"))
    t0 = time.time()

    def one(i):
        wt = clone(base / f"repo{i}")
        runner = gainlib.Recording(EXECUTORS[i][2](), LOG, EXECUTORS[i][0])
        r = runner.run(TASK.format(cap=cap_executor(len(g[i])), files="\n".join(f"- {f}" for f in g[i])), wt, empty_mcp(base, f"mcp{i}.json"), {}, TURN_TIMEOUT, read_only=True)
        return r.final_text, r.ok, round(time.time() - t0)

    with ThreadPoolExecutor(K) as ex:
        res = list(ex.map(one, range(K)))
    return {"text": "\n\n".join(t for t, _, _ in res), "secs": round(time.time() - t0), "ok": all(ok for _, ok, _ in res), "parts": [t for t, _, _ in res],
            "part_secs": [s for _, _, s in res], "files_by_group": g}


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
    exec_people = {p for _, p, _ in EXECUTORS}
    wake_from = {"bob": exec_people - {"alice"}, **{p: {"bob"} for p in exec_people}}
    daemons = {}
    for agent, person, kind in people:
        # the planner is woken once for the request and once for each executor's answer: its budget must cover K + 1 wakes (the default is 6 per ten minutes)
        extra = {"max_wakes": K + 6} if person == "bob" else {}
        daemons[person] = WorkerDaemon(store, rooms[person], person, {agent: gainlib.Recording(kind(), LOG, agent)}, bundles=bundles, task_timeout_s=TURN_TIMEOUT, claim_timeout_s=TURN_TIMEOUT + 900,
                                       wake_cooldown_s=0.0, max_wake_depth=4, wake_from=wake_from.get(person, set()), poll_interval_s=0.5, **extra)
    pid, p_person = PLANNER[0], PLANNER[1]
    ex_ids = [a for a, _, _ in EXECUTORS]

    def job(agent, text):
        store.append(EventDraft(rid, f"job:{agent}", "room", EventType.SYSTEM, "alice", None, None, {"subtype": "standing_job", "agent": agent, "text": text}))

    cap_text = ("at most one and a half findings for each file assigned to it (rounded up)" if CAPS == "per-file" else f"at most {cap_executor(0)} findings")
    job(pid, f"You coordinate a code review done by {K} executor agents: {', '.join(ex_ids)}. When you are asked to review, do not read the code yourself first. Split the "
             f"files into {K} groups of roughly equal size and send ONE message that mentions all {K} executors (write each as @name), gives each its own group of files "
             f"and tells each to report {cap_text} (file, function, line, what is wrong and what it should be; real defects only). Executors answer one at a time "
             f"and each answer wakes you: until all {K} have answered, reply with one short line saying who is still missing, and mention nobody. When all {K} have "
             f"answered, write the final review: a numbered list of at most {CAP_ALONE} findings, most confident first, combining the reports (you may read the code to check "
             "a finding before you include it, and drop one that is wrong), each with file, function, line and what is wrong. The last line must be exactly: REVIEW COMPLETE. "
             "The final review must not mention anyone with an @.")
    for a in ex_ids:
        job(a, f"You are a code reviewer. The message that woke you names {K} reviewers, each with its own files: review ONLY the files listed for you ({a}). Real defects only, "
               f"{cap_text}, each with file, function, line, and what is wrong and what it should be. End your reply with one line that mentions @{pid} so the planner sees it.")

    stop = threading.Event()
    threads = [threading.Thread(target=d.run_forever, args=(stop,), daemon=True) for d in daemons.values()]
    for t in threads:
        t.start()
    files_text = "; ".join(f"{', '.join(Path(f).name for f in grp)}" for grp in g)
    t0 = time.time()
    store.append(EventDraft(rid, "human:1", "room", EventType.QUESTION, "alice", None, None,
                            {"kind": "chat", "text": f"@{pid} review the networkx modules in this repo: {', '.join(FILES)}. Some were changed recently and have subtle bugs. Your {K} "
                                                     f"executors are {', '.join(ex_ids)}. I will not answer questions; work it out between you."}))

    def state():
        ev = read_all(store, rid)
        ex_answers = {e.sender_agent: e for e in ev if e.sender_agent in ex_ids and e.body.get("wake_for") and e.type is EventType.QUESTION}
        first_all = max((e.seq for e in ex_answers.values()), default=None) if len(ex_answers) == K else None
        final = next((e for e in ev if e.sender_agent == pid and first_all and e.seq > first_all and "REVIEW COMPLETE" in str(e.body.get("text", ""))), None)
        return ev, ex_answers, final

    last_n, last_change = 0, time.time()
    while time.time() - t0 < 150 * 60:
        ev, ex_answers, final = state()
        if final is not None:
            break
        if len(ev) != last_n:
            last_n, last_change = len(ev), time.time()
        elif time.time() - last_change > 55 * 60:
            break
        if any(e.body.get("subtype") in ("wake_failed", "worker_error") for e in ev) and time.time() - last_change > 240:
            break   # an agent failed and the room has gone quiet: the planner would wait forever for its answer
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


def healthy(kind_cls):
    d = Path(tempfile.mkdtemp(prefix="gain-health-"))
    subprocess.run("git init -q && git -c user.name=t -c user.email=t@t commit -q --allow-empty -m init", shell=True, cwd=d, check=True)   # Codex only runs inside a git repository
    r = kind_cls().run("Reply with the single word: ok", d, empty_mcp(d), {}, 240, read_only=True)
    return r.ok and bool(r.final_text.strip()), r.final_text.strip()[:200]


def wait_until_healthy(max_wait_s=6 * 3600):
    """Both agent CLIs must answer a trivial question before a condition starts. A usage limit or sign-in problem then waits (growing pauses, up to six hours)
    instead of burning the condition: the first large run lost three replicates to Claude failing instantly for hours."""
    t0, delay = time.time(), 60
    while True:
        bad = []
        for name, cls in (("claude", ClaudeRunner), ("codex", CodexRunner)):
            ok, note = healthy(cls)
            if not ok:
                bad.append(f"{name}: {note}")
        if not bad:
            return
        if time.time() - t0 > max_wait_s:
            raise SystemExit(f"agents still not answering after {max_wait_s}s: {bad}")
        print(f"  waiting for the agent CLIs ({'; '.join(bad)}); trying again in {delay}s", flush=True)
        time.sleep(delay)
        delay = min(delay * 2, 900)


def score(text, judge, label):
    """(found ids, judge_failed). A judge that cannot answer is recorded as a failure, never as zero found."""
    if not text.strip():
        return [], False
    try:
        return found_ids(text, MANIFEST, judge), False
    except JudgeFailed:
        print(f"  judge failed for {label}: the review text is saved; run again to score it", flush=True)
        return None, True


def last_record(out, cond):
    f = out / "results.jsonl"
    rec = None
    if f.exists():
        for line in f.read_text().splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("condition") == cond:
                rec = r
    return rec


def load_saved(out, cond, rec):
    """A run that finished fine but could not be scored: rebuild its result from the saved texts, so it is scored again and not run again."""
    text = (out / f"{cond}.txt").read_text() if (out / f"{cond}.txt").exists() else ""
    if not text.strip():
        return None
    res = {"text": text, "secs": rec["secs"], "ok": True}
    if cond == "split":
        files = sorted(out.glob("split-part*.txt"), key=lambda p: int(p.stem.replace("split-part", "")))
        res.update({"parts": [f.read_text() for f in files], "part_secs": rec.get("part_secs", []), "files_by_group": rec.get("files_by_group", [])})
    if cond == "council":
        res.update({"executor_reports": {f.stem.replace("council-executor-", ""): f.read_text() for f in out.glob("council-executor-*.txt")},
                    "planner_messages": [], "notices": rec.get("notices", []), "person_messages": rec.get("person_messages"), "planner_wakes": rec.get("planner_wakes"),
                    "files_by_group": rec.get("files_by_group", [])})
    return res


def main():
    out = OUT / f"rep{REP}"
    out.mkdir(parents=True, exist_ok=True)
    judge = ClaudeRunner()
    runners = {"claude-alone": lambda: run_alone("claude"), "codex-alone": lambda: run_alone("codex"), "split": run_split, "council": run_council}
    for cond in CONDITIONS:
        if gainlib.already_done(out, cond):
            print(f"[rep {REP}] {cond}: already done, skipped", flush=True)
            continue
        prev = last_record(out, cond)
        res = load_saved(out, cond, prev) if prev and prev.get("ok") and prev.get("judge_failed") else None
        if res is not None:
            print(f"[rep {REP}] {cond}: scoring the saved review again (the run itself was fine)", flush=True)
            wait_until_healthy()
        else:
            for attempt in range(1, 4):
                wait_until_healthy()
                print(f"[rep {REP}] {cond} ... (attempt {attempt})", flush=True)
                LOG.clear()
                res = runners[cond]()
                if res["ok"] and res["text"].strip():
                    break
                print(f"[rep {REP}] {cond}: failed ({res['secs']}s); {'trying again' if attempt < 3 else 'giving up'}", flush=True)
                with open(out / "results.jsonl", "a") as f:   # failures stay on record
                    f.write(json.dumps({"condition": cond, "rep": REP, "secs": res["secs"], "ok": False, "n_found": None, "found": None, "judge_failed": False,
                                        "attempt": attempt, "notices": res.get("notices", [])}) + "\n")
        # the review text is saved first, so a scoring problem never costs a run
        (out / f"{cond}.txt").write_text(res["text"])
        if LOG:   # what every agent ran or read, and whether any of it looks like a search for an oracle (a condition with flags is not to be reported as a result)
            (out / f"{cond}-commands.json").write_text(json.dumps([{"agent": a, "commands": c} for a, c in LOG]))
        flags = [(a, c, why) for a, cmds in LOG for c, why in gainlib.audit_commands(cmds)]
        if flags:
            print(f"  AUDIT: {len(flags)} command(s) look like oracle-seeking in {cond}, for example: {flags[0][2]}: {flags[0][1][:120]}", flush=True)
        record = {"condition": cond, "rep": REP, "secs": res["secs"], "ok": res["ok"], "audit_flags": len(flags), "audit_examples": [[a, c[:200], why] for a, c, why in flags[:5]]}
        if cond == "council":
            for a, t in res["executor_reports"].items():
                (out / f"council-executor-{a}.txt").write_text(t)
            (out / "council-planner-messages.txt").write_text("\n\n=====\n\n".join(res["planner_messages"]))
            parts, failed = {}, False
            found, f1 = score(res["text"], judge, "the final review")
            failed = f1
            for a, t in res["executor_reports"].items():
                parts[a], f2 = score(t, judge, a)
                failed = failed or f2
            ok_parts = [v for v in parts.values() if v is not None]
            record.update({"found": found, "n_found": len(found) if found is not None else None, "executor_found": parts,
                           "executor_union": sorted({i for v in ok_parts for i in v}), "notices": res["notices"], "person_messages": res["person_messages"],
                           "planner_wakes": res["planner_wakes"], "files_by_group": res["files_by_group"], "judge_failed": failed})
        elif cond == "split":
            for i, t in enumerate(res["parts"]):
                (out / f"split-part{i}.txt").write_text(t)
            parts, failed = [], False
            for i, t in enumerate(res["parts"]):
                v, f = score(t, judge, f"split part {i}")
                parts.append(v)
                failed = failed or f
            ok_parts = [v for v in parts if v is not None]
            union = sorted({i for v in ok_parts for i in v})
            record.update({"found": None if failed else union, "n_found": None if failed else len(union), "part_found": parts, "part_secs": res["part_secs"],
                           "files_by_group": res["files_by_group"], "judge_failed": failed})
        else:
            found, failed = score(res["text"], judge, cond)
            record.update({"found": found, "n_found": len(found) if found is not None else None, "judge_failed": failed})
        print(f"[rep {REP}] {cond}: {record['n_found']}/{len(MANIFEST)} in {record['secs']}s ok={record['ok']} judge_failed={record['judge_failed']}", flush=True)
        with open(out / "results.jsonl", "a") as f:
            f.write(json.dumps(record) + "\n")


if __name__ == "__main__":
    main()
