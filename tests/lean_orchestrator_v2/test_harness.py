"""Behavioral checks using saved artifacts and real fresh Python processes."""

import copy
import json
from dataclasses import replace

import pytest

from lean_orchestrator.usage import TokenUsage
from lean_orchestrator import notify
from lean_orchestrator.notify import NotificationError
from lean_orchestrator_v2.assessment import verify
from lean_orchestrator_v2.backends import Completion
from lean_orchestrator_v2.cli import init, main
from lean_orchestrator_v2.config import NotifyConfig, load_config
from lean_orchestrator_v2.demo import PACKET, DemoBackend, allocation, review
from lean_orchestrator_v2.engine import Harness
from lean_orchestrator_v2.models import CAMPAIGN_SCHEMA, Candidate
from lean_orchestrator_v2.storage import Store, safe_path, seal


class Scripted(DemoBackend):
    def __init__(self, *, no_claim=False, original=False, fail="", assessment=None, usage=TokenUsage(10, 20), ready=True):
        super().__init__()
        self.no_claim, self.original, self.fail, self.usage, self.ready = no_claim, original, fail, usage, ready
        self.assessment = assessment

    def complete(self, request, work, max_output_tokens):
        role = request["role"]
        if role == self.fail:
            self.requests.append(copy.deepcopy(request))
            return Completion(error="simulated transport timeout")
        result = super().complete(request, work, max_output_tokens)
        result.usage = self.usage
        if role == "solver":
            draft = json.loads((work / "draft.json").read_text())
            claim = draft["claim"]
            claim["scope"] = "original" if self.original else "restricted"
            claim["remaining_gap"] = "" if self.original else "Generalize beyond the restricted target."
            claim["statement"] = "A restricted parity observation."
            claim.pop("experiment")
            if self.no_claim:
                claim.update(kind="no_claim", statement="No accepted advance.", advance="A complete failure account.")
            (work / "draft.json").write_text(json.dumps(draft))
            result.data["ready_to_submit"] = self.ready
            result.data["state"] = "PRIVATE_SOLVER_NOTE"
        elif role == "reviewer":
            result.data = self.assessment or review(original_resolved=self.original, material_advance=not self.no_claim,
                                                    target_met=not self.no_claim,
                                                    remaining_gap="" if self.original else "Generalize beyond the restricted target.")
        return result


@pytest.fixture
def harness(tmp_path):
    store = init(tmp_path / "campaign")
    config = load_config(store.root)
    config = replace(config, policy=replace(config.policy, sandbox="local"))
    campaign = store.load()
    store.add(campaign, PACKET, tmp_path)
    store.save(campaign)
    backend = Scripted()
    return Harness(store, config, backends={role: backend for role in ("solver", "coordinator", "reviewer")})


def backend_for(harness, backend):
    harness.backends = {role: backend for role in ("solver", "coordinator", "reviewer")}
    return backend


def run(harness, proposals=None):
    plans = harness.propose(proposals or [allocation(target_scope="restricted")])
    return harness.run_round([item.id for item in plans])


def test_complete_candidate_is_frozen_and_awaits_human_acceptance(harness):
    backend = backend_for(harness, Scripted(original=True))
    result = run(harness)
    attempt = result["attempts"][0]
    assert attempt["effective"] and attempt["material_progress"]
    candidate = harness.campaign.candidates[attempt["candidate_id"]]
    assert candidate.status == "retained" and candidate.external_confirmation == ""
    assert harness.campaign.problems[PACKET["id"]].status == "awaiting_human"
    reviewer = next(item for item in backend.requests if item["role"] == "reviewer")
    assert "PRIVATE_SOLVER_NOTE" not in json.dumps(reviewer)
    assert not {"history", "state", "feedback", "conversation"} & set(reviewer)
    harness.human_review(candidate.id, True, "Both parity cases checked by the researcher.")
    assert harness.campaign.problems[PACKET["id"]].status == "closed"
    assert candidate.external_confirmation == "", "human acceptance is not expert confirmation"


def test_multiple_arms_and_retries_count_as_one_effective_round(harness):
    backend_for(harness, Scripted(no_claim=True))
    proposals = [allocation(target_scope="restricted", method="first arm"),
                 allocation(target_scope="restricted", method="second arm")]
    result = run(harness, proposals)
    problem = harness.campaign.problems[PACKET["id"]]
    assert len(result["attempts"]) == 2 and problem.stagnation == 1
    assert len(problem.rounds) == 1 and problem.rounds[0]["counts_for_stagnation"]
    run(harness, proposals)
    assert problem.stagnation == 2 and problem.status == "parked"
    with pytest.raises(ValueError, match="new method"):
        harness.propose(proposals)


