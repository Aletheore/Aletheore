"""A planner agent and an executor agent review one file together, with no person in between. Real Claude Code and Codex CLIs.

    python real_review_check.py [planner executor [easy|hard [out-folder]]]      (default: claude codex easy; the other pairing is: codex claude)

`easy` is one 30-line file with five obvious bugs. `hard` is a four-file project with seven bugs, several of which only show when two files are read together
(a caller that does not undo a reservation when a later one fails, a report that ignores what an order's discount was, ...). With an out-folder, each agent's
final text is saved there for reading, and one machine-readable RESULT line is printed so repeated runs can be added up.

One person message starts it: "plan a review of inventory.py". After that the two agents talk only through the room: the planner (standing job: plan, delegate,
write the verdict) hands the executor concrete questions with an @mention, the executor reads the file and reports back with an @mention, the planner writes
the final review. The file has five planted bugs, so the result can be scored. The same file is then reviewed by each agent alone, as a baseline.

Checks: it finishes without a second person message; the executor was woken by the planner and the planner by the executor; no limit notices; and the
planted bugs found (the numbers are printed for pipeline and baselines, to compare). Uses your own agent sign-ins, so it counts against your usage.
"""
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from aletheore.council.bundle_store import LocalBundleStore
from aletheore.council.daemon import WorkerDaemon
from aletheore.council.protocol import EventDraft, EventType
from aletheore.council.room import Room
from aletheore.council.runners import ClaudeRunner, CodexRunner
from aletheore.council.sqlite_store import SqliteRoomStore
from aletheore.council.state import Area
from aletheore.council.store import read_all

PLANNER_KIND, EXECUTOR_KIND = (sys.argv[1], sys.argv[2]) if len(sys.argv) >= 3 else ("claude", "codex")
PROJECT = sys.argv[3] if len(sys.argv) >= 4 else "easy"
OUT = Path(sys.argv[4]) if len(sys.argv) >= 5 else None
RUNNERS = {"claude": ClaudeRunner, "codex": CodexRunner}
TURN_TIMEOUT = 300
MAX_SECONDS = 15 * 60

EASY_FILE = '''"""Small inventory helpers."""
import sqlite3


def total_price(prices):
    total = 0
    for i in range(len(prices) - 1):
        total += prices[i]
    return total


def average(values):
    return sum(values) / len(values)


def add_tag(tag, tags=[]):
    tags.append(tag)
    return tags


def find_item(conn, name):
    cur = conn.execute(f"SELECT * FROM items WHERE name = '{name}'")
    return cur.fetchall()


def read_config(path):
    try:
        return open(path).read()
    except:
        return ""
'''
EASY_BUGS = {
    "1 loop skips the last price": r"range\(len|off[- ]by[- ]one|skips? the last|last (price|item|element)|len\(prices\)\s*-\s*1",
    "2 average of an empty list": r"empty|zero\s*division|division by zero|len\(values\)",
    "3 mutable default argument": r"mutable default|default argument|shared (between|across)|tags\s*=\s*\[\]",
    "4 SQL injection": r"sql injection|injection|parameteri[sz]ed|string formatting|f-string",
    "5 file never closed / bare except": r"never closed|not closed|not (being )?closed|with open|bare except|bare `?except|except:|context manager|resource leak|leak",
}

