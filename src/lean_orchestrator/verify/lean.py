"""Lean 4 verification: the formal judge of the research loop.

A claim backed by Lean counts as verified only when all of the following hold:

1. the file lies inside the problem's Lean directory, and neither it nor its
   local imports contain constructs that can bypass the kernel or spoof the
   checks (new axioms, unsafe code, metaprograms, command macros, ``#eval``);
2. ``lake build`` compiles its module (builds are serialized by a file lock,
   because Lake itself has no build lock);
3. a probe file, whose metaprogram the orchestrator owns, confirms that the
   declaration exists, is defined in that module, has the expected kind, and
   depends only on the allowed axioms (this rejects ``sorry`` and
   ``native_decide``, which appear as axioms);
4. for main results, the theorem's type is syntactically the locked statement or
   its negation, and ``lake comparator`` accepts it (several independent kernels
   with ``--paranoid``), falling back to a ``leanchecker`` replay when the
   comparator is unavailable.

Formal statements additionally pass a triviality probe: if standard automation
proves the statement or its negation, the formalization is almost surely wrong.
Whether a Lean statement faithfully expresses the informal claim is judged by the
critic, who receives the pretty-printed type produced here.
"""

from __future__ import annotations

import json
import os
import platform
import re
import secrets
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..config import LeanConfig
from ..paths import LEAN_ROOT_NAMESPACE, WorkspacePaths
from ..procutil import Completed, FileLock, run_captured

FORBIDDEN_PATTERNS: dict[str, str] = {
    "axiom declaration": r"(?m)^\s*(?:@\[[^\]]*\]\s*)?(?:(?:private|protected|noncomputable)\s+)*axiom\b",
    "prelude": r"(?m)^\s*prelude\b",
    "unsafe": r"\bunsafe\b",
    "implemented_by": r"\bimplemented_by\b",
    "extern": r"@\[\s*extern\b",
    "debug option": r"set_option\s+debug\.",
    "ofReduceBool": r"\bofReduceBool\b",
    "trustCompiler": r"\btrustCompiler\b",
    "bv_decide": r"\bbv_decide\b",
    "elab": r"(?m)^\s*(?:@\[[^\]]*\]\s*)?(?:(?:local|scoped)\s+)?elab(?:_rules)?\b",
    "macro": r"(?m)^\s*(?:@\[[^\]]*\]\s*)?(?:(?:local|scoped)\s+)?(?:macro|macro_rules)\b",
    "syntax": r"(?m)^\s*(?:@\[[^\]]*\]\s*)?(?:(?:local|scoped)\s+)?syntax\b",
    "run_cmd": r"\brun_(?:cmd|elab|meta|tac)\b",
    "#eval": r"#eval\b",
    "initialize": r"(?m)^\s*(?:builtin_)?initialize\b",
    "open private": r"\bopen\s+private\b",
    "environment modification": r"\b(?:modifyEnv|addDeclWithoutChecking|setEnv|addDecl)\b",
    "#exit": r"(?m)^\s*#exit\b",
}
NATIVE_DECIDE_PATTERN = r"\bnative_decide\b"
FLAG_PATTERNS: dict[str, str] = {
    "sorry": r"\b(?:sorry|admit)\b",
    "custom notation": r"(?m)^\s*(?:(?:local|scoped)\s+)?(?:notation|infix[lr]?|prefix|postfix)\b",
    "prioritized instance": r"(?m)^\s*(?:(?:local|scoped)\s+)?instance\b.*\bpriority\b",
    "attribute removal": r"attribute\s*\[\s*-",
    "opaque constant": r"(?m)^\s*(?:(?:private|protected|noncomputable)\s+)*opaque\b",
    "maxHeartbeats 0": r"maxHeartbeats\s+0\b",
}
DECLARATION_PATTERN = re.compile(r"(?:[^\W\d]|«)[\w'.«»!?₀-₉]*")
TRIVIALITY_TACTICS = ("decide", "simp", "aesop", "omega", "norm_num")


