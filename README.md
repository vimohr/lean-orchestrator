# lean-orchestrator

`lean-orch` runs an autonomous research loop over a portfolio of open problems
in quantum information. Disposable CLI agents (`codex exec`, `claude -p`) take
the roles of researcher, skeptic, supervisor, literature agent, and triage
agent. The orchestrator, not a language model, decides what counts as knowledge:
Lean 4 checks formal proofs, experiments are re-run independently, citations
are resolved, and every other claim is only as strong as the critic's review.
Compute moves between problems according to measured, verified progress.

The problem catalogue is the [QIQCOP Zoo](https://qiqc-op.com) (open problems
in quantum information and computation), synchronized through its public API.
Problems can also be added by hand.

## How it works

```text
QIQCOP Zoo ──sync──> catalogue ──triage──> portfolio ──scheduler──> epoch on one problem
                                                                        │
      ┌─────────────────────────────────────────────────────────────────┘
      │  literature check (is it still open? precise statement, references)
      │  supervisor: plan one concrete step (prove or disprove branch)
      │  ┌─> researcher attempts it (notes, Lean files, experiments)
      │  │   verification: lake build + axiom audit, experiment re-run, citation lookup
      │  │   critic: adversarial review; faithfulness of Lean statements
      │  │   orchestrator records claims with trust levels it assigns itself
      │  └── supervisor scores information gain and decides the next step
      │
      └─> continue, branch, reformulate, suspend on stagnation, or claim a resolution
          (resolutions wait for human review)
```

* **Research epochs.** Work proceeds in bounded epochs (12 iterations by default).
  Each iteration is plan, attempt, verify, criticize, record, decide.
* **Persistent state.** Every problem that is tried gets `problems/<slug>/` with a
  description (`PROBLEM.md`), a progress report (`PROGRESS.md`), a compact dossier for
  agents (`DOSSIER.md`), epoch reports, the raw iteration records, and its Lean files.
  The state follows the template of the design: precise statement, assumptions,
  literature, verified lemmas, Lean results, attempted approaches with failure reasons,
  counterexample searches, experiments, reductions, open subgoals, promising
  directions, dead ends, supervisor assessment, and stagnation count.
* **Prove and disprove in parallel.** Sharp problems start with a prove branch and a
  counterexample branch; the supervisor is reminded when one is neglected.
* **Progress, not time.** The supervisor scores each iteration from 0 to 3, but the
  score is grounded: without accepted artifacts it is capped at 1, a new Lean-verified
  result always counts as meaningful, and repeating a failed approach scores 0.
  Artifacts are weighted by how they were checked (Lean and reproduced experiments 1.0,
  informal accepted claims and dead ends 0.5, verified references 0.25). Two stagnating
  iterations trigger a forced change of strategy; four suspend the problem.
* **Portfolio scheduling.**
  `priority = expected_progress + exploration_bonus + relevance_of_new_results - stagnation_penalty - suspension_penalty`,
  with a UCB-style exploration bonus, a triage prior, and a reserved share of epochs
  (20 %) for untried or suspended problems. Suspended problems return when their
  penalty decays, when another problem produces a relevant result, or on a human hint.

## Trust model

Only the orchestrator assigns trust levels:

| Level | Meaning |
| --- | --- |
| `lean_verified` | Lean verified the declaration and the critic confirmed the Lean statement formalizes the claim. |
| `reproduced` | An independent re-run of the registered experiment matched and the critic accepted it. |
| `critic_accepted` | Informal claim that survived adversarial review; all cited artifacts passed. |
| `proposed`, `rejected`, `retracted` | Everything else. |

The Lean pipeline (`src/lean_orchestrator/verify/lean.py`):

1. Static scan of the file and its local imports. It rejects new axioms, `prelude`,
   `unsafe`, `implemented_by`, `extern`, `native_decide`, `bv_decide`, `#eval`,
   `elab`/`macro`/`syntax`, `run_cmd`, and environment modification.
2. `lake build` of the module, serialized by a workspace lock because Lake has none.
3. A probe with an orchestrator-owned metaprogram reports the declaration's kind,
   defining module, pretty-printed type, and axioms (`collectAxioms`). Only `propext`,
   `Classical.choice`, and `Quot.sound` are allowed, which also excludes `sorry`.
4. A formal statement of the main problem must be a `def ... : Prop`; a triviality
   probe rejects it if standard automation proves it or its negation. After the
   critic accepts it, its files are locked.
5. A main result must be a theorem whose type is syntactically the locked statement
   or its negation. It then goes to `lake comparator --paranoid` (Lean 4.35 and later),
   which re-checks it in several independent kernels; `leanchecker` is the fallback.

Cited references are resolved through the arXiv API (batched), DataCite (which
registers every arXiv paper), and Crossref. A reference whose identifier does not
resolve, or resolves to a different title, cannot support an accepted claim.

An integrity guard keeps a hashed copy of every orchestrator-owned file (state,
iteration records, locked Lean files, configuration). After each agent call it
restores modified or deleted files, quarantines unexpected new files, and records
a violation that later agents are warned about.

## Requirements

