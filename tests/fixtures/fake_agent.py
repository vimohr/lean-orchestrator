"""A scripted stand-in for CLI agents, used by the end-to-end tests.

The orchestrator runs ``python fake_agent.py <prompt>`` for every role. The script
reads the role and output path from the environment, and the behaviour for the
current problem from ``fake_scenario.json`` in the workspace:

* ``progress``: the researcher proves a lemma backed by a reproducible
  experiment and the critic accepts it;
* ``stagnate``: the researcher reports failures that the supervisor scores 0;
* ``resolve``: the researcher claims an informal resolution that the critic accepts;
* ``tamper``: like ``progress``, but the researcher also edits ``state.json``;
* ``garbage``: the researcher writes invalid JSON every time.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

workspace = Path(os.environ["LEAN_ORCH_WORKSPACE"])
role = os.environ["LEAN_ORCH_ROLE"]
output = os.environ.get("LEAN_ORCH_OUTPUT", "")
problem = os.environ.get("LEAN_ORCH_PROBLEM", "")
prompt = sys.argv[-1]
scenario_file = workspace / "fake_scenario.json"
scenarios = json.loads(scenario_file.read_text()) if scenario_file.is_file() else {}
scenario = scenarios.get(problem, scenarios.get("*", "progress"))
calls_file = workspace / "fake_calls.jsonl"
with calls_file.open("a") as handle:
    handle.write(json.dumps({"role": role, "problem": problem, "scenario": scenario}) + "\n")


def write(data) -> None:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def problem_folder() -> Path:
    match = re.search(r"Folder: `([^`]+)`", prompt) or re.search(r"`(problems/[^/`]+)/DOSSIER\.md`", prompt)
    if match is None:
        raise SystemExit("could not find the problem folder in the prompt")
    return workspace / match.group(1)


def state() -> dict:
    return json.loads((problem_folder() / "state.json").read_text())


if role == "triage":
    ids = re.search(r"using their exact IDs: (.+)\.", prompt).group(1).split(", ")
    write({"problems": [
        {
            "id": problem_id,
            "scores": {"precision": 4, "finite_dimensional": 4, "formalizability": 3, "closability": 4,
                       "tractable_subcases": 4, "background": 3},
            "recommendation": "defer" if "defer" in problem_id else "activate",
            "modes": ["prove", "disprove"],
            "likely_resolved": False,
            "rationale": "A sharp finite-dimensional statement.",
            "first_steps": "Check small dimensions numerically.",
        }
        for problem_id in ids
    ]})
elif role == "literature":
    write({
        "still_open": "no" if scenario == "known" else "yes",
        "summary": "No resolution found in the recent literature.",
        "resolution": "Solved by Example et al." if scenario == "known" else "",
        "precise_statement": "For every $d \\geq 2$, the quantity $Q_d$ is nonnegative.",
        "success_criteria": "A proof for all d, or an explicit d with Q_d < 0.",
        "assumptions": ["Finite dimension d"],
        "references": [{"citation": "A. Author, Some paper (2020)", "relevance": "background"}],
        "known_results": [{"statement": "Q_2 >= 0.", "reference": "A. Author (2020)"}],
        "techniques": ["semidefinite programming"],
        "suggested_branches": [],
        "formalization_notes": "Matrices over C with PosSemidef.",
    })
elif role == "researcher":
    if scenario == "garbage":
        Path(output).write_text("this is not json")
        raise SystemExit(0)
    if scenario == "tamper":
        state_path = problem_folder() / "state.json"
        state_path.write_text(state_path.read_text().replace('"stagnation_count": 0', '"stagnation_count": 99'))
    iteration_dir = Path(output).parent
    tag = iteration_dir.name
    if scenario in ("progress", "tamper"):
        experiment = problem_folder() / "experiments" / f"check-{tag}"
        experiment.mkdir(parents=True, exist_ok=True)
        (experiment / "verify.py").write_text(
            "import json\nfrom fractions import Fraction\n"
            "value = Fraction(1, 3) + Fraction(1, 6)\n"
            "json.dump({'value': float(value), 'exact': str(value), 'passed': value == Fraction(1, 2)},"
            " open('result.json', 'w'))\n"
        )
        (experiment / "experiment.json").write_text(json.dumps({"script": "verify.py", "result": "result.json"}))
        (experiment / "result.json").write_text(json.dumps({"value": 0.5, "exact": "1/2", "passed": True}))
        rel = experiment.relative_to(workspace).as_posix()
        write({
            "summary": f"Established a lemma in {tag}.",
            "approach": {"name": f"exact computation {tag}", "description": f"Rational arithmetic check {tag}.",
                         "fingerprint": f"exact rational certificate :: subcase {tag}"},
            "outcome": "success",
            "claims": [{"id": "c1", "kind": "lemma", "statement": f"Lemma {tag}: 1/3 + 1/6 = 1/2.",
                        "argument": "Direct computation.", "experiment": rel, "depends_on": []}],
            "dead_ends": [{"approach": f"naive bound {tag}", "reason": "too weak", "scope": "all d"}],
            "searches": [], "experiments": [{"path": rel, "purpose": "exact check"}],
            "new_subgoals": [f"Generalize {tag}"], "references": [], "lessons": "Exact arithmetic works.",
            "next_steps": [],
        })
    elif scenario == "stagnate":
        write({
            "summary": "No progress.",
            "approach": {"name": "same idea again", "description": "The same bound as before.",
                         "fingerprint": "triangle inequality :: main statement"},
            "outcome": "failure", "claims": [], "failure_reason": "The bound is too weak.",
            "dead_ends": [], "searches": [], "experiments": [], "new_subgoals": [], "references": [],
            "next_steps": [],
        })
    elif scenario == "resolve":
        write({
            "summary": "Resolved the problem.",
            "approach": {"name": "full proof", "description": "A complete argument.",
                         "fingerprint": "convexity :: main statement"},
            "outcome": "success",
            "claims": [{"id": "c1", "kind": "main_result", "statement": "The statement holds for all d.",
                        "argument": "By convexity.", "resolves_main": "proves"}],
        })
elif role == "critic":
    report = json.loads(Path(output).with_name("report.json").read_text())
    write({
        "reviews": [
            {"claim": claim["id"], "verdict": "accept", "formalization_faithful": "not_applicable",
             "issues": [], "hidden_assumptions": [], "confidence": 0.9,
             "checks_performed": ["recomputed the value by hand"], "weakest_step": "none identified"}
            for claim in report.get("claims", [])
        ],
        "overall": "The claims hold.",
    })
elif role == "supervisor":
    current = state()
    branches = [branch["id"] for branch in current["branches"] if branch["status"] == "open"]
    after_iteration = "the iteration just recorded" in prompt.lower()
    ending = "this epoch ends after your decision" in prompt.lower()
    score = {"progress": 2, "tamper": 2, "stagnate": 0, "resolve": 3}.get(scenario, 1)
    decision = {
        "assessment": ({"progress_score": score, "categories": ["new_lemma"] if score else ["none"],
                        "justification": "Scored by the fake supervisor.", "repeated_approach": False}
                       if after_iteration else None),
        "decision": "continue",
        "rationale": "Keep going.",
        "branch_updates": [] if branches else [
            {"label": "p", "kind": "prove", "goal": "Prove it.", "status": "open"}],
        "subgoal_updates": [],
        "next_step": {"branch": branches[0] if branches else "p", "task": "research",
                      "goal": "Do the next small step.", "instructions": "Compute exactly.",
                      "success_test": "An exact certificate.", "novelty": "A new subcase."},
        "promising_directions": ["exact certificates"],
        "assessment_text": "Steady progress.",
        "epoch_report": "## Summary\n\nThe fake epoch went fine." if ending else None,
        "cross_problem_relevance": [],
    }
    write(decision)
elif role == "repair":
    raise SystemExit("unexpected role")