@pytest.mark.parametrize("failure", ["solver", "submitter", "reviewer"])
def test_execution_and_inconclusive_review_do_not_add_stagnation(harness, failure):
    backend_for(harness, Scripted(fail=failure))
    problem = harness.campaign.problems[PACKET["id"]]
    problem.stagnation = 1
    result = run(harness)
    assert not result["attempts"][0]["effective"] and problem.stagnation == 1
    assert not problem.rounds[0]["counts_for_stagnation"]
    assert harness.pool.ledger.unknown_reserve > 0


def test_review_uncertainty_freezes_original_candidate_for_audit(harness):
    uncertain = review(verdict="uncertain", conclusive=False, original_resolved=False, remaining_gap="Unverified limit step.")
    backend_for(harness, Scripted(original=True, assessment=uncertain))
    result = run(harness)
    candidate_id = result["attempts"][0]["candidate_id"]
    problem = harness.campaign.problems[PACKET["id"]]
    assert problem.status == "awaiting_audit" and problem.stagnation == 0
    with pytest.raises(ValueError, match="review before"):
        harness.propose([allocation()])
    backend_for(harness, Scripted(original=True))
    audit = allocation(role="audit", action="audit", candidate_id=candidate_id, max_tokens=20_000)
    audited = run(harness, [audit])
    assert not audited["attempts"][0]["effective"]
    assert problem.status == "awaiting_human" and problem.stagnation == 0


def test_repeating_a_correct_result_does_not_reset_stagnation(harness):
    run(harness)
    problem = harness.campaign.problems[PACKET["id"]]
    problem.stagnation = 1
    result = run(harness)
    assert not result["attempts"][0]["material_progress"]
    assert problem.stagnation == 2 and problem.status == "parked"


def test_new_method_gets_one_effective_revisit_and_failures_preserve_it(harness):
    backend_for(harness, Scripted(no_claim=True))
    run(harness)
    run(harness)
    problem = harness.campaign.problems[PACKET["id"]]
    harness.revisit(problem.id, "new representation", "A new source supplies the missing representation.")
    backend_for(harness, Scripted(fail="solver"))
    run(harness, [allocation(method="new representation", target_scope="restricted")])
    assert problem.stagnation == 2 and problem.revisit_credit == 1
    backend_for(harness, Scripted(no_claim=True))
    run(harness, [allocation(method="new representation", target_scope="restricted")])
    assert problem.stagnation == 3 and problem.revisit_credit == 0


def test_focus_promotion_needs_checked_baseline_and_renewal_needs_approval(harness):
    with pytest.raises(ValueError, match="checked prior"):
        harness.propose([allocation(role="focus", action="continue", max_tokens=160_000)])
    result = run(harness)
    baseline = result["attempts"][0]["candidate_id"]
    focus = allocation(role="focus", action="continue", max_tokens=160_000, baseline_candidates=[baseline])
    run(harness, [focus])
    pending = harness.propose([focus])[0]
    assert pending.status == "pending_approval"
    assert harness.run_round([pending.id])["attempts"] == []
    harness.approve(pending.id, "The checked construction exposes a specific removable assumption.")
    assert pending.status == "proposed" and pending.approved


def test_research_allocation_reserves_at_least_one_call_and_later_phases(harness):
    minimum = harness.policy.call_tokens + harness.policy.submission_tokens + harness.policy.review_tokens
    with pytest.raises(ValueError, match="protected later phases"):
        harness.propose([allocation(max_tokens=minimum - 1)])
    assert harness.propose([allocation(max_tokens=minimum)])[0].status == "proposed"


def test_submission_and_review_allowance_survives_research_limit(harness):
    policy = replace(harness.policy, call_tokens=40, submission_tokens=60, review_tokens=70,
                     exploration_tokens=170, focus_tokens=300, audit_tokens=70)
    config = replace(harness.config, policy=policy)
    backend = Scripted(ready=False)
    bounded = Harness(harness.store, config, backends={role: backend for role in ("solver", "coordinator", "reviewer")})
    result = run(bounded, [allocation(max_tokens=170, target_scope="restricted")])
    roles = [item["role"] for item in backend.requests]
    assert roles == ["solver", "submitter", "reviewer"]
    assert result["attempts"][0]["status"] == "completed" and result["attempts"][0]["charged_tokens"] == 90


