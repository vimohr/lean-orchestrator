"""Offline end-to-end demonstration; no model requests or simulated spending."""

import json

from lean_orchestrator.usage import TokenUsage

from .backends import Completion


PACKET = {
    "id": "consecutive-parity", "title": "Consecutive integer products",
    "original_statement": "For every integer n, n(n+1) is even.",
    "scope_contract": {"quantifiers": ["all integers n"], "dimensions": [], "allowed_resources": [],
                       "conventions": ["even means divisible by 2"], "source_locators": [], "unresolved": []},
    "sources": [],
}


def allocation(problem_id: str = "consecutive-parity", **changes) -> dict:
    return {"problem_id": problem_id, "role": "exploration", "action": "explore", "target_scope": "original",
            "target": "Prove the parity statement for every integer.", "baseline": "No inherited result.",
            "remaining_gap": "A proof covering every integer.", "method": "parity cases",
            "success_evidence": "A complete elementary proof, with a reproducible illustrative calculation.",
            "why_now": "A small certifiable opening.", "alternative": "Explore a different finite problem.",
            "reversal_condition": "If a parity case fails, inspect it before further generation.",
            "revisit_condition": "A new method addressing a named obstruction.", "max_tokens": 80_000,
            "baseline_candidates": [], **changes}


def review(**changes) -> dict:
    return {"verdict": "accept", "conclusive": True, "proof_matches": True, "scope_matches": True,
            "target_met": True, "material_advance": True, "novel": True, "original_resolved": True,
            "advance": "An elementary proof of the original statement.", "remaining_gap": "",
            "checks": ["Checked both parity cases; finite computation is illustrative only."], "issues": [], **changes}


class DemoBackend:
    """Return deterministic saved artifacts, explicitly recording zero model usage."""
    def __init__(self) -> None:
        self.requests: list[dict] = []

    def complete(self, request, work, max_output_tokens):
        self.requests.append(request)
        role = request["role"]
        if role == "solver":
            proof = "If n is even, 2 divides n(n+1). If n is odd, n+1 is even, so again 2 divides n(n+1)."
            (work / "proof.md").write_text(proof)
            (work / "verify.py").write_text(
                "import json\njson.dump({'checked': 101, 'even': all(n*(n+1)%2 == 0 for n in range(-50,51))}, open('result.json','w'))\n")
            claim = {"kind": "result", "statement": PACKET["original_statement"], "scope": "original",
                     "proof_path": "proof.md", "advance": "Both parity cases establish the full claim.",
                     "remaining_gap": "", "depends_on": [],
                     "experiment": {"script": "verify.py", "result_file": "result.json",
                                    "expected": {"checked": 101, "even": True}, "certificate": "The proof uses parity, not finite sampling."}}
            (work / "draft.json").write_text(json.dumps({"complete": True, "claim": claim}))
            data = {"state": "Proof saved in proof.md; complete submission in draft.json.",
                    "action": {"kind": "none"}, "draft_path": "draft.json", "ready_to_submit": True}
        elif role == "submitter":
            data = {"claim": request["draft"]["claim"]}
        elif role == "reviewer":
            data = review()
        else:
            data = {"allocations": [allocation()], "rationale": "Offline demonstration of the research workflow."}
        return Completion(data=data, usage=TokenUsage(0, 0), stdout=json.dumps(data))
