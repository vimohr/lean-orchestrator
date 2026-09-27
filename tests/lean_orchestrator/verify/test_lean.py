from __future__ import annotations

import json
import os
import shutil
from importlib import resources
from pathlib import Path

import pytest

from lean_orchestrator.config import LeanConfig
from lean_orchestrator.paths import WorkspacePaths
from lean_orchestrator.verify.lean import (
    LeanVerifier, parse_imports, parse_name_list, static_scan, strip_comments_and_strings,
)
from lean_orchestrator.workspace import InitOptions, lean_requires


def test_comments_and_strings_are_ignored_by_the_scan():
    source = '/- axiom in a /- nested -/ comment -/\n-- sorry here\ndef s := "unsafe native_decide"\ntheorem t : True := trivial\n'
    assert "axiom" not in strip_comments_and_strings(source)
    assert static_scan(source) == ([], [])


@pytest.mark.parametrize(
    ("source", "forbidden"),
    [
        ("axiom cheat : False", "axiom declaration"),
        ("@[simp] private axiom cheat : False", "axiom declaration"),
        ("prelude\nimport Init", "prelude"),
        ("unsafe def f : Nat := 0", "unsafe"),
        ("@[implemented_by g] def f : Nat := 0", "implemented_by"),
        ("@[extern \"c_f\"] opaque f : Nat", "extern"),
        ("set_option debug.skipKernelTC true in", "debug option"),
        ("theorem t : 2 + 2 = 4 := by native_decide", "native_decide"),
        ("theorem t : x = y := by bv_decide", "bv_decide"),
        ("elab \"#hack\" : command => pure ()", "elab"),
        ("macro_rules | `(#print axioms $x) => `(#check $x)", "macro"),
        ("syntax \"#spoof\" : command", "syntax"),
        ("run_cmd pure ()", "run_cmd"),
        ("#eval IO.FS.writeFile \"x\" \"y\"", "#eval"),
        ("initialize registerThing", "initialize"),
        ("open private secret in", "open private"),
        ("#exit", "#exit"),
    ],
)
def test_forbidden_constructs(source, forbidden):
    assert forbidden in static_scan(source)[0]


def test_flags_are_reported_without_rejecting():
    forbidden, flags = static_scan("theorem t : True := by sorry\nnotation \"⟪\" x \"⟫\" => x\nopaque c : Nat\n")
    assert forbidden == []
    assert flags == ["sorry", "custom notation", "opaque constant"]
    assert static_scan("theorem t : True := by native_decide", forbid_native_decide=False) == ([], [])


def test_import_and_axiom_list_parsing():
    assert parse_imports("import Mathlib.Data.Real.Basic\n-- import Fake\nimport OpenQ.A OpenQ.B\n") == [
        "Mathlib.Data.Real.Basic", "OpenQ.A", "OpenQ.B"]
    assert parse_name_list(" [propext, Classical.choice,\n Quot.sound]") == ["propext", "Classical.choice", "Quot.sound"]
    assert parse_name_list("[]") == []


def test_module_names_and_path_rules(tmp_path):
    paths = WorkspacePaths(tmp_path)
    verifier = LeanVerifier(paths, LeanConfig())
    lean = tmp_path / "lean"
    assert verifier.module_name(lean / "OpenQ" / "Problems" / "X" / "A.lean") == "OpenQ.Problems.X.A"
    assert verifier.module_name(lean / "Other" / "A.lean") is None
    assert verifier.module_name(lean / "OpenQ" / "bad-name.lean") is None
    assert verifier.module_file("OpenQ.Problems.X.A") == lean / "OpenQ" / "Problems" / "X" / "A.lean"
    missing = verifier.check(lean / "OpenQ" / "Nope.lean", "OpenQ.x")
    assert not missing.ok and "file not found" in missing.errors[0]


def test_forbidden_constructs_in_local_imports_block_the_check(tmp_path):
    paths = WorkspacePaths(tmp_path)
    problem = tmp_path / "lean" / "OpenQ" / "Problems" / "X"
    problem.mkdir(parents=True)
    (problem / "Helper.lean").write_text("axiom cheat : False\n")
    (problem / "Main.lean").write_text("import OpenQ.Problems.X.Helper\ntheorem t : False := cheat\n")
    result = LeanVerifier(paths, LeanConfig(lake="/nonexistent/lake")).check(problem / "Main.lean", "t")
    assert not result.ok and not result.compiled
    assert result.forbidden == ["axiom declaration (in lean/OpenQ/Problems/X/Helper.lean)"]
    assert "forbidden constructs" in result.summary


