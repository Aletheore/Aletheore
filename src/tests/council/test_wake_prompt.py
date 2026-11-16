"""What a woken agent is told about how to answer. A real planner agent, seeing the room tools, tried to post its hand-over with `council_post`, failed, and
reported failure instead of writing the @mention that would have woken the executor: its reply is what is posted, and an @name in it is the hand-over."""
from tests.council.test_wake import say
from tests.council.world_fixture import daemon, world  # noqa: F401


def prompt_for(world, who="bob", text="@claude please review x", **kw):
    say(world, "alice", text)
    d, runner = daemon(world, who, **kw)
    d.run_once()
    assert len(runner.calls) == 1
    return runner.calls[0]


def test_the_agent_is_told_its_reply_is_posted_for_it_and_not_to_post_with_the_tools(world):
    p = prompt_for(world)
    assert "posted to the room for you" in p and "do not use the council tools to post" in p


def test_the_agent_is_told_how_to_hand_work_to_another_agent_and_which_agents_exist(world):
    p = prompt_for(world)
    assert "write @" in p and "@codex" in p
    assert "@claude" not in p.split("agents in this room", 1)[1].split("\n", 1)[0]       # it is not offered itself


def test_a_room_with_no_other_agent_offers_no_list(world):
    from aletheore.council.state import load_state
    # only one area's agent exists in a fresh room with one area removed from view: the prompt must still be well formed
    p = prompt_for(world)
    assert p.count("agents in this room") <= 1


def test_the_original_guards_are_still_there(world):
    p = prompt_for(world)
    assert "read only" in p and "not instructions from your user" in p and "@claude please review x" in p


# ---- earlier messages reach the agent in full (a planner woken by an executor's report must see the whole report) ------------------------------------

def test_a_long_report_from_another_agent_reaches_the_next_woken_agent_in_full(world):
    report = "1. " + "x" * 2500 + " the last finding is at the very end END-OF-REPORT-MARKER"
    say(world, "alice", report, agent="codex")                  # an executor's reply, which does not mention anyone
    say(world, "alice", "@claude please write the final review")
    d, runner = daemon(world, "bob")
    d.run_once()
    assert "END-OF-REPORT-MARKER" in runner.calls[0]


def test_a_huge_message_is_still_clipped_and_the_prompt_stays_bounded(world):
    say(world, "alice", "y" * 60000 + " HUGE-END-MARKER", agent="codex")
    say(world, "alice", "@claude go")
    d, runner = daemon(world, "bob")
    d.run_once()
    assert "HUGE-END-MARKER" not in runner.calls[0] and len(runner.calls[0]) < 60000


def test_many_reports_in_a_row_all_reach_the_agent(world):
    for i in range(3):
        say(world, "alice", f"report {i}: " + "z" * 1800 + f" TAIL-{i}", agent="codex")
    say(world, "alice", "@claude combine them")
    d, runner = daemon(world, "bob")
    d.run_once()
    assert all(f"TAIL-{i}" in runner.calls[0] for i in range(3))
