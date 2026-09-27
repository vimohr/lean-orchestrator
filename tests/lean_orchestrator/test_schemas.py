from __future__ import annotations

import json

import pytest

from lean_orchestrator.schemas import SCHEMAS, schema_filename, semantic_errors, validate, write_schema_files


def test_validator_reports_types_enums_required_and_extra_keys():
    schema = {
        "type": "object", "required": ["a", "b"], "additionalProperties": False,
        "properties": {"a": {"type": "integer", "minimum": 0, "maximum": 3}, "b": {"enum": ["x", None]},
                       "c": {"type": "array", "items": {"type": "string", "minLength": 1}}},
    }
    assert validate({"a": 1, "b": None, "c": ["ok"]}, schema) == []
    errors = validate({"a": 5, "c": ["", 3], "z": 1}, schema)
    assert "$: missing required key 'b'" in errors
    assert "$: unexpected key 'z'" in errors
    assert "$.a: must be at most 3" in errors
    assert "$.c[0]: must be a non-empty string" in errors
    assert "$.c[1]: expected string, got int" in errors
    assert validate(True, {"type": "integer"}) == ["$: expected integer, got bool"]


def test_schema_files_are_valid_json(tmp_path):
    write_schema_files(tmp_path)
    for role in SCHEMAS:
        assert json.loads((tmp_path / schema_filename(role)).read_text())["type"] == "object"


def _review(**overrides):
    base = {"claim": "c1", "verdict": "accept", "formalization_faithful": "yes", "issues": [], "confidence": 0.5,
            "checks_performed": ["recomputed"], "weakest_step": "none"}
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    ("output", "message"),
    [
        ({"reviews": [], "overall": "o"}, "every claim needs a review"),
        ({"reviews": [_review(verdict="reject")], "overall": "o"}, "must cite at least one fatal or major issue"),
        ({"reviews": [_review(checks_performed=[])], "overall": "o"}, "must list the checks"),
        ({"reviews": [_review(issues=[{"severity": "fatal", "location": "l", "problem": "p"}])], "overall": "o"},
         "cannot accept a claim with a fatal or major issue"),
    ],
)
def test_critic_rules(output, message):
    context = {"claim_ids": ["c1"]}
    assert validate(output, SCHEMAS["critic"]) == []
    assert any(message in error for error in semantic_errors("critic", output, context))


def _decision(**overrides):
    base = {"assessment": None, "decision": "continue", "rationale": "r", "assessment_text": "t",
            "next_step": {"branch": "B1", "task": "research", "goal": "g", "instructions": "i",
                          "success_test": "s", "novelty": "n"}}
    base.update(overrides)
    return base


def test_supervisor_rules():
    context = {"branch_ids": ["B1"], "expects_assessment": False, "epoch_ending": False}
    assert validate(_decision(), SCHEMAS["supervisor"]) == []
    assert semantic_errors("supervisor", _decision(), context) == []
    assert semantic_errors("supervisor", _decision(next_step=None), context)
    assert semantic_errors("supervisor", _decision(decision="suspend", next_step=None), context) == [
        "$.epoch_report: required when the epoch ends"]
    assert semantic_errors("supervisor", _decision(), {**context, "expects_assessment": True})
    assert semantic_errors("supervisor", _decision(decision="reformulate"), context)
    assert semantic_errors("supervisor", _decision(decision="branch"), context)
    unknown = _decision(next_step={**_decision()["next_step"], "branch": "B9"})
    assert "neither an existing branch" in semantic_errors("supervisor", unknown, context)[0]
    labelled = _decision(decision="branch", branch_updates=[{"label": "new", "goal": "g", "status": "open"}],
                         next_step={**_decision()["next_step"], "branch": "new"})
    assert semantic_errors("supervisor", labelled, context) == []


def test_researcher_and_literature_and_triage_rules():
    report = {"summary": "s", "approach": {"name": "n", "description": "d", "fingerprint": "f"},
              "outcome": "success",
              "claims": [{"id": "c1", "kind": "lemma", "statement": "s", "argument": "a", "resolves_main": "proves"},
                         {"id": "c1", "kind": "formal_statement", "statement": "s", "argument": "a"}]}
    assert validate(report, SCHEMAS["researcher"]) == []
    errors = semantic_errors("researcher", report)
    assert len(errors) == 3
    literature = {"still_open": "no", "summary": "s", "precise_statement": "p", "success_criteria": "c",
                  "references": []}
    assert semantic_errors("literature", literature) == ["$.resolution: required when still_open is 'no'"]
    triage = {"problems": [{"id": "a"}, {"id": "zzz"}]}
    errors = semantic_errors("triage", triage, {"problem_ids": ["a", "b"]})
    assert any("missing" in error for error in errors) and any("unknown" in error for error in errors)
