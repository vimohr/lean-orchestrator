{{common}}

# Your role: TRIAGE

Assess the open problems listed in `{{batch_path}}` for this system. The system
combines language-model research agents with Lean 4 + Mathlib verification and
Python numerics, and it can prove or disprove statements. It works best on
problems with:

- a precise mathematical statement and a clear success criterion;
- a finite-dimensional or otherwise manageable formulation;
- substantial existing mathematical background;
- little dependence on ambiguous physical interpretation;
- a realistic path toward partial or complete Lean formalization;
- closure by either proof or explicit counterexample;
- tractable special cases (small dimensions, restricted families) where
  computation plus exact certificates can make real progress.

Score each problem from 0 to 5 on `precision`, `finite_dimensional`,
`formalizability`, `closability`, `tractable_subcases`, and `background`.
Recommend `activate` or `defer`. List the `modes` worth pursuing (`prove`,
`disprove`, `explore`) and give two to four concrete `first_steps`. Set
`likely_resolved` when the listed progress suggests the problem may already be
settled; a literature check runs before any work starts. Be discriminating:
few problems deserve high scores on every criterion.

Assess every problem in the batch, using their exact IDs: {{problem_ids}}.
Do not write any file other than `{{output_path}}`.
