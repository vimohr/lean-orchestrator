# Multi-Agent System for Exploring Open Quantum Information Problems

## Central Idea

Build an autonomous research system that continuously explores a
catalogue of open problems in quantum information. Rather than assigning
one AI agent to a single problem indefinitely, the system maintains a
**portfolio of open problems** and dynamically allocates research effort
according to measurable progress.

The core system has at least three roles:

-   **Researcher** --- attempts to solve, reduce, or better understand
    the current problem.
-   **Skeptic/Critic** --- actively searches for errors, hidden
    assumptions, counterexamples, and gaps in proposed arguments.
-   **Supervisor** --- decides whether to continue the current
    direction, branch into a new approach, reformulate the problem,
    suspend it, or switch to another problem.

Lean 4 serves as a formal verification layer whenever claims can be
formalized. Lean is the judge of formal correctness, not the language
model.

## Research Loop

``` text
Open-problem catalogue
        |
        v
Literature/status check
        |
        v
     Supervisor
        |
        v
     Researcher
        |
        v
   Skeptic/Critic
        |
        v
Lean / numerical / symbolic verification
        |
        v
Persistent research state
        |
        +---- meaningful progress ---> continue / branch
        |
        +---- stagnation ------------> suspend and try another problem
```

Work proceeds in bounded **research epochs** rather than allowing an
agent to think indefinitely. An epoch might contain 10--20 iterations.
Each iteration should:

1.  Read the accumulated research state.
2.  Choose one promising next step.
3.  Attempt that step.
4.  Verify or criticize the result.
5.  Record what was learned.
6.  Let the supervisor decide what happens next.

## Persistent Research State

The important output of an unsuccessful attempt is not a conversation
transcript but structured knowledge that future agents can reuse.

For every problem, maintain something like:

``` text
Problem:
Status:

Precise statement:
Known assumptions:
Relevant literature:

Verified lemmas:
Formalized Lean results:

Attempted approaches:
- approach
- result
- reason for failure

Counterexamples searched:
Numerical experiments:

Current reductions:
Open subgoals:

Promising directions:
Known dead ends:

Supervisor assessment:
Stagnation count:
```

This allows an agent to stop without losing useful work.

## Progress, Not Just Time

A problem should not be abandoned merely because it remains unsolved
after a fixed amount of time. The supervisor should measure
**information gain**.

Examples of meaningful progress include:

-   proving a new auxiliary lemma;
-   Lean-verifying part of an argument;
-   reducing the main conjecture to a smaller statement;
-   finding a counterexample to a subsidiary conjecture;
-   discovering a relevant theorem in the literature;
-   eliminating an entire class of approaches;
-   improving the formal statement or assumptions;
-   identifying a genuinely new line of attack.

Repeatedly generating variations of the same failed argument counts as
stagnation.

When progress remains low for several iterations, the problem is
**suspended**, not declared impossible or false.

## Portfolio of Problems

The supervisor maintains many candidate problems simultaneously.

``` text
Problem A  suspended
Problem B  active
Problem C  unexplored
Problem D  promising
Problem E  suspended
```

Compute is preferentially allocated to problems showing progress, while
some resources remain reserved for exploration of new or previously
suspended problems.

A conceptual scheduling rule is:

``` text
priority(problem)
    = expected_progress
    + exploration_bonus
    + relevance_of_new_results
    - stagnation_penalty
```

Suspended problems can later be revisited when another problem produces
a useful theorem, technique, counterexample, or conceptual insight.

## Prove and Disprove in Parallel

For sufficiently sharp mathematical conjectures, the system should
explicitly maintain competing branches:

``` text
Branch A: attempt to prove P
Branch B: attempt to find a counterexample to P
```

The counterexample branch can use numerical experiments,
finite-dimensional searches, symbolic computation, and Lean-certified
witnesses where appropriate.

This prevents the system from wasting large amounts of compute trying to
prove a false statement.

## Problem Selection

Initially prioritize problems with:

-   a precise mathematical statement;
-   a clear success criterion;
-   finite-dimensional or otherwise manageable formulations;
-   substantial existing mathematical background;
-   relatively little dependence on ambiguous physical interpretation;
-   a realistic path toward partial or complete Lean formalization;
-   closure by either proof or explicit counterexample.

Before allocating significant compute, a literature agent should verify
that the problem is still open.

## Guiding Principle

The system should be viewed as **distributed mathematical search with
persistent verified knowledge**, rather than several LLMs talking to one
another.

Individual agents are disposable. Their useful discoveries are not.

The long-lived object is the evolving research state:

``` text
problem
  -> attempts
  -> criticism
  -> verified lemmas
  -> reductions
  -> failed directions
  -> new conjectures
  -> reusable knowledge
```

The ultimate goal is an autonomous research loop in which compute
migrates toward problems and approaches that continue to generate
genuine mathematical information, while Lean and other verification
tools prevent plausible-sounding but incorrect reasoning from
accumulating as accepted knowledge.
