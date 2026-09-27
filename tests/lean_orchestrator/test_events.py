from __future__ import annotations

import io
import threading

from lean_orchestrator.events import Console, EventLog


def test_event_log_is_append_only_and_filterable(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    threads = [threading.Thread(target=log.emit, args=("tick",), kwargs={"index": index}) for index in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    log.emit("other", value=1)
    events = log.read()
    assert len(events) == 21 and all("time" in event for event in events)
    assert sorted(event["index"] for event in log.read({"tick"})) == list(range(20))


def test_console_prefixes_lines():
    out, err = io.StringIO(), io.StringIO()
    console = Console(out, err)
    console.info("a\nb", tag="p")
    console.warn("c")
    lines = out.getvalue().splitlines()
    assert len(lines) == 2 and all(line.endswith(("[p] a", "[p] b")) for line in lines)
    assert err.getvalue().rstrip().endswith(" c")
