"""Freeze before checking; reproduction and inference receive separate labels."""

import json
import shutil
import tempfile
from dataclasses import asdict
from pathlib import Path

from lean_orchestrator.config import LeanConfig
from lean_orchestrator.paths import WorkspacePaths
from lean_orchestrator.verify.lean import LeanVerifier

from .models import Candidate, Policy
from .sandbox import Sandbox
from .storage import check_seal, safe_path


def verify(candidate: Candidate, policy: Policy, sandbox: Sandbox, root: Path) -> dict:
    frozen = safe_path(root, candidate.frozen_dir)
    check_seal(frozen, candidate.manifest)
    claim = candidate.claim
    report = {"ok": True, "execution_failed": False, "evidence": "informal_argument", "runs": []}
    experiment = claim.get("experiment")
    if experiment:
        report["evidence"] = "reproduced_code"
        for _ in range(2):
            with tempfile.TemporaryDirectory(prefix="lean-orch-v2-verify-") as scratch:
                work = Path(scratch) / "work"
                shutil.copytree(frozen / "artifacts", work)
                result_file = safe_path(work, experiment["result_file"])
                # A submitted result cannot stand in for an actual fresh execution.
                result_file.unlink(missing_ok=True)
                result = sandbox.run(work, experiment["script"])
                try:
                    output = json.loads(result_file.read_text()) if result.get("ok") else None
                except (OSError, ValueError):
                    output = None
                try:
                    matches = (output is not None and json.dumps(output, sort_keys=True, allow_nan=False)
                               == json.dumps(experiment["expected"], sort_keys=True, allow_nan=False))
                except ValueError:
                    matches = False
                result["declared_output_matches"] = matches
                result["result"] = output
                report["runs"].append(result)
                report["execution_failed"] |= result.get("execution_failed", False)
                report["ok"] &= bool(result.get("ok") and result["declared_output_matches"])
        report["certificate_claim"] = experiment.get("certificate", "")
        report["limitation"] = "Reproduction establishes recorded code behaviour; mathematical inference requires review."
    lean = claim.get("lean")
    if lean:
        if not policy.lean_template:
            report.update(ok=False, execution_failed=True, lean={"error": "a prepared lean_template is required"})
        else:
            with tempfile.TemporaryDirectory(prefix="lean-orch-v2-lean-") as scratch:
                work = Path(scratch)
                shutil.copytree(Path(policy.lean_template).expanduser(), work / "lean")
                submitted = frozen / "artifacts" / "lean"
                if submitted.exists():
                    # Build configuration and dependencies come from the prepared,
                    # trusted template; submitted mathematics can only add OpenQ modules.
                    for source in (submitted / "OpenQ").rglob("*.lean"):
                        target = safe_path(work / "lean", source.relative_to(submitted).as_posix())
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(source, target)
                checker = LeanVerifier(WorkspacePaths(work), LeanConfig())
                if not checker.available():
                    report.update(ok=False, execution_failed=True, lean={"error": "prepared Lean tools are unavailable"})
                    return report
                check = checker.check(safe_path(work / "lean", lean["file"]), lean["declaration"],
                                      allowed_root=work / "lean" / "OpenQ",
                                      target=lean.get("target"), negated=lean.get("negated", False), kernel_recheck=True)
                report["lean"] = asdict(check)
                report["ok"] &= check.ok
                if check.ok:
                    report["evidence"] = "lean_checked_declaration"
    check_seal(frozen, candidate.manifest)
    return report
