import io
import json

from lean_orchestrator_v2.backends import Backend
from lean_orchestrator_v2.config import BackendConfig
from lean_orchestrator_v2.context import TokenCounter


def test_offline_cap_preserves_unicode_and_is_conservative():
    counter = TokenCounter("utf8")
    clipped = counter.clip("数学 ∀ n " * 1000, 600)
    assert counter.count(clipped) <= 600
    assert clipped.encode().decode() == clipped


def test_responses_backend_makes_one_request_without_prior_conversation(tmp_path, monkeypatch):
    captured = []
    monkeypatch.setenv("OPENAI_API_KEY", "offline-test-key")
    def urlopen(request, timeout):
        captured.append(json.loads(request.data))
        return io.StringIO(json.dumps({"status": "completed", "output": [{"content": [{"type": "output_text", "text": '{"state":"ok"}'}]}],
                                      "usage": {"input_tokens": 100, "output_tokens": 20,
                                                "input_tokens_details": {"cached_tokens": 40},
                                                "output_tokens_details": {"reasoning_tokens": 10}}}))
    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    backend = Backend(BackendConfig(kind="responses"))
    result = backend.complete({"role": "solver", "packet": {}, "state": "current", "feedback": "latest"}, tmp_path, 800)
    assert len(captured) == 1 and captured[0]["store"] is False
    assert "previous_response_id" not in captured[0]
    assert result.data == {"state": "ok"} and result.usage.total_tokens == 120
