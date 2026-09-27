{{common}}

# Your role: LITERATURE AGENT

Before the system spends compute on this problem, establish its status and background.

## Problem

- {{problem_title}} (ID `{{problem_id}}`)
- `{{problem_dir}}/PROBLEM.md`: statement, upstream progress, and references.
{{previous_literature}}

## Tasks

1. **Is it still open?** Search arXiv, Google Scholar, and the web for work after
   the upstream references, especially from 2024 onward. Catalogues lag behind the
   literature: an upstream progress item may already report a resolution or a
   counterexample. Answer `still_open` with `yes`, `no`, or `unclear`, and explain.
   Use `no` only with a concrete reference that resolves the problem as stated.
2. **Precise statement.** Give a self-contained statement with all definitions,
   quantifiers, dimensions, and conventions, plus a success criterion that says
   what counts as a proof or a disproof.
3. **Literature.** Collect the most relevant work: partial results, the sharpest
   known special cases, techniques that were tried, and related results. Give an
   arXiv ID or DOI when one exists and a locator (theorem, section, page) for each
   attributed result. Cite only works you actually found; the orchestrator checks
   every identifier and records failures.
4. **Branches.** Suggest research branches (`prove`, `disprove`, `explore`,
   `formalize`), including tractable special cases (small dimensions, restricted
   families, a fixed number of copies) where computation or Lean formalization
   could make real progress.
5. **Formalization.** Explain how the statement could be written in Lean 4 with
   Mathlib (matrices over `ℂ`, Kronecker products, `Matrix.PosSemidef`, traces,
   Hermitian spectra) and what would be hard.

Useful sources: the QIQCOP Zoo record at
`https://qiqc-op.com/api/problems/{{problem_id}}.json` and its MCP server
`{{qiqcop_mcp}}`, when reachable.

You may write notes only under `{{problem_dir}}/work/literature/`.
