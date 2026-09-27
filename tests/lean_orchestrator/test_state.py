from __future__ import annotations

from lean_orchestrator.state import (
    CRITIC_ACCEPTED, PROPOSED, Branch, Claim, FormalStatement, ResearchState, Subgoal, load_state, save_state,
)


def test_identifiers_lookups_and_persistence(tmp_path):
    state = ResearchState(problem_id="p", title="t", folder="f", lean_namespace="N")
    assert [state.next_id("C"), state.next_id("C"), state.next_id("B")] == ["C1", "C2", "B1"]
    state.claims += [Claim(id="C1", kind="lemma", statement="a", trust=CRITIC_ACCEPTED),
                     Claim(id="C2", kind="lemma", statement="b", trust=PROPOSED)]
    state.branches += [Branch(id="B1", kind="prove", goal="g"), Branch(id="B2", kind="disprove", goal="h",
                                                                        status="closed")]
    state.subgoals.append(Subgoal(id="G1", statement="s"))
    state.formal_statements += [FormalStatement(file="a", decl="A", claim="C1", active=False),
                                FormalStatement(file="b", decl="B", claim="C2")]
    assert state.claim("C2").statement == "b" and state.claim("C9") is None
    assert [branch.id for branch in state.open_branches()] == ["B1"]
    assert [claim.id for claim in state.accepted_claims()] == ["C1"]
    assert state.active_formal_statement.decl == "B"
    assert state.subgoal("G1").statement == "s" and state.branch(None) is None
    assert state.current_statement == "" and state.current_epoch is None
    save_state(tmp_path / "state.json", state)
    assert load_state(tmp_path / "state.json") == state
