from __future__ import annotations

from lean_orchestrator.catalogue.base import CatalogueEntry, CatalogueReference
from lean_orchestrator.portfolio import ACTIVE, DEFERRED, Portfolio, PortfolioEntry, TriageResult
from lean_orchestrator.render import (
    render_dossier, render_epoch_report_fallback, render_portfolio, render_problem, render_progress, tex_to_markdown,
)
from lean_orchestrator.state import (
    LEAN_VERIFIED, Attempt, Branch, Claim, DeadEnd, FormalStatement, Hint, ResearchState, Stamp, StatementVersion,
    Subgoal,
)


def test_tex_to_markdown_converts_common_constructs():
    tex = ("See \\emph{this} and \\textbf{that} in \\href{https://x.org}{the paper} \\sourcecite{ref:a}{AB20}, "
           "Eq.~\\eqref{eq:main}:\n\\begin{equation}\n a = b \\label{eq:main}\n\\end{equation}\n"
           "``quoted'' \\texttt{code}\n\\begin{align*} x &= y \\end{align*}")
    markdown = tex_to_markdown(tex)
    assert "*this*" in markdown and "**that**" in markdown and "[the paper](https://x.org)" in markdown
    assert "[AB20]" in markdown and "(eq:main)" in markdown and "\\label" not in markdown
    assert "$$\na = b\n$$" in markdown
    assert "\u201cquoted\u201d" in markdown and "`code`" in markdown
    assert "\\begin{aligned}\nx &= y\n\\end{aligned}" in markdown


def test_render_problem_includes_statement_progress_and_references():
    entry = CatalogueEntry(
        id="op_1", source="qiqcop", title="A problem", status="Unsolved", statement="Is $x>0$?",
        url="https://qiqc-op.com/problem/op_1/", fields=["F"], topics=["T"], progress=["Partial result."],
        references=[CatalogueReference(key="AB20", text="A. B., Paper.", arxiv="2001.00001")], related=["op_2"],
    )
    text = render_problem(entry, {"op_2": "Related one"})
    assert text.startswith("# A problem")
    assert "## Statement" in text and "Is $x>0$?" in text
    assert "## Known progress (upstream)" in text and "Partial result." in text
    assert "[arXiv:2001.00001](https://arxiv.org/abs/2001.00001)" in text
    assert "`op_2` Related one" in text and "QIQCOP Zoo" in text


def _state() -> ResearchState:
    state = ResearchState(problem_id="p", title="Problem P", folder="problem-p", lean_namespace="P_1")
    state.statement_versions.append(StatementVersion(1, "Show $Q \\geq 0$.", "upstream"))
    state.claims += [
        Claim(id="C1", kind="lemma", statement="Q_2 >= 0", trust=LEAN_VERIFIED, lean_decl="OpenQ.Problems.P_1.q2",
              lean_file="lean/OpenQ/Problems/P_1/Q2.lean"),
        Claim(id="C2", kind="lemma", statement="false claim", trust="rejected", critic_notes="major: gap"),
    ]
    state.formal_statements.append(FormalStatement(file="lean/OpenQ/Problems/P_1/Statement.lean",
                                                   decl="OpenQ.Problems.P_1.MainStatement", claim="C0"))
    state.branches.append(Branch(id="B1", kind="prove", goal="prove it", iterations=2))
    state.subgoals.append(Subgoal(id="G1", statement="d = 3"))
    state.dead_ends.append(DeadEnd(approach="triangle inequality", reason="too weak"))
    state.attempts.append(Attempt(id="A1", approach="convexity", description="d", outcome="failure",
                                  fingerprint="convexity :: main", failure_reason="no bound", created=Stamp(1, 1)))
    state.hints.append(Hint(text="Try d = 3 first."))
    return state


def test_progress_report_and_dossier_sections():
    entry = PortfolioEntry(id="p", source="local", title="Problem P", status=ACTIVE, ewma_progress=0.7)
    progress = render_progress(_state(), entry)
    for heading in ("## Supervisor assessment", "## Precise statement", "## Formal statement (Lean)",
                    "## Verified and accepted results", "## Open subgoals", "## Branches", "## Known dead ends",
                    "## Attempted approaches", "## Rejected or retracted claims", "## Human hints", "## Timeline"):
        assert heading in progress, heading
    assert "Status: **promising**" in progress
    assert "`OpenQ.Problems.P_1.q2`" in progress
    dossier = render_dossier(_state(), entry)
    assert "## Do not retry" in dossier and "convexity :: main" in dossier and "triangle inequality" in dossier
    assert "Main results must have exactly this type" in dossier
    assert "Try d = 3 first." in dossier


def test_portfolio_orders_by_priority_and_lists_deferred_problems():
    triage = TriageResult(scores={}, suitability=4.0, recommendation="activate")
    portfolio = Portfolio(global_epochs=3, entries={
        "a": PortfolioEntry(id="a", source="local", title="Alpha", status=ACTIVE, folder="alpha", triage=triage),
        "b": PortfolioEntry(id="b", source="local", title="Beta", status="candidate"),
        "c": PortfolioEntry(id="c", source="local", title="Gamma", status=DEFERRED, triage=triage),
    })
    text = render_portfolio(portfolio, {"a": 0.4, "b": 0.9})
    assert text.index("Beta") < text.index("Alpha")
    assert "[Alpha](problems/alpha/PROGRESS.md)" in text
    assert "## Deferred by triage (1)" in text and "`c` Gamma" in text


def test_fallback_epoch_report_lists_attempts():
    text = render_epoch_report_fallback(_state(), 1)
    assert "# Epoch 1 report: Problem P" in text and "A1: convexity (failure). no bound" in text