def test_complete_draft_is_recovered_after_transport_failure(harness):
    class SaveThenFail(Scripted):
        def complete(self, request, work, max_output_tokens):
            result = super().complete(request, work, max_output_tokens)
            if request["role"] == "solver":
                return Completion(error="transport lost after saving complete draft")
            return result
    backend = backend_for(harness, SaveThenFail())
    result = run(harness)
    attempt = result["attempts"][0]
    assert attempt["effective"] and attempt["failures"]
    assert attempt["checkpoint"] == "draft.json"
    assert [item["role"] for item in backend.requests] == ["solver", "submitter", "reviewer"]
    assert harness.pool.ledger.unknown_reserve == harness.policy.call_tokens


def test_repair_only_submits_complete_checkpoint_and_retains_original_failure(harness):
    backend_for(harness, Scripted(fail="submitter"))
    failed = run(harness)["attempts"][0]
    backend = backend_for(harness, Scripted())
    repair = allocation(action="repair", repair_attempt_id=failed["id"], target_scope="restricted")
    result = run(harness, [repair])
    assert result["attempts"][0]["effective"]
    assert [item["role"] for item in backend.requests] == ["submitter", "reviewer"]
    assert harness.campaign.attempts[failed["id"]].failures


def test_full_numerical_submission_runs_in_two_fresh_processes(harness):
    class Counting(Scripted):
        def complete(self, request, work, max_output_tokens):
            result = super().complete(request, work, max_output_tokens)
            if request["role"] == "solver":
                (work / "verify.py").write_text("from pathlib import Path\nimport json\np=Path('counter')\n"
                                               "n=int(p.read_text())+1 if p.exists() else 1\np.write_text(str(n))\n"
                                               "json.dump({'count':n},open('result.json','w'))\n")
                draft = json.loads((work / "draft.json").read_text())
                draft["claim"]["experiment"] = {"script": "verify.py", "result_file": "result.json", "expected": {"count": 1}}
                (work / "draft.json").write_text(json.dumps(draft))
            return result
    backend_for(harness, Counting())
    result = run(harness)
    candidate = harness.campaign.candidates[result["attempts"][0]["candidate_id"]]
    assert candidate.verification["ok"]
    assert [item["result"] for item in candidate.verification["runs"]] == [{"count": 1}, {"count": 1}]
    assert candidate.verification["evidence"] == "reproduced_code"


def test_frozen_files_and_manifest_cannot_both_be_rewritten(harness):
    result = run(harness)
    candidate = harness.campaign.candidates[result["attempts"][0]["candidate_id"]]
    frozen = harness.store.root / candidate.frozen_dir
    (frozen / "artifacts" / "proof.md").write_text("Different proof.")
    seal(frozen)
    with pytest.raises(ValueError, match="manifest changed"):
        verify(candidate, harness.policy, harness.sandbox, harness.store.root)


def test_retraction_invalidates_transitive_dependents(harness):
    parent_id = run(harness)["attempts"][0]["candidate_id"]
    child = Candidate("child", PACKET["id"], "old", "unused", {"statement": "child", "scope": "restricted", "depends_on": [parent_id]}, status="retained")
    grandchild = Candidate("grandchild", PACKET["id"], "old", "unused", {"statement": "grandchild", "scope": "restricted", "depends_on": ["child"]}, status="retained")
    harness.campaign.candidates.update(child=child, grandchild=grandchild)
    harness.human_review(parent_id, False, "Primary definition excludes the claimed inference.")
    assert child.status == grandchild.status == "disputed"
    assert harness.campaign.problems[PACKET["id"]].status == "awaiting_audit"


def test_combined_saved_plans_cannot_exceed_round_limits(harness):
    harness.propose([allocation(method="arm 1"), allocation(method="arm 2")])
    harness.propose([allocation(method="arm 3")])
    with pytest.raises(ValueError, match="arm limit"):
        harness.run_round()


def test_offline_demo_is_runnable_without_models_or_docker(tmp_path):
    root = tmp_path / "demo"
    assert main(["demo", str(root)]) == 0
    campaign = Store(root).load()
    assert campaign.problems[PACKET["id"]].status == "awaiting_human"
    candidate = next(iter(campaign.candidates.values()))
    assert len(candidate.verification["runs"]) == 2 and candidate.verification["ok"]
    result = json.loads((root / "demo-result.json").read_text())
    assert result["usage"]["known_tokens"] == 0 and result["mode"].startswith("offline")
    assert main(["-w", str(root), "review", candidate.id, "--accept", "--reason", "Checked both parity cases."]) == 0
    assert Store(root).load().problems[PACKET["id"]].status == "closed"