def strip_comments_and_strings(source: str) -> str:
    """Blank out Lean comments (nested block comments included) and string literals."""
    output: list[str] = []
    index, depth, length = 0, 0, len(source)
    in_string = False
    while index < length:
        character = source[index]
        if depth:
            if source.startswith("/-", index):
                depth += 1
                index += 2
            elif source.startswith("-/", index):
                depth -= 1
                index += 2
            else:
                output.append("\n" if character == "\n" else " ")
                index += 1
            continue
        if in_string:
            if character == "\\" and index + 1 < length:
                output.append("  ")
                index += 2
                continue
            if character == '"':
                in_string = False
            output.append("\n" if character == "\n" else " ")
            index += 1
            continue
        if source.startswith("/-", index):
            depth = 1
            index += 2
            continue
        if source.startswith("--", index):
            end = source.find("\n", index)
            index = length if end == -1 else end
            continue
        if character == '"':
            in_string = True
            output.append(" ")
            index += 1
            continue
        output.append(character)
        index += 1
    return "".join(output)


def static_scan(source: str, *, forbid_native_decide: bool = True) -> tuple[list[str], list[str]]:
    """Return (forbidden constructs, constructs the critic should inspect)."""
    code = strip_comments_and_strings(source)
    forbidden = [name for name, pattern in FORBIDDEN_PATTERNS.items() if re.search(pattern, code)]
    if forbid_native_decide and re.search(NATIVE_DECIDE_PATTERN, code):
        forbidden.append("native_decide")
    flags = [name for name, pattern in FLAG_PATTERNS.items() if re.search(pattern, code)]
    return forbidden, flags


_IMPORT_PATTERN = re.compile(r"^\s*import\s+(.+)$", re.M)


def parse_imports(source: str) -> list[str]:
    code = strip_comments_and_strings(source)
    modules: list[str] = []
    for match in _IMPORT_PATTERN.finditer(code):
        modules.extend(match.group(1).split())
    return modules


def parse_name_list(text: str) -> list[str]:
    inner = text.strip()
    if inner.startswith("[") and inner.endswith("]"):
        inner = inner[1:-1]
    return [name.strip() for name in inner.replace("\n", " ").split(",") if name.strip()]


@dataclass
class ComparatorResult:
    status: str
    sandboxed: bool = False
    paranoid: bool = False
    output_tail: str = ""


@dataclass
class TrivialityResult:
    statement_provable: bool = False
    negation_provable: bool = False
    ran: bool = False
    detail: str = ""

    @property
    def trivial(self) -> bool:
        return self.statement_provable or self.negation_provable


@dataclass
class LeanCheck:
    file: str
    declaration: str
    module: str | None = None
    ok: bool = False
    compiled: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sorry_in_file: bool = False
    axioms: list[str] | None = None
    disallowed_axioms: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    decl_kind: str | None = None
    decl_module: str | None = None
    decl_type: str | None = None
    definition: str | None = None
    unfold: list[str] = field(default_factory=list)
    is_prop_def: bool | None = None
    type_matches_target: bool | None = None
    target: str | None = None
    negated: bool = False
    kernel_recheck: str = "skipped"
    comparator: ComparatorResult | None = None
    triviality: TrivialityResult | None = None
    duration_seconds: float = 0.0
    summary: str = ""


class LeanUnavailable(RuntimeError):
    """Lean or the Lake project is not set up."""


