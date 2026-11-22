"""A host's `council wait` hears about a person waiting to join, through the real server code (not a stand-in list), once, and only the host does."""
import json

from tests.council.test_invites_cli import cli, w  # noqa: F401


def invite_link(w):
    w.make_host()
    out = w.run("invite-link", "--room", "r1", "--me", "hosty")
    return next(word for word in out.output.split() if word.startswith("http"))


def test_the_host_hears_about_a_waiting_guest_once_and_not_after_approval(w):
    link = invite_link(w)
    seen = {}

    def while_guest_waits(_seconds):
        if seen:
            return
        w.as_person("host")
        first = w.run("wait", "--room", "r1", "--me", "hosty", "--timeout", "2", "--poll", "0.1", "--json")
        seen["first"] = first
        seen["again"] = w.run("wait", "--room", "r1", "--me", "hosty", "--timeout", "0.3", "--poll", "0.1", "--json")
        pending = cli.open_store().list_join_requests("r1")
        seen["request_id"] = pending[0]["request_id"]
        seen["words"] = pending[0]["words"]
        assert w.run("approve-join", "--room", "r1", "--me", "hosty", pending[0]["request_id"]).exit_code == 0
        seen["after"] = w.run("wait", "--room", "r1", "--me", "hosty", "--timeout", "0.3", "--poll", "0.1", "--json", "--peek")
        w.as_person("guest")

    w.as_person("guest")
    w.mp.setattr(cli.time, "sleep", while_guest_waits)
    assert w.run("join-link", link, "--handle", "guesty").exit_code == 0

    assert seen["first"].exit_code == 0, seen["first"].output
    got = json.loads(seen["first"].output)
    assert [e["body"]["subtype"] for e in got] == ["join_request_pending"]
    assert got[0]["body"]["handle"] == "guesty" and got[0]["body"]["request_id"] == seen["request_id"] and got[0]["body"]["words"] == seen["words"]
    assert seen["again"].exit_code == 3                       # nothing new: the same request is not reported twice
    assert seen["after"].exit_code == 3                       # approved: nothing left to report


def test_a_guest_room_member_hears_nothing_about_other_peoples_join_requests(w):
    link = invite_link(w)
    seen = {}

    def while_guest_waits(_seconds):
        if seen:
            return
        w.as_person("host")
        seen["ok"] = True
        pending = cli.open_store().list_join_requests("r1")
        w.run("approve-join", "--room", "r1", "--me", "hosty", pending[0]["request_id"])
        w.as_person("guest")

    w.as_person("guest")
    w.mp.setattr(cli.time, "sleep", while_guest_waits)
    assert w.run("join-link", link, "--handle", "guesty").exit_code == 0
    # the guest is now a member; a second invitation's pending request must not show up for them
    w.as_person("host")
    second = w.run("invite-link", "--room", "r1", "--me", "hosty")
    assert second.exit_code == 0
    w.as_person("guest")
    out = w.run("wait", "--room", "r1", "--me", "guesty", "--timeout", "0.3", "--poll", "0.1", "--json")
    assert out.exit_code == 3
