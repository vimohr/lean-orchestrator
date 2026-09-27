from __future__ import annotations

from lean_orchestrator.integrity import IntegrityGuard
from lean_orchestrator.knowledge import KnowledgeBase
from lean_orchestrator.paths import WorkspacePaths
from lean_orchestrator.state import LEAN_VERIFIED, REPRODUCED, Claim


def test_results_and_relevance_links(tmp_path):
    paths = WorkspacePaths(tmp_path)
    knowledge = KnowledgeBase(paths, IntegrityGuard(paths))
    claims = [Claim(id="C1", kind="lemma", statement="A | B", trust=REPRODUCED),
              Claim(id="C2", kind="lemma", statement="L", trust=LEAN_VERIFIED, lean_decl="OpenQ.P.l")]
    knowledge.add_results(problem_id="p", title="P", folder="p-folder", claims=claims, global_epoch=4)
    assert [item["uid"] for item in knowledge.results()] == ["p:C1", "p:C2"]
    table = paths.knowledge_md.read_text()
    assert table.index("lean_verified") < table.index("| reproduced")
    assert "A \\| B" in table and "`OpenQ.P.l`" in table and "(../problems/p-folder/PROGRESS.md)" in table

    links = [{"problem_id": "q", "claims": ["C1"], "why": "same bound"}, {"problem_id": "zzz", "why": "unknown"},
             {"problem_id": "p", "why": "self"}]
    accepted = knowledge.add_relevance(source_problem="p", links=links, known_problems={"p", "q"}, global_epoch=4)
    assert [link["to"] for link in accepted] == ["q"]
    assert len(knowledge.relevance_to("q", after_global_epoch=None)) == 1
    assert knowledge.relevance_to("q", after_global_epoch=4) == []
    assert len(knowledge.relevance_to("q", after_global_epoch=3)) == 1