# --------------------------------------------------------------------------- integration with a real Lean install

REFERENCE = os.environ.get("LEAN_ORCH_TEST_LEAN_PROJECT")
requires_lean = pytest.mark.skipif(
    not REFERENCE or not (Path(REFERENCE) / ".lake" / "packages" / "mathlib").is_dir(),
    reason="set LEAN_ORCH_TEST_LEAN_PROJECT to a Lake project with a built Mathlib",
)


@pytest.fixture(scope="module")
def lean_workspace(tmp_path_factory) -> WorkspacePaths:
    """A workspace whose Lake project reuses the reference project's Mathlib build."""
    reference = Path(REFERENCE)
    root = tmp_path_factory.mktemp("lean-ws")
    lean = root / "lean"
    (lean / ".lake").mkdir(parents=True)
    os.symlink(reference / ".lake" / "packages", lean / ".lake" / "packages")
    shutil.copy(reference / "lean-toolchain", lean / "lean-toolchain")
    manifest = json.loads((reference / "lake-manifest.json").read_text())
    manifest["name"] = "openq"
    (lean / "lake-manifest.json").write_text(json.dumps(manifest, indent=2))
    revisions = {package["name"]: package.get("rev") or package.get("inputRev") for package in manifest["packages"]}
    if "Physlib" in revisions:
        options = InitOptions(lean_library="physlib", physlib_rev=revisions["Physlib"])
    else:
        mathlib_rev = next(package["inputRev"] for package in manifest["packages"] if package["name"] == "mathlib")
        options = InitOptions(lean_library="mathlib", mathlib_rev=mathlib_rev)
    template = resources.files("lean_orchestrator").joinpath("templates", "lean")
    (lean / "lakefile.toml").write_text(template.joinpath("lakefile.toml.in").read_text().replace(
        "{requires}", lean_requires(options).rstrip()))
    (lean / "OpenQ" / "Foundations").mkdir(parents=True)
    (lean / "OpenQ.lean").write_text(template.joinpath("OpenQ.lean").read_text())
    (lean / "OpenQ" / "Foundations" / "Basic.lean").write_text(
        template.joinpath("OpenQ", "Foundations", "Basic.lean").read_text())
    problem = lean / "OpenQ" / "Problems" / "T"
    problem.mkdir(parents=True)
    (problem / "Statement.lean").write_text(
        "import Mathlib.Tactic\nnamespace OpenQ.Problems.T\n\n"
        "def MainStatement : Prop :=\n  ∀ a b c : ℕ, 0 < a → 0 < b → 0 < c → a ^ 3 + b ^ 3 ≠ c ^ 3\n\n"
        "def TrivialStatement : Prop := ∀ n : ℕ, n + 0 = n\n\n"
        "def Hidden : Prop := ∀ n : ℕ, n < 0 → False\n\n"
        "def IndirectStatement : Prop := Hidden\n\nend OpenQ.Problems.T\n")
    (problem / "Proofs.lean").write_text(
        "import OpenQ.Foundations.Basic\nimport OpenQ.Problems.T.Statement\nnamespace OpenQ.Problems.T\n\n"
        "theorem good (n : ℕ) : n + 0 = n := by simp\n\n"
        "theorem bad : (1 : ℕ) = 2 := by sorry\n\n"
        "theorem trivial_main : TrivialStatement := fun n => by simp\n\n"
        "theorem restated : ∀ n : ℕ, n + 0 = n := fun n => by simp\n\n"
        "theorem ppt_involution (ρ : Matrix (Fin 2 × Fin 2) (Fin 2 × Fin 2) ℂ) :\n"
        "    OpenQ.partialTranspose (OpenQ.partialTranspose ρ) = ρ :=\n"
        "  OpenQ.partialTranspose_partialTranspose ρ\n\nend OpenQ.Problems.T\n")
    return WorkspacePaths(root)


