"""Validate model decisions before they can become research facts."""

from .storage import safe_path


CLAIM_FIELDS = ("statement", "scope", "proof_path", "advance", "remaining_gap")
REVIEW_FIELDS = ("verdict", "conclusive", "proof_matches", "scope_matches", "target_met",
                 "material_advance", "novel", "original_resolved", "advance", "remaining_gap", "checks", "issues")


def require_text(data: dict, fields: tuple[str, ...]) -> None:
    for field in fields:
        if not isinstance(data.get(field), str) or not data[field].strip():
            raise ValueError(f"{field} must be a nonempty string")


def claim_errors(claim: dict, work) -> None:
    if not isinstance(claim, dict):
        raise ValueError("claim must be an object")
    require_text(claim, ("statement", "proof_path", "advance"))
    if claim.get("scope") not in ("original", "restricted") or not isinstance(claim.get("remaining_gap"), str):
        raise ValueError("claim requires an explicit scope and remaining_gap")
    if claim["scope"] == "restricted" and not claim["remaining_gap"].strip():
        raise ValueError("a restricted result must name the remaining original-problem gap")
    proof = safe_path(work, claim["proof_path"])
    if not proof.is_file() or not proof.read_text().strip():
        raise ValueError("claim must point to a complete inspectable proof/draft")
    if not isinstance(claim.get("depends_on", []), list) or not all(isinstance(item, str) for item in claim.get("depends_on", [])):
        raise ValueError("depends_on must contain candidate identifiers")
    experiment = claim.get("experiment")
    if experiment is not None:
        require_text(experiment, ("script", "result_file"))
        if not isinstance(experiment.get("expected"), dict):
            raise ValueError("numerical submission must declare its expected JSON result")
        for key in ("script", "result_file"):
            safe_path(work, experiment[key])
        if not safe_path(work, experiment["script"]).is_file():
            raise ValueError("numerical verifier script is missing")


def validate_review(review: dict) -> None:
    if any(field not in review for field in REVIEW_FIELDS):
        raise ValueError("review is missing required assessment fields")
    if review["verdict"] not in ("accept", "reject", "uncertain"):
        raise ValueError("review verdict must be accept, reject or uncertain")
    for field in ("conclusive", "proof_matches", "scope_matches", "target_met", "material_advance", "novel", "original_resolved"):
        if type(review[field]) is not bool:
            raise ValueError(f"review.{field} must be boolean")
    for field in ("advance", "remaining_gap"):
        if not isinstance(review[field], str):
            raise ValueError(f"review.{field} must be text")
    for field in ("checks", "issues"):
        if not isinstance(review[field], list) or not all(isinstance(item, str) for item in review[field]):
            raise ValueError(f"review.{field} must be a list of text")
    if review["original_resolved"] and review["remaining_gap"].strip():
        raise ValueError("an original resolution cannot leave a remaining original-problem gap")
