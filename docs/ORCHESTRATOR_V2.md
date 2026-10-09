# Lean Orchestrator 2.0

`lean-orch-v2` manages bounded research campaigns with explicit allocations,
persistent mathematical artifacts, and independent assessment. It has a separate
campaign model, engine, configuration, and CLI. The original
`lean-orch` remains available with the targeted improvements described below.

The central decision is whether the next bounded task merits its slot. The
engine records correctness, alignment with the original question, target-relative
progress, execution status, and checking status separately. A restricted result
can be retained without closing its original problem.

## Workflow

| Mechanism | Implementation |
| --- | --- |
| Human priorities, budget, and evidence standards | `harness.toml`; human approval of focus renewals; explicit candidate review and external-confirmation fields. |
| Focus, exploration, and additional review | Per-round roles with configurable slots and token ceilings; staged dispatch with up to three concurrent attempts by default. |
| Justify the next allocation | An allocation names the saved baseline and checking status, original gap, method, success evidence, why now, alternative, reversal condition, return condition, and budget including review. |
| Explore, continue, redirect, audit, repair, park, close target | Explicit actions with evidence requirements; focus/continuation requires checked prior work; closing a restricted target preserves the original problem. |
| Two effective rounds without accepted material progress | Per-original-problem round integration; multiple arms count once. Incomplete execution, broken verification, missing submission, and inconclusive review do not increase stagnation. Exact repeated claims do not reset it. |
| One documented revisit | Human authorization names a new method and reason. One effective revisit is allowed; an execution failure preserves the credit. |
| Fixed problem packets and prior-work snapshots | Prepared source files are cached and hashed. Each attempt snapshots its original statement, scope contract, allocation, and inherited candidates with checking status. Concurrent attempts do not receive each other's new work. |
| `(packet, STATE, latest feedback)` | Ordinary requests carry the fixed packet, a capped navigation note, and capped latest feedback. Full request/reply logs are saved outside the solver work directory and are not replayed. Complete mathematics lives in files. |
| Reserved submission-only synthesis | Research admission protects submission and review reserves. A complete inspectable `draft.json` can survive a transport failure and be submitted without another research phase. |
| Freeze, reproduce twice, independently review | Candidate artifacts are copied and sealed before assessment. Numerical scripts run in two fresh directories/processes. Review receives the original question, source excerpts, frozen claim, proof, baseline, and reproduction report without the solver conversation. |
| Scope correction and retained checking status | Contracts explicitly list quantifiers, dimensions, permitted resources, conventions, locators, and unresolved interpretation choices. Retracted premises invalidate dependent candidates and trigger audit. |
| Returned usage and missing-usage reserves | Every model call has a durable admission and settlement record. Known input plus output tokens, unknown-usage planning charges, and in-flight reserves remain distinct. Allocation leases protect planned later phases. |

V2 uses explicit qualitative allocation proposals. Its defaults are two focus
slots, five explorations, three concurrent attempts, and two stagnating rounds.
These defaults, token ceilings, and field limits are configurable starting
points; they have not been established as optimal.

The execution engine is implemented in Python. It reuses v1's general JSON,
serialization, process, usage, and Lean-checking utilities while keeping its
research state and allocation rules independent.

## Run the offline demonstration

After the normal project installation (`uv sync`), run:

```sh
lean-orch-v2 demo /tmp/lean-orch-v2-demo
lean-orch-v2 -w /tmp/lean-orch-v2-demo status
lean-orch-v2 -w /tmp/lean-orch-v2-demo usage
```

The demo uses deterministic local backends, an elementary parity proof, and two
real fresh Python verifier processes. It makes no model requests and reports
zero model tokens. `demo-result.json` identifies it as an offline demonstration.
The candidate remains `awaiting_human`; inspect `REPORT.md` and the frozen
candidate before accepting it. Choose a fresh directory for each demo.

`python -m lean_orchestrator_v2` also exposes the CLI.