_PROBE_PRELUDE = """\
import Lean
open Lean Elab Command

elab "#orch_info " decl:ident : command => do
  let env ← getEnv
  let name := decl.getId
  match env.find? name with
  | none => logError m!"ORCH_MISSING {name}"
  | some info =>
    let kind := match info with
      | .thmInfo _ => "theorem" | .defnInfo _ => "def" | .axiomInfo _ => "axiom"
      | .opaqueInfo _ => "opaque" | .quotInfo _ => "quot" | .inductInfo _ => "inductive"
      | .ctorInfo _ => "constructor" | .recInfo _ => "recursor"
    let moduleName := match env.getModuleIdxFor? name with
      | some index => env.header.moduleNames[index.toNat]!
      | none => `_current
    let isProp := info.type == mkSort levelZero
    logInfo m!"ORCH_INFO kind={kind} module={moduleName} prop_def={isProp}"
    let axioms ← collectAxioms name
    logInfo m!"ORCH_AXIOMS {axioms.toList}"
    let type ← liftTermElabM (Meta.ppExpr info.type)
    logInfo m!"ORCH_TYPE {type}"
    if let .defnInfo definition := info then
      let body ← liftTermElabM (Meta.ppExpr definition.value)
      logInfo m!"ORCH_DEF {body}"
      -- Definitions from the same module that the body uses, for the triviality probe.
      let home := (env.getModuleIdxFor? name).map (·.toNat)
      let mut seen : NameSet := {}
      let mut todo : List Name := definition.value.getUsedConstants.toList
      let mut found : Array Name := #[name]
      for _ in [0:10000] do
        match todo with
        | [] => break
        | constant :: rest =>
          todo := rest
          if seen.contains constant || constant.isInternal then continue
          seen := seen.insert constant
          if (env.getModuleIdxFor? constant).map (·.toNat) == home then
            if let some (.defnInfo inner) := env.find? constant then
              found := found.push constant
              todo := inner.value.getUsedConstants.toList ++ todo
      logInfo m!"ORCH_UNFOLD {found.toList}"

elab "#orch_match " decl:ident target:ident neg:(" negated")? : command => do
  let env ← getEnv
  let some info := env.find? decl.getId | throwError "ORCH_MISSING {decl.getId}"
  let some _ := env.find? target.getId | throwError "ORCH_MISSING_TARGET {target.getId}"
  let expected : Expr :=
    if neg.isSome then mkApp (mkConst ``Not) (mkConst target.getId) else mkConst target.getId
  logInfo m!"ORCH_MATCH {info.type == expected}"
"""


