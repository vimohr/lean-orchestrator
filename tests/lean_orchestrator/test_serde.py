from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from lean_orchestrator.serde import from_dict, to_dict
from lean_orchestrator.state import Branch, Claim, ResearchState, Stamp


@dataclass
class Inner:
    value: float
    label: str | None = None


@dataclass
class Outer:
    name: str
    items: list[Inner] = field(default_factory=list)
    lookup: dict[str, int] = field(default_factory=dict)
    optional: Inner | None = None


def test_round_trip_of_nested_dataclasses():
    original = Outer("x", [Inner(1.5, "a"), Inner(2)], {"k": 3}, Inner(0.0))
    assert from_dict(Outer, json.loads(json.dumps(to_dict(original)))) == original


def test_missing_optional_fields_take_defaults_and_unknown_keys_are_ignored():
    assert from_dict(Outer, {"name": "x", "unknown": 1}) == Outer("x")


def test_type_errors_name_the_offending_field():
    with pytest.raises(TypeError, match=r"Outer.items\[0\].value"):
        from_dict(Outer, {"name": "x", "items": [{"value": "big"}]})
    with pytest.raises(TypeError, match="missing required field 'name'"):
        from_dict(Outer, {})
    assert from_dict(Inner, {"value": 2}).value == 2.0


def test_research_state_round_trip():
    state = ResearchState(problem_id="p", title="t", folder="f", lean_namespace="N")
    state.claims.append(Claim(id="C1", kind="lemma", statement="s", created=Stamp(1, 2)))
    state.branches.append(Branch(id="B1", kind="prove", goal="g"))
    state.next_plan = {"branch": "B1", "goal": "g"}
    restored = from_dict(ResearchState, json.loads(json.dumps(to_dict(state))))
    assert restored == state
