from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from lean_orchestrator.pause import (
    PauseError, active_pause, clear_pause, next_clock_time, parse_duration, set_pause,
)
from lean_orchestrator.paths import WorkspacePaths


@pytest.mark.parametrize(("text", "minutes"), [("90m", 90), ("6h", 360), ("1d", 1440), ("1h30m", 90), ("1.5h", 90)])
def test_durations(text, minutes):
    assert parse_duration(text) == timedelta(minutes=minutes)


@pytest.mark.parametrize("text", ["", "90", "h", "5x", "0m", "1h 30"])
def test_unreadable_durations(text):
    with pytest.raises(PauseError):
        parse_duration(text)


def test_clock_times_refer_to_the_next_occurrence():
    now = datetime(2026, 9, 28, 10, 0)
    assert next_clock_time("22:00", now).replace(tzinfo=None) == datetime(2026, 9, 28, 22, 0)
    assert next_clock_time("9:30", now).replace(tzinfo=None) == datetime(2026, 9, 29, 9, 30)
    assert next_clock_time("10:00", now).replace(tzinfo=None) == datetime(2026, 9, 29, 10, 0)
    assert next_clock_time("22:00", now).tzinfo is not None
    for text in ("25:00", "7pm", "12:60"):
        with pytest.raises(PauseError):
            next_clock_time(text, now)


def test_pause_until_resumed_or_until_a_time(tmp_path):
    paths = WorkspacePaths(tmp_path)
    assert active_pause(paths) is None and not clear_pause(paths)
    set_pause(paths, None)
    assert active_pause(paths).until is None and "resume" in active_pause(paths).describe()
    assert clear_pause(paths) and active_pause(paths) is None
    now = datetime.now().astimezone()
    set_pause(paths, now + timedelta(hours=2))
    assert active_pause(paths, now).until is not None
    assert active_pause(paths, now + timedelta(hours=3)) is None, "a pause ends by itself at its end time"


@pytest.mark.parametrize("content", ["", "not json", '{"until": "tomorrow"}', "[1, 2]"])
def test_a_pause_file_without_a_readable_end_lasts_until_resumed(tmp_path, content):
    paths = WorkspacePaths(tmp_path)
    paths.pause_file.parent.mkdir(parents=True)
    paths.pause_file.write_text(content)
    assert active_pause(paths) is not None and active_pause(paths).until is None
