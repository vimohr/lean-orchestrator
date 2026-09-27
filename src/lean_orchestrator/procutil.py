"""Run short-lived verification commands with reliable timeouts."""

from __future__ import annotations

import fcntl
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar


class FileLock:
    """An exclusive lock shared by threads of this process and by other processes.

    Lake has no build lock, so every ``lake build`` in a workspace (by the
    orchestrator or by agents through ``lean-orch build``) takes this lock.
    """

    _thread_locks: ClassVar[dict[str, threading.Lock]] = {}
    _registry_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, path: Path) -> None:
        self.path = path
        with FileLock._registry_lock:
            self._thread_lock = FileLock._thread_locks.setdefault(str(path.resolve()), threading.Lock())
        self._handle = None

    def __enter__(self) -> FileLock:
        self._thread_lock.acquire()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self.path.open("a+")
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX)
        except BaseException:
            if self._handle is not None:
                self._handle.close()
                self._handle = None
            self._thread_lock.release()
            raise
        return self

    def __exit__(self, *exc_info: object) -> None:
        try:
            if self._handle is not None:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
                self._handle.close()
                self._handle = None
        finally:
            self._thread_lock.release()


@dataclass
class Completed:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_seconds: float

    @property
    def output(self) -> str:
        return self.stdout + self.stderr


def run_captured(
    command: list[str],
    *,
    cwd: Path,
    timeout: float | None,
    env: dict[str, str] | None = None,
) -> Completed:
    """Run ``command`` in its own process group and kill the whole group on timeout."""
    started = time.monotonic()
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()
        stdout, stderr = process.communicate()
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()
        process.wait()
        raise
    return Completed(
        returncode=process.returncode if not timed_out else 124,
        stdout=stdout or "",
        stderr=stderr or "",
        timed_out=timed_out,
        duration_seconds=time.monotonic() - started,
    )
