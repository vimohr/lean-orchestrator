from __future__ import annotations

import json
import sys

from lean_orchestrator.config import CitationsConfig, ExperimentsConfig, LeanConfig
from lean_orchestrator.paths import WorkspacePaths
from lean_orchestrator.state import FormalStatement, ResearchState
from lean_orchestrator.verification import Verifier, resolve_workspace_path
from lean_orchestrator.verify.citations import CitationChecker
from lean_orchestrator.verify.experiments import ExperimentVerifier
from lean_orchestrator.verify.lean import LeanVerifier


def verifier(tmp_path) -> Verifier:
    paths = WorkspacePaths(tmp_path)
    return Verifier(paths, LeanVerifier(paths, LeanConfig(enabled=False)),
                    ExperimentVerifier(paths, ExperimentsConfig(python=(sys.executable,))),
                    CitationChecker(enabled=CitationsConfig(enabled=False).enabled))


def test_paths_resolve_against_workspace_then_problem(tmp_path):
    paths = WorkspacePaths(tmp_path)
    problem = paths.problem("p", "P")
    (problem.root / "experiments" / "e").mkdir(parents=True)
    assert resolve_workspace_path(paths, problem, "experiments/e") == problem.root / "experiments" / "e"
    assert resolve_workspace_path(paths, problem, "problems/p/experiments/e") == problem.root / "experiments" / "e"
    assert resolve_workspace_path(paths, problem, "/abs/x") == type(tmp_path)("/abs/x")


def test_report_verification_without_lean(tmp_path):
    check = verifier(tmp_path)
    problem = check.paths.problem("p", "P")
    experiment = problem.experiments_dir / "e"
    experiment.mkdir(parents=True)
    (experiment / "run.py").write_text("import json\njson.dump({'passed': True}, open('result.json', 'w'))\n")
    (experiment / "experiment.json").write_text(json.dumps({"script": "run.py"}))
    (experiment / "result.json").write_text(json.dumps({"passed": True}))
    state = ResearchState(problem_id="p", title="t", folder="p", lean_namespace="P")
    state.formal_statements.append(FormalStatement(file="lean/OpenQ/Problems/P/Statement.lean",
                                                   decl="OpenQ.Problems.P.MainStatement", claim="C1"))
    report = {"claims": [
        {"id": "c1", "kind": "lemma", "statement": "s", "argument": "a", "experiment": "problems/p/experiments/e"},
        {"id": "c2", "kind": "main_result", "statement": "s", "argument": "a", "resolves_main": "proves",
         "lean": {"file": "lean/OpenQ/Problems/P/Main.lean", "declaration": "OpenQ.Problems.P.main"}},
    ], "references": [{"citation": "Some paper", "arxiv": "2405.09613"}]}
    outcome = check.verify_report(report, state, problem)
    assert outcome.claims["c1"].experiment.ok
    assert outcome.claims["c2"].lean is None and "not available" in outcome.claims["c2"].notes[0]
    assert outcome.references[0].check.status == "unchecked"
    assert not outcome.lean_available
