from __future__ import annotations

from lean_orchestrator.context import ControlCenter


def test_control_center_routes_instructions_to_running_problems():
    control = ControlCenter()
    control.mark_running("p")
    assert control.is_running("p") and control.running() == {"p"}
    control.push_hint("p", "h1")
    control.push_hint("p", "h2")
    control.push_note("p", "upstream changed")
    control.request_status("p", "suspended")
    assert control.take_hints("p") == ["h1", "h2"] and control.take_hints("p") == []
    assert control.take_notes("p") == ["upstream changed"]
    assert control.take_status("p") == "suspended" and control.take_status("p") is None
    control.mark_done("p")
    assert not control.is_running("p")


def test_services_should_stop_on_the_stop_file(make_orchestrator):
    services = make_orchestrator().services
    assert not services.should_stop()
    services.paths.stop_file.touch()
    assert services.should_stop()
    services.paths.stop_file.unlink()
    services.drain_event.set()
    assert services.should_stop()