HARD_FILES = {
    "pricing.py": '''"""Prices and discounts. All money is in integer cents."""

TAX_RATE_PERCENT = 8


def apply_discount(cents, percent):
    """Return `cents` reduced by `percent`."""
    return cents - cents * percent // 100


def add_tax(cents):
    """Return `cents` plus sales tax."""
    return cents + cents * TAX_RATE_PERCENT / 100
''',
    "inventory.py": '''"""Stock levels, as a dict of sku -> units on hand."""


class OutOfStock(Exception):
    pass


def reserve(stock, sku, qty):
    have = stock.get(sku, 0)
    if have < qty:
        raise OutOfStock(sku)
    stock[sku] = have - qty


def restock(stock, sku, qty):
    stock[sku] = stock.get(sku, 0) + qty
''',
    "orders.py": '''"""Placing and pricing orders. A line is (sku, qty, unit_cents)."""
from inventory import reserve
from pricing import add_tax, apply_discount


def order_total(lines, discount_percent=0):
    subtotal = 0
    for sku, qty, unit in lines:
        subtotal += qty * unit
    return add_tax(apply_discount(subtotal, discount_percent))


def place_order(stock, lines, discount_percent=0):
    for sku, qty, unit in lines:
        reserve(stock, sku, qty)
    return order_total(lines, discount_percent)
''',
    "report.py": '''"""End of day report."""
from orders import order_total


def format_money(cents):
    return f"${cents / 100:.2f}"


def daily_report(orders):
    """`orders` is a list of (lines, discount_percent) pairs, as placed during the day."""
    total = 0
    for lines, discount_percent in orders:
        total += order_total(lines)
    avg = total / len(orders)
    return f"orders={len(orders)} total={format_money(total)} avg={format_money(avg)}"
''',
}
_G = r"[^\n]{0,350}"
HARD_BUGS = {
    "1 add_tax returns a float, not integer cents": rf"add_tax{_G}(float|integer|round|not an int|int\b)|(float|round|fraction){_G}add_tax|TAX_RATE_PERCENT\s*/\s*100",
    "2 place_order does not undo earlier reservations when a later line fails": rf"(place_order|reserve){_G}(roll ?back|undo|partial|atomic|already (been )?reserved|earlier (line|reserv)|leak|restore|release)",
    "3 restock accepts negative quantities": rf"restock{_G}(negative|validat|check|<\s*0|positive)",
    "4 daily_report divides by zero with no orders": rf"(daily_report|avg|average|len\(orders\)){_G}(zero|empty|no orders|division)",
    "5 daily_report ignores each order's discount": rf"(daily_report|report){_G}(discount_percent|discount){_G}(ignor|not (passed|used|applied|included|forwarded)|never|unused|without|omit|drops?|missing)|(ignor|never|unused|omit|drops?){_G}discount",
    "6 apply_discount does not validate the percent": rf"apply_discount{_G}(validat|range|over 100|above 100|exceed|negative|>\s*100|outside|bound|clamp|0.{1,6}100)",
    "7 reserve accepts zero or negative quantities": rf"reserve{_G}(negative|zero|non-?positive|<=?\s*0|positive)|(negative|zero|non-?positive){_G}(qty|quantit){_G}(reserve|place_order)",
}
REQUEST = {
    "easy": "plan a review of inventory.py",
    "hard": "plan a review of this project (pricing.py, inventory.py, orders.py, report.py), including how the files call each other",
}
PLANNER_EXTRA = {
    "easy": "",
    "hard": " You may read the code yourself to double-check what the executor reports before it goes into the final review, and you may add a defect you find that way.",
}
EXECUTOR_EXTRA = {"easy": "", "hard": " Read every file named and follow calls from one file into another; say which file and function each defect is in."}
FILES = {"easy": {"inventory.py": EASY_FILE}, "hard": HARD_FILES}[PROJECT]
BUGS = {"easy": EASY_BUGS, "hard": HARD_BUGS}[PROJECT]
TARGET = {"easy": "inventory.py", "hard": "the project"}[PROJECT]


def score(text):
    return {name: bool(re.search(rx, text, re.I)) for name, rx in BUGS.items()}


def git(repo, *a):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + name + (f"   [{detail}]" if detail and not ok else ""), flush=True)


base = Path(tempfile.mkdtemp(prefix="real-review-"))
repo, clone = base / "alice-repo", base / "bob-repo"
repo.mkdir()
git(repo, "init", "-q", "-b", "main")
for name, text in FILES.items():
    (repo / name).write_text(text)
(repo / "README.txt").write_text("a small project\n")
git(repo, "add", "-A")
git(repo, "commit", "-q", "-m", "init")
git(base, "clone", "-q", str(repo), str(clone))

