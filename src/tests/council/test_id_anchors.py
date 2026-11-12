"""Ids that become file names or git refs must match exactly: `$` would also accept a trailing newline."""

import pytest

from aletheore.council import bundle_store, bundle_transport, protocol, server


@pytest.mark.parametrize("pattern,good", [
    (bundle_store.ROOM_RE, "room-1a2b"), (bundle_store.SHA_RE, "a" * 64), (server._SHA_RE, "b" * 64),
    (bundle_transport._SHA_RE, "c" * 40), (bundle_transport._PARTICIPANT_RE, "alice"),
    (bundle_transport._BRANCH_RE, "council/room-1/alice-codex"), (protocol._SHA_RE, "d" * 40), (protocol._THREAD_RE, "t:1"),
])
def test_an_id_with_a_trailing_newline_is_refused(pattern, good):
    assert pattern.match(good)
    assert not pattern.match(good + "\n")
