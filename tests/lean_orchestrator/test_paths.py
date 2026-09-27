from __future__ import annotations

from pathlib import Path

from lean_orchestrator.paths import WorkspacePaths, lean_identifier, short_id, slugify


def test_slugify_makes_readable_ascii_folder_names():
    assert slugify("Second-level collapse of the exact PPT entanglement-cost hierarchy") == \
        "second-level-collapse-of-the-exact-ppt-entanglement-cost"
    assert slugify("Rényi $\\alpha$-divergences for $d=3$") == "renyi-divergences-for"
    assert slugify("$$") == "problem"
    assert len(slugify("word " * 40, max_length=20)) <= 20


def test_lean_identifier_is_a_valid_upper_camel_case_name():
    assert lean_identifier("Second-level collapse of the exact PPT hierarchy") == "SecondLevelCollapseExactPPT"
    assert lean_identifier("3 mutually unbiased bases") == "P3MutuallyUnbiasedBases"


def test_short_id_uses_the_upstream_identifier():
    assert short_id("op_2e43f525333b67c0") == "2e43f5"
    assert short_id("local_ppt_squared") == "pptsqu"


def test_problem_paths_layout(tmp_path: Path):
    paths = WorkspacePaths(tmp_path)
    problem = paths.problem("ppt-2e43f5", "PPT_2e43f5")
    assert problem.root == tmp_path / "problems" / "ppt-2e43f5"
    assert problem.lean_dir == tmp_path / "lean" / "OpenQ" / "Problems" / "PPT_2e43f5"
    assert problem.lean_module_prefix == "OpenQ.Problems.PPT_2e43f5"
    assert problem.iteration_dir(3, 7).name == "e003-i07"
    assert problem.epoch_report(12).name == "epoch-012.md"
    assert paths.relative(problem.state) == "problems/ppt-2e43f5/state.json"