## Start a research campaign

```sh
lean-orch-v2 init ~/lean-orch-v2-session
# Edit ~/lean-orch-v2-session/harness.toml: budget, model commands, calculation image, imports.
lean-orch-v2 -w ~/lean-orch-v2-session add --packet examples/v2/parity-packet.json
lean-orch-v2 -w ~/lean-orch-v2-session plan
lean-orch-v2 -w ~/lean-orch-v2-session run --rounds 1 --no-plan
```

`plan` asks the coordinator for concrete allocations and persists them for
inspection. `run` executes saved proposals; without `--no-plan`, it can
ask for another plan between rounds. It stops when there is no admissible work,
the round limit is reached, or a stop is requested. Unused allowance does not
require another attempt. Plans that require focus renewal approval remain pending.

For supplied allocation notes, use
`plan --allocations examples/v2/parity-allocations.json`. The file contains a
JSON list. New proposals and saved proposals are both checked against the
current policy and candidate status before dispatch.

### Prepare sources before solving

A packet is a JSON object with `id`, `title`, `original_statement`, a
`scope_contract`, and local `sources`. See the example packet. The original
question always accompanies narrower targets.

Each source has a `path` relative to the packet file's directory, a precise
`locator`, and preferably an `excerpt` containing the relevant definitions or
result. Text files can supply their own excerpt. PDFs require an explicit
prepared excerpt; PDF extraction and source retrieval belong outside the
network-isolated solver. Sources are copied into the campaign and hashed when
added. Source files at their original paths can subsequently change without
changing the saved packet.

`scope_contract` explicitly lists `quantifiers`, `dimensions`,
`allowed_resources`, `conventions`, `source_locators`, and `unresolved` choices.
Original-scope work is held back while interpretation gaps remain. Restricted
work must name its assumptions and the gap to the original question. Optional
`checked_prior_work` supplies a human-prepared baseline for an initial focus
allocation; otherwise promotion requires retained candidate IDs.

### Provision calculations

The default calculation backend is Docker. Every calculation uses a fresh
process with `--network none`, and numerical verification uses two independent
copies of the frozen artifacts. Images are inspected before research starts and
`--pull never` prevents runtime image downloads. Provision the image first,
then set `docker_image` to the tested image, preferably by digest.

The generated default uses a minimal Python image. Scientific campaigns should
use an image with their required libraries. Set `preflight_imports`, for example
`["numpy", "scipy", "sympy"]`, to check availability before spending model
tokens. Provision and test the required package versions in your image;
scientific dependencies are not installed automatically.

`sandbox = "local"` is available for trusted development and the offline demo.
It runs fresh processes but does not provide Docker's network/filesystem
isolation. API requests and source preparation occur outside the calculation
sandbox.

### Choose the context-control mode

