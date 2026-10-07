import os
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from aletheore.git_intel.graph_store import CommitTouch, GraphSnapshot
from aletheore.git_intel.incremental import (
    MAX_CO_CHANGE_PARTNERS_TRACKED,
    RECENT_COMMITS_PER_FILE,
    GitLogStreamError,
    compute_repo_key,
    fold,
    parse_commit_date,
    stream_commit_touches,
)


def run(repo: Path, *args: str):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def commit(repo: Path, message: str, date_str: str):
    env = os.environ.copy()
    env["GIT_AUTHOR_DATE"] = date_str
    env["GIT_COMMITTER_DATE"] = date_str
    subprocess.run(["git", "commit", "-m", message], cwd=repo, check=True, capture_output=True, env=env)


def head_sha(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    run(repo, "init", "-b", "main")
    run(repo, "config", "user.email", "a@example.com")
    run(repo, "config", "user.name", "Alice")
    return repo


def _touch(sha, name, email, date_str, files, renames=()):
    return CommitTouch(sha, name, email, datetime.fromisoformat(date_str), files, renames=renames)


# --- parse_commit_date: real git history has commits with genuinely
# malformed author/committer dates, not just unusual-but-valid ones ---


def test_parse_commit_date_parses_a_normal_iso_strict_date():
    assert parse_commit_date("2026-06-01T00:00:00+00:00") == datetime.fromisoformat(
        "2026-06-01T00:00:00+00:00"
    )


def test_parse_commit_date_recovers_the_real_date_from_a_corrupted_offset():
    # Real, observed data: a historical commit in psf/requests' upstream
    # history has this exact author date. '+518:00' is not a valid UTC
    # offset (max is +/-14:00) - datetime.fromisoformat rejects it outright,
    # which crashed the whole git-history analysis for this repo before this
    # fix. The date/time itself (everything before the offset) is genuine
    # and worth keeping, so this recovers it as UTC rather than discarding
    # the commit or crashing.
    result = parse_commit_date("2011-09-08T02:38:50+518:00")
    assert result == datetime(2011, 9, 8, 2, 38, 50, tzinfo=timezone.utc)


def test_parse_commit_date_falls_back_to_epoch_for_a_totally_unparseable_string():
    # Not just a bad offset - the date/time portion itself is garbage, so
    # even the first-19-characters fallback can't recover a real date.
    result = parse_commit_date("not-a-date-at-all")
    assert result == datetime(1970, 1, 1, tzinfo=timezone.utc)


# --- stream_commit_touches: reads real git output, never buffers it whole ---


def test_stream_commit_touches_reads_sha_author_date_and_files(tmp_path):
    repo = init_repo(tmp_path)
    (repo / "a.txt").write_text("1")
    run(repo, "add", "a.txt")
    commit(repo, "first", "2026-06-01T00:00:00+00:00")

    touches = list(stream_commit_touches(repo, "HEAD"))
    assert len(touches) == 1
    touch = touches[0]
    assert touch.sha == head_sha(repo)
    assert touch.author_name == "Alice"
    assert touch.author_email == "a@example.com"
    assert touch.committed_at == datetime.fromisoformat("2026-06-01T00:00:00+00:00")
    assert touch.files == ("a.txt",)


def test_stream_commit_touches_orders_newest_first(tmp_path):
    repo = init_repo(tmp_path)
    (repo / "a.txt").write_text("1")
    run(repo, "add", "a.txt")
    commit(repo, "first", "2026-06-01T00:00:00+00:00")
    (repo / "a.txt").write_text("2")
    run(repo, "add", "a.txt")
    commit(repo, "second", "2026-06-02T00:00:00+00:00")

    touches = list(stream_commit_touches(repo, "HEAD"))
    assert len(touches) == 2
    assert touches[0].committed_at > touches[1].committed_at


def test_stream_commit_touches_respects_rev_range(tmp_path):
    repo = init_repo(tmp_path)
    (repo / "a.txt").write_text("1")
    run(repo, "add", "a.txt")
    commit(repo, "first", "2026-06-01T00:00:00+00:00")
    first_sha = head_sha(repo)
    (repo / "b.txt").write_text("1")
    run(repo, "add", "b.txt")
    commit(repo, "second", "2026-06-02T00:00:00+00:00")

    touches = list(stream_commit_touches(repo, f"{first_sha}..HEAD"))
    assert len(touches) == 1
    assert touches[0].files == ("b.txt",)


def test_stream_commit_touches_respects_max_commits(tmp_path):
    repo = init_repo(tmp_path)
    for i in range(5):
        (repo / "a.txt").write_text(str(i))
        run(repo, "add", "a.txt")
        commit(repo, f"commit {i}", f"2026-06-0{i + 1}T00:00:00+00:00")

    touches = list(stream_commit_touches(repo, "HEAD", max_commits=2))
    assert len(touches) == 2
    # -n limits to the newest N, matching git's own semantics.
    assert touches[0].committed_at > touches[1].committed_at


def test_stream_commit_touches_raises_on_bad_rev_range(tmp_path):
    repo = init_repo(tmp_path)
    (repo / "a.txt").write_text("1")
    run(repo, "add", "a.txt")
    commit(repo, "first", "2026-06-01T00:00:00+00:00")

    with pytest.raises(GitLogStreamError):
        list(stream_commit_touches(repo, "not-a-real-ref"))


def test_stream_commit_touches_handles_merge_commit_with_no_file_changes(tmp_path):
    # A --no-ff merge that isn't resolving any conflict produces zero
    # --name-only lines for that commit - the parser must not silently drop
    # it or merge its (absent) files into the next commit's list.
    repo = init_repo(tmp_path)
    (repo / "a.txt").write_text("1")
    run(repo, "add", "a.txt")
    commit(repo, "first", "2026-06-01T00:00:00+00:00")
    run(repo, "checkout", "-b", "feature")
    (repo / "b.txt").write_text("1")
    run(repo, "add", "b.txt")
    commit(repo, "feature commit", "2026-06-02T00:00:00+00:00")
    run(repo, "checkout", "main")
    run(repo, "merge", "feature", "--no-ff", "--no-edit")

    touches = list(stream_commit_touches(repo, "HEAD"))
    assert len(touches) == 3


def test_stream_commit_touches_survives_a_control_character_in_the_author_name(tmp_path):
    # Real bug found via audit: the intra-line field parser used to split on
    # `\x1f` (unit separator), on the assumption a real commit would never
    # contain a raw control byte in that position. GIT_AUTHOR_NAME accepts
    # arbitrary bytes though, and a name containing a literal `\x1f`
    # (confirmed directly against the pre-fix parser) shifted every field
    # after it: author_email became a fragment of the author name, the
    # commit's real date landed in the wrong field and failed to parse
    # (silently falling back to the 1970 epoch), and the subject came out as
    # the real date and subject concatenated - all with no error raised.
    repo = init_repo(tmp_path)
    (repo / "a.txt").write_text("1")
    run(repo, "add", "a.txt")
    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = "Weird\x1fName"
    env["GIT_AUTHOR_EMAIL"] = "weird@example.com"
    env["GIT_AUTHOR_DATE"] = "2026-06-01T00:00:00+00:00"
    env["GIT_COMMITTER_DATE"] = "2026-06-01T00:00:00+00:00"
    subprocess.run(
        ["git", "commit", "-m", "commit with a control character in the author name"],
        cwd=repo,
        check=True,
        capture_output=True,
        env=env,
    )

    touches = list(stream_commit_touches(repo, "HEAD"))
    assert len(touches) == 1
    touch = touches[0]
    assert touch.author_name == "Weird\x1fName"
    assert touch.author_email == "weird@example.com"
    assert touch.committed_at == datetime.fromisoformat("2026-06-01T00:00:00+00:00")
    assert touch.subject == "commit with a control character in the author name"
    assert touch.files == ("a.txt",)


def test_stream_commit_touches_detects_a_rename(tmp_path):
    # Real audit finding: a plain `git mv` is, by default (no -M), reported
    # by `git log --name-only` as an unrelated delete-of-old plus add-of-new
    # - nothing ties the two together, so fold() had no way to carry the old
    # path's churn/ownership history forward. -M turns this into a single
    # explicit "R100\told\tnew" line instead.
    repo = init_repo(tmp_path)
    (repo / "old.txt").write_text("content\n" * 5)
    run(repo, "add", "old.txt")
    commit(repo, "add old.txt", "2026-06-01T00:00:00+00:00")
    run(repo, "mv", "old.txt", "new.txt")
    run(repo, "add", "-A")
    commit(repo, "rename old.txt to new.txt", "2026-06-02T00:00:00+00:00")

    touches = list(stream_commit_touches(repo, "HEAD"))
    assert len(touches) == 2
    rename_touch = touches[0]  # newest-first
    assert rename_touch.files == ("new.txt",)
    assert rename_touch.renames == (("old.txt", "new.txt"),)


def test_stream_commit_touches_does_not_report_a_rename_below_the_similarity_threshold(tmp_path):
    # A "rename" so heavily rewritten it no longer resembles the old content
    # is correctly reported as an unrelated delete+add, not a rename -
    # -M's default similarity threshold (50%) is git's own judgment call,
    # not something this module overrides.
    repo = init_repo(tmp_path)
    (repo / "old.txt").write_text("alpha\n")
    run(repo, "add", "old.txt")
    commit(repo, "add old.txt", "2026-06-01T00:00:00+00:00")
    run(repo, "rm", "old.txt")
    (repo / "new.txt").write_text("completely different content, nothing shared\n" * 10)
    run(repo, "add", "-A")
    commit(repo, "unrelated delete+add", "2026-06-02T00:00:00+00:00")

    touches = list(stream_commit_touches(repo, "HEAD"))
    second_touch = touches[0]
    assert second_touch.renames == ()
    # Below the similarity threshold, git reports an unrelated delete + add
    # - both paths are genuinely touched by this commit, same as before -M.
    assert set(second_touch.files) == {"old.txt", "new.txt"}


# --- fold: pure aggregation, must be additive for incremental correctness ---


def test_fold_aggregates_ownership_case_insensitively():
    commits = [
        _touch("s1", "Alice", "a@example.com", "2026-06-01T00:00:00+00:00", ("a.txt",)),
        _touch("s2", "Alice", "A@EXAMPLE.COM", "2026-06-02T00:00:00+00:00", ("a.txt",)),
        _touch("s3", "Bob", "b@example.com", "2026-06-03T00:00:00+00:00", ("b.txt",)),
    ]
    result = fold(GraphSnapshot.empty(), commits)
    assert result.ownership["a@example.com"].commit_count == 2
    assert result.ownership["a@example.com"].names == {"Alice"}
    assert result.ownership["b@example.com"].commit_count == 1


def test_fold_tracks_file_churn_and_co_change():
    commits = [
        _touch("s1", "Alice", "a@example.com", "2026-06-01T00:00:00+00:00", ("a.txt", "b.txt")),
        _touch("s2", "Alice", "a@example.com", "2026-06-02T00:00:00+00:00", ("a.txt",)),
    ]
    result = fold(GraphSnapshot.empty(), commits)
    assert result.file_churn["a.txt"].churn_count == 2
    assert result.file_churn["b.txt"].churn_count == 1
    assert result.file_churn["a.txt"].co_change_counts == {"b.txt": 1}
    assert result.file_churn["b.txt"].co_change_counts == {"a.txt": 1}


def test_fold_tracks_complete_per_file_ownership_separately_from_recent_commits():
    commits = [
        _touch("s1", "Alice", "a@example.com", "2026-06-01T00:00:00+00:00", ("a.txt", "shared.txt")),
        _touch("s2", "Bob", "b@example.com", "2026-06-02T00:00:00+00:00", ("shared.txt", "b.txt")),
        _touch("s3", "Alice", "a@example.com", "2026-06-03T00:00:00+00:00", ("a.txt",)),
    ]

    result = fold(GraphSnapshot.empty(), commits)

    assert result.file_churn["a.txt"].owners["a@example.com"].commit_count == 2
    assert result.file_churn["a.txt"].owners.keys() == {"a@example.com"}
    assert result.file_churn["shared.txt"].owners["a@example.com"].commit_count == 1
    assert result.file_churn["shared.txt"].owners["b@example.com"].commit_count == 1
    assert result.file_churn["b.txt"].owners.keys() == {"b@example.com"}


def test_fold_skips_co_change_for_mass_commits():
    files = tuple(f"f{i}.txt" for i in range(60))  # over MASS_COMMIT_FILE_THRESHOLD (50)
    commits = [_touch("s1", "Alice", "a@example.com", "2026-06-01T00:00:00+00:00", files)]
    result = fold(GraphSnapshot.empty(), commits)
    assert result.file_churn["f0.txt"].churn_count == 1
    assert result.file_churn["f0.txt"].co_change_counts == {}


def test_fold_caps_co_change_partners_tracked_per_file():
    # A "hub" file (e.g. MAINTAINERS) touched alongside a different partner
    # in every commit must not grow its co_change_counts dict without bound
    # - that unbounded growth is what OOM-killed a cold sync of
    # torvalds/linux under the production 1GB memory limit.
    commits = [
        _touch(f"s{i}", "Alice", "a@example.com", "2026-06-01T00:00:00+00:00", ("hub.txt", f"partner{i}.txt"))
        for i in range(MAX_CO_CHANGE_PARTNERS_TRACKED + 50)
    ]
    result = fold(GraphSnapshot.empty(), commits)
    assert len(result.file_churn["hub.txt"].co_change_counts) == MAX_CO_CHANGE_PARTNERS_TRACKED
    # churn_count itself is never capped - only the co-change partner dict.
    assert result.file_churn["hub.txt"].churn_count == MAX_CO_CHANGE_PARTNERS_TRACKED + 50


def test_fold_bumping_an_already_tracked_partner_never_evicts():
    commits = [_touch("s0", "Alice", "a@example.com", "2026-06-01T00:00:00+00:00", ("hub.txt", "steady.txt"))]
    for i in range(MAX_CO_CHANGE_PARTNERS_TRACKED - 1):
        commits.append(
            _touch(f"s{i + 1}", "Alice", "a@example.com", "2026-06-01T00:00:00+00:00", ("hub.txt", f"p{i}.txt"))
        )
    # hub.txt is now exactly at capacity, with "steady.txt" at count 1 like
    # everything else. Bumping it again should never be at risk of eviction
    # just because the dict happens to be full.
    commits.append(_touch("sN", "Alice", "a@example.com", "2026-06-01T00:00:00+00:00", ("hub.txt", "steady.txt")))
    result = fold(GraphSnapshot.empty(), commits)
    counts = result.file_churn["hub.txt"].co_change_counts
    assert len(counts) == MAX_CO_CHANGE_PARTNERS_TRACKED
    assert counts["steady.txt"] == 2


def test_fold_caps_recent_commits_per_file_newest_first():
    # Newest-first, matching git log's real default order (no --reverse is
    # ever passed - see stream_commit_touches) - the exact order every real
    # caller (analyzer.py's list(stream_commit_touches(...))) actually feeds
    # fold(). Regression: this fixture used to build commits oldest-first
    # (ascending dates, s0..s14), which is backwards from reality and made
    # the old, buggy insert(0, ...)-in-forward-order code look correct
    # purely because the test's input order happened to be the mirror image
    # of what real git log produces - confirmed directly against fold()
    # itself before fixing either the code or this fixture.
    commits = [
        _touch(f"s{i}", "Alice", "a@example.com", f"2026-06-{i + 1:02d}T00:00:00+00:00", ("a.txt",))
        for i in reversed(range(15))
    ]
    result = fold(GraphSnapshot.empty(), commits)
    recent = result.file_churn["a.txt"].recent_commits
    assert len(recent) == RECENT_COMMITS_PER_FILE
    assert recent[0].sha == "s14"
    assert result.file_churn["a.txt"].churn_count == 15  # the count isn't capped, only the list


def test_fold_buckets_cadence_by_calendar_week():
    day1 = datetime(2026, 6, 1)
    day2 = day1 + timedelta(days=2)  # same ISO week
    day3 = day1 + timedelta(days=9)  # next ISO week
    commits = [
        _touch("s1", "Alice", "a@example.com", day1.isoformat(), ("a.txt",)),
        _touch("s2", "Alice", "a@example.com", day2.isoformat(), ("a.txt",)),
        _touch("s3", "Alice", "a@example.com", day3.isoformat(), ("a.txt",)),
    ]
    result = fold(GraphSnapshot.empty(), commits)
    week1_start = date(2026, 6, 1) - timedelta(days=date(2026, 6, 1).weekday())
    week2_start = week1_start + timedelta(days=7)
    assert result.cadence_weekly_counts[week1_start] == 2
    assert result.cadence_weekly_counts[week2_start] == 1


def test_fold_is_additive_across_batches():
    # The property that makes incremental scanning correct: folding two
    # separate batches (baseline, then a later delta) must equal folding
    # everything in one pass, or a delta scan would silently drift from
    # what a full rescan would have found.
    commits = [
        _touch("s1", "Alice", "a@example.com", "2026-06-01T00:00:00+00:00", ("a.txt", "b.txt")),
        _touch("s2", "Bob", "b@example.com", "2026-06-02T00:00:00+00:00", ("a.txt",)),
        _touch("s3", "Alice", "a@example.com", "2026-06-15T00:00:00+00:00", ("c.txt",)),
    ]
    combined = fold(GraphSnapshot.empty(), commits)
    incremental = fold(fold(GraphSnapshot.empty(), commits[:2]), commits[2:])

    assert combined.ownership.keys() == incremental.ownership.keys()
    for email in combined.ownership:
        assert combined.ownership[email].commit_count == incremental.ownership[email].commit_count
        assert combined.ownership[email].names == incremental.ownership[email].names

    assert combined.cadence_weekly_counts == incremental.cadence_weekly_counts

    assert combined.file_churn.keys() == incremental.file_churn.keys()
    for path in combined.file_churn:
        assert combined.file_churn[path].churn_count == incremental.file_churn[path].churn_count
        assert combined.file_churn[path].co_change_counts == incremental.file_churn[path].co_change_counts


def test_fold_does_not_mutate_the_input_snapshot():
    # Callers may hold a reference to the pre-fold snapshot (e.g. to compare
    # before/after) - fold() must return a new object, never edit in place.
    original = fold(GraphSnapshot.empty(), [_touch("s1", "Alice", "a@example.com", "2026-06-01T00:00:00+00:00", ("a.txt",))])
    fold(original, [_touch("s2", "Bob", "b@example.com", "2026-06-02T00:00:00+00:00", ("b.txt",))])

    assert original.ownership.keys() == {"a@example.com"}
    assert original.file_churn.keys() == {"a.txt"}


# --- fold: rename handling - the real audit gap this fix closes. Without
# the merge, a renamed file's pre-rename churn/ownership stayed stranded
# under its old path, so a just-renamed hot file looked artificially cold
# under its current name. ---


def test_fold_carries_pre_rename_churn_and_ownership_forward_onto_the_new_path():
    # fold()'s own documented contract: commits are fed newest-first,
    # matching real git log output - listed here oldest-to-newest for
    # readability, then reversed, same as test_fold_caps_recent_commits_per_file_newest_first does.
    commits = list(reversed([
        _touch("s1", "Alice", "a@example.com", "2026-06-01T00:00:00+00:00", ("old.txt",)),
        _touch("s2", "Alice", "a@example.com", "2026-06-02T00:00:00+00:00", ("old.txt",)),
        _touch(
            "s3", "Bob", "b@example.com", "2026-06-03T00:00:00+00:00", ("new.txt",),
            renames=(("old.txt", "new.txt"),),
        ),
        _touch("s4", "Bob", "b@example.com", "2026-06-04T00:00:00+00:00", ("new.txt",)),
    ]))
    result = fold(GraphSnapshot.empty(), commits)

    assert "old.txt" not in result.file_churn
    churn = result.file_churn["new.txt"]
    # 2 pre-rename (old.txt) + 1 rename commit itself + 1 post-rename = 4,
    # not 2 (what it would be if only post-rename touches counted - the
    # exact "looks artificially cold" failure mode the audit reproduced).
    assert churn.churn_count == 4
    assert churn.owners["a@example.com"].commit_count == 2
    assert churn.owners["b@example.com"].commit_count == 2
    assert [rc.sha for rc in churn.recent_commits] == ["s4", "s3", "s2", "s1"]


def test_fold_carries_pre_rename_history_forward_across_separate_batches():
    # The realistic incremental case: old.txt's entire history was folded in
    # a prior sync (now sitting in the persisted snapshot), and the only new
    # commit in this batch is the rename itself - the merge must still find
    # old.txt's totals in the snapshot, not just within one batch's commits.
    baseline = fold(
        GraphSnapshot.empty(),
        [_touch("s1", "Alice", "a@example.com", "2026-06-01T00:00:00+00:00", ("old.txt",))],
    )
    result = fold(
        baseline,
        [
            _touch(
                "s2", "Bob", "b@example.com", "2026-06-02T00:00:00+00:00", ("new.txt",),
                renames=(("old.txt", "new.txt"),),
            )
        ],
    )

    assert "old.txt" not in result.file_churn
    assert result.file_churn["new.txt"].churn_count == 2
    assert result.file_churn["new.txt"].owners.keys() == {"a@example.com", "b@example.com"}


def test_fold_rename_onto_an_already_populated_new_path_sums_both_histories():
    # new_path already has its own independent history (e.g. a second,
    # unrelated rename chain landed on the same final name) - merging must
    # add to it, not overwrite it.
    commits = list(reversed([
        _touch("s1", "Alice", "a@example.com", "2026-06-01T00:00:00+00:00", ("old.txt",)),
        _touch("s2", "Bob", "b@example.com", "2026-06-02T00:00:00+00:00", ("new.txt",)),
        _touch(
            "s3", "Carol", "c@example.com", "2026-06-03T00:00:00+00:00", ("new.txt",),
            renames=(("old.txt", "new.txt"),),
        ),
    ]))
    result = fold(GraphSnapshot.empty(), commits)
    assert result.file_churn["new.txt"].churn_count == 3
    assert result.file_churn["new.txt"].owners.keys() == {"a@example.com", "b@example.com", "c@example.com"}


def test_fold_rename_of_a_path_never_touched_in_this_view_is_a_no_op():
    # old_path has no prior entry (e.g. it existed before the window this
    # fold() call's commits/snapshot cover) - nothing to carry forward, and
    # this must not create a spurious old_path entry either.
    commits = [
        _touch(
            "s1", "Alice", "a@example.com", "2026-06-01T00:00:00+00:00", ("new.txt",),
            renames=(("old.txt", "new.txt"),),
        ),
    ]
    result = fold(GraphSnapshot.empty(), commits)
    assert "old.txt" not in result.file_churn
    assert result.file_churn["new.txt"].churn_count == 1


def test_fold_rename_chain_ends_up_entirely_under_the_final_name():
    # a.txt -> b.txt -> c.txt, processed oldest-first (as fold() always
    # does) - the full history must end up entirely under c.txt, the one
    # name any current query would actually use.
    commits = list(reversed([
        _touch("s1", "Alice", "a@example.com", "2026-06-01T00:00:00+00:00", ("a.txt",)),
        _touch(
            "s2", "Alice", "a@example.com", "2026-06-02T00:00:00+00:00", ("b.txt",),
            renames=(("a.txt", "b.txt"),),
        ),
        _touch("s3", "Alice", "a@example.com", "2026-06-03T00:00:00+00:00", ("b.txt",)),
        _touch(
            "s4", "Alice", "a@example.com", "2026-06-04T00:00:00+00:00", ("c.txt",),
            renames=(("b.txt", "c.txt"),),
        ),
    ]))
    result = fold(GraphSnapshot.empty(), commits)
    assert "a.txt" not in result.file_churn
    assert "b.txt" not in result.file_churn
    assert result.file_churn["c.txt"].churn_count == 4


# --- compute_repo_key: stable identity, independent of clone directory ---


def test_compute_repo_key_stable_for_same_repo(tmp_path):
    repo = init_repo(tmp_path)
    (repo / "a.txt").write_text("1")
    run(repo, "add", "a.txt")
    commit(repo, "first", "2026-06-01T00:00:00+00:00")

    assert compute_repo_key(repo) == compute_repo_key(repo)


def test_compute_repo_key_differs_for_different_repos(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    repo_a = init_repo(tmp_path / "a")
    (repo_a / "f.txt").write_text("1")
    run(repo_a, "add", "f.txt")
    commit(repo_a, "first", "2026-06-01T00:00:00+00:00")

    repo_b = init_repo(tmp_path / "b")
    (repo_b / "f.txt").write_text("1")
    run(repo_b, "add", "f.txt")
    commit(repo_b, "first", "2026-06-01T00:00:00+00:00")

    assert compute_repo_key(repo_a) != compute_repo_key(repo_b)


def test_compute_repo_key_uses_remote_when_present(tmp_path):
    repo = init_repo(tmp_path)
    (repo / "a.txt").write_text("1")
    run(repo, "add", "a.txt")
    commit(repo, "first", "2026-06-01T00:00:00+00:00")

    key_without_remote = compute_repo_key(repo)
    run(repo, "remote", "add", "origin", "https://github.com/example/repo.git")
    key_with_remote = compute_repo_key(repo)

    assert key_without_remote != key_with_remote
    assert "https://github.com/example/repo.git" in key_with_remote


def test_stream_commit_touches_normalizes_paths_when_scan_root_is_subdirectory(tmp_path):
    repo = tmp_path / "repo"
    subdir = repo / "component"
    subdir.mkdir(parents=True)
    run(repo, "init", "-b", "main")
    run(repo, "config", "user.email", "a@example.com")
    run(repo, "config", "user.name", "Alice")
    (subdir / "a.py").write_text("1")
    (repo / "README.md").write_text("outside")
    run(repo, "add", "-A")
    commit(repo, "initial", "2026-06-01T00:00:00+00:00")

    touches = list(stream_commit_touches(subdir, "HEAD"))
    assert len(touches) == 1
    assert touches[0].files == ("a.py",)


def test_stream_commit_touches_does_not_deadlock_on_large_stderr(tmp_path, monkeypatch):
    # stdout is drained before stderr is read; with stderr on a PIPE, git
    # writing more than the pipe buffer to stderr blocked forever.
    import shutil
    import stat
    import threading

    from aletheore.git_intel.incremental import stream_commit_touches

    repo = tmp_path / "repo"
    repo.mkdir()
    run(repo, "init", "-q")
    run(repo, "config", "user.email", "a@example.com")
    run(repo, "config", "user.name", "A")
    (repo / "a.txt").write_text("1")
    run(repo, "add", "-A")
    commit(repo, "first", "2026-06-01T00:00:00+00:00")

    real_git = shutil.which("git")
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    shim = shim_dir / "git"
    shim.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        f'  *"log "*|log*) head -c 300000 /dev/zero | tr "\\0" "x" >&2; exec {real_git} "$@" ;;\n'
        f'  *) exec {real_git} "$@" ;;\n'
        "esac\n"
    )
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{shim_dir}{os.pathsep}{os.environ['PATH']}")

    result: list = []
    thread = threading.Thread(
        target=lambda: result.extend(stream_commit_touches(repo, "HEAD")), daemon=True
    )
    thread.start()
    thread.join(timeout=20)

    assert not thread.is_alive(), "stream_commit_touches deadlocked on large stderr"
    assert len(result) == 1
