# Research workspace

This workspace is managed by `lean-orch`. It holds a portfolio of open problems
in quantum information, the persistent research state of every problem that has
been tried, and a Lean 4 library in which results are formally verified.

## Where to look

| Path | Content |
| --- | --- |
| `PORTFOLIO.md` | Every problem with its status, priority, and progress. Start here. |
| `problems/<slug>/PROBLEM.md` | Description of a problem that has been tried. |
| `problems/<slug>/PROGRESS.md` | Its full progress report: verified results, attempts, dead ends, timeline. |
| `problems/<slug>/DOSSIER.md` | Compact summary that every agent reads first. |
| `problems/<slug>/reports/` | Narrative report written at the end of each research epoch. |
| `problems/<slug>/iterations/` | Raw record of every iteration: plan, report, verification, critique, decision. |
| `problems/<slug>/work/`, `experiments/` | Notes, derivations, and experiment scripts written by agents. |
| `problems/<slug>/lean` | Link to the problem's Lean files in `lean/OpenQ/Problems/<Namespace>/`. |
| `knowledge/RESULTS.md` | Accepted results across all problems. |
| `catalogue/` | Imported problem records (QIQCOP Zoo and problems added by hand). |
| `lean/` | Lake project on Physlib or Mathlib (see `lakefile.toml`); `OpenQ/Foundations/` holds shared definitions. |
| `agents.toml` | Which CLI and model runs each agent role (confirmed at the first run and after every change). |
| `lean-orch.toml` | Configuration: budgets, scheduling, verification, catalogue filters. |
| `prompts/` | Role prompts (editable between runs). |
| `schemas/` | JSON output contracts, regenerated from the code at every start. |
| `.lean-orch/` | Portfolio state, event log, and (untracked) agent run logs. |

## Trust levels

Only the orchestrator assigns trust. `lean_verified` means Lean checked the proof
(only standard axioms) and the critic confirmed that the Lean statement formalizes
the claim; main results additionally pass `lake comparator`. `reproduced` means an
independent re-run of the experiment matched. `critic_accepted` is an informal
result that survived review. Everything else is `proposed`, `rejected`, or `retracted`.

## Commands

```sh
lean-orch doctor                 # check agents, Lean, Python, and network
lean-orch status                 # portfolio overview
lean-orch run --epochs 5         # run the research loop
lean-orch show <problem>         # print a progress report
lean-orch hint <problem> "..."   # give agents a hint (reactivates a suspended problem)
lean-orch review <problem> --accept | --reject "reason"
lean-orch stop                   # finish the current iterations, then stop
lean-orch refresh-prompts        # install updated default prompts (keeps a backup)
```
