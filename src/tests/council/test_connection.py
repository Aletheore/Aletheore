from aletheore.council.connection import ConnectionMonitor


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_stages_reconnecting_unreachable_away_at_30s_and_5min():
    c = Clock()
    m = ConnectionMonitor(c, retry_s=2)
    assert m.state().kind == "live"
    m.failed("network")
    for secs, kind in ((1, "reconnecting"), (29, "reconnecting"), (30, "unreachable"), (299, "unreachable"), (300, "away")):
        c.t = 100.0 + secs
        assert m.state().kind == kind, secs
    m.ok()
    assert m.state().kind == "live" and m.recovered  # a long outage leaves a note to say it ended


def test_a_short_blip_leaves_no_returned_notice_and_countdown_runs_down():
    c = Clock()
    m = ConnectionMonitor(c, retry_s=4)
    m.failed()
    c.t += 1
    assert m.state().next_try_s == 3
    c.t += 5
    assert m.state().next_try_s == 0
    m.ok()
    assert not m.recovered
