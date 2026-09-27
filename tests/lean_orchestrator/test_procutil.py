from __future__ import annotations

import sys
import threading
import time

from lean_orchestrator.procutil import FileLock, run_captured


def test_run_captured_returns_output_and_exit_code(tmp_path):
    completed = run_captured([sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"],
                             cwd=tmp_path, timeout=30)
    assert (completed.returncode, completed.stdout, completed.stderr, completed.timed_out) == (3, "out\n", "err\n", False)
    assert completed.output == "out\nerr\n"


def test_timeouts_kill_the_whole_process_group(tmp_path):
    marker = tmp_path / "child-finished"
    child = f"import time, pathlib; time.sleep(3); pathlib.Path({str(marker)!r}).write_text('late')"
    parent = f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(30)"
    started = time.monotonic()
    completed = run_captured([sys.executable, "-c", parent], cwd=tmp_path, timeout=0.5)
    assert completed.timed_out and completed.returncode == 124
    assert time.monotonic() - started < 10
    time.sleep(3.5)
    assert not marker.exists(), "the grandchild should have been killed with its group"


def test_file_lock_excludes_concurrent_holders(tmp_path):
    lock_path = tmp_path / "build.lock"
    active, overlaps = [0], [0]

    def work():
        with FileLock(lock_path):
            active[0] += 1
            if active[0] > 1:
                overlaps[0] += 1
            time.sleep(0.02)
            active[0] -= 1

    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert overlaps[0] == 0