The generated configuration uses the existing Codex and Claude CLIs, preserving
the requested integration. Each harness turn starts a fresh invocation and does
not resume a previous agent conversation. Codex's structured JSONL output makes
returned usage available; Claude's JSON result supplies its result and usage.
The flags are supported by the installed CLIs and documented in
[OpenAI's non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode)
and [Claude's programmatic usage](https://code.claude.com/docs/en/headless).

**CLI mode controls context per invocation.** A CLI may run its own internal
tool/conversation loop during that invocation. The harness cannot enforce the
configured context bound on those hidden internal model requests. The host tool
protocol and bounded note still apply between invocations.

**The Responses transport controls context per model request.** To use the
strict one-request-per-turn structure, configure a backend as:

```toml
[solver]
kind = "responses"
model = "gpt-6-astra"
reasoning_effort = "max"
timeout_seconds = 300
```

This requires `OPENAI_API_KEY`. It sends one stateless request, does not set a
previous response ID, and executes the returned host action separately. The
coordinator and reviewer can use independently configured backends. The CLI
models' own authentication remains available in compatibility mode.

With `tokenizer = "o200k_base"`, install the optional extra
`pip install 'lean-orchestrator[v2]'`. This enables explicit token counting.
The dependency-free default, `utf8`, uses a stricter byte cap: at most 600 bytes
of state and 800 bytes of feedback. It conservatively satisfies those token
upper bounds while making no claim to count o200k tokens.

### Lean checking

V2 can also assess a `lean` reference attached to a submitted claim. Set
`lean_template` to a prepared OpenQ/Mathlib Lake project. The verifier creates a
fresh check project from that trusted template and overlays only submitted
`OpenQ/**/*.lean` modules. Submitted build configuration cannot replace the
template's configuration. It calls the existing axiom-auditing Lean verifier
with kernel rechecking. Missing Lean tools are execution failures.

For example, a claim's reference can contain:

```json
{"file": "OpenQ/Proof.lean", "declaration": "OpenQ.result", "target": "OpenQ.question", "negated": false}
```

Corresponding submitted modules live under `work/lean/OpenQ/`. Source/statement
faithfulness remains a separate review obligation. The label
`lean_checked_declaration` concerns the checked declaration; original-question
resolution still requires the scope assessment and human review.

## Human decisions and recovery

```sh
lean-orch-v2 -w ~/lean-orch-v2-session approve ALLOCATION --reason "The saved proof exposes a removable assumption."
lean-orch-v2 -w ~/lean-orch-v2-session revisit PROBLEM --method "new representation" --reason "A source resolves the obstruction."
lean-orch-v2 -w ~/lean-orch-v2-session review CANDIDATE --accept --reason "Checked the original domain and proof."
lean-orch-v2 -w ~/lean-orch-v2-session review CANDIDATE --reject --reason "The permitted reference system was omitted."
lean-orch-v2 -w ~/lean-orch-v2-session stop
lean-orch-v2 -w ~/lean-orch-v2-session resume
```

Acceptance is a recorded human decision. Expert confirmation is recorded only
when explicitly supplied with `--external-confirmation`; accepting a candidate
does not automatically create it. Restricted candidates never close the original
question. A rejection retracts the candidate and marks its transitive dependents
disputed pending audit.

On restart, running attempts become interrupted records. Already saved terminal
work is integrated once, incomplete rounds do not add stagnation, and lost
calls retain their unknown-usage planning charges. The coordinator can propose
`repair` using the failed attempt ID. Repair requires a complete saved draft and
performs submission/assessment without another research phase. The original
failure and usage stay in the archive.

## Email notifications

Notifications are optional and disabled when the recipient is empty. New
campaigns include this section in `harness.toml`; existing campaigns can add it:

```toml
[notify]
email = "you@example.org"
events = ["resolution_claimed", "approval_required", "run_stopped", "run_failed"]
```

| Event | When it is sent |
| --- | --- |
| `resolution_claimed` | An internally retained original-scope result moves its problem to `awaiting_human`. Restricted and inconclusive results do not trigger it. Multiple arms resolving one problem send one alert. |
| `approval_required` | A focus renewal is held for human approval, either during planning or when a saved plan is checked before dispatch. |
| `run_stopped` | A `run` invocation ends, including its round limit, a stop request, no admissible work, or an interruption. The message includes problem status, usage, and the report location. |
| `run_failed` | A `run` invocation aborts with an error. |

Messages use the subject prefix `lean-orch-v2:`. Review and approval alerts name
the saved candidate or allocation and include the corresponding CLI command.
Research state is saved before these alerts are sent. Restart recovery does not
resend an already integrated resolution. Delivery errors are printed and
recorded in `notifications.jsonl` without failing the research run.

To override the recipient for one run without editing the saved configuration:

```sh
lean-orch-v2 -w ~/lean-orch-v2-session run --rounds 3 --email you@example.org
```

Configured event filters still apply. Use `--email ""` to disable delivery for
that invocation. Standalone `plan` commands use the recipient in `harness.toml`.

Delivery uses the same transport as v1: an SMTP relay when
`LEAN_ORCH_SMTP_HOST` is set, otherwise a local `sendmail`, `mail`, or `mailx`
command. For an SMTP relay, set the environment before launching:

```sh
export LEAN_ORCH_SMTP_HOST="smtp.example.org"
export LEAN_ORCH_SMTP_PORT="587"
export LEAN_ORCH_SMTP_SECURITY="starttls"
export LEAN_ORCH_EMAIL_FROM="research-bot@example.org"
```

If authentication is required, also set `LEAN_ORCH_SMTP_USERNAME` and
`LEAN_ORCH_SMTP_PASSWORD` together. Credentials stay in the environment.
Supported security modes are `starttls`, `ssl`, and `none`; authentication
requires TLS. An existing v1 transport setup works with v2. There is no need to
change the calculation sandbox for email delivery.

## Record layout and accounting

| Path | Content |
| --- | --- |
| `harness.toml` | Human policy and backend configuration. |
| `campaign.json` | Original problems, allocation decisions, attempts, candidates, checking and human status. |
| `REPORT.md` | Overview with frozen candidate links and concrete allocations. |
| `packets/` | Prepared original packets, cached sources, hashes. |
| `attempts/ID/fixed-packet.json` | The exact task and prior-work snapshot given to an attempt. |
| `attempts/ID/work/` | Complete mathematics and working programs; fixed inherited library. |
| `attempts/ID/feedback-*.json` | Full tool feedback, beyond the bounded note sent to the next request. |
| `candidates/ID/` | Frozen claim, packet, artifact copies, and controller-held manifest. |
| `reviews/ID/` | Separate review workspace with frozen artifacts. |
| `calls/ID/` | Requests, replies, stdout/stderr, usage and failure metadata. |
| `usage.jsonl` | Durable call admission and settlement journal. |
| `notifications.jsonl` | Email delivery outcomes and errors. |

The campaign token limit is an admission limit over known spend plus unknown
charges and active allocation leases. Returned usage can overshoot a reserve;
it is recorded honestly and restricts later admission. It is not a provider-side
hard cap. Reserves are never presented as measured consumption. Codex cached
input and reasoning output are components of their corresponding totals;
Claude cache read/write input is normalized into total input.

Models' coordinator, solver, submission, and review calls are included. Local
calculation time and human attention are not token usage. V2 never commits or
publishes research artifacts.

## Changes to the original engine

The v1 engine now separates completed, conclusive mathematical attempts from
execution/verification failures, excludes incomplete assessments from its
progress average, requires a material advance on a named gap for meaningful
progress, treats lexical repetition as a warning, recovers complete saved
researcher reports after process failures, records scope/checkpoint/return
metadata, and journals returned usage for all roles and retries. Suspended
problems require a new relevant result or explicit human reactivation by default.

Existing workspaces can add `[budget] max_tokens` and `call_reserve_tokens` to
`lean-orch.toml`, run `lean-orch refresh-prompts` to install the updated contracts
with backups, and add Codex `--json` or Claude `--output-format json` to existing
agent commands. Schemas regenerate on preparation. Old persistent state remains
readable through additive defaults. The two engines use separate workspaces;
V2 does not silently migrate v1's accumulated research judgments.

## Validation

The tests exercise round aggregation, repeated accepted claims, missing usage,
protected final phases, complete-draft recovery, submission-only repair, blinded
review inputs, frozen source/claim integrity, human acceptance, focus renewal,
single effective revisits, and transitive retraction. Numerical tests use actual
fresh local Python processes; backend responses are deterministic or mocked.
The offline demo exercises planning through candidate retention.

These checks validate the software workflow. They do not establish the quality
or efficiency of a live mathematical research campaign. A controlled evaluation
should fix packets and budgets and measure retained advances, caught errors,
returned spend, and human effort.