@pytest.mark.lean
@requires_lean
def test_real_lean_accepts_clean_proofs_and_rejects_sorry(lean_workspace):
    verifier = LeanVerifier(lean_workspace, LeanConfig())
    proofs = lean_workspace.lean_problems_dir / "T" / "Proofs.lean"
    good = verifier.check(proofs, "OpenQ.Problems.T.good", allowed_root=proofs.parent)
    assert good.ok, good.summary
    assert good.decl_kind == "theorem" and good.decl_module == "OpenQ.Problems.T.Proofs"
    assert set(good.axioms) <= {"propext", "Classical.choice", "Quot.sound"}
    assert good.decl_type and "n + 0 = n" in good.decl_type
    bad = verifier.check(proofs, "OpenQ.Problems.T.bad")
    assert not bad.ok and bad.disallowed_axioms == ["sorryAx"] and bad.sorry_in_file
    foreign = verifier.check(proofs, "OpenQ.partialTranspose_partialTranspose")
    assert not foreign.ok and "is defined in OpenQ.Foundations.Basic" in foreign.errors[0]
    reuse = verifier.check(proofs, "OpenQ.Problems.T.ppt_involution")
    assert reuse.ok, reuse.summary


@pytest.mark.lean
@requires_lean
def test_real_lean_formal_statements_and_the_triviality_probe(lean_workspace):
    verifier = LeanVerifier(lean_workspace, LeanConfig())
    statement = lean_workspace.lean_problems_dir / "T" / "Statement.lean"
    main = verifier.check(statement, "OpenQ.Problems.T.MainStatement", expect="prop_def")
    assert main.ok and main.is_prop_def and "a ^ 3 + b ^ 3 ≠ c ^ 3" in main.definition
    probe = verifier.triviality("OpenQ.Problems.T.Statement", "OpenQ.Problems.T.MainStatement")
    assert probe.ran and not probe.trivial, probe.detail
    trivial = verifier.triviality("OpenQ.Problems.T.Statement", "OpenQ.Problems.T.TrivialStatement")
    assert trivial.statement_provable and not trivial.negation_provable
    as_theorem = verifier.check(statement, "OpenQ.Problems.T.MainStatement")
    assert not as_theorem.ok and "expected a theorem" in as_theorem.errors[0]
    indirect = verifier.check(statement, "OpenQ.Problems.T.IndirectStatement", expect="prop_def")
    assert indirect.ok and indirect.unfold == ["OpenQ.Problems.T.IndirectStatement", "OpenQ.Problems.T.Hidden"]
    hidden = verifier.triviality("OpenQ.Problems.T.Statement", "OpenQ.Problems.T.IndirectStatement", indirect.unfold)
    assert hidden.statement_provable, "a vacuous statement behind a helper definition must be detected"


@pytest.mark.lean
@requires_lean
def test_real_lean_main_results_need_the_exact_locked_type(lean_workspace):
    verifier = LeanVerifier(lean_workspace, LeanConfig())
    proofs = lean_workspace.lean_problems_dir / "T" / "Proofs.lean"
    target = "OpenQ.Problems.T.TrivialStatement"
    exact = verifier.check(proofs, "OpenQ.Problems.T.trivial_main", target=target, kernel_recheck=True)
    assert exact.ok and exact.type_matches_target and exact.kernel_recheck == "passed", exact.summary
    restated = verifier.check(proofs, "OpenQ.Problems.T.restated", target=target)
    assert not restated.ok and restated.type_matches_target is False
    negated = verifier.check(proofs, "OpenQ.Problems.T.trivial_main", target=target, negated=True)
    assert not negated.ok
    if verifier.comparator_available():
        verdict = verifier.judge_main_result(
            namespace="T", statement_module="OpenQ.Problems.T.Statement", statement_decl=target,
            proof_module="OpenQ.Problems.T.Proofs", proof_decl="OpenQ.Problems.T.trivial_main", negated=False)
        assert verdict.status == "accepted", verdict.output_tail
        rejected = verifier.judge_main_result(
            namespace="T", statement_module="OpenQ.Problems.T.Statement", statement_decl="OpenQ.Problems.T.MainStatement",
            proof_module="OpenQ.Problems.T.Proofs", proof_decl="OpenQ.Problems.T.bad", negated=False)
        assert rejected.status == "rejected"
