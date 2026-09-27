"""Command-line interface: ``lean-orch <command>``."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__, inbox
from .agents import AgentError
from .catalogue.local import make_entry
from .catalogue.qiqcop import CatalogueFetchError
from .config import ROLES, ConfigurationError, load_config
from .doctor import FAIL, OK, run_checks
from .events import Console
from .notify import NotificationError, check_delivery, valid_email
from .orchestrator import Orchestrator, WorkspaceBusy, build_services
from .paths import CONFIG_FILENAME, WorkspacePaths
from .portfolio import ACTIVE, CANDIDATE, DEFERRED, PENDING_REVIEW, SUSPENDED, PortfolioEntry, PortfolioStore
from .scheduler import Scheduler
from .serde import to_dict
from .verify.experiments import ExperimentVerifier
from .verify.lean import LeanVerifier
from .workspace import (
    DEFAULT_LEAN_LIBRARY, DEFAULT_MATHLIB_REV, DEFAULT_PHYSLIB_REV, LEAN_LIBRARIES, InitOptions, WorkspaceError,
    agents_confirmed, confirm_agents, init_workspace, refresh_prompts, write_agents_config,
)


def _workspace(args: argparse.Namespace) -> Path:
    if args.workspace:
        return Path(args.workspace).expanduser().resolve()
    environment = os.environ.get("LEAN_ORCH_WORKSPACE")
    start = Path(environment) if environment else Path.cwd()
    for candidate in (start, *start.parents):
        if (candidate / CONFIG_FILENAME).is_file():
            return candidate.resolve()
    raise ConfigurationError(f"no {CONFIG_FILENAME} found in {start} or its parents; use --workspace or 'lean-orch init'")


def _email(value: str) -> str:
    if not valid_email(value):
        raise argparse.ArgumentTypeError("must be a single email address")
    return value


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lean-orch",
        description="Autonomous portfolio research on open problems in quantum information, with Lean 4 as judge.",
    )
    parser.add_argument("-w", "--workspace", help="workspace directory (default: search upward from the current one)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")

    init = commands.add_parser("init", help="create a research workspace")
    init.add_argument("directory", nargs="?", default=".")
    init.add_argument("--no-lean-setup", action="store_true", help="skip fetching Mathlib and its build cache")
    init.add_argument("--no-python-env", action="store_true", help="skip 'uv sync' for the experiment environment")
    init.add_argument("--no-git", action="store_true", help="do not create a git repository")
    init.add_argument("--lean-library", choices=LEAN_LIBRARIES, default=DEFAULT_LEAN_LIBRARY,
                      help="physlib (Mathlib plus the QuantumInfo library, default) or plain mathlib")
    init.add_argument("--physlib-rev", default=DEFAULT_PHYSLIB_REV, help="Physlib revision to pin")
    init.add_argument("--mathlib-rev", default=DEFAULT_MATHLIB_REV,
                      help=f"Mathlib revision for --lean-library mathlib (default {DEFAULT_MATHLIB_REV})")
    init.add_argument("--toolchain", help="override the Lean toolchain (must match the library)")

    sync = commands.add_parser("sync", help="import new and changed problems from the QIQCOP Zoo")
    sync.add_argument("--force", action="store_true", help="download even if the catalogue digest is unchanged")

    triage = commands.add_parser("triage", help="assess untriaged problems with the triage agent")
    triage.add_argument("--limit", type=_positive_int, help="assess at most this many problems")
    triage.add_argument("--problem", action="append", help="(re)assess a specific problem; repeatable")
    triage.add_argument("--yes", action="store_true", help="accept agents.toml without asking")

    run = commands.add_parser("run", help="run the research loop")
    run.add_argument("--epochs", type=int, help="maximum number of epochs to start (0: unlimited)")
    run.add_argument("--hours", type=float, help="wall-clock budget in hours (0: unlimited)")
    run.add_argument("--parallel", type=_positive_int, help="problems researched concurrently")
    run.add_argument("--problem", action="append", help="restrict the run to this problem; repeatable")
    run.add_argument("--email", type=_email, help="email address for notifications")
    run.add_argument("--no-sync", action="store_true", help="do not contact the QIQCOP Zoo")
    run.add_argument("--no-triage", action="store_true", help="do not triage new problems before running")
    run.add_argument("--verbose", action="store_true", help="stream agent output to the terminal")
    run.add_argument("--yes", action="store_true", help="accept agents.toml without asking")

    commands.add_parser("doctor", help="check agents, Lean, Python, and network for this workspace")

    status = commands.add_parser("status", help="show the portfolio")
    status.add_argument("--all", action="store_true", help="also list deferred and untriaged problems")
    status.add_argument("--json", action="store_true", help="print the portfolio as JSON")

    show = commands.add_parser("show", help="print a problem's progress report")
    show.add_argument("problem")
    show.add_argument("--dossier", action="store_true", help="print the compact dossier instead")

    add = commands.add_parser("add", help="add a problem by hand")
    add.add_argument("--title", required=True)
    group = add.add_mutually_exclusive_group(required=True)
    group.add_argument("--statement", help="statement text (Markdown with LaTeX)")
    group.add_argument("--statement-file", type=Path, help="file containing the statement")
    add.add_argument("--id", help="problem ID (default: derived from the title)")
    add.add_argument("--field", action="append", default=[])
    add.add_argument("--topic", action="append", default=[])
    add.add_argument("--reference", action="append", default=[])
    add.add_argument("--candidate", action="store_true", help="skip triage and make it a candidate immediately")

    hint = commands.add_parser("hint", help="give the agents working on a problem a hint")
    hint.add_argument("problem")
    hint.add_argument("text")
    for name, help_text in (("activate", "make a problem schedulable again"),
                            ("suspend", "suspend a problem"), ("defer", "exclude a problem from scheduling")):
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument("problem")
    pin = commands.add_parser("pin", help="give a problem a large, fixed priority bonus")
    pin.add_argument("problem")
    pin.add_argument("--off", action="store_true", help="remove the pin")

    review = commands.add_parser("review", help="list or decide claimed resolutions")
    review.add_argument("problem", nargs="?")
    decision = review.add_mutually_exclusive_group()
    decision.add_argument("--accept", action="store_true")
    decision.add_argument("--reject", metavar="REASON")

    commands.add_parser("stop", help="finish the current iterations, then stop the running loop")
    commands.add_parser("refresh-prompts", help="replace workspace prompts with the packaged defaults (with backup)")

    check_lean = commands.add_parser("check-lean", help="verify a Lean declaration as the orchestrator does")
    check_lean.add_argument("file", type=Path)
    check_lean.add_argument("--decl", required=True, help="fully qualified declaration name")
    check_lean.add_argument("--expect", choices=("theorem", "prop_def"), default="theorem")
    check_lean.add_argument("--target", help="require the type to be exactly this Prop constant")
    check_lean.add_argument("--negated", action="store_true", help="require the negation of --target")

    build = commands.add_parser("build", help="lake build one module under the workspace build lock")
    build.add_argument("module")

    experiment = commands.add_parser("check-experiment", help="re-run an experiment as the orchestrator does")
    experiment.add_argument("directory", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    console = Console()
    try:
        return _dispatch(args, console)
    except (ConfigurationError, WorkspaceError, WorkspaceBusy, CatalogueFetchError, NotificationError) as error:
        console.warn(f"lean-orch: {error}")
        return 2
    except KeyError as error:
        console.warn(f"lean-orch: {error.args[0] if error.args else error}")
        return 2
    except AgentError as error:
        console.warn(f"lean-orch: {error}")
        return 1


def _dispatch(args: argparse.Namespace, console: Console) -> int:
    if args.command == "init":
        options = InitOptions(lean_setup=not args.no_lean_setup, python_env=not args.no_python_env,
                              git=not args.no_git, lean_library=args.lean_library, physlib_rev=args.physlib_rev,
                              mathlib_rev=args.mathlib_rev, toolchain=args.toolchain)
        paths = init_workspace(Path(args.directory), options, console)
        console.info(f"workspace ready: {paths.root}\n"
                     f"next: review {paths.agents_config.name} (which CLI runs which agent) and {paths.config.name}, "
                     "then run 'lean-orch doctor' and 'lean-orch run'")
        return 0
    workspace = _workspace(args)
    handler = {
        "sync": _sync, "triage": _triage, "run": _run, "status": _status, "show": _show, "add": _add,
        "hint": _hint, "activate": _set_status, "suspend": _set_status, "defer": _set_status, "pin": _pin,
        "review": _review, "stop": _stop, "refresh-prompts": _refresh_prompts, "doctor": _doctor,
        "check-lean": _check_lean,
        "build": _build,
        "check-experiment": _check_experiment,
    }[args.command]
    return handler(args, workspace, console)


def _agents_ready(workspace: Path, console: Console, *, assume_yes: bool) -> bool:
    """Create agents.toml if needed and have a human confirm it once, as agent-runner does."""
    paths = WorkspacePaths(workspace)
    created = not paths.agents_config.exists()
    write_agents_config(paths)
    if agents_confirmed(paths):
        return True
    agents = load_config(workspace).agents
    if paths.agents_confirmed.exists():
        print(f"{paths.agents_config} changed since it was last confirmed.")
    print(("Created " if created else "Using ") + f"{paths.agents_config} with these agent commands:")
    for role in ROLES:
        command = agents.for_role(role).command
        missing = "" if shutil.which(command[0]) else "   <- not found on PATH"
        print(f"  {role:<11} {shlex.join(command)}{missing}")
    if assume_yes:
        confirm_agents(paths)
        return True
    if not sys.stdin.isatty():
        console.warn(f"review {paths.agents_config}, then rerun with --yes to accept it")
        return False
    while True:
        try:
            answer = input("Start with these agent settings? [y/N]: ").strip().lower()
        except EOFError:
            answer = "n"
        if answer in ("y", "yes"):
            confirm_agents(paths)
            return True
        if answer in ("", "n", "no"):
            print(f"Stopped. Edit {paths.agents_config}, then rerun.")
            return False
        print("Please answer yes or no.")


def _with_orchestrator(workspace: Path, console: Console, **kwargs) -> Orchestrator:
    orchestrator = Orchestrator(build_services(workspace, console=console, **kwargs))
    orchestrator.prepare()
    return orchestrator


def _sync(args, workspace: Path, console: Console) -> int:
    orchestrator = _with_orchestrator(workspace, console)
    try:
        orchestrator.sync(force=args.force)
    finally:
        orchestrator.close()
    return 0


def _triage(args, workspace: Path, console: Console) -> int:
    if not _agents_ready(workspace, console, assume_yes=args.yes):
        return 0
    orchestrator = _with_orchestrator(workspace, console)
    try:
        ids = [orchestrator.services.portfolio.resolve(item) for item in args.problem] if args.problem else None
        orchestrator.triage(limit=args.limit, problem_ids=ids)
    finally:
        orchestrator.close()
    return 0


def _run(args, workspace: Path, console: Console) -> int:
    if not _agents_ready(workspace, console, assume_yes=args.yes):
        return 0
    config = load_config(workspace)
    email = args.email or config.notify.email
    if email:
        check_delivery()
    orchestrator = _with_orchestrator(workspace, console, verbose=args.verbose, email=email)
    try:
        summary = orchestrator.run(max_epochs=args.epochs, max_hours=args.hours, parallel=args.parallel,
                                   only=args.problem, sync=not args.no_sync, triage=not args.no_triage)
    finally:
        orchestrator.close()
    console.info(f"run finished: {len(summary.epochs)} epoch(s); {summary.stopped_reason or 'done'}")
    for error in summary.errors:
        console.warn(error)
    return 1 if summary.errors and not summary.epochs else 0


def _status(args, workspace: Path, console: Console) -> int:
    paths = WorkspacePaths(workspace)
    portfolio = PortfolioStore(paths).snapshot()
    if args.json:
        print(json.dumps(to_dict(portfolio), indent=2, ensure_ascii=False))
        return 0
    scheduler = Scheduler(load_config(workspace))
    priorities = {pid: priority.total for pid, priority in scheduler.priorities(portfolio).items()}
    counts: dict[str, int] = {}
    for entry in portfolio.entries.values():
        counts[entry.display_status] = counts.get(entry.display_status, 0) + 1
    print(f"Workspace {paths.root}")
    print(f"Global epochs: {portfolio.global_epochs} (exploration {portfolio.exploration_epochs}); "
          + ", ".join(f"{status} {count}" for status, count in sorted(counts.items())))
    shown = [entry for entry in portfolio.entries.values()
             if args.all or entry.status in (ACTIVE, CANDIDATE, SUSPENDED, PENDING_REVIEW) or entry.epochs]
    shown.sort(key=lambda entry: (-priorities.get(entry.id, -9.0), entry.id))
    if shown:
        print(f"\n{'priority':>8}  {'status':<20} {'epochs':>6} {'stag':>4} {'ewma':>5}  problem")
    for entry in shown:
        priority = f"{priorities[entry.id]:.2f}" if entry.id in priorities else "-"
        ewma = "" if entry.ewma_progress is None else f"{entry.ewma_progress:.2f}"
        print(f"{priority:>8}  {entry.display_status:<20} {entry.epochs:>6} {entry.stagnation:>4} {ewma:>5}  "
              f"{entry.id}  {entry.title[:70]}")
    pending = inbox.pending(paths)
    if pending:
        print(f"\n{len(pending)} queued command(s) will be applied by the running loop.")
    print(f"\nDetails: {paths.portfolio_md}")
    return 0


def _show(args, workspace: Path, console: Console) -> int:
    paths = WorkspacePaths(workspace)
    store = PortfolioStore(paths)
    entry = store.get(store.resolve(args.problem))
    if not entry.folder:
        console.warn(f"{entry.id} has not been worked on yet")
        return 1
    path = paths.problems_dir / entry.folder / ("DOSSIER.md" if args.dossier else "PROGRESS.md")
    print(path.read_text(encoding="utf-8"))
    return 0


def _add(args, workspace: Path, console: Console) -> int:
    statement = args.statement if args.statement is not None else args.statement_file.read_text(encoding="utf-8")
    entry = make_entry(args.title, statement, problem_id=args.id, fields=args.field, topics=args.topic,
                       references=args.reference)
    orchestrator = _with_orchestrator(workspace, console)
    try:
        services = orchestrator.services
        if services.catalogue.exists(entry.source, entry.id):
            raise WorkspaceError(f"a problem with ID {entry.id} already exists")
        services.catalogue.save(entry)
        with services.portfolio.edit() as portfolio:
            portfolio.entries[entry.id] = PortfolioEntry(
                id=entry.id, source=entry.source, title=entry.title,
                status=CANDIDATE if args.candidate else "unexplored",
                upstream_status=entry.status, upstream_digest=entry.digest,
            )
        orchestrator.render_portfolio()
        services.git.commit([services.paths.catalogue_dir, services.paths.portfolio, services.paths.portfolio_md],
                            f"catalogue: add {entry.id}")
    finally:
        orchestrator.close()
    console.info(f"added {entry.id}")
    return 0


def _queue(workspace: Path, console: Console, kind: str, problem: str, **payload) -> int:
    paths = WorkspacePaths(workspace)
    store = PortfolioStore(paths)
    problem_id = store.resolve(problem)
    inbox.post(paths, kind, problem_id, **payload)
    try:
        orchestrator = _with_orchestrator(workspace, console)
    except WorkspaceBusy:
        console.info("queued; the running loop applies it at its next safe point")
        return 0
    try:
        orchestrator.apply_inbox()
    finally:
        orchestrator.close()
    console.info(f"applied {kind} to {problem_id}")
    return 0


def _hint(args, workspace: Path, console: Console) -> int:
    return _queue(workspace, console, "hint", args.problem, text=args.text)


def _set_status(args, workspace: Path, console: Console) -> int:
    status = {"activate": ACTIVE, "suspend": SUSPENDED, "defer": DEFERRED}[args.command]
    return _queue(workspace, console, "set_status", args.problem, status=status)


def _pin(args, workspace: Path, console: Console) -> int:
    return _queue(workspace, console, "pin", args.problem, pinned=not args.off)


def _review(args, workspace: Path, console: Console) -> int:
    paths = WorkspacePaths(workspace)
    store = PortfolioStore(paths)
    if args.problem is None:
        pending = [entry for entry in store.snapshot().entries.values() if entry.status == PENDING_REVIEW]
        if not pending:
            print("No claimed resolutions await review.")
        for entry in pending:
            print(f"{entry.id}  {entry.title}\n  {entry.note}\n  report: {paths.problems_dir / (entry.folder or '')}/PROGRESS.md")
        return 0
    if not args.accept and args.reject is None:
        console.warn("give --accept or --reject REASON")
        return 2
    decision = "accept" if args.accept else "reject"
    return _queue(workspace, console, "review", args.problem, decision=decision, reason=args.reject or "")


def _stop(args, workspace: Path, console: Console) -> int:
    paths = WorkspacePaths(workspace)
    paths.stop_file.parent.mkdir(parents=True, exist_ok=True)
    paths.stop_file.touch()
    console.info("stop requested: the loop finishes its current iterations and exits")
    return 0


def _refresh_prompts(args, workspace: Path, console: Console) -> int:
    orchestrator = _with_orchestrator(workspace, console)
    try:
        changed = refresh_prompts(orchestrator.paths)
    finally:
        orchestrator.close()
    console.info(f"updated prompts: {', '.join(changed)}" if changed else "prompts are already current")
    return 0


def _doctor(args, workspace: Path, console: Console) -> int:
    paths = WorkspacePaths(workspace)
    checks = run_checks(paths, load_config(workspace))
    width = max(len(check.name) for check in checks)
    for check in checks:
        print(f"{check.status.upper():<5} {check.name:<{width}}  {check.detail}")
    failures = [check for check in checks if check.status == FAIL]
    print(f"\n{sum(check.status == OK for check in checks)} ok, {len(failures)} failing")
    return 1 if failures else 0


def _check_lean(args, workspace: Path, console: Console) -> int:
    config = load_config(workspace)
    verifier = LeanVerifier(WorkspacePaths(workspace), config.verification.lean)
    result = verifier.check(args.file.resolve(), args.decl, target=args.target, negated=args.negated,
                            expect=args.expect)
    print(json.dumps(to_dict(result), indent=2, ensure_ascii=False))
    return 0 if result.ok else 1


def _build(args, workspace: Path, console: Console) -> int:
    config = load_config(workspace)
    completed = LeanVerifier(WorkspacePaths(workspace), config.verification.lean).build(args.module)
    sys.stdout.write(completed.output)
    return completed.returncode


def _check_experiment(args, workspace: Path, console: Console) -> int:
    config = load_config(workspace)
    result = ExperimentVerifier(WorkspacePaths(workspace), config.verification.experiments).check(args.directory.resolve())
    print(json.dumps(to_dict(result), indent=2, ensure_ascii=False))
    return 0 if result.ok else 1