# alice (the host) owns the executor's area, bob owns the planner's
store = SqliteRoomStore(base / "room.db")
alice_room = Room.create(store, repo, "alice", base / "state_alice")
EXEC_ID, PLAN_ID = f"alice-{EXECUTOR_KIND}", f"bob-{PLANNER_KIND}"
alice_room.define_area(Area("exec", ["**"], "alice", EXEC_ID))
alice_room.define_area(Area("plan", ["**"], "bob", PLAN_ID))
bob_room = Room.open_remote(store, alice_room.room_id, clone, base / "state_bob")
RID = alice_room.room_id
bundles = LocalBundleStore(base / "bundles")
kw = dict(bundles=bundles, task_timeout_s=TURN_TIMEOUT, claim_timeout_s=TURN_TIMEOUT + 600, wake_cooldown_s=0.0, max_wake_depth=4)
executor_runner, planner_runner = RUNNERS[EXECUTOR_KIND](), RUNNERS[PLANNER_KIND]()
alice = WorkerDaemon(store, alice_room, "alice", {EXEC_ID: executor_runner}, wake_from={"bob"}, **kw)
bob = WorkerDaemon(store, bob_room, "bob", {PLAN_ID: planner_runner}, **kw)


def job(agent, text):
    store.append(EventDraft(RID, f"job:{agent}", "room", EventType.SYSTEM, "alice", None, None, {"subtype": "standing_job", "agent": agent, "text": text}))


job(PLAN_ID, f"You are the planner of a code review. When asked to review a file, do NOT review it yourself first. Send the executor, {EXEC_ID}, one message that "
             f"mentions @{EXEC_ID} and says which file to read and the three or four specific kinds of defect to look for. When the executor answers, write the final "
             "review yourself: a numbered list of the defects that are really there, with line numbers, and the last line must be exactly: REVIEW COMPLETE. "
             "Only write REVIEW COMPLETE once the executor has answered; if you could not reach the executor, say so and do not write it. "
             "The final review must not mention anyone with an @." + PLANNER_EXTRA[PROJECT])
job(EXEC_ID, f"You are the executor of a code review. Read the file you are asked about, check what you are asked to check, and answer with a numbered list of concrete "
             f"defects with line numbers (say 'none' if there are none). End your answer with one line that mentions @{PLAN_ID} so the planner sees it." + EXECUTOR_EXTRA[PROJECT])


def events():
    return read_all(store, RID)


started = time.time()
store.append(EventDraft(RID, "human:1", "room", EventType.QUESTION, "alice", None, None,
                        {"kind": "chat", "text": f"@{PLAN_ID} {REQUEST[PROJECT]} and have the executor agent ({EXEC_ID}) do the reading. I will not answer questions; "
                                                 "work it out between you."}))


def finished():
    """The planner wrote the completion line AFTER the executor had answered (a planner that could not reach the executor and wrote it anyway does not count)."""
    ev = events()
    executor_seq = min((e.seq for e in ev if e.sender_agent == EXEC_ID and e.body.get("wake_for")), default=None)
    return executor_seq is not None and any(e.sender_agent == PLAN_ID and e.seq > executor_seq and "REVIEW COMPLETE" in str(e.body.get("text", "")) for e in ev)


IDLE_STOP_S = 120   # nobody has said anything for this long and no review: the chain is over (stuck), do not wait out the clock
last_count, last_change = 0, time.time()
while time.time() - started < MAX_SECONDS and not finished():
    alice.run_once()
    bob.run_once()
    n = len(events())
    if n != last_count:
        last_count, last_change = n, time.time()
    elif time.time() - last_change > IDLE_STOP_S:
        break
    time.sleep(0.5)
elapsed = time.time() - started
ev = events()
people = [e for e in ev if e.type is EventType.QUESTION and e.sender_agent is None and e.body.get("kind") == "chat"]
claims = [e for e in ev if e.body.get("subtype") == "wake_claimed"]
planner_wakes, executor_wakes = [e for e in claims if e.sender_agent == PLAN_ID], [e for e in claims if e.sender_agent == EXEC_ID]
notices = [e for e in ev if e.body.get("subtype") in ("wake_skipped", "wake_failed", "worker_error")]
final = next((e for e in reversed(ev) if e.sender_agent == PLAN_ID and "REVIEW COMPLETE" in str(e.body.get("text", ""))), None) if finished() else None
final_text = str(final.body["text"]) if final else ""
pipeline = score(final_text)

