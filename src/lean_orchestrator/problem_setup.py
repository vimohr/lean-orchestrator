"""Create the folder of a problem the first time the system works on it.

Every problem that is tried gets ``problems/<slug>/`` with its description
(``PROBLEM.md``), progress report (``PROGRESS.md``), compact dossier
(``DOSSIER.md``), machine-readable state, iteration records, epoch reports, a
work area, experiments, and a link to its Lean directory.
"""

from __future__ import annotations

import os

from .catalogue.base import CatalogueEntry
from .context import Services
from .jsonio import sha256_file
from .paths import ProblemPaths, lean_identifier, short_id, slugify
from .portfolio import PortfolioEntry
from .render import render_dossier, render_problem, render_progress
from .serde import to_dict
from .state import ResearchState, Stamp, StatementVersion, load_state, state_to_json

_LEAN_README = """\
# Lean files for: {title}

Module prefix `{prefix}`. Put declarations in `namespace {prefix}`.
Files holding verified results or the locked formal statement are protected by
the orchestrator; build on them by importing them from new files.
"""


def problem_paths(services: Services, entry: PortfolioEntry) -> ProblemPaths:
    if entry.folder is None or entry.lean_namespace is None:
        raise ValueError(f"{entry.id} has no folder yet")
    return services.paths.problem(entry.folder, entry.lean_namespace)


def _unique_folder(services: Services, entry: PortfolioEntry) -> tuple[str, str]:
    suffix = short_id(entry.id)
    folder = f"{slugify(entry.title, 60)}-{suffix}"
    namespace = f"{lean_identifier(entry.title)}_{suffix}"
    taken_folders = {item.folder for item in services.portfolio.snapshot().entries.values() if item.id != entry.id}
    counter = 2
    base_folder, base_namespace = folder, namespace
    while folder in taken_folders or (services.paths.problems_dir / folder).exists():
        folder, namespace = f"{base_folder}-{counter}", f"{base_namespace}{counter}"
        counter += 1
    return folder, namespace


def ensure_problem_folder(services: Services, entry: PortfolioEntry, source: CatalogueEntry) -> tuple[ProblemPaths, ResearchState]:
    """Create the folder on first use; otherwise load the existing state."""
    guard = services.guard
    if entry.folder is not None and entry.lean_namespace is not None:
        problem = problem_paths(services, entry)
        if problem.state.is_file():
            return problem, load_state(problem.state)
    folder, namespace = (entry.folder, entry.lean_namespace) if entry.folder and entry.lean_namespace \
        else _unique_folder(services, entry)
    problem = services.paths.problem(folder, namespace)
    for directory in (problem.root, problem.work_dir, problem.experiments_dir, problem.iterations_dir,
                      problem.reports_dir, problem.lean_dir):
        directory.mkdir(parents=True, exist_ok=True)
    readme = problem.lean_dir / "README.md"
    if not readme.exists():
        guard.write_text(readme, _LEAN_README.format(title=source.title, prefix=problem.lean_module_prefix))
    if not problem.lean_link.exists() and not problem.lean_link.is_symlink():
        try:
            os.symlink(os.path.relpath(problem.lean_dir, problem.root), problem.lean_link)
        except OSError as error:  # the link is a convenience; the Lean files stay reachable by path
            services.console.warn(f"could not link {problem.lean_link} to the Lean directory: {error}")
    titles = {item.id: item.title for item in services.portfolio.snapshot().entries.values()}
    guard.write_text(problem.problem_md, render_problem(source, titles))
    guard.write_json(problem.source, to_dict(source))
    state = ResearchState(
        problem_id=entry.id,
        title=source.title,
        folder=folder,
        lean_namespace=namespace,
        statement_versions=[StatementVersion(version=1, text=source.statement,
                                             reason="upstream statement", created=Stamp(0, 0))],
    )
    guard.write_json(problem.state, state_to_json(state))
    guard.protect_tree(problem.iterations_dir, watch=True)
    guard.protect_tree(problem.reports_dir, watch=True)

    def assign(item: PortfolioEntry) -> None:
        item.folder = folder
        item.lean_namespace = namespace

    updated = services.portfolio.update_entry(entry.id, assign)
    write_views(services, problem, state, updated)
    services.events.emit("problem_folder_created", problem=entry.id, folder=folder, lean_namespace=namespace)
    return problem, state


def write_views(services: Services, problem: ProblemPaths, state: ResearchState, entry: PortfolioEntry) -> None:
    """Persist the state and regenerate the progress report and dossier."""
    services.guard.write_json(problem.state, state_to_json(state))
    services.guard.write_text(problem.progress_md, render_progress(state, entry))
    services.guard.write_text(problem.root / "DOSSIER.md", render_dossier(state, entry))


def protect_problem(services: Services, problem: ProblemPaths, state: ResearchState) -> list[str]:
    """Register a problem's orchestrator-owned files with the integrity guard at startup."""
    guard = services.guard
    warnings = []
    for path in (problem.problem_md, problem.progress_md, problem.state, problem.source, problem.root / "DOSSIER.md",
                 problem.lean_dir / "README.md"):
        if path.is_file():
            guard.protect(path)
    guard.protect_tree(problem.iterations_dir, watch=True)
    guard.protect_tree(problem.reports_dir, watch=True)
    for relative, digest in state.locked_lean_files.items():
        path = services.paths.root / relative
        if not path.is_file():
            warnings.append(f"locked Lean file {relative} is missing")
            continue
        if sha256_file(path) != digest:
            warnings.append(f"locked Lean file {relative} changed while the orchestrator was not running")
        guard.protect(path)
    return warnings
