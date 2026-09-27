import Mathlib.Analysis.Complex.Order
import Mathlib.LinearAlgebra.Matrix.PosDef
import Mathlib.LinearAlgebra.Matrix.Kronecker
import Mathlib.LinearAlgebra.Matrix.Trace

/-!
# Shared foundations for quantum-information problems

Conventions used throughout the `OpenQ` library:

* A finite-dimensional system is an index type `n` with `[Fintype n] [DecidableEq n]`;
  operators on it are `Matrix n n ℂ`.
* A bipartite operator on `A ⊗ B` is a `Matrix (m × n) (m × n) ℂ`, where `m` indexes `A`
  and `n` indexes `B`. This matches `Matrix.kronecker`:
  `(X ⊗ₖ Y) (a, b) (a', b') = X a a' * Y b b'`.
* Positivity over `ℂ` uses the scoped order `open scoped ComplexOrder`.

These definitions are shared by all problems. Formal statements should use them rather
than ad hoc variants, so that results transfer between problems.
-/

namespace OpenQ

open Matrix
open scoped Kronecker ComplexOrder

variable {m n : Type*} [Fintype m] [Fintype n] [DecidableEq m] [DecidableEq n]

/-- A density matrix: positive semidefinite with unit trace. -/
def IsDensityMatrix (ρ : Matrix n n ℂ) : Prop :=
  ρ.PosSemidef ∧ ρ.trace = 1

/-- Partial transpose on the second tensor factor:
`(ρ^{T_B}) (a, b) (a', b') = ρ (a, b') (a', b)`. -/
def partialTranspose (ρ : Matrix (m × n) (m × n) ℂ) : Matrix (m × n) (m × n) ℂ :=
  fun i j => ρ (i.1, j.2) (j.1, i.2)

/-- A bipartite operator has positive partial transpose (PPT). -/
def IsPPT (ρ : Matrix (m × n) (m × n) ℂ) : Prop :=
  (partialTranspose ρ).PosSemidef

/-- Partial trace over the second factor `B`. -/
def partialTraceRight (ρ : Matrix (m × n) (m × n) ℂ) : Matrix m m ℂ :=
  fun a a' => ∑ b, ρ (a, b) (a', b)

/-- Partial trace over the first factor `A`. -/
def partialTraceLeft (ρ : Matrix (m × n) (m × n) ℂ) : Matrix n n ℂ :=
  fun b b' => ∑ a, ρ (a, b) (a, b')

/-- A bipartite state is separable if it is a finite convex combination of product states. -/
def IsSeparable (ρ : Matrix (m × n) (m × n) ℂ) : Prop :=
  ∃ (k : ℕ) (p : Fin k → ℝ) (α : Fin k → Matrix m m ℂ) (β : Fin k → Matrix n n ℂ),
    (∀ i, 0 ≤ p i) ∧ (∑ i, p i = 1) ∧ (∀ i, IsDensityMatrix (α i)) ∧
    (∀ i, IsDensityMatrix (β i)) ∧ ρ = ∑ i, ((p i : ℝ) : ℂ) • (α i ⊗ₖ β i)

/-- The separable cone: nonnegative combinations of tensor products of positive semidefinite
operators. Unlike `IsSeparable`, it does not require unit trace, so it applies to unnormalized
operators such as Choi matrices of completely positive maps. -/
def IsSeparableOperator (ρ : Matrix (m × n) (m × n) ℂ) : Prop :=
  ∃ (k : ℕ) (p : Fin k → ℝ) (α : Fin k → Matrix m m ℂ) (β : Fin k → Matrix n n ℂ),
    (∀ i, 0 ≤ p i) ∧ (∀ i, (α i).PosSemidef) ∧ (∀ i, (β i).PosSemidef) ∧
    ρ = ∑ i, ((p i : ℝ) : ℂ) • (α i ⊗ₖ β i)

/-- Every separable state lies in the separable cone. -/
theorem IsSeparable.isSeparableOperator {ρ : Matrix (m × n) (m × n) ℂ} (h : IsSeparable ρ) :
    IsSeparableOperator ρ := by
  obtain ⟨k, p, α, β, hp, -, hα, hβ, rfl⟩ := h
  exact ⟨k, p, α, β, hp, fun i => (hα i).1, fun i => (hβ i).1, rfl⟩

/-- The partial transpose is an involution. -/
theorem partialTranspose_partialTranspose (ρ : Matrix (m × n) (m × n) ℂ) :
    partialTranspose (partialTranspose ρ) = ρ := by
  ext i j
  rfl

/-- The partial transpose preserves the trace. -/
theorem trace_partialTranspose (ρ : Matrix (m × n) (m × n) ℂ) :
    (partialTranspose ρ).trace = ρ.trace := by
  simp [Matrix.trace, partialTranspose]

/-- The partial transpose of a product operator transposes the second factor. -/
theorem partialTranspose_kronecker (X : Matrix m m ℂ) (Y : Matrix n n ℂ) :
    partialTranspose (X ⊗ₖ Y) = X ⊗ₖ Yᵀ := by
  ext i j
  simp [partialTranspose, Matrix.kronecker, Matrix.kroneckerMap_apply, Matrix.transpose_apply]

/-- Tracing out `B` from a product operator scales the `A` factor by `tr Y`. -/
theorem partialTraceRight_kronecker (X : Matrix m m ℂ) (Y : Matrix n n ℂ) :
    partialTraceRight (X ⊗ₖ Y) = Y.trace • X := by
  ext a a'
  simp [partialTraceRight, Matrix.kronecker, Matrix.kroneckerMap_apply, Matrix.trace,
    Finset.mul_sum, mul_comm]

end OpenQ