def test_packet_keeps_source_snapshot_when_original_file_changes(tmp_path):
    store = init(tmp_path / "campaign")
    source = tmp_path / "definition.md"
    source.write_text("Reference systems are permitted.")
    packet = {**PACKET, "sources": [{"path": "definition.md", "locator": "Definition 2"}]}
    campaign = store.load()
    problem = store.add(campaign, packet, tmp_path)
    source.write_text("A changed external source.")
    cached = store.packet(problem)
    assert cached["sources"][0]["excerpt"] == "Reference systems are permitted."
    cached_file = store.root / problem.packet / cached["sources"][0]["path"]
    cached_file.write_text("Tampered cache.")
    seal(cached_file.parent.parent)
    with pytest.raises(ValueError, match="source changed"):
        store.packet(problem)


def test_previous_campaign_namespace_preserves_saved_research(harness):
    run(harness)
    data = json.loads(harness.store.state_path.read_text())
    data["schema"] = "previous-namespace/campaign/2"
    harness.store.state_path.write_text(json.dumps(data))
    restored = harness.store.load()
    assert restored == harness.campaign
    harness.store.save(restored)
    assert json.loads(harness.store.state_path.read_text())["schema"] == CAMPAIGN_SCHEMA


@pytest.mark.parametrize("schema", ["lean-orchestrator/campaign/3", "other-engine/state/2"])
def test_unsupported_campaign_formats_remain_rejected(harness, schema):
    data = json.loads(harness.store.state_path.read_text())
    data["schema"] = schema
    harness.store.state_path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="unsupported campaign schema"):
        harness.store.load()


def test_path_traversal_and_symlinks_are_rejected(tmp_path):
    with pytest.raises(ValueError):
        safe_path(tmp_path, "../outside")
    (tmp_path / "link").symlink_to(tmp_path.parent)
    with pytest.raises(ValueError, match="symlink"):
        safe_path(tmp_path, "link/outside")


def test_broken_verifier_does_not_spend_review_budget_or_count_stagnation(harness):
    class BrokenVerifier(Scripted):
        def complete(self, request, work, max_output_tokens):
            result = super().complete(request, work, max_output_tokens)
            if request["role"] == "solver":
                (work / "verify.py").write_text("import definitely_missing_scientific_package\n")
                draft = json.loads((work / "draft.json").read_text())
                draft["claim"]["experiment"] = {"script": "verify.py", "result_file": "result.json", "expected": {"ok": True}}
                (work / "draft.json").write_text(json.dumps(draft))
            return result
    backend = backend_for(harness, BrokenVerifier())
    result = run(harness)
    assert result["attempts"][0]["status"] == "execution_failed"
    assert not result["attempts"][0]["effective"]
    assert [item["role"] for item in backend.requests] == ["solver", "submitter"]
    assert harness.campaign.problems[PACKET["id"]].stagnation == 0


def test_preflight_failure_spends_no_model_tokens(harness, monkeypatch):
    backend = backend_for(harness, Scripted())
    def unavailable(work):
        raise ValueError("calculation image unavailable")
    monkeypatch.setattr(harness.sandbox, "preflight", unavailable)
    result = run(harness)
    assert result["attempts"][0]["status"] == "execution_failed" and backend.requests == []
    assert harness.pool.ledger.summary()["calls"] == 0
    assert harness.campaign.problems[PACKET["id"]].stagnation == 0


def test_ordinary_requests_only_receive_bounded_note_and_latest_feedback(harness):
    class Noisy(Scripted):
        def __init__(self):
            super().__init__()
            self.turn = 0
        def complete(self, request, work, max_output_tokens):
            result = super().complete(request, work, max_output_tokens)
            if request["role"] == "solver":
                self.turn += 1
                (work / "noisy.py").write_text("print('x'*10000)\n")
                result.data.update(state="数学 " * 1000, action={"kind": "python", "path": "noisy.py"},
                                   ready_to_submit=self.turn == 2)
            return result
    backend = backend_for(harness, Noisy())
    result = run(harness)
    requests = [item for item in backend.requests if item["role"] == "solver"]
    assert len(requests) == 2 and result["attempts"][0]["effective"]
    assert len(requests[1]["state"].encode()) <= 600 and len(requests[1]["feedback"].encode()) <= 800
    assert set(requests[1]) == {"role", "instruction", "packet", "state", "feedback"}


def test_invalid_original_resolution_label_cannot_promote_restricted_claim(harness):
    backend_for(harness, Scripted(assessment=review()))
    result = run(harness)
    candidate = harness.campaign.candidates[result["attempts"][0]["candidate_id"]]
    assert candidate.status == "review_inconclusive" and not result["attempts"][0]["effective"]
    assert harness.campaign.problems[PACKET["id"]].status == "active"