* Linux or macOS with Python 3.11 or newer, git, and [uv](https://docs.astral.sh/uv/)
* The `codex` and `claude` CLIs, installed and authenticated (either one alone also works)
* [elan](https://github.com/leanprover/elan) for Lean 4; `lean-orch init` fetches the
  Lean libraries and Mathlib's prebuilt cache (about 8 GB per workspace, 10 minutes)

## Install

```sh
uv tool install git+https://github.com/vimohr/lean-orchestrator
uv tool upgrade lean-orchestrator        # later updates
```

For development:

```sh
git clone git@github.com:vimohr/lean-orchestrator.git && cd lean-orchestrator
uv sync                                  # creates .venv with the development tools
uv run pytest
```

## Quick start

```sh
lean-orch init ~/qi-research          # workspace, Lean project, Python env, git, agents.toml
cd ~/qi-research
$EDITOR agents.toml                   # which CLI and model runs which agent
lean-orch doctor                      # check agents, Lean, Python, and network
lean-orch run --epochs 20 --email you@example.org
lean-orch status                      # portfolio overview (also PORTFOLIO.md)
lean-orch show <problem>              # progress report of one problem
```

The first `lean-orch run` (or `triage`) shows the agent commands from `agents.toml` and
asks for confirmation once, as agent-runner does; `--yes` confirms without asking.
`agents.toml` is generated from the CLIs found on the machine, and it is recreated if
deleted. General settings (budgets, scheduling, verification, catalogue filters) live
in `lean-orch.toml`.

`lean-orch run` also syncs the catalogue and triages new problems unless `--no-sync` or
`--no-triage` is given. Triage is bounded (`triage.max_per_run`, 40 problems per
start, preferred topics first) and resumes whenever the pool of candidates runs dry.
A run stops when its budget (`--epochs`, `--hours`) is spent, when no problem is
eligible, on `lean-orch stop` (after the current iterations), or on Ctrl-C (press
twice to stop running agents immediately). Runs resume from the saved state.

Steering while it runs (commands are applied between iterations):

```sh
lean-orch hint <problem> "Try the symmetric subspace first."   # also revives a suspended problem
lean-orch activate | suspend | defer <problem>
lean-orch pin <problem>                                          # large fixed priority bonus
lean-orch review                                                 # list claimed resolutions
lean-orch review <problem> --accept | --reject "reason"
lean-orch add --title "..." --statement-file statement.md --candidate
```

Agents use three helper commands: `lean-orch check-lean FILE --decl NAME` (the
same Lean check the orchestrator runs), `lean-orch build MODULE` (a locked
`lake build`), and `lean-orch check-experiment DIR`.

Prompts in `prompts/` belong to the workspace and may be edited. After upgrading
`lean-orch`, `lean-orch refresh-prompts` installs the new defaults and backs up the
old files; output schemas are regenerated automatically on every start.

## Configuration notes

* **Agents (`agents.toml`).** By default Codex (`gpt-6-astra`, maximum reasoning)
  researches and Claude (`claude-opus-5-5`, maximum effort) criticizes, supervises,
  checks the literature, and triages; a critic from another model family makes
  correlated errors less likely. Every role is told to build on the literature: the
  literature agent reviews each problem before work starts and every five epochs, and
  the researcher, supervisor, and critic search for prior work as they go.
* **Safety without containers.** Codex runs in its own sandbox
  (`--sandbox workspace-write`, based on Landlock on Linux): it can write only inside
  the workspace and has no network, while `--search` keeps web search available.
  Claude runs with your normal Claude Code settings, so allow the tools it needs there
  (shell for Lean and Python, web search, edits in the workspace). Independently of
  both, the integrity guard restores anything an agent changes among the
  orchestrator's own files. `lean-orch doctor` checks that Landlock is active.
* **MCP.** `.lean-orch/mcp.json` lists the QIQCOP Zoo MCP server (and, with
  `mcp.lean_lsp = true`, `lean-lsp-mcp`). Add `"--mcp-config", "{mcp_config}"` to a
  Claude command to use it.
* **Lean library.** The default is [Physlib](https://github.com/leanprover-community/physlib)
  at a pinned revision (Lean 4.34.1). Its QuantumInfo library formalizes states,
  channels (Choi and Kraus forms), partial traces, von Neumann, relative, and Rényi
  entropies, fidelity, and separability, and agents are told to use these definitions
  instead of ad hoc ones. `OpenQ.Foundations` adds the partial transpose, PPT, and the
  separable cone, which Physlib lacks. Building QuantumInfo once takes about four
  minutes on a 12-core machine. `lake comparator` ships with Lean 4.35; until Physlib
  moves there, main results get a `leanchecker` kernel replay instead, and
  `lean-orch doctor` shows which check applies. `lean-orch init --lean-library mathlib`
  uses plain Mathlib 4.35 with the comparator.
* **Long runs.** Run inside `tmux` or a batch job. If bubblewrap (`bwrap`) is installed,
  `lake comparator` sandboxes its builds; otherwise it runs unsandboxed and says so.
  For email, the local `sendmail`/`mail` or an SMTP relay (`LEAN_ORCH_SMTP_HOST`,
  `LEAN_ORCH_EMAIL_FROM`, ...) is used.
* **Parallelism.** `loop.max_parallel` researches several problems at once. Keep it at
  1 unless your model quotas allow concurrent agents.

## Development

The test suite drives complete epochs with a scripted fake agent
(`tests/fixtures/fake_agent.py`), so it needs no model access. The Lean integration
tests run against a real Mathlib build:

```sh
uv run pytest
LEAN_ORCH_TEST_LEAN_PROJECT=/path/to/a/mathlib/project uv run pytest -m lean
```

## Authors

Vinícius Mohr and Claude (Anthropic).

## Acknowledgements

The command structure follows [agent-runner](https://github.com/vimohr/agent-runner).
Design choices draw on published lessons from AlphaEvolve, the AI co-scientist,
Kosmos, Aletheia, AlphaProof Nexus, Seed-Prover, and Ax-Prover, and on the Lean FRO's
comparator.
