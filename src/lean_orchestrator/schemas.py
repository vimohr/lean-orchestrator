"""Output contracts for agent roles.

Each role writes one JSON file. The same JSON Schema documents are copied into
the workspace (``schemas/``) so agents read exactly what the orchestrator
validates. The validator supports the subset of JSON Schema used here; the
role-specific checks in :func:`semantic_errors` cover rules a schema cannot.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .jsonio import atomic_write_text
from .state import BRANCH_KINDS, CLAIM_KINDS

_STRING = {"type": "string"}
_TEXT_LIST = {"type": "array", "items": _STRING}
_NULLABLE_STRING = {"type": ["string", "null"]}

_REFERENCE = {
    "type": "object",
    "required": ["citation"],
    "properties": {
        "citation": {"type": "string", "minLength": 1, "description": "Authors, title, venue, year."},
        "arxiv": {**_NULLABLE_STRING, "description": "arXiv identifier such as 2405.09613, if any."},
        "doi": {**_NULLABLE_STRING, "description": "DOI such as 10.1103/PhysRevLett.134.090202, if any."},
        "url": _NULLABLE_STRING,
        "relevance": _STRING,
        "locator": {**_STRING, "description": "Theorem, section, or page number of the cited result."},
        "quote": {**_STRING, "description": "Short verbatim quote of the cited statement, if you read it."},
    },
    "additionalProperties": False,
}

TRIAGE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "lean-orch triage output",
    "type": "object",
    "required": ["problems"],
    "properties": {
        "problems": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "scores", "recommendation", "modes", "likely_resolved", "rationale"],
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "scores": {
                        "type": "object",
                        "required": [
                            "precision", "finite_dimensional", "formalizability",
                            "closability", "tractable_subcases", "background",
                        ],
                        "properties": {
                            name: {"type": "integer", "minimum": 0, "maximum": 5}
                            for name in (
                                "precision", "finite_dimensional", "formalizability",
                                "closability", "tractable_subcases", "background",
                            )
                        },
                        "additionalProperties": False,
                    },
                    "recommendation": {"enum": ["activate", "defer"]},
                    "modes": {"type": "array", "items": {"enum": ["prove", "disprove", "explore"]}},
                    "likely_resolved": {"type": "boolean"},
                    "rationale": {"type": "string", "minLength": 1},
                    "first_steps": _STRING,
                },
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}

LITERATURE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "lean-orch literature check",
    "type": "object",
    "required": ["still_open", "summary", "precise_statement", "success_criteria", "references"],
    "properties": {
        "still_open": {"enum": ["yes", "no", "unclear"]},
        "summary": {"type": "string", "minLength": 1},
        "resolution": {**_STRING, "description": "Required when still_open is 'no': who resolved it and where."},
        "precise_statement": {"type": "string", "minLength": 1},
        "success_criteria": {"type": "string", "minLength": 1},
        "assumptions": _TEXT_LIST,
        "references": {"type": "array", "items": _REFERENCE},
        "known_results": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["statement", "reference"],
                "properties": {"statement": {"type": "string", "minLength": 1}, "reference": _STRING},
                "additionalProperties": False,
            },
        },
        "techniques": _TEXT_LIST,
        "suggested_branches": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["kind", "goal"],
                "properties": {"kind": {"enum": list(BRANCH_KINDS)}, "goal": _STRING, "rationale": _STRING},
                "additionalProperties": False,
            },
        },
        "formalization_notes": _STRING,
    },
    "additionalProperties": False,
}

PROGRESS_CATEGORIES = (
    "new_lemma", "lean_verified", "reduction", "subsidiary_counterexample", "literature_theorem",
    "eliminated_approach_class", "improved_formal_statement", "new_line_of_attack",
    "numerical_evidence", "resolution", "none",
)

SUPERVISOR_DECISIONS = (
    "continue", "branch", "reformulate", "suspend", "switch", "close_proved", "close_disproved",
)
EPOCH_ENDING_DECISIONS = frozenset({"suspend", "switch", "close_proved", "close_disproved"})

SUPERVISOR_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "lean-orch supervisor decision",
    "type": "object",
    "required": ["assessment", "decision", "rationale", "next_step", "assessment_text"],
    "properties": {
        "assessment": {
            "type": ["object", "null"],
            "description": "Assessment of the iteration just recorded; null when no iteration has run yet in this epoch.",
            "required": ["progress_score", "categories", "justification", "repeated_approach"],
            "properties": {
                "progress_score": {"type": "integer", "minimum": 0, "maximum": 3},
                "categories": {"type": "array", "items": {"enum": list(PROGRESS_CATEGORIES)}},
                "justification": {"type": "string", "minLength": 1},
                "repeated_approach": {"type": "boolean"},
                "repeats": _TEXT_LIST,
            },
            "additionalProperties": False,
        },
        "decision": {"enum": list(SUPERVISOR_DECISIONS)},
        "rationale": {"type": "string", "minLength": 1},
        "branch_updates": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["goal", "status"],
                "properties": {
                    "id": {**_NULLABLE_STRING, "description": "Existing branch ID; omit or null for a new branch."},
                    "label": {**_STRING, "description": "Name for a new branch so next_step.branch can refer to it."},
                    "kind": {"enum": list(BRANCH_KINDS)},
                    "goal": _STRING,
                    "status": {"enum": ["open", "closed", "suspended"]},
                    "reason": _STRING,
                },
                "additionalProperties": False,
            },
        },
        "subgoal_updates": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["statement", "status"],
                "properties": {
                    "id": _NULLABLE_STRING,
                    "statement": _STRING,
                    "status": {"enum": ["open", "closed", "abandoned"]},
                    "closed_by": _NULLABLE_STRING,
                    "branch": _NULLABLE_STRING,
                },
                "additionalProperties": False,
            },
        },
        "next_step": {
            "type": ["object", "null"],
            "required": ["branch", "task", "goal", "instructions", "success_test", "novelty"],
            "properties": {
                "branch": {"type": "string", "minLength": 1},
                "task": {"enum": ["research", "formalize", "experiment", "literature"]},
                "goal": {"type": "string", "minLength": 1},
                "instructions": {"type": "string", "minLength": 1},
                "success_test": {"type": "string", "minLength": 1},
                "novelty": {
                    "type": "string", "minLength": 1,
                    "description": "How this step differs from every earlier failed attempt and dead end.",
                },
                "deliverables": _TEXT_LIST,
            },
            "additionalProperties": False,
        },
        "promising_directions": _TEXT_LIST,
        "assessment_text": {"type": "string", "minLength": 1},
        "statement_refinement": {
            "type": ["object", "null"],
            "required": ["text", "reason"],
            "properties": {"text": {"type": "string", "minLength": 1}, "reason": {"type": "string", "minLength": 1}},
            "additionalProperties": False,
        },
        "success_criteria": _NULLABLE_STRING,
        "epoch_report": {**_NULLABLE_STRING, "description": "Markdown report; required when the epoch ends."},
        "cross_problem_relevance": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["problem_id", "why"],
                "properties": {"problem_id": _STRING, "claims": _TEXT_LIST, "why": _STRING},
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}

RESEARCHER_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "lean-orch researcher report",
    "type": "object",
    "required": ["summary", "approach", "outcome", "claims"],
    "properties": {
        "summary": {"type": "string", "minLength": 1},
        "approach": {
            "type": "object",
            "required": ["name", "description", "fingerprint"],
            "properties": {
                "name": {"type": "string", "minLength": 1},
                "description": {"type": "string", "minLength": 1},
                "fingerprint": {
                    "type": "string", "minLength": 1,
                    "description": "Canonical 'technique :: target' phrase used to detect repeated approaches.",
                },
            },
            "additionalProperties": False,
        },
        "outcome": {"enum": ["success", "partial", "failure", "inconclusive"]},
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "kind", "statement", "argument"],
                "properties": {
                    "id": {"type": "string", "minLength": 1, "description": "Local ID such as c1."},
                    "kind": {"enum": list(CLAIM_KINDS)},
                    "statement": {"type": "string", "minLength": 1},
                    "argument": {"type": "string", "minLength": 1},
                    "lean": {
                        "type": ["object", "null"],
                        "required": ["file", "declaration"],
                        "properties": {
                            "file": {"type": "string", "minLength": 1},
                            "declaration": {"type": "string", "minLength": 1},
                        },
                        "additionalProperties": False,
                    },
                    "experiment": {**_NULLABLE_STRING,
                                   "description": "Experiment directory (the one containing experiment.json)."},
                    "depends_on": _TEXT_LIST,
                    "subgoal": _NULLABLE_STRING,
                    "resolves_main": {"enum": ["proves", "disproves", None]},
                },
                "additionalProperties": False,
            },
        },
        "failure_reason": _NULLABLE_STRING,
        "dead_ends": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["approach", "reason"],
                "properties": {"approach": _STRING, "reason": _STRING, "scope": _STRING},
                "additionalProperties": False,
            },
        },
        "searches": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["space", "method", "result"],
                "properties": {"space": _STRING, "method": _STRING, "result": _STRING, "experiment": _NULLABLE_STRING},
                "additionalProperties": False,
            },
        },
        "experiments": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["path", "purpose"],
                "properties": {"path": _STRING, "purpose": _STRING, "result_summary": _STRING},
                "additionalProperties": False,
            },
        },
        "new_subgoals": _TEXT_LIST,
        "references": {"type": "array", "items": _REFERENCE},
        "lessons": {**_STRING, "description": "What a future agent should learn from this attempt."},
        "next_steps": _TEXT_LIST,
    },
    "additionalProperties": False,
}

CRITIC_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "lean-orch critic review",
    "type": "object",
    "required": ["reviews", "overall"],
    "properties": {
        "reviews": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "claim", "verdict", "formalization_faithful", "issues", "confidence",
                    "checks_performed", "weakest_step",
                ],
                "properties": {
                    "claim": {"type": "string", "minLength": 1},
                    "verdict": {"enum": ["accept", "reject", "uncertain"]},
                    "formalization_faithful": {"enum": ["yes", "no", "not_applicable"]},
                    "checks_performed": {
                        "type": "array", "items": _STRING,
                        "description": "Concrete checks you ran: recomputations, scripts, Lean commands, sources read.",
                    },
                    "weakest_step": {"type": "string", "minLength": 1},
                    "issues": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["severity", "location", "problem"],
                            "properties": {
                                "severity": {"enum": ["fatal", "major", "minor"]},
                                "location": {"type": "string", "minLength": 1},
                                "problem": {"type": "string", "minLength": 1},
                                "counterexample": _STRING,
                            },
                            "additionalProperties": False,
                        },
                    },
                    "hidden_assumptions": _TEXT_LIST,
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "additionalProperties": False,
            },
        },
        "dead_end_disputes": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["index", "reason"],
                "properties": {"index": {"type": "integer", "minimum": 0}, "reason": _STRING},
                "additionalProperties": False,
            },
        },
        "repeat_of": {**_TEXT_LIST, "description": "Attempt IDs this iteration merely repeats."},
        "retractions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["claim", "reason"],
                "properties": {"claim": _STRING, "reason": {"type": "string", "minLength": 1}},
                "additionalProperties": False,
            },
        },
        "overall": {"type": "string", "minLength": 1},
        "suggested_checks": _TEXT_LIST,
    },
    "additionalProperties": False,
}

SCHEMAS: dict[str, dict[str, Any]] = {
    "triage": TRIAGE_SCHEMA,
    "literature": LITERATURE_SCHEMA,
    "supervisor": SUPERVISOR_SCHEMA,
    "researcher": RESEARCHER_SCHEMA,
    "critic": CRITIC_SCHEMA,
}


def schema_filename(role: str) -> str:
    return f"{role}.schema.json"


def write_schema_files(directory: Path) -> None:
    for role, schema in SCHEMAS.items():
        atomic_write_text(directory / schema_filename(role), json.dumps(schema, indent=2) + "\n")


_TYPES = {
    "object": dict, "array": list, "string": str, "boolean": bool, "null": type(None),
}


def _type_ok(value: Any, name: str) -> bool:
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, _TYPES[name])


def validate(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Validate ``value`` against the supported JSON Schema subset."""
    errors: list[str] = []
    expected = schema.get("type")
    if expected is not None:
        names = expected if isinstance(expected, list) else [expected]
        if not any(_type_ok(value, name) for name in names):
            return [f"{path}: expected {' or '.join(names)}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        return [f"{path}: {value!r} is not one of {schema['enum']}"]
    if isinstance(value, str) and len(value.strip()) < schema.get("minLength", 0):
        errors.append(f"{path}: must be a non-empty string")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: must be at least {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: must be at most {schema['maximum']}")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                errors.append(f"{path}: missing required key {name!r}")
        if schema.get("additionalProperties") is False:
            for name in value:
                if name not in properties:
                    errors.append(f"{path}: unexpected key {name!r}")
        for name, subschema in properties.items():
            if name in value:
                errors.extend(validate(value[name], subschema, f"{path}.{name}"))
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            errors.extend(validate(item, schema["items"], f"{path}[{index}]"))
    return errors


def semantic_errors(role: str, output: dict[str, Any], context: dict[str, Any] | None = None) -> list[str]:
    """Role-specific rules beyond the JSON Schema."""
    context = context or {}
    errors: list[str] = []
    if role == "literature":
        if output.get("still_open") == "no" and not str(output.get("resolution", "")).strip():
            errors.append("$.resolution: required when still_open is 'no'")
    elif role == "triage":
        expected = set(context.get("problem_ids", []))
        seen = [item.get("id") for item in output.get("problems", [])]
        missing = expected - set(seen)
        unknown = set(seen) - expected if expected else set()
        if missing:
            errors.append(f"$.problems: missing assessments for {sorted(missing)}")
        if unknown:
            errors.append(f"$.problems: unknown problem IDs {sorted(unknown)}")
    elif role == "researcher":
        ids = [claim.get("id") for claim in output.get("claims", [])]
        if len(ids) != len(set(ids)):
            errors.append("$.claims: claim IDs must be unique")
        for index, claim in enumerate(output.get("claims", [])):
            if claim.get("resolves_main") and claim.get("kind") != "main_result":
                errors.append(f"$.claims[{index}]: resolves_main requires kind 'main_result'")
            if claim.get("kind") == "formal_statement" and not claim.get("lean"):
                errors.append(f"$.claims[{index}]: a formal_statement claim needs a Lean file and declaration")
    elif role == "critic":
        expected = set(context.get("claim_ids", []))
        reviewed = [review.get("claim") for review in output.get("reviews", [])]
        missing = expected - set(reviewed)
        if missing:
            errors.append(f"$.reviews: every claim needs a review; missing {sorted(missing)}")
        for index, review in enumerate(output.get("reviews", [])):
            serious = [issue for issue in review.get("issues", []) if issue.get("severity") in ("fatal", "major")]
            if review.get("verdict") == "reject" and not serious:
                errors.append(f"$.reviews[{index}]: a rejection must cite at least one fatal or major issue")
            if review.get("verdict") == "accept" and not [c for c in review.get("checks_performed", []) if c.strip()]:
                errors.append(f"$.reviews[{index}]: an acceptance must list the checks you performed")
            if review.get("verdict") == "accept" and serious:
                errors.append(f"$.reviews[{index}]: cannot accept a claim with a fatal or major issue")
    elif role == "supervisor":
        decision = output.get("decision")
        ends_epoch = decision in EPOCH_ENDING_DECISIONS or context.get("epoch_ending", False)
        if not ends_epoch and not output.get("next_step"):
            errors.append("$.next_step: required unless the decision ends the epoch")
        if ends_epoch and not str(output.get("epoch_report") or "").strip():
            errors.append("$.epoch_report: required when the epoch ends")
        if context.get("expects_assessment") and output.get("assessment") is None:
            errors.append("$.assessment: required after an iteration has been recorded")
        if decision == "reformulate" and not output.get("statement_refinement"):
            errors.append("$.statement_refinement: required for decision 'reformulate'")
        if decision == "branch" and not any(
            not update.get("id") for update in output.get("branch_updates", [])
        ):
            errors.append("$.branch_updates: decision 'branch' must open at least one new branch")
        labels = {update.get("label") for update in output.get("branch_updates", []) if update.get("label")}
        known = set(context.get("branch_ids", [])) | labels
        step = output.get("next_step")
        if step and known and step.get("branch") not in known:
            errors.append(f"$.next_step.branch: {step.get('branch')!r} is neither an existing branch nor a new label")
    return errors
