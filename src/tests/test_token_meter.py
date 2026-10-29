from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from aletheore.token_meter import (
    load_events,
    record_mcp_trim,
    record_read_guard,
    summarize,
)


def test_meter_never_creates_aletheore_directory_by_itself(tmp_path: Path):
    assert not record_read_guard(tmp_path, 128)
    assert not record_mcp_trim(tmp_path, 1000, 500)
    assert not (tmp_path / ".aletheore").exists()
    assert summarize(tmp_path).all_time.read_denials == 0


def test_records_only_valid_read_and_actual_mcp_savings(tmp_path: Path):
    state = tmp_path / ".aletheore"
    state.mkdir()
    moment = datetime(2026, 10, 28, 8, tzinfo=timezone.utc)
    assert record_read_guard(tmp_path, 9, occurred_at=moment)
    assert record_mcp_trim(tmp_path, 300, 201, occurred_at=moment)
    assert not record_read_guard(tmp_path, -1)
    assert not record_mcp_trim(tmp_path, 10, 10)
    assert not record_mcp_trim(tmp_path, 10, 11)
    events, corrupt = load_events(tmp_path)
    assert corrupt == 0
    assert len(events) == 2
    assert {event.source for event in events} == {"read_guard", "mcp_budget"}


def test_summarizes_by_today_week_and_all_time_with_explicit_units(tmp_path: Path):
    (tmp_path / ".aletheore").mkdir()
    tz = timezone(timedelta(hours=5, minutes=30))
    now = datetime(2026, 10, 28, 12, tzinfo=tz)  # Wednesday
    assert record_read_guard(
        tmp_path, 5, occurred_at=datetime(2026, 10, 28, 5, tzinfo=timezone.utc)
    )
    assert record_read_guard(
        tmp_path, 8, occurred_at=datetime(2026, 10, 26, 6, tzinfo=timezone.utc)
    )
    assert record_mcp_trim(
        tmp_path, 200, 189, occurred_at=datetime(2026, 10, 27, 4, tzinfo=timezone.utc)
    )
    assert record_mcp_trim(
        tmp_path, 100, 91, occurred_at=datetime(2026, 10, 25, 4, tzinfo=timezone.utc)
    )

    result = summarize(tmp_path, now=now)
    assert result.today.read_denials == 1
    assert result.today.read_bytes == 5
    assert result.today.read_tokens_estimated == 2  # ceil(5 bytes / 4 bytes per token)
    assert result.today.mcp_trims == 0
    assert result.this_week.read_denials == 2
    assert result.this_week.read_tokens_estimated == 4  # ceil((5 + 8) / 4)
    assert result.this_week.mcp_omitted_chars_measured == 11
    assert (
        result.this_week.mcp_tokens_estimated == 3
    )  # ceil(11 chars / 4 chars per token)
    assert result.all_time.mcp_omitted_chars_measured == 20
    assert result.all_time.mcp_tokens_estimated == 5
    assert result.corrupt_events == 0


def test_corrupt_or_wrong_schema_event_is_skipped_and_counted(tmp_path: Path):
    events_dir = tmp_path / ".aletheore" / "token_meter" / "events"
    events_dir.mkdir(parents=True)
    (events_dir / "broken.json").write_text("not json")
    (events_dir / "wrong.json").write_text(json.dumps({"schema": 99}))
    assert record_read_guard(tmp_path, 4)
    events, corrupt = load_events(tmp_path)
    assert len(events) == 1
    assert corrupt == 2
    result = summarize(tmp_path)
    assert result.all_time.read_tokens_estimated == 1
    assert result.corrupt_events == 2


def test_concurrent_event_recording_does_not_lose_events(tmp_path: Path):
    (tmp_path / ".aletheore").mkdir()
    moment = datetime(2026, 10, 28, tzinfo=timezone.utc)
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(
            pool.map(
                lambda n: record_mcp_trim(tmp_path, 100 + n, 10, occurred_at=moment),
                range(64),
            )
        )
    events, corrupt = load_events(tmp_path)
    assert all(outcomes)
    assert len(events) == 64
    assert corrupt == 0
    assert summarize(tmp_path, now=moment).all_time.mcp_trims == 64


def test_naive_timestamp_is_normalized_to_local_timezone(tmp_path: Path):
    (tmp_path / ".aletheore").mkdir()
    local_now = datetime.now().astimezone()
    moment = local_now.replace(tzinfo=None)
    assert record_read_guard(tmp_path, 4, occurred_at=moment)
    result = summarize(tmp_path, now=local_now)
    assert result.today.read_denials == 1


def test_non_directory_event_path_is_reported_as_corrupt(tmp_path: Path):
    state = tmp_path / ".aletheore"
    state.mkdir()
    (state / "token_meter").write_text("not a directory")
    result = summarize(tmp_path)
    assert result.corrupt_events == 1
    assert result.all_time.read_denials == 0


def test_oversized_event_file_is_skipped(tmp_path: Path):
    events_dir = tmp_path / ".aletheore" / "token_meter" / "events"
    events_dir.mkdir(parents=True)
    (events_dir / "huge.json").write_text(" " * 5000)
    result = summarize(tmp_path)
    assert result.corrupt_events == 1
    assert result.all_time.mcp_trims == 0


def test_meter_event_files_are_private_on_posix(tmp_path: Path):
    import os
    import stat

    if os.name == "nt":
        return
    (tmp_path / ".aletheore").mkdir()
    assert record_read_guard(tmp_path, 4)
    event_file = next(
        (tmp_path / ".aletheore" / "token_meter" / "events").glob("*.json")
    )
    assert stat.S_IMODE(event_file.stat().st_mode) & 0o077 == 0


def test_meter_refuses_symlink_state_directories(tmp_path: Path):
    import os

    if os.name == "nt":
        return
    outside = tmp_path / "outside"
    outside.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".aletheore").mkdir()
    (repo / ".aletheore" / "token_meter").symlink_to(outside, target_is_directory=True)
    assert not record_read_guard(repo, 4)
    assert list(outside.iterdir()) == []
    assert summarize(repo).corrupt_events == 1
