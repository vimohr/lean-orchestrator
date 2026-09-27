import OpenQ.Foundations.Basic

/-!
# OpenQ research library

`OpenQ.Foundations` holds shared, orchestrator-protected definitions.
`OpenQ.Problems.<Namespace>` holds the work on one problem: its locked formal statement,
verified results, and scratch files. `OpenQ.Judge` holds challenge and solution files that
the orchestrator generates for `lake comparator`.

Build single modules (`lake build OpenQ.Problems.X.File`); a full `lake build` also
compiles unfinished scratch files.
-/