class LeanVerifier:
    def __init__(self, paths: WorkspacePaths, config: LeanConfig) -> None:
        self.paths = paths
        self.config = config
        self.project = paths.root / config.project_dir
        self.build_lock = FileLock(paths.internal_dir / "lake.lock")
        self._comparator_available: bool | None = None
        self._availability_lock = threading.Lock()

    def lake_command(self) -> str | None:
        candidate = self.config.lake
        if "/" in candidate:
            return candidate if Path(candidate).is_file() else None
        found = shutil.which(candidate)
        if found:
            return found
        elan_lake = Path.home() / ".elan" / "bin" / "lake"
        return str(elan_lake) if elan_lake.is_file() else None

    def available(self) -> bool:
        return (
            self.config.enabled
            and self.lake_command() is not None
            and any((self.project / name).is_file() for name in ("lakefile.toml", "lakefile.lean"))
        )

    def module_name(self, file: Path) -> str | None:
        try:
            relative = file.resolve().relative_to(self.project.resolve())
        except ValueError:
            return None
        if relative.suffix != ".lean" or not relative.parts or relative.parts[0] != LEAN_ROOT_NAMESPACE:
            return None
        parts = relative.with_suffix("").parts
        if not all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_']*", part) for part in parts):
            return None
        return ".".join(parts)

    def module_file(self, module: str) -> Path:
        return self.project.joinpath(*module.split(".")).with_suffix(".lean")

    def local_imports(self, file: Path) -> list[Path]:
        """Transitive imports of ``file`` that live in this Lake project."""
        seen: dict[str, Path] = {}
        pending = [file]
        while pending:
            current = pending.pop()
            if not current.is_file():
                continue
            for module in parse_imports(current.read_text(encoding="utf-8")):
                if module.split(".")[0] != LEAN_ROOT_NAMESPACE or module in seen:
                    continue
                path = self.module_file(module)
                seen[module] = path
                pending.append(path)
        return [seen[module] for module in sorted(seen)]

    def _lake(self, arguments: list[str], timeout: float) -> Completed:
        lake = self.lake_command()
        if lake is None:
            raise LeanUnavailable("lake was not found; install elan or set verification.lean.lake")
        environment = dict(os.environ)
        elan_bin = str(Path.home() / ".elan" / "bin")
        if elan_bin not in environment.get("PATH", "").split(os.pathsep):
            environment["PATH"] = elan_bin + os.pathsep + environment.get("PATH", "")
        return run_captured([lake, *arguments], cwd=self.project, timeout=timeout, env=environment)

    def build(self, module: str) -> Completed:
        """``lake build`` one module under the workspace build lock."""
        with self.build_lock:
            return self._lake(["build", module], self.config.build_timeout_minutes * 60)

    def check(
        self,
        file: Path,
        declaration: str,
        *,
        allowed_root: Path | None = None,
        target: str | None = None,
        negated: bool = False,
        expect: str = "theorem",
        kernel_recheck: bool = False,
    ) -> LeanCheck:
        """Verify that ``declaration`` in ``file`` is a sorry-free, axiom-clean Lean fact.

        ``expect`` is ``"theorem"`` for proofs or ``"prop_def"`` for a formal statement
        (a definition of type ``Prop``). With ``target`` set, the theorem's type must be
        exactly that constant, or its negation when ``negated`` is true.
        """
        if expect not in ("theorem", "prop_def"):
            raise ValueError(f"unknown expectation {expect!r}")
        started = time.monotonic()
        result = LeanCheck(file=self._display(file), declaration=declaration, target=target, negated=negated)
        try:
            self._check(result, file, declaration, allowed_root, target, negated, expect, kernel_recheck)
        except LeanUnavailable as error:
            result.errors.append(str(error))
        result.duration_seconds = round(time.monotonic() - started, 1)
        result.summary = self.summarize(result, expect)
        return result

    def _display(self, file: Path) -> str:
        try:
            return self.paths.relative(file)
        except ValueError:
            return str(file)

    def _check(self, result: LeanCheck, file: Path, declaration: str, allowed_root: Path | None,
               target: str | None, negated: bool, expect: str, kernel_recheck: bool) -> None:
        if not file.is_file():
            result.errors.append(f"file not found: {result.file}")
            return
        if allowed_root is not None:
            try:
                file.resolve().relative_to(allowed_root.resolve())
            except ValueError:
                result.errors.append(f"{result.file} is outside the problem's Lean directory")
                return
        module = self.module_name(file)
        if module is None:
            result.errors.append(f"{result.file} is not a module of the {LEAN_ROOT_NAMESPACE} library")
            return
        result.module = module
        if not DECLARATION_PATTERN.fullmatch(declaration):
            result.errors.append(f"invalid declaration name {declaration!r}; use the fully qualified name")
            return

        for path in [file, *self.local_imports(file)]:
            if not path.is_file():
                result.errors.append(f"imported module file not found: {self._display(path)}")
                continue
            forbidden, flags = static_scan(
                path.read_text(encoding="utf-8"), forbid_native_decide=self.config.forbid_native_decide,
            )
            label = "" if path == file else f" (in {self._display(path)})"
            result.forbidden.extend(name + label for name in forbidden)
            result.flags.extend(name + label for name in flags)
        if result.forbidden or result.errors:
            return

        timeout = self.config.build_timeout_minutes * 60
        with self.build_lock:
            build = self._lake(["build", module], timeout)
            for line in build.output.splitlines():
                stripped = line.strip()
                if stripped.startswith("error:") or ": error:" in stripped:
                    result.errors.append(stripped[:500])
                elif stripped.startswith("warning:") or ": warning:" in stripped:
                    result.warnings.append(stripped[:500])
            result.sorry_in_file = bool(re.search(r"declaration uses [`']sorry[`']", build.output))
            result.compiled = build.returncode == 0
            if not result.compiled:
                if build.timed_out:
                    result.errors.append(f"lake build timed out after {timeout:g}s")
                if not result.errors:
                    result.errors.append(build.output.strip()[-2000:] or f"lake build exited with {build.returncode}")
                return
            self._probe(result, module, declaration, target, negated, timeout)
            if kernel_recheck and result.decl_kind is not None and not result.errors:
                replay = self._lake(["env", "leanchecker", module], timeout)
                failed = (
                    replay.returncode != 0 or replay.timed_out
                    or "uncaught exception" in replay.output
                    or re.search(r"(?m)^\s*error", replay.output) is not None
                )
                result.kernel_recheck = "failed" if failed else "passed"
                if failed:
                    result.errors.append("kernel replay (leanchecker) failed: " + replay.output.strip()[-1000:])

        allowed = set(self.config.allowed_axioms)
        if result.axioms is not None:
            result.disallowed_axioms = [name for name in result.axioms if name not in allowed]
        if expect == "theorem":
            kind_ok = result.decl_kind == "theorem"
        else:
            kind_ok = result.decl_kind == "def" and bool(result.is_prop_def)
        if result.decl_kind is not None and not kind_ok:
            wanted = "a theorem" if expect == "theorem" else "a definition of type Prop"
            result.errors.append(f"{declaration} is a {result.decl_kind}, expected {wanted}")
        if result.decl_module is not None and result.decl_module != module:
            result.errors.append(f"{declaration} is defined in {result.decl_module}, not in {module}")
        if target is not None and result.type_matches_target is False:
            wanted = f"¬ {target}" if negated else target
            result.errors.append(f"the type of {declaration} is not syntactically {wanted}")
        result.ok = self._passes(result, kind_ok, target)

    @staticmethod
    def _passes(result: LeanCheck, kind_ok: bool, target: str | None) -> bool:
        return (
            result.compiled
            and not result.errors
            and not result.forbidden
            and result.axioms is not None
            and not result.disallowed_axioms
            and kind_ok
            and (target is None or result.type_matches_target is True)
            and result.kernel_recheck != "failed"
            and (result.comparator is None or result.comparator.status != "rejected")
        )

    def _write_probe(self, lines: list[str]) -> Path:
        self.paths.tmp_dir.mkdir(parents=True, exist_ok=True)
        probe = self.paths.tmp_dir / f"probe_{secrets.token_hex(4)}.lean"
        probe.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return probe

    @staticmethod
    def _messages(stdout: str) -> list[dict]:
        messages = []
        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    messages.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return messages

    def _probe(self, result: LeanCheck, module: str, declaration: str, target: str | None,
               negated: bool, timeout: float) -> None:
        lines = [f"import {module}", *_PROBE_PRELUDE.splitlines(), ""]
        info_line = len(lines) + 1
        lines.append(f"#orch_info {declaration}")
        match_line = None
        if target:
            match_line = len(lines) + 1
            lines.append(f"#orch_match {declaration} {target}{' negated' if negated else ''}")
        probe = self._write_probe(lines)
        try:
            completed = self._lake(["env", "lean", "--json", str(probe)], timeout)
        finally:
            probe.unlink(missing_ok=True)
        messages = self._messages(completed.stdout)
        for message in messages:
            data = str(message.get("data", ""))
            line = int(message.get("pos", {}).get("line", 0))
            if message.get("severity") == "error":
                result.errors.append(f"probe: {data.strip()[:500]}")
            elif line == info_line and data.startswith("ORCH_INFO"):
                info = dict(part.split("=", 1) for part in data.split()[1:] if "=" in part)
                result.decl_kind = info.get("kind")
                result.decl_module = info.get("module")
                result.is_prop_def = info.get("prop_def") == "true"
            elif line == info_line and data.startswith("ORCH_AXIOMS"):
                result.axioms = parse_name_list(data[len("ORCH_AXIOMS"):])
            elif line == info_line and data.startswith("ORCH_TYPE "):
                result.decl_type = data[len("ORCH_TYPE "):].strip()[:4000]
            elif line == info_line and data.startswith("ORCH_DEF "):
                result.definition = data[len("ORCH_DEF "):].strip()[:4000]
            elif line == info_line and data.startswith("ORCH_UNFOLD"):
                result.unfold = parse_name_list(data[len("ORCH_UNFOLD"):])
            elif match_line is not None and line == match_line and data.startswith("ORCH_MATCH"):
                result.type_matches_target = data.split()[-1] == "true"
        if completed.timed_out:
            result.errors.append(f"probe timed out after {timeout:g}s")
        elif not messages:
            result.errors.append(f"probe produced no output: {completed.output.strip()[-1000:]}")

    def triviality(self, module: str, declaration: str, unfold: list[str] | None = None) -> TrivialityResult:
        """Try standard automation on a Prop definition and on its negation.

        ``unfold`` lists the definitions to expand first (the statement and the
        definitions of its own module that it uses), so that a vacuous hypothesis
        hidden behind a helper definition is still exposed.
        """
        names = ", ".join(unfold or [declaration])
        lines = [f"import {module}", "import Mathlib.Tactic", ""]
        ranges = {}
        for label, goal in (("statement", declaration), ("negation", f"¬ {declaration}")):
            start = len(lines) + 1
            lines += [
                "set_option maxHeartbeats 200000 in",
                f"example : {goal} := by",
                f"  (try simp only [{names}]) <;>",
                "  first",
                *(f"    | {tactic}" for tactic in TRIVIALITY_TACTICS),
                "",
            ]
            ranges[label] = (start, len(lines))
        probe = self._write_probe(lines)
        try:
            with self.build_lock:
                completed = self._lake(["env", "lean", "--json", str(probe)], self.config.triviality_timeout_minutes * 60)
        finally:
            probe.unlink(missing_ok=True)
        if completed.timed_out:
            return TrivialityResult(ran=False, detail="triviality probe timed out")
        messages = self._messages(completed.stdout)
        if not messages and completed.returncode != 0:
            return TrivialityResult(ran=False, detail=completed.output.strip()[-500:])
        errors_by_goal = {label: 0 for label in ranges}
        for message in messages:
            if message.get("severity") != "error":
                continue
            line = int(message.get("pos", {}).get("line", 0))
            for label, (start, end) in ranges.items():
                if start <= line <= end:
                    errors_by_goal[label] += 1
        outcome = TrivialityResult(
            statement_provable=errors_by_goal["statement"] == 0,
            negation_provable=errors_by_goal["negation"] == 0,
            ran=True,
        )
        if outcome.statement_provable:
            outcome.detail = f"automation ({', '.join(TRIVIALITY_TACTICS)}) proves the statement"
        elif outcome.negation_provable:
            outcome.detail = f"automation ({', '.join(TRIVIALITY_TACTICS)}) proves the negation"
        else:
            outcome.detail = "automation proves neither the statement nor its negation"
        return outcome

    def comparator_available(self) -> bool:
        with self._availability_lock:
            if self._comparator_available is None:
                try:
                    probe = self._lake(["comparator", "--help"], 60)
                    self._comparator_available = "Judge a solution" in probe.output
                except LeanUnavailable:
                    self._comparator_available = False
            return self._comparator_available

    def judge_main_result(self, *, namespace: str, statement_module: str, statement_decl: str,
                          proof_module: str, proof_decl: str, negated: bool) -> ComparatorResult:
        """Check a main result with ``lake comparator`` against a challenge the orchestrator writes."""
        if self.config.comparator == "off":
            return ComparatorResult(status="off")
        if not self.comparator_available():
            return ComparatorResult(status="unavailable", output_tail="lake comparator is not available")
        sandboxed = shutil.which(os.environ.get("COMPARATOR_BWRAP", "bwrap")) is not None
        if not sandboxed and not self.config.allow_unsandboxed_comparator:
            return ComparatorResult(status="unavailable", output_tail="bubblewrap is not installed")
        judge_dir = self.project / LEAN_ROOT_NAMESPACE / "Judge" / namespace
        judge_dir.mkdir(parents=True, exist_ok=True)
        goal = f"¬ {statement_decl}" if negated else statement_decl
        theorem = f"{LEAN_ROOT_NAMESPACE}.Judge.{namespace}.main"
        (judge_dir / "Challenge.lean").write_text(
            f"import {statement_module}\n\ntheorem {theorem} : {goal} := sorry\n", encoding="utf-8")
        (judge_dir / "Solution.lean").write_text(
            f"import {statement_module}\nimport {proof_module}\n\ntheorem {theorem} : {goal} :=\n  {proof_decl}\n",
            encoding="utf-8")
        config = {
            "challenge_module": f"{LEAN_ROOT_NAMESPACE}.Judge.{namespace}.Challenge",
            "solution_module": f"{LEAN_ROOT_NAMESPACE}.Judge.{namespace}.Solution",
            "theorem_names": [theorem],
            "permitted_axioms": list(self.config.allowed_axioms),
        }
        self.paths.tmp_dir.mkdir(parents=True, exist_ok=True)
        config_path = self.paths.tmp_dir / f"comparator-{namespace}-{secrets.token_hex(3)}.json"
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        arguments = ["comparator", "--config", str(config_path)]
        if self.config.comparator_paranoid:
            arguments.append("--paranoid")
        if not sandboxed:
            arguments.append("--inadvisably-no-sandbox")
        try:
            with self.build_lock:
                completed = self._lake(arguments, self.config.build_timeout_minutes * 60 * 2)
        finally:
            config_path.unlink(missing_ok=True)
        status = {0: "accepted", 1: "rejected"}.get(completed.returncode, "unavailable")
        if completed.timed_out:
            status = "rejected"
        return ComparatorResult(status=status, sandboxed=sandboxed, paranoid=self.config.comparator_paranoid,
                                output_tail=completed.output.strip()[-3000:])

    @staticmethod
    def summarize(result: LeanCheck, expect: str) -> str:
        if result.ok:
            base = "verified theorem" if expect == "theorem" else "compiles as a definition of type Prop"
            detail = f"; axioms: {', '.join(result.axioms or []) or 'none'}"
            if result.target:
                detail += f"; type is exactly {'¬ ' if result.negated else ''}{result.target}"
            if result.kernel_recheck == "passed":
                detail += "; kernel replay passed"
            if result.comparator is not None and result.comparator.status == "accepted":
                detail += "; comparator accepted" + (" (paranoid)" if result.comparator.paranoid else "")
                if not result.comparator.sandboxed:
                    detail += " without sandbox"
            if result.triviality is not None and result.triviality.ran:
                detail += f"; triviality probe: {result.triviality.detail}"
            return base + detail
        reasons = []
        if result.forbidden:
            reasons.append("forbidden constructs: " + ", ".join(result.forbidden))
        if result.disallowed_axioms:
            reasons.append("disallowed axioms: " + ", ".join(result.disallowed_axioms))
        if result.comparator is not None and result.comparator.status == "rejected":
            reasons.append("comparator rejected the proof")
        if result.errors:
            reasons.append(f"{len(result.errors)} error(s); first: {result.errors[0][:300]}")
        if not reasons and result.axioms is None:
            reasons.append("the axiom report was not produced")
        return "not verified: " + ("; ".join(reasons) or "unknown reason")


def host_has_sandbox() -> bool:
    return platform.system() == "Linux" and shutil.which("bwrap") is not None
