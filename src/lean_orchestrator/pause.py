"""Pause agent calls without ending a run, for example to save model quota.

A pause is the file ``.lean-orch/PAUSE``. While it is in effect, calls that are
already running finish, but no new agent call starts; the run keeps its place.
The file holds an optional end time; an empty file (``touch``) pauses until it
is removed with ``lean-orch resume``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from .jsonio import atomic_write_text
from .paths import WorkspacePaths

_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)([dhm])")
_SECONDS = {"d": 86400, "h": 3600, "m": 60}


class PauseError(ValueError):
    """A pause duration or clock time could not be read."""


@dataclass(frozen=True)
class Pause:
    until: datetime | None  # None: until 'lean-orch resume'

    def describe(self) -> str:
        return f"until {self.until:%Y-%m-%d %H:%M}" if self.until else "until 'lean-orch resume'"


def parse_duration(text: str) -> timedelta:
    """Read a duration such as ``90m``, ``6h``, ``1d``, or ``1h30m``."""
    compact = text.strip().lower().replace(" ", "")
    parts = _DURATION_PART.findall(compact)
    if not parts or "".join(number + unit for number, unit in parts) != compact:
        raise PauseError(f"cannot read the duration {text!r}; use for example 90m, 6h, or 1d")
    duration = timedelta(seconds=sum(float(number) * _SECONDS[unit] for number, unit in parts))
    if duration <= timedelta(0):
        raise PauseError("a pause must last longer than zero minutes")
    return duration


def next_clock_time(text: str, now: datetime | None = None) -> datetime:
    """The next moment the local clock shows ``HH:MM``, after the naive local time ``now``."""
    match = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", text.strip())
    if match is None:
        raise PauseError(f"cannot read the time {text!r}; use HH:MM, for example 22:00")
    now = now or datetime.now()
    target = now.replace(hour=int(match[1]), minute=int(match[2]), second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target.astimezone()


def set_pause(paths: WorkspacePaths, until: datetime | None) -> None:
    """Pause new agent calls until ``until``, or until :func:`clear_pause` when it is None."""
    record = {"since": datetime.now().astimezone().isoformat(timespec="seconds"),
              "until": until.isoformat(timespec="seconds") if until else None}
    atomic_write_text(paths.pause_file, json.dumps(record) + "\n")


def clear_pause(paths: WorkspacePaths) -> bool:
    """End a pause; return whether one was set."""
    existed = paths.pause_file.exists()
    paths.pause_file.unlink(missing_ok=True)
    return existed


def active_pause(paths: WorkspacePaths, now: datetime | None = None) -> Pause | None:
    """The pause in effect at the aware time ``now``, if any."""
    try:
        text = paths.pause_file.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    until = _read_until(text)
    if until is not None and until <= (now or datetime.now().astimezone()):
        return None
    return Pause(until)


def _read_until(text: str) -> datetime | None:
    """The end time in a pause file; a file without a readable one pauses until resumed."""
    try:
        record = json.loads(text) if text else None
    except json.JSONDecodeError:
        return None
    value = record.get("until") if isinstance(record, dict) else None
    if not isinstance(value, str):
        return None
    try:
        until = datetime.fromisoformat(value)
    except ValueError:
        return None
    return until if until.tzinfo else until.astimezone()
