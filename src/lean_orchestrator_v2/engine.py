"""Allocation decisions and bounded attempts with persistent artifacts."""

import copy
import json
import shutil
import threading
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

from lean_orchestrator.jsonio import utc_now, write_json
from lean_orchestrator.serde import from_dict, to_dict

from . import prompts
from .assessment import verify
from .backends import Backend
from .budget import BudgetExhausted, BudgetPool
from .config import Config
from .context import TokenCounter
from .contracts import claim_errors, require_text, validate_review
from .models import Allocation, Attempt, Candidate
from .sandbox import Sandbox
from .storage import Store, check_seal, copy_files, safe_path, seal


def normalized(statement: str) -> str:
    return " ".join(statement.split()).casefold().rstrip(".")


class Harness:
    def __init__(self, store: Store, config: Config, *, backends: dict | None = None, sandbox: Sandbox | None = None) -> None:
        config.validate()
        self.store, self.config, self.policy = store, config, config.policy
        self.campaign = store.load()
        self.pool = BudgetPool(store.root, self.policy.max_tokens, self.policy.call_tokens)
        self.counter = TokenCounter(self.policy.tokenizer)
        self.backends = backends or {role: Backend(getattr(config, role)) for role in ("solver", "coordinator", "reviewer")}
        self.sandbox = sandbox or Sandbox(self.policy)
        self.lock = threading.RLock()

    def save(self) -> None:
        with self.lock:
            self.store.save(self.campaign)

    def _checkpoint(self, attempt: Attempt, candidate: Candidate | None = None) -> None:
        with self.lock:
            self.campaign.attempts[attempt.id] = copy.deepcopy(attempt)
            if candidate is not None:
                self.campaign.candidates[candidate.id] = copy.deepcopy(candidate)
            self.save()

    def _stopped(self) -> bool:
        return (self.store.root / "STOP").exists()

    def _round_limits(self, allocations: list[Allocation]) -> None:
        for role, slots in (("focus", self.policy.focus_slots), ("exploration", self.policy.exploration_slots)):
            ids = {item.problem_id for item in allocations if item.role == role and item.action not in ("park", "close_target")}
            if len(ids) > slots:
                raise ValueError(f"proposal exceeds {role} slots")
        for problem_id in {item.problem_id for item in allocations}:
            tasks = [item for item in allocations if item.problem_id == problem_id and item.action not in ("park", "close_target")]
            if len({item.role for item in tasks}) > 1:
                raise ValueError("one original problem cannot occupy different roles in the same round")
            if tasks and tasks[0].role != "audit" and len(tasks) > self.policy.max_arms_per_problem:
                raise ValueError("proposal exceeds the per-problem arm limit")

    def propose(self, proposals: list[dict] | None = None) -> list[Allocation]:
        if proposals is None:
            request = {"role": "coordinator", "instruction": prompts.COORDINATOR, "policy": to_dict(self.policy),
                       "usage": self.pool.ledger.summary(), "problems": [], "candidates": []}
            for problem in self.campaign.problems.values():
                request["problems"].append({**to_dict(problem), "packet": self.store.packet(problem)})
            request["candidates"] = [{"id": item.id, "problem_id": item.problem_id, "status": item.status,
                                      "claim": item.claim, "review": item.review, "verification": item.verification,
                                      "human_review": item.human_review, "external_confirmation": item.external_confirmation}
                                     for item in self.campaign.candidates.values()]
            planning = self.store.root / "coordination"
            planning.mkdir(exist_ok=True)
            if hasattr(self.backends["coordinator"], "preflight"):
                self.backends["coordinator"].preflight()
            reply = self.pool.call(self.backends["coordinator"], request, planning, reserve=self.policy.call_tokens)
            if not isinstance(reply.get("allocations"), list):
                raise ValueError("coordinator must return an allocations list")
            proposals = reply["allocations"]
            self.campaign.decisions.append({"actor": "coordinator", "time": utc_now(), "rationale": reply.get("rationale", "")})
        prepared = []
        for proposal in proposals:
            proposal = {**proposal, "id": uuid.uuid4().hex[:12], "approved": False,
                        "approval_reason": "", "status": "proposed", "decision_actor": "coordinator"}
            allocation = from_dict(Allocation, proposal)
            self._validate_allocation(allocation)
            prepared.append(allocation)
        self._round_limits(prepared)
        for allocation in prepared:
            problem = self.campaign.problems[allocation.problem_id]
            if allocation.role == "focus" and problem.focus_rounds and self.policy.focus_renewal_requires_approval:
                allocation.status = "pending_approval"
            self.campaign.allocations[allocation.id] = allocation
        self.save()
        return prepared

    def _validate_allocation(self, allocation: Allocation) -> None:
        problem = self.campaign.problems.get(allocation.problem_id)
        if problem is None:
            raise ValueError(f"unknown problem {allocation.problem_id}")
        require_text(to_dict(allocation), ("target", "baseline", "remaining_gap", "method", "success_evidence",
                                          "why_now", "alternative", "reversal_condition", "revisit_condition"))
        if allocation.role not in ("focus", "exploration", "audit"):
            raise ValueError("allocation role must be focus/exploration/audit")
        if allocation.action not in ("explore", "continue", "redirect", "audit", "repair", "park", "close_target"):
            raise ValueError("unknown allocation action")
        if allocation.target_scope not in ("original", "restricted"):
            raise ValueError("allocation requires an explicit target scope")
        ceiling = {"focus": self.policy.focus_tokens, "exploration": self.policy.exploration_tokens,
                   "audit": self.policy.audit_tokens}[allocation.role]
        minimum = (self.policy.review_tokens if allocation.role == "audit" else
                   self.policy.submission_tokens + self.policy.review_tokens
                   + (0 if allocation.action == "repair" else self.policy.call_tokens))
        if type(allocation.max_tokens) is not int or not minimum <= allocation.max_tokens <= ceiling:
            raise ValueError("allocation budget must fit its role ceiling and protected later phases")
        packet = self.store.packet(problem)
        if packet["scope_contract"]["unresolved"] and allocation.target_scope == "original" and allocation.role != "audit":
            raise ValueError("original-scope work needs the source interpretation gaps resolved first")
        for identifier in allocation.baseline_candidates:
            item = self.campaign.candidates.get(identifier)
            if item is None or item.status != "retained":
                raise ValueError("inherited candidate must be retained with its checking status")
        if allocation.role == "focus" or allocation.action == "continue":
            if not allocation.baseline_candidates and not packet.get("checked_prior_work"):
                raise ValueError("focus/continuation requires checked prior work and a concrete gap")
        if allocation.action in ("audit", "close_target"):
            item = self.campaign.candidates.get(allocation.candidate_id)
            if item is None or item.problem_id != problem.id:
                raise ValueError("audit/close_target requires a saved candidate for this problem")
        if allocation.role == "audit" and allocation.action != "audit":
            raise ValueError("audit slots only assess frozen candidates")
        if allocation.action == "audit" and allocation.role != "audit":
            raise ValueError("candidate audits must use the audit role")
        if problem.status in ("awaiting_human", "awaiting_audit", "closed") and allocation.action not in ("audit", "close_target", "park"):
            raise ValueError("freeze complete/disputed candidates for review before generating variants")
        if problem.status == "parked" and allocation.action not in ("audit", "close_target", "park"):
            if not problem.revisit_credit or allocation.method != problem.revisit_method:
                raise ValueError("parked problem needs a documented human-authorized new method")
        if allocation.action == "repair":
            old = self.campaign.attempts.get(allocation.repair_attempt_id)
            if old is None or old.problem_id != problem.id or not old.checkpoint:
                raise ValueError("repair requires an inspectable checkpoint from the failed attempt")

    def approve(self, allocation_id: str, reason: str) -> None:
        if not reason.strip():
            raise ValueError("human approval needs a reason")
        allocation = self.campaign.allocations[allocation_id]
        if allocation.status != "pending_approval":
            raise ValueError("allocation is not pending approval")
        allocation.approved, allocation.approval_reason, allocation.status = True, reason, "proposed"
        self.campaign.decisions.append({"actor": "human", "action": "approve_allocation", "allocation": allocation_id,
                                        "reason": reason, "time": utc_now()})
        self.save()

    def run_round(self, allocation_ids: list[str] | None = None) -> dict:
        allocations = [self.campaign.allocations[identifier] for identifier in allocation_ids] if allocation_ids else [
            item for item in self.campaign.allocations.values() if item.status == "proposed"]
        allocations = [item for item in allocations if item.status == "proposed"]
        for allocation in allocations:
            if (allocation.role == "focus" and self.campaign.problems[allocation.problem_id].focus_rounds
                    and self.policy.focus_renewal_requires_approval and not allocation.approved):
                allocation.status = "pending_approval"
        allocations = [item for item in allocations if item.status == "proposed"]
        self._round_limits(allocations)
        if not allocations:
            return {"round": self.campaign.round, "attempts": [], "reason": "no approved allocations"}
        # Revalidate saved plans against current evidence and human policy before dispatch.
        for allocation in allocations:
            self._validate_allocation(allocation)
        self.campaign.round += 1
        current_round = self.campaign.round
        results: list[Attempt] = []
        queue = []
        for allocation in allocations:
            problem = self.campaign.problems[allocation.problem_id]
            if allocation.action == "park":
                problem.status, problem.revisit_condition = "parked", allocation.revisit_condition
                allocation.status = "completed"
                continue
            if allocation.action == "close_target":
                candidate = self.campaign.candidates[allocation.candidate_id]
                if candidate.status != "retained":
                    raise ValueError("only a retained result can close its declared target")
                allocation.status = "completed"
                self.campaign.decisions.append({"actor": "coordinator", "action": "close_target", "candidate": candidate.id,
                                                "scope": candidate.claim["scope"], "time": utc_now()})
                continue
            queue.append(allocation)
        self.save()
        with ThreadPoolExecutor(max_workers=self.policy.parallel) as executor:
            running = {}
            while queue or running:
                while queue and len(running) < self.policy.parallel and not self._stopped():
                    allocation = queue[0]
                    if not self.pool.acquire(allocation.id, allocation.max_tokens):
                        break
                    queue.pop(0)
                    problem = self.campaign.problems[allocation.problem_id]
                    if problem.status == "parked":
                        problem.revisit_credit = 0
                    allocation.status = "running"
                    attempt = Attempt(uuid.uuid4().hex[:12], allocation.id, allocation.problem_id, current_round, allocation.role)
                    self._checkpoint(attempt)
                    running[executor.submit(self._attempt, copy.deepcopy(allocation), attempt)] = allocation
                if not running:
                    break  # unused allowance does not oblige another allocation
                done, _ = wait(running, timeout=0.5, return_when=FIRST_COMPLETED)
                for future in done:
                    allocation = running.pop(future)
                    try:
                        results.append(future.result())
                    finally:
                        self.pool.release(allocation.id)
                    allocation.status = "completed"
                    self.save()
        self._integrate_round(current_round, results)
        return {"round": current_round, "attempts": [to_dict(item) for item in results],
                "pending": [item.id for item in queue], "usage": self.pool.ledger.summary()}

    def _prepare(self, allocation: Allocation, attempt: Attempt) -> tuple[Path, dict]:
        problem = self.campaign.problems[allocation.problem_id]
        packet = self.store.packet(problem)
        directory = self.store.root / "attempts" / attempt.id
        work = directory / "work"
        work.mkdir(parents=True)
        library = work / "library"
        copy_files(safe_path(self.store.root, problem.packet), library / "packet")
        inherited = []
        for identifier in allocation.baseline_candidates:
            candidate = copy.deepcopy(self.campaign.candidates[identifier])
            frozen = safe_path(self.store.root, candidate.frozen_dir)
            check_seal(frozen, candidate.manifest)
            copy_files(frozen, library / "prior" / identifier)
            inherited.append({"id": identifier, "claim": candidate.claim, "checking_status": candidate.status,
                              "verification": candidate.verification, "review": candidate.review,
                              "human_review": candidate.human_review, "external_confirmation": candidate.external_confirmation,
                              "path": f"library/prior/{identifier}"})
        if allocation.action == "repair":
            old = self.campaign.attempts[allocation.repair_attempt_id]
            old_work = self.store.root / "attempts" / old.id / "work"
            for path in old_work.rglob("*"):
                if path.is_file() and "library" not in path.relative_to(old_work).parts:
                    target = safe_path(work, path.relative_to(old_work).as_posix())
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if path.is_symlink():
                        raise ValueError("repair checkpoint contains a symlink")
                    shutil.copyfile(path, target)
            attempt.checkpoint = old.checkpoint
        sources = [{**source, "path": f"library/packet/{source['path']}"} for source in packet["sources"]]
        fixed = {**packet, "sources": sources, "allocation": to_dict(allocation), "inherited": inherited}
        write_json(directory / "fixed-packet.json", fixed)
        seal(library)
        self.sandbox.preflight(work)
        for role in ("solver", "reviewer"):
            if hasattr(self.backends[role], "preflight"):
                self.backends[role].preflight()
        return work, fixed

    def _draft(self, attempt: Attempt, work: Path) -> dict | None:
        relative = attempt.checkpoint or "draft.json"
        path = safe_path(work, relative)
        if not path.is_file():
            return None
        try:
            draft = json.loads(path.read_text())
            if draft.get("complete") is not True:
                return None
            claim_errors(draft.get("claim"), work)
            attempt.checkpoint = relative
            return draft
        except (ValueError, TypeError, AttributeError):
            return None

    def _attempt(self, allocation: Allocation, attempt: Attempt) -> Attempt:
        candidate = None
        try:
            if allocation.role == "audit":
                if hasattr(self.backends["reviewer"], "preflight"):
                    self.backends["reviewer"].preflight()
                candidate = copy.deepcopy(self.campaign.candidates[allocation.candidate_id])
                frozen = safe_path(self.store.root, candidate.frozen_dir)
                check_seal(frozen, candidate.manifest)
                fixed = json.loads((frozen / "packet.json").read_text())
                self._assess(allocation, attempt, candidate, fixed)
                # Audits update checking status but are not failed mathematical research rounds.
                attempt.effective = False
                self._checkpoint(attempt, candidate)
                return attempt
            work, fixed = self._prepare(allocation, attempt)
            if allocation.action != "repair":
                for turn in range(self.policy.turns):
                    if self._stopped():
                        attempt.status = "interrupted"
                        return attempt
                    try:
                        reply = self.pool.call(self.backends["solver"],
                            {"role": "solver", "instruction": prompts.SOLVER, "packet": fixed,
                             "state": attempt.state, "feedback": attempt.feedback}, work,
                            reserve=self.policy.call_tokens, allocation_id=allocation.id, attempt_id=attempt.id,
                            retain=self.policy.submission_tokens + self.policy.review_tokens)
                        check_seal(work / "library")
                        if not isinstance(reply.get("state"), str) or not isinstance(reply.get("action"), dict):
                            raise ValueError("solver requires state and one action")
                        attempt.state = self.counter.clip(reply["state"], self.policy.state_tokens)
                        feedback = self.sandbox.action(work, reply["action"])
                        check_seal(work / "library")
                        attempt.feedback = self.counter.clip(json.dumps(feedback), self.policy.feedback_tokens)
                        write_json(work.parent / f"feedback-{turn + 1:03d}.json", feedback)
                        if reply.get("draft_path"):
                            safe_path(work, reply["draft_path"])
                            attempt.checkpoint = reply["draft_path"]
                        attempt.turns = turn + 1
                        self._checkpoint(attempt)
                        if reply.get("ready_to_submit") is True and self._draft(attempt, work):
                            break
                    except BudgetExhausted:
                        break
                    except (RuntimeError, ValueError, OSError) as error:
                        attempt.failures.append(str(error))
                        attempt.feedback = self.counter.clip(str(error), self.policy.feedback_tokens)
                        break
            draft = self._draft(attempt, work)
            check_seal(work / "library")
            if self._stopped():
                attempt.status = "interrupted"
                return attempt
            if draft is None:
                attempt.status = "execution_failed" if attempt.failures else "unsubmitted"
                return attempt
            # Synthesis is an explicit exception, bounded in characters and fed only
            # complete artifacts; ordinary inputs never replay the saved transcript.
            proof = safe_path(work, draft["claim"]["proof_path"]).read_text()
            if len(json.dumps(draft)) + len(proof) > self.policy.synthesis_chars:
                attempt.status = "unsubmitted"
                attempt.failures.append("complete draft exceeds final-synthesis field allowance")
                return attempt
            submitted = self.pool.call(self.backends["solver"],
                {"role": "submitter", "instruction": prompts.SUBMITTER, "packet": fixed,
                 "draft": draft, "proof": proof}, work, reserve=self.policy.submission_tokens,
                allocation_id=allocation.id, attempt_id=attempt.id, retain=self.policy.review_tokens)
            check_seal(work / "library")
            claim = submitted.get("claim")
            claim_errors(claim, work)
            if claim["scope"] == "original" and fixed["scope_contract"]["unresolved"]:
                raise ValueError("original resolution has unresolved source interpretation gaps")
            if claim != draft["claim"]:
                raise ValueError("submission changed the frozen draft's mathematical claim or artifact references")
            if any(identifier not in allocation.baseline_candidates for identifier in claim.get("depends_on", [])):
                raise ValueError("submission depends on work outside its fixed inherited baseline")
            candidate_id = uuid.uuid4().hex[:12]
            frozen = self.store.root / "candidates" / candidate_id
            copy_files(work, frozen / "artifacts")
            write_json(frozen / "claim.json", claim)
            write_json(frozen / "packet.json", fixed)
            candidate = Candidate(candidate_id, attempt.problem_id, attempt.id,
                                  frozen.relative_to(self.store.root).as_posix(), claim, manifest=seal(frozen))
            attempt.candidate_id = candidate_id
            self._checkpoint(attempt, candidate)
            self._assess(allocation, attempt, candidate, fixed)
        except BudgetExhausted as error:
            attempt.status = "budget_exhausted"
            attempt.failures.append(str(error))
        except (RuntimeError, ValueError, OSError, KeyError, TypeError) as error:
            attempt.status = "execution_failed"
            attempt.failures.append(f"{type(error).__name__}: {error}")
        finally:
            attempt.charged_tokens = self.pool.spent.get(allocation.id, 0)
            self._checkpoint(attempt, candidate)
        return attempt

    def _assess(self, allocation: Allocation, attempt: Attempt, candidate: Candidate, fixed: dict) -> None:
        if self._stopped():
            attempt.status = "interrupted"
            return
        frozen = safe_path(self.store.root, candidate.frozen_dir)
        candidate.verification = verify(candidate, self.policy, self.sandbox, self.store.root)
        write_json(frozen.parent / f"{candidate.id}-verification-{attempt.id}.json", candidate.verification)
        if candidate.verification["execution_failed"]:
            attempt.status = "execution_failed"
            attempt.failures.append("machine verification could not execute; repair the environment before assessment")
            return
        proof = safe_path(frozen / "artifacts", candidate.claim["proof_path"]).read_text()
        if len(proof) > self.policy.synthesis_chars:
            raise ValueError("frozen proof exceeds review field allowance")
        review_work = self.store.root / "reviews" / attempt.id
        review_work.mkdir(parents=True, exist_ok=True)
        request = {"role": "reviewer", "instruction": prompts.REVIEWER, "original_question": fixed["original_statement"],
                   "scope_contract": fixed["scope_contract"], "allocation": to_dict(allocation),
                   "baseline": fixed.get("inherited", []), "claim": candidate.claim, "proof": proof,
                   "reproduction": candidate.verification}
        request["source_excerpts"] = fixed.get("sources", [])
        # Reviewers receive source files and frozen artifacts in a separate workspace;
        # no solver call logs, state notes, or conversation are copied there.
        copy_files(frozen / "artifacts", review_work / "artifacts")
        try:
            review = self.pool.call(self.backends["reviewer"], request, review_work, reserve=self.policy.review_tokens,
                                    allocation_id=allocation.id, attempt_id=attempt.id)
            validate_review(review)
            if review["original_resolved"] and (candidate.claim["scope"] != "original" or candidate.claim.get("kind") == "no_claim"):
                raise ValueError("a restricted/no-result submission cannot resolve the original question")
        except (RuntimeError, ValueError, OSError) as error:
            candidate.status = "review_inconclusive"
            attempt.status = "review_inconclusive"
            attempt.failures.append(str(error))
            return
        check_seal(frozen, candidate.manifest)
        candidate.review = review
        attempt.candidate_id = candidate.id
        conclusive = review["conclusive"] and review["verdict"] != "uncertain"
        attempt.effective = conclusive and not candidate.verification["execution_failed"]
        accepted = (conclusive and review["verdict"] == "accept" and review["proof_matches"]
                    and review["scope_matches"] and candidate.verification["ok"])
        dependencies_ok = all(self.campaign.candidates.get(identifier) is not None
                              and self.campaign.candidates[identifier].status == "retained"
                              for identifier in candidate.claim.get("depends_on", []))
        accepted &= dependencies_ok
        no_claim = candidate.claim.get("kind") == "no_claim"
        candidate.status = ("no_result" if accepted and no_claim else "retained" if accepted
                            else "rejected" if conclusive else "review_inconclusive")
        if not dependencies_ok:
            candidate.status = "disputed"
        attempt.material_progress = (accepted and not no_claim and review["target_met"] and review["material_advance"]
                                     and review["novel"] and bool(review["advance"].strip()))
        attempt.status = "completed" if attempt.effective else "review_inconclusive"

    def _integrate_round(self, number: int, attempts: list[Attempt]) -> None:
        with self.lock:
            for problem_id in {item.problem_id for item in attempts}:
                problem = self.campaign.problems[problem_id]
                arms = [item for item in attempts if item.problem_id == problem_id]
                research = [item for item in arms if item.role != "audit"]
                progress = False
                seen = {normalized(self.campaign.candidates[identifier].claim["statement"])
                        for identifier in problem.candidates if self.campaign.candidates[identifier].status == "retained"}
                for attempt in arms:
                    candidate = self.campaign.candidates.get(attempt.candidate_id)
                    if candidate is None:
                        continue
                    if attempt.role == "audit" and candidate.status != "retained":
                        self._invalidate_dependents(candidate.id)
                    repeated = normalized(candidate.claim["statement"]) in seen
                    if candidate.id not in problem.candidates:
                        problem.candidates.append(candidate.id)
                    if candidate.status == "retained":
                        seen.add(normalized(candidate.claim["statement"]))
                    if attempt.role != "audit" and attempt.material_progress and not repeated:
                        progress = True
                    if repeated and attempt.role != "audit":
                        attempt.material_progress = False
                        attempt.notes.append("exact repeated claim did not reset stagnation")
                        self.campaign.attempts[attempt.id] = copy.deepcopy(attempt)
                    if (candidate.status == "retained" and candidate.claim["scope"] == "original"
                            and candidate.review.get("original_resolved") and not candidate.review.get("remaining_gap", "").strip()):
                        problem.status = "awaiting_human"
                    elif candidate.claim["scope"] == "original" and candidate.status in ("proposed", "review_inconclusive", "disputed"):
                        problem.status = "awaiting_audit"
                counts = bool(research) and (progress or all(item.effective for item in research))
                if counts:
                    problem.stagnation = 0 if progress else problem.stagnation + 1
                    if progress and problem.status == "parked":
                        problem.status = "active"
                elif research and problem.status == "parked":
                    # A broken execution did not consume the authorized effective revisit.
                    problem.revisit_credit = 1
                if any(item.role == "audit" for item in arms) and problem.status in ("awaiting_human", "awaiting_audit"):
                    pending = [self.campaign.candidates[identifier] for identifier in problem.candidates
                               if self.campaign.candidates[identifier].claim["scope"] == "original"
                               and self.campaign.candidates[identifier].status in ("proposed", "review_inconclusive", "disputed", "retained")]
                    if not pending:
                        problem.status = "active"
                if any(item.role == "focus" for item in research):
                    problem.focus_rounds += 1
                if problem.stagnation >= self.policy.stagnation_rounds and problem.status == "active":
                    problem.status = "parked"
                    problem.revisit_condition = self.campaign.allocations[arms[-1].allocation_id].revisit_condition
                problem.rounds.append({"round": number, "attempts": [item.id for item in arms], "counts_for_stagnation": counts,
                                       "material_progress": progress, "stagnation": problem.stagnation})
            self.campaign.decisions.append({"actor": "harness", "action": "integrate_round", "round": number,
                                            "time": utc_now(), "usage": self.pool.ledger.summary()})
            self.save()

    def revisit(self, problem_id: str, method: str, reason: str) -> None:
        if not method.strip() or not reason.strip():
            raise ValueError("a revisit requires a documented new method and human rationale")
        problem = self.campaign.problems[problem_id]
        if problem.status != "parked":
            raise ValueError("only parked targets require revisit authorization")
        problem.revisit_method, problem.revisit_credit = method, 1
        self.campaign.decisions.append({"actor": "human", "action": "authorize_one_revisit", "problem": problem_id,
                                        "method": method, "reason": reason, "time": utc_now()})
        self.save()

    def human_review(self, candidate_id: str, accept: bool, reason: str, external_confirmation: str = "") -> None:
        if not reason.strip():
            raise ValueError("human review requires a reason")
        candidate = self.campaign.candidates[candidate_id]
        if accept and candidate.status != "retained":
            raise ValueError("only internally retained candidates can receive human acceptance")
        candidate.human_review, candidate.human_reason = ("accepted" if accept else "rejected"), reason
        candidate.external_confirmation = external_confirmation if accept else ""
        problem = self.campaign.problems[candidate.problem_id]
        if accept and candidate.claim["scope"] == "original" and candidate.review.get("original_resolved"):
            problem.status = "closed"
        if not accept:
            candidate.status = "retracted"
            problem.status = "active"
            self._invalidate_dependents(candidate.id)
        self.campaign.decisions.append({"actor": "human", "action": "review_candidate", "candidate": candidate_id,
                                        "accept": accept, "reason": reason, "external_confirmation": external_confirmation,
                                        "time": utc_now()})
        self.save()

    def _invalidate_dependents(self, candidate_id: str) -> None:
        invalid = {candidate_id}
        while True:
            descendants = [item for item in self.campaign.candidates.values()
                           if item.id not in invalid and set(item.claim.get("depends_on", [])) & invalid]
            if not descendants:
                break
            for descendant in descendants:
                invalid.add(descendant.id)
                descendant.status = "disputed"
                self.campaign.problems[descendant.problem_id].status = "awaiting_audit"

    def recover_interrupted(self) -> None:
        """Keep failures/costs and integrate saved terminal work after a lost process."""
        interrupted = []
        for attempt in self.campaign.attempts.values():
            if attempt.status == "running":
                attempt.status = "interrupted"
                attempt.failures.append("previous harness process ended before attempt completion")
                work = self.store.root / "attempts" / attempt.id / "work"
                if work.exists():
                    self._draft(attempt, work)
                interrupted.append(attempt.id)
            attempt.charged_tokens = sum(record["total_tokens"] if record["total_tokens"] is not None else record["unknown_reserve"]
                                         for record in self.pool.ledger.records if record.get("attempt") == attempt.id)
        for allocation in self.campaign.allocations.values():
            if allocation.status == "running":
                allocation.status = "interrupted"
        rounds = sorted({item.round for item in self.campaign.attempts.values()})
        for number in rounds:
            unintegrated = [copy.deepcopy(item) for item in self.campaign.attempts.values() if item.round == number
                            and not any(record["round"] == number for record in self.campaign.problems[item.problem_id].rounds)]
            if unintegrated:
                self._integrate_round(number, unintegrated)
        if interrupted:
            self.campaign.decisions.append({"actor": "harness", "action": "recover_interrupted",
                                            "attempts": interrupted, "time": utc_now()})
        self.save()