print("\n--- room log ---")
for e in ev:
    who = e.sender_participant + (f"/{e.sender_agent}" if e.sender_agent else "")
    if e.type is EventType.QUESTION or e.body.get("subtype") in ("wake_skipped", "wake_failed", "worker_error"):
        print(f"#{e.seq} {who} (depth {e.body.get('wake_depth', 0)}): {str(e.body.get('text') or e.body.get('note'))[:1200]}\n")

check("the review finished (the planner wrote REVIEW COMPLETE)", final is not None, f"{elapsed:.0f}s")
check("only one person message was sent: the agents did the rest alone", len(people) == 1, f"{len(people)} person messages")
check("the planner handed work to the executor (the executor was woken)", len(executor_wakes) >= 1)
check("the executor's findings woke the planner again (a real round trip)", len(planner_wakes) >= 2, f"planner wakes: {len(planner_wakes)}")
check("no failure, skip or worker-error notices", not notices, "; ".join(str(e.body.get('note')) for e in notices)[:200])
check("the final review mentions no one (so the chain ends by itself)", final is not None and "@" not in final_text)
print(f"\nplanner wakes {len(planner_wakes)}, executor wakes {len(executor_wakes)}, time {elapsed:.0f}s, messages by agents {len([e for e in ev if e.body.get('wake_for')])}")
print(f"PIPELINE ({PLANNER_KIND} plans, {EXECUTOR_KIND} executes): {sum(pipeline.values())}/{len(BUGS)} planted bugs in the final review")
for name, hit in pipeline.items():
    print(f"   {'found ' if hit else 'MISSED'} {name}")

# baselines: each agent alone, one read-only turn, same file
print("\n--- baselines: one agent alone ---")
alone = {}
if OUT:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "pipeline-final.txt").write_text(final_text)
    (OUT / "log.txt").write_text("\n\n".join(f"#{e.seq} {e.sender_participant}/{e.sender_agent} depth {e.body.get('wake_depth', 0)}: {e.body.get('text') or e.body.get('note')}" for e in ev if e.type is EventType.QUESTION or e.body.get("subtype") in ("wake_skipped", "wake_failed", "worker_error")))
PROMPT = f"Review {TARGET} in this project. List every real defect, with the file and line numbers, as a numbered list. Plain text."
for kind, runner, room in ((PLANNER_KIND, planner_runner, bob_room), (EXECUTOR_KIND, executor_runner, alice_room)):
    agent = PLAN_ID if room is bob_room else EXEC_ID
    wt, _ = room._worktree(agent)
    t0 = time.time()
    r = runner.run(PROMPT, wt, room._mcp_config(room.host if room is alice_room else "bob", agent), room._identity("alice" if room is alice_room else "bob", agent),
                   TURN_TIMEOUT, read_only=True)
    s = score(r.final_text)
    print(f"ALONE ({kind}): {sum(s.values())}/{len(BUGS)} in {time.time() - t0:.0f}s  ok={r.ok}   missed: {[n for n, h in s.items() if not h] or 'none'}")
    alone[agent] = {"found": sum(s.values()), "secs": round(time.time() - t0), "ok": r.ok, "missed": [n for n, h in s.items() if not h]}
    if OUT:
        (OUT / f"alone-{agent}.txt").write_text(r.final_text)

import json
print("RESULT " + json.dumps({"project": PROJECT, "planner": PLANNER_KIND, "executor": EXECUTOR_KIND, "pipeline_found": sum(pipeline.values()), "of": len(BUGS),
                              "pipeline_missed": [n for n, h in pipeline.items() if not h], "pipeline_secs": round(elapsed), "planner_wakes": len(planner_wakes),
                              "executor_wakes": len(executor_wakes), "finished": final is not None, "person_messages": len(people), "notices": len(notices),
                              "alone": alone}))
alice_room.close_brokers()
bob_room.close_brokers()
print(f"\n{sum(results)} of {len(results)} checks passed   (scratch folder: {base})")
sys.exit(0 if results and all(results) else 1)
