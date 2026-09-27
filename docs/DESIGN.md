# Design notes

This document records why `lean-orch` is built the way it is. The guiding
principle is the one of the original brief: the system is distributed
mathematical search with persistent verified knowledge, not several language
models talking to one another. Agents are disposable; their verified discoveries
are not.

## 1. Who decides what is true

Published agent research systems show how easily unverified claims accumulate.
A Gemini-based study of 700 Erdős problems listed as open addressed 13 of them, and
8 of these turned out to be solved already in the literature
([arXiv:2601.22401](https://arxiv.org/abs/2601.22401)). Independent scientists
judged 79.4 % of the statements in Kosmos reports accurate, so roughly one in five
was not ([arXiv:2511.02824](https://arxiv.org/abs/2511.02824)). Language models
also write "proofs" of false statements when prompted to
([BrokenMath, arXiv:2510.04721](https://arxiv.org/abs/2510.04721)).

`lean-orch` therefore separates judgement from generation and ranks evidence:

1. **Lean** decides formal correctness. The orchestrator runs the checks itself,
   computes axioms with its own metaprogram (imported modules can override
   `#print axioms` with macros), and sends main results to `lake comparator`,
   which replays them in several independent kernels.
2. **Re-execution** decides numerical claims: the registered verification script
   runs again in a clean copy and its output must match.
3. **Identifier resolution** decides whether a cited paper exists (arXiv, DataCite,
   Crossref). It does not decide whether the paper says what is claimed.
4. **The critic** judges everything machines cannot: whether a Lean statement
   formalizes the informal claim, whether an argument has gaps, whether a
   computation supports its interpretation. The critic can only lower a claim's
   standing below what the machine checks support, never raise it above them.

Trust levels (`lean_verified`, `reproduced`, `critic_accepted`, `proposed`,
`rejected`, `retracted`) are assigned by `recording.decide_trust`, a pure function
that is unit-tested over its whole decision table.

## 2. Tamper resistance

Agents with write access exploit their verifiers, for example by editing the tests
that judge them ([ImpossibleBench, arXiv:2510.20270](https://arxiv.org/abs/2510.20270)).
Search systems likewise exploit imprecise numerical checks
([arXiv:2511.02864](https://arxiv.org/abs/2511.02864)), and formal proof search must
guard against moving the difficulty into unproved auxiliary lemmas
([arXiv:2605.22763](https://arxiv.org/abs/2605.22763)). The defenses here:

* the integrity guard restores any orchestrator-owned file an agent changes and
  quarantines forged files in watched directories, recording a violation that
  later prompts mention;
* locked formal statements and verified Lean files cannot be edited, only imported;
* a main result must have exactly the locked statement's type (checked on the
  `Expr`, not on text), so restating an easier variant does not count;
* the static scan rejects constructs that could bypass the kernel or spoof the
  probe (`axiom`, `unsafe`, `implemented_by`, `native_decide`, `#eval`, `elab`,
  `macro`, `syntax`, environment modification);
* a triviality probe rejects formal statements that automation proves or refutes,
  a common symptom of misformalization
  ([Xena blog on Erdős formalizations](https://xenaproject.wordpress.com/2025/12/05/formalization-of-erdos-problems/)).

## 3. Measuring progress

The brief asks for information gain rather than elapsed time. The supervisor
scores each iteration from 0 to 3 with explicit categories (new lemma, Lean-verified
step, reduction, subsidiary counterexample, literature theorem, eliminated class of
approaches, improved formal statement, new line of attack). To keep that score
honest, `recording.score_progress` grounds it in recorded artifacts, weighted by how
they were checked. Without a weighted gain of at least 1.0 the score is capped at 1;
a new Lean-verified result is always meaningful; repeating a failed approach scores
0 whatever the supervisor says. Repeats are detected lexically (approach
fingerprints) and by the critic, and every plan must state its novelty relative to
earlier failures before compute is spent, which follows ShinkaEvolve's rejection of
near-duplicates before evaluation ([arXiv:2509.19349](https://arxiv.org/abs/2509.19349)).

Stagnation escalates in two steps, as in AdaEvolve's stagnation ladder
([arXiv:2602.20133](https://arxiv.org/abs/2602.20133)): after two low-gain
iterations the supervisor must change strategy; after four the problem is
suspended, not declared false.

## 4. Scheduling

The priority formula of the brief is implemented literally
(`scheduler.Scheduler.priority`): expected progress is a discounted mean of epoch
scores shrunk toward the triage prior; the exploration bonus is UCB1-shaped; relevance
counts links from other problems' new results; stagnation and suspension enter as
penalties, the latter decaying with elapsed epochs. A reserved share of epochs goes
to untried or suspended problems, following the exploration reserves of AlphaEvolve
and DeepScientist ([arXiv:2509.26603](https://arxiv.org/abs/2509.26603)). Human
hints revive suspended problems, as Agent Laboratory found human checkpoints
valuable ([arXiv:2501.04227](https://arxiv.org/abs/2501.04227)).

## 5. Memory

Three layers keep the state reusable across hundreds of iterations:

* an append-only event log (`.lean-orch/events.jsonl`) and git history;
* per-problem structured state (`state.json`) with human views (`PROGRESS.md`) and a
  compact dossier (`DOSSIER.md`) that every agent reads first: established results,
  open subgoals, branches, a do-not-retry list, recent lessons, and human hints;
* a cross-problem registry of accepted results (`knowledge/`) and relevance links.

Anthropic's Fermat formalization found that disposable agents lose track of
project state unless statements live in a durable structure separate from proofs
([anthropic.com](https://www.anthropic.com/research/formalizing-fermats-last-theorem));
the locked formal statement and the claim registry play that role here.

## 6. Prove and disprove

Sharp questions start with a prove branch and a counterexample branch; the
supervisor is reminded when one lags. Counterexample work uses numerics first
and exact certification second (rational or algebraic witnesses, verification
scripts, and, where feasible, a Lean proof of the negation), as in Aristotle's use
of goal negation as a search action ([arXiv:2510.01346](https://arxiv.org/abs/2510.01346)).

## 7. Lean library

Most catalogue problems are stated in terms of quantum states, channels, and
entropies. Physlib's QuantumInfo library (formerly Lean-QuantumInfo) formalizes
these objects with a substantial theory: Choi and Kraus representations, partial
traces, strong subadditivity, data processing for sandwiched Rényi divergences,
joint convexity of the relative entropy, and the generalized quantum Stein lemma.
With plain Mathlib, every problem would redefine them, and each ad hoc definition
is one more place where a formal statement can silently differ from the intended
one. The default workspace therefore depends on Physlib, and `OpenQ.Foundations`
supplies what it still lacks (partial transpose, PPT, the separable cone). The
price is one Lean version: Physlib pins Lean 4.34.1, while the built-in
`lake comparator` arrives with 4.35, so main results are replayed with
`leanchecker` until Physlib upgrades. QuantumInfo still contains a few `sorry`
lemmas; the axiom audit rejects any proof that depends on them.

## 8. Known limitations

* Informal claims can only reach `critic_accepted`; the critic is a model, and two
  models from different families reduce but do not remove correlated errors.
* `lake comparator` sandboxes with bubblewrap on Linux only; on macOS it runs
  unsandboxed, which the verification record states.
* Literature checks confirm that references exist, not what they prove.
* The triage agent's scores are a prior, not evidence; compute allocation adapts
  once problems have been tried.
