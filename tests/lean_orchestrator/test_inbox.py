from __future__ import annotations

import pytest

from lean_orchestrator import inbox
from lean_orchestrator.paths import WorkspacePaths


def test_post_pending_acknowledge(tmp_path):
    paths = WorkspacePaths(tmp_path)
    inbox.post(paths, "hint", "p1", text="try d = 3")
    inbox.post(paths, "set_status", "p2", status="suspended")
    (inbox.inbox_dir(paths) / "broken.json").write_text("{not json")
    messages = inbox.pending(paths)
    assert [(message.kind, message.problem) for message in messages] == [("hint", "p1"), ("set_status", "p2")]
    assert messages[0].payload == {"text": "try d = 3"}
    inbox.acknowledge(messages[0])
    assert [message.problem for message in inbox.pending(paths)] == ["p2"]
    with pytest.raises(ValueError):
        inbox.post(paths, "delete_everything", "p1")