def test_lost_process_recovers_complete_work_without_double_counting_round(harness):
    backend_for(harness, Scripted(fail="submitter"))
    failed = run(harness)["attempts"][0]
    attempt = harness.campaign.attempts[failed["id"]]
    attempt.status = "running"
    harness.campaign.problems[PACKET["id"]].rounds.clear()
    harness.save()
    restored = Harness(harness.store, harness.config, backends=harness.backends)
    restored.recover_interrupted()
    problem = restored.campaign.problems[PACKET["id"]]
    assert restored.campaign.attempts[attempt.id].status == "interrupted" and problem.stagnation == 0
    assert len(problem.rounds) == 1 and not problem.rounds[0]["counts_for_stagnation"]
    restored.recover_interrupted()
    assert len(problem.rounds) == 1


@pytest.fixture
def emailed_harness(harness, monkeypatch):
    messages = []
    monkeypatch.setattr(notify, "send_email", lambda recipient, subject, body: messages.append((recipient, subject, body)))
    config = replace(harness.config, notify=NotifyConfig(email="researcher@example.org"))
    return Harness(harness.store, config, backends=harness.backends), messages


def test_resolution_email_is_once_per_problem_and_survives_restart_recovery(emailed_harness):
    harness, messages = emailed_harness
    backend_for(harness, Scripted(original=True))
    result = run(harness, [allocation(method="first arm"), allocation(method="second arm")])
    assert len(result["attempts"]) == 2 and len(messages) == 1
    recipient, subject, body = messages[0]
    assert recipient == "researcher@example.org" and subject.startswith("lean-orch-v2: result awaiting human review")
    assert "Human acceptance: pending" in body and "External confirmation: none recorded" in body
    assert "review" in body and "--reason" in body
    assert harness.store.load().problems[PACKET["id"]].status == "awaiting_human"
    restored = Harness(harness.store, harness.config, backends=harness.backends)
    restored.recover_interrupted()
    assert len(messages) == 1


@pytest.mark.parametrize("uncertain", [False, True])
def test_restricted_and_uncertain_results_do_not_send_resolution_email(emailed_harness, uncertain):
    harness, messages = emailed_harness
    assessment = review(verdict="uncertain", conclusive=False, original_resolved=False) if uncertain else None
    backend_for(harness, Scripted(original=uncertain, assessment=assessment))
    run(harness)
    assert messages == []


def test_focus_approval_email_is_sent_once_and_names_saved_allocation(emailed_harness):
    harness, messages = emailed_harness
    baseline = run(harness)["attempts"][0]["candidate_id"]
    focus = allocation(role="focus", action="continue", max_tokens=160_000, baseline_candidates=[baseline])
    run(harness, [focus])
    pending = harness.propose([focus])[0]
    assert pending.status == "pending_approval" and len(messages) == 1
    assert pending.id in messages[0][2] and "approve" in messages[0][2]
    assert harness.store.load().allocations[pending.id].status == "pending_approval"
    harness.run_round()
    assert len(messages) == 1


def test_focus_approval_discovered_at_dispatch_is_saved_and_notified_once(emailed_harness):
    harness, messages = emailed_harness
    baseline = run(harness)["attempts"][0]["candidate_id"]
    plan = harness.propose([allocation(role="focus", action="continue", max_tokens=160_000,
                                      baseline_candidates=[baseline])])[0]
    harness.campaign.problems[PACKET["id"]].focus_rounds = 1
    harness.save()
    assert harness.run_round()["attempts"] == []
    assert harness.store.load().allocations[plan.id].status == "pending_approval" and len(messages) == 1
    harness.run_round()
    assert len(messages) == 1


@pytest.mark.parametrize("error", [NotificationError("relay unavailable"), OSError("mail command unavailable"),
                                  ValueError("invalid message header")])
def test_email_delivery_failure_does_not_lose_candidate_or_change_progress(emailed_harness, monkeypatch, error):
    harness, _ = emailed_harness
    def fail(*args):
        raise error
    monkeypatch.setattr(notify, "send_email", fail)
    backend_for(harness, Scripted(original=True))
    result = run(harness)
    candidate = harness.store.load().candidates[result["attempts"][0]["candidate_id"]]
    assert candidate.status == "retained" and result["attempts"][0]["material_progress"]
    assert harness.campaign.problems[PACKET["id"]].stagnation == 0
    assert harness.notifier.log.read()[0]["kind"] == "notification_failed"
