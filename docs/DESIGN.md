# Design notes

This document records why `lean-orch` is built the way it is. The system runs
distributed mathematical search with persistent verified knowledge. Agents are
disposable; their verified discoveries remain available to later attempts.

## 1. Who decides what is true

Generated arguments can contain errors, overlook existing results, or answer a
different question. `lean-orch` separates judgement from generation and ranks
evidence:

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

An agent with write access could change the checks that judge its output.
Imprecise numerical tolerances and unproved auxiliary lemmas can also conceal a
failed argument. The defenses here:

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
  a common symptom of misformalization.

## 3. Measuring progress

Progress measures information gain rather than elapsed time. The supervisor
scores each iteration from 0 to 3 with explicit categories (new lemma, Lean-verified
step, reduction, subsidiary counterexample, literature theorem, eliminated class of
approaches, improved formal statement, new line of attack). To keep that score
honest, `recording.score_progress` grounds it in recorded artifacts, weighted by how
they were checked. Without a weighted gain of at least 1.0 the score is capped at 1;
a meaningful score additionally requires an explicit material advance on the
declared gap. Lean verification establishes correctness without automatically
establishing research value. Lexical matches (approach fingerprints) are warnings
for assessment rather than an automatic zero, and every plan must state its novelty
relative to earlier failures before compute is spent.

Stagnation escalates in two steps: after two low-gain
effective, conclusively assessed iterations the supervisor must change strategy;
after four the problem is suspended, not declared false. Execution failures,
broken verification, and inconclusive review retain their records and costs without
increasing mathematical stagnation or lowering the progress average.

## 4. Scheduling

The priority formula is implemented in `scheduler.Scheduler.priority`:
expected progress is a discounted mean of epoch
scores shrunk toward the triage prior; the exploration bonus is UCB1-shaped; relevance
counts links from other problems' new results; stagnation and suspension enter as
penalties, the latter decaying with elapsed epochs. A reserved share of epochs goes
to untried or eligible suspended problems. Human hints revive suspended problems.
By default a suspended problem requires new relevant evidence or explicit human
reactivation before returning; elapsed-time penalty decay alone does not reopen it.

The v2 engine uses explicit qualitative allocation notes and
per-original-problem effective rounds. Its independent state model and workflow
are documented in [ORCHESTRATOR_V2.md](ORCHESTRATOR_V2.md).

## 5. Memory

Three layers keep the state reusable across hundreds of iterations:

* an append-only event log (`.lean-orch/events.jsonl`) and git history;
* per-problem structured state (`state.json`) with human views (`PROGRESS.md`) and a
  compact dossier (`DOSSIER.md`) that every agent reads first: established results,
  open subgoals, branches, a do-not-retry list, recent lessons, and human hints;
* a cross-problem registry of accepted results (`knowledge/`) and relevance links.

Locked formal statements and the claim registry retain project state separately
from individual agents' proofs and conversations.

## 6. Prove and disprove

Sharp questions start with a prove branch and a counterexample branch; the
supervisor is reminded when one lags. Counterexample work uses numerics first
and exact certification second (rational or algebraic witnesses, verification
scripts, and, where feasible, a Lean proof of the negation).

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
