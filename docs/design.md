# agent-evals: major components

Status: **Target architecture; offline contracts/planner/record storage/reporting
implemented in v0.1.0; Docker/network controls and no-inference Codex CLI
conformance verified. Authenticated runtime/native adapters remain unimplemented.**
Audience: project owner, implementers, task authors, and evaluation reviewers.

Accepted campaign methodology is owned by [evaluation-protocol.md](evaluation-protocol.md).
Implemented behavior and limitations are owned by [offline-core.md](offline-core.md).
The general execution flow below remains a target. The independently scoped
[runtime controls](runtime-controls.md) exercise only its fixed deterministic boundary.

## 1. Purpose and scope

Answer: does this exact agent composition accomplish these tasks, under this
environment and budget, and what evidence explains the result?

The subject is the composition of harness version, primary model, delegation
policy, child models, prompts, skills, plugins, tools, and permissions. The
evaluator is a separate trusted system.

Initial scope: local Docker execution through Harbor, small coding/terminal
tasks, explicit subject profiles, repeated independent attempts, deterministic
outcome grading, and local reports. Later task families reuse the same trial
boundary when their environment and grader requirements fit.

Non-goals: an agent framework, a universal delegation implementation, a new
scheduler, an initial dashboard/service, or simultaneous integration of multiple
evaluation frameworks. A declarative configuration does not imply deterministic
model behavior or identical semantics across harnesses.

## 2. Proposed structure

Scope: one evaluation campaign; concern: responsibility and trust boundaries.

```text
Operator
  |
  v
Experiment + subject profiles + task suite
  |
  v
Planner / capability validation --> immutable campaign lock + trial plans
  |
  v
Harbor worker (trusted controller; subject credentials resolved at execution)
  |                         |
  | harness adapter         | task/environment definition
  v                         v
Isolated subject environment: actual harness + models + tools (untrusted)
  |
  | bounded artifact export; subject execution stops before grading
  v
Separate verifier (trusted hidden grader, fresh environment)
  |
  v
Evidence collector --> immutable trial records + private artifacts
  |
  v
Reporter --> task-level comparisons + campaign summary
```

Subject execution has no access to the controller, artifact-store authority,
reference solutions, or hidden grading inputs. Docker is the initial execution
boundary, not a claim of microVM-level isolation against hostile workloads.

## 3. Major pieces

### A. Subject profiles and harness adapters

A subject profile describes one candidate composition independently of the task
suite. A stable display ID identifies the profile for humans; a resolved digest
identifies its actual locked inputs.

The profile includes:

- Harness identity and exact version, primary provider/model and settings.
- Delegation policy: disabled, native defaults, or explicitly configured roles.
- Role-specific models, instructions, permissions, and limits where supported.
- Content-bound instructions, skills, plugin definitions, and MCP/tool settings.
- Harness-native configuration and references to credentials, never secret values.

A harness adapter translates this intent into native configuration/files/options
accepted by the pinned Harbor agent adapter. Most integrations reuse Harbor's
built-ins; a trusted custom Harbor agent class is an escape hatch only when needed.
Custom imports execute in the worker process and are allowlisted evaluator code,
not arbitrary import paths supplied by an untrusted profile.

Adapters declare capabilities scoped to exact tested harness/adapter versions.
They reject unsupported role/model/permission requirements before execution.
Shared fields and native settings cannot silently disagree: conflicts are
rejected or resolved by an explicitly documented adapter rule captured in the lock.
Native settings cannot override evaluator-owned budgets or isolation policy.

Three evidence levels remain distinct:

| Level | Meaning |
| --- | --- |
| Requested | Intent recorded in the profile. |
| Effective | Resolved native settings applied to the runtime, with conformance evidence. |
| Observed | Actual invocations/delegation/model usage supported by trace or request evidence. |

Writing a config file proves deployment, not that the harness honored every field.
An explicitly configured role is available for delegation; it is not guaranteed
to run. A disabled-delegation claim requires adapter conformance evidence, not
the absence of recorded child events. Native defaults are labeled uncontrolled.
Missing evidence is unavailable, not zero. Trace fidelity requirements belong to
the experiment; insufficient evidence invalidates the affected inference, not
automatically an otherwise verifiable task outcome.

### B. Task suites and graders

Use Harbor-native task packages rather than inventing a second executable task
format. A suite selects task revisions/subsets and records their checksums,
fixture/image identities, and grading revisions.

Agent-visible instructions and public tests are distinct from evaluator-only
tests, reference solutions, and trusted state. The initial controlled-task
profile uses separate no-network verifiers and exports only declared outputs.
Grader dependencies are prebuilt; grading does not install packages on the fly.

Imported benchmarks preserve their documented grading semantics. If a task
requires shared-environment grading or a specialized topology, it is either
rejected by the initial profile or evaluated under an explicit, separately
reviewed integrity profile. It is never silently relabeled as hidden-safe.

Agent-generated artifacts remain untrusted in the verifier. Artifact transfer
enforces path confinement, symlink policy, size limits, and an allowlist. Verifiers
run without host credentials or sockets. Executing submitted code happens only
inside that grading boundary, never in the reporting process.

Separate verification protects grading inputs during the solving phase; it does
not itself protect tests/results from submitted code executed during grading.
The initial controls therefore inspect output artifacts without executing submitted
code. Code-execution graders require a reviewed task integrity profile: submitted
code runs in a disposable execution compartment, cannot write grader-owned expected
results or authoritative records, and returns observations the trusted grader checks.
Inputs necessarily delivered to the program are not claimed to remain hidden from
it. Benchmark-native test runners without these properties carry a weaker integrity
claim; they do not inherit the controlled-task assurance label.

### C. Experiment planner and immutable identities

An experiment selects subject profiles, a locked suite, attempts, evaluator
budgets, execution policy, and requested evidence. Start with explicit subject
lists instead of an unrestricted Cartesian parameter language.

The planner expands subjects x tasks x attempts, validates compatibility, and
emits a campaign lock plus individual trial plans. Planning is offline and has
no model, container, installer, or remote-resource side effects. Nonlocal inputs
must already be materialized and digest-bound before planning.

The lock records Harbor/adapter versions, harness versions, model identifiers,
resolved native settings, task/grader/fixture/image digests, environment policy,
budgets, evidence requirements, and the scoring policy. Secrets are referenced
by logical binding names; values and secret-value hashes are excluded.

Identical resolved inputs produce identical plan digests. Each actual execution
receives a distinct trial ID. Model aliases without immutable provider snapshots
are marked mutable; locking a label does not freeze the provider's weights.
Seeds are recorded when supported, not treated as a promise of determinism.

The offline v1 JSON contracts are owned by [typed models](../src/agent_evals/models.py)
and [generated schemas](../schemas/). Resolution/canonicalization and current
limitations are described in [offline-core.md](offline-core.md). No YAML contract
or verified native configuration translation is implemented.

### D. Harbor worker and trial lifecycle

Harbor owns environment provisioning, agent setup/run, artifact transfer, and
verification plumbing. The worker translates a locked trial plan into Harbor
configuration and refuses drift before launch. It runs with a clean agent home
and an explicit environment allowlist; no developer profile is inherited.

Use an isolated, pinned worker dependency environment. The inspected Harbor
commit below is installed and control-tested in `worker/`; these checks do not
establish a general authenticated harness worker. Evidence and limits are in
[runtime-controls.md](runtime-controls.md).
Generate separate Harbor jobs per subject profile because Harbor aggregation
does not distinguish all profile settings.

Initial lifecycle:

1. Validate the lock and materialized input digests; allocate a new trial ID.
2. Prepare a fresh subject environment; record setup/runtime identities.
3. Apply native configuration and collect effective-setting evidence.
4. Execute the actual harness within evaluator-owned wall-clock/resource limits.
5. Collect partial logs on failure, stop subject processes, export bounded outputs.
6. Grade in a fresh trusted verifier; preserve cleanup status.
7. Finalize a record referencing immutable evidence; never overwrite an attempt.

Provider access is a separately approved agent-phase policy. Build/setup access,
agent/provider access, and verifier access are independent policies. Secrets are
execution-time bindings with least privilege; model-proxy isolation is a later
option, not a current credential-protection claim.

#### Trial state and external-service isolation

Accepted requirement (2026-10-10); no-network deterministic enforcement is verified
in [runtime controls](runtime-controls.md), not in real harnesses or remote-memory
services. Every harness
runs inside the approved disposable subject sandbox/container, never directly in
the developer's host workspace. Fresh filesystem state alone is insufficient:
remote memory, MCP servers, session stores and background services can retain
answers across otherwise fresh containers.

Independent trials use cold-start state by default: fresh task workspace, agent
home, sessions and writable caches, with no shared writable volumes or inherited
host configuration, memory credentials or service connections. All primary agents,
subagents, plugins and spawned processes share this authority boundary.

Memory/service policy is explicit and bound into the plan:

- Disabled: no memory endpoint or credential; external access is blocked by runtime
  controls, not merely by removing a tool from the prompt.
- Trial-private: a disposable service/store initialized empty or from a locked,
  audited seed snapshot. The snapshot is copied per trial, never mutated in place.
  Service version, initial-state digest, access scope and lifecycle are recorded.
- Stateful sequence: a separately declared experiment with explicit state transitions
  and exposure history; it is not reported as independent cold-start performance.

An OpenCode candidate that requires Hindsight uses trial-private Hindsight rather
than the developer's shared memory. Prefer a disposable service per trial. A bank
name alone is not an isolation boundary: namespace isolation requires enforced
authorization and evidence that the subject cannot enumerate/read/write another
bank or bypass the scoped route. Otherwise that topology is rejected. Never clear
or reset the developer's shared service to prepare an evaluation.

Required memory integration cannot be silently disabled: use a conforming isolated
binding or reject the profile. Candidate memory seeds are audited for task-answer
leakage and labeled in reports; unknown provenance blocks clean-start assurance.
No hidden grading material is inserted into subject memory.

Agent-phase egress is deny-by-default with explicit provider routes and, where
required, approved trial-private service routes. Those routes do not authorize
host/LAN/shared-memory access. Effective enforcement, including alternate routes
and subprocess access, is a readiness requirement. Containers do not establish
this network boundary automatically. Verifiers remain separately isolated.

After execution, stop all trial writers/background services before final state
capture; preserve approved evidence and revoke the trial's access. Disposable-state
cleanup follows the approved lifecycle/retention policy and cannot affect another
trial or shared service. Missing isolation evidence invalidates cold-start assurance;
confirmed cross-trial answer access marks the affected attempt contaminated.

Wall-clock/process termination and environment resource limits are initial hard
controls. Token/USD limits are declared or observed unless a tested enforcement
mechanism exists; they are not advertised as hard caps. Setup, agent execution,
grading, and total wall-clock durations are recorded separately.

Harbor automatic retries are disabled initially. A deliberate rerun receives a
new ID linked to the prior attempt; artifacts are preserved before any backend
resume/retry operation. Cancellation/cleanup failures remain visible and cannot
silently become task success. Native in-harness/provider retries are recorded
when observable; disabling Harbor retries does not disable those mechanisms.

Each locked repeat slot has one original execution. Deliberate reruns are diagnostic
attempts outside the primary campaign score; they never replace an original result
or enlarge its denominator. A scored recovery policy needs a separately locked
campaign contract. Unstarted/cancelled slots remain visible in the scheduled count.

### E. Evidence and result records

Retain native logs plus ATIF when available. Child trajectories, model identity,
token usage, and billing evidence declare their source and coverage; a normalized
trajectory alone is not proof that every child was captured.

Evidence also declares authority: subject-reported, controller-observed, or
provider-reported. A digest authenticates stored bytes, not the truth of subject
claims. Native transcripts support diagnostics, not authoritative proof of a child
model or actual billing. Stronger claims require attributed controller/proxy/provider
observations with documented coverage; when unavailable, reports label the claim
subject-reported or unknown. Provider usage is distinguished from provider invoices.

A trial record binds its plan, task, subject, attempt, input/output artifacts,
timings, grading dimensions, configuration evidence, and cleanup state. It keeps
three independent axes:

- Execution: completed, timeout, budget exhausted, subject error, setup error,
  infrastructure error, or cancelled.
- Validity: valid, invalid, or contaminated, with reasons.
- Outcome: passed, failed, partial, or ungraded, with grader evidence.

A timeout can still produce gradeable artifacts. Infrastructure errors are not
silently reported as valid agent failures. Outcome validity and optional trace
coverage are recorded separately; a missing mandatory outcome-integrity check
invalidates the grade.

Raw artifacts live in ignored local storage initially; records reference them
by content identity and controlled location. Logs/config evidence are sanitized
for reporting, but secret-free raw logs are not assumed. Publication exports
only explicitly reviewed data. Hashes alone do not confer trust or privacy.

Artifact roots use owner-only access (directories `0700`, files `0600`) and are
excluded from unapproved synchronization/backups. Before credentialed runs, the
operator selects a retention policy, access scope, and deletion procedure covering
raw artifacts and any authorized backups. No automatic retention deletion is
implemented or authorized by this draft. Git exclusion alone is not a privacy control.

### F. Comparison and reporting

Aggregate by subject digest, task revision, grading policy, and compatible
environment/budget strata—not just harness and model names. Do not combine
unmatched task subsets into a ranking.

Report scheduled trial counts, execution/validity distributions, pass rate over
all scheduled attempts, and pass rate over valid graded attempts with explicit
denominators. Missing/invalid attempts remain visible; partial scores are not
binary passes. Mandatory grading-integrity failures cannot be overridden by
an LLM judge or an attractive terminal reward.

Both pass rates use the same numerator: original planned attempts with valid,
authoritatively passed outcomes and successful cleanup. Invalid/contaminated,
ungraded, or cleanup-failed attempts never count as passes. Cleanup failures
invalidate score eligibility even if the grader returns a passing reward. The
valid-graded denominator contains original attempts eligible for authoritative
grading, including valid failures/partial outcomes; diagnostic reruns are excluded.
An empty valid-graded denominator produces an unavailable rate, not zero success.

Compare subjects on matched tasks/repeat slots, using task-level paired summaries
and uncertainty when sample size permits. Repeats are independent trials, not
an opportunity to select only successful attempts.

Report latency, tokens, costs, and delegation statistics with coverage. Distinguish
reported billing, price-based estimates, and unavailable cost. Avoid double
counting children included in parent totals. Subscription usage does not imply
zero cost. Diagnostic metrics do not substitute for authoritative task outcomes.

#### Efficiency dimensions

| Dimension | Measurements and interpretation |
| --- | --- |
| Token usage | Input, output, cache-read/cache-write, and reasoning usage when available; parent/child breakdown and aggregate coverage. Provider-specific categories declare overlap and are not blindly summed. Different tokenizers do not imply equal work per token. |
| Monetary cost | Subject execution cost, with reported/estimated/unavailable attribution; estimates bind a pricing revision and usage categories. Setup/infrastructure and grader cost are separate overhead dimensions. |
| Latency | Controller-observed agent wall time plus setup, grading, and total trial time. Per-request latency and time-to-first-token are optional and require suitable instrumentation. Parallel child durations are not summed into task wall time. |
| Turns and actions | Count completed logical model generations as normalized turns, with parent/child breakdown; model requests/retries, tool calls, and delegation calls are separate counts. Native harness turns/ATIF steps retain their own definitions and are not assumed equivalent. Interrupted generations are separate partial events; streaming chunks are not turns. |

Every measurement declares units, definition, authority, and coverage. A harness
without sufficiently complete generation evidence reports normalized turns as
unavailable rather than guessing from tool calls or final messages.

Report efficiency over all original attempts with usable measurements and,
separately, over successful attempts. Both views show their denominators and
missing-data counts; timed-out durations are labeled censored, not successful
completion latency. Latency summaries include median and tail percentiles only
when the sample size supports them. Output tokens divided by whole-task wall time
are task throughput, not model decoding speed.

For a campaign with complete subject-cost evidence, cost per successful task is
total subject cost across original attempts (including failures) divided by the
number of eligible passes. Zero passes or incomplete cost coverage makes this
metric unavailable; known spend and missing coverage remain visible separately.

Correctness and efficiency remain separate score dimensions. An experiment can
declare explicit cost/latency/turn gates or an efficiency scoring rule before
execution. Reports distinguish task success from meeting those gates; a cheap,
fast failure cannot become a successful task. Missing mandatory efficiency
evidence produces an unassessable efficiency grade, not a passing gate. No default
weighted combination hides correctness/cost/latency tradeoffs.

Within-harness ablations support claims about delegation/config changes when
other inputs are matched. Cross-harness results compare complete systems; they
do not isolate model quality or subagent benefits by themselves.

## 4. Dependency decision and extension boundary

Proposed decision: Harbor is the only initial execution framework. Adopt its
tasks/agent adapters instead of rebuilding isolation and scheduling. The tradeoff
is dependency/API drift and uneven child-session observability; pin versions and
validate with deterministic controls plus adapter conformance tests.

Promptfoo and Inspect remain alternatives for demonstrated gaps, not immediate
dependencies. Add a second backend only after a concrete requirement cannot be
met reasonably through Harbor. Common locks/results preserve identity and trust
requirements; they do not promise identical feature semantics across backends.

Task families are added incrementally: coding/terminal first, then data/research,
then interactive/browser/computer-use where their topology and grading fit.
Peer-agent cooperation is distinct from parent/child delegation; adding a
CooperBench-like topology does not establish native subagent support.

## 5. Observable design requirements

Scored campaigns also meet the task-audit, human-review, reliability, contamination
and predeclared-comparison requirements in [evaluation-protocol.md](evaluation-protocol.md).

| Requirement | Acceptance evidence |
| --- | --- |
| Reproducible configuration | Same materialized inputs produce the same lock; version/content drift is rejected. |
| Honest capability handling | Unsupported role/model constraints fail before execution; absence of evidence is unavailable. |
| Independent grading | Hidden graders/reference solutions are absent from subject; oracle passes and no-op fails. |
| Independent attempts | Fresh state per attempt; retries preserve prior evidence and receive distinct IDs. |
| Recoverable failures | Crash/timeout/cancellation leaves a readable partial record and cleanup status. |
| Correct comparisons | Same-harness/model profiles remain distinct; fixture reports prove denominator and missing-cost handling. |
| Comparable efficiency | Fixtures prove token-category overlap handling, parent/child deduplication, parallel wall time, logical-turn definitions, and separate correctness/efficiency gates. |

## 6. Evidence baseline and remaining decisions

Source inspection: `harbor-framework/harbor` at
`09f5b964f8e5075f7e52fd08c9994dfc80ad5d24` (2026-10-10 source inspection).
Subsequent bounded runtime evidence is separate in [runtime-controls.md](runtime-controls.md).

- [Agent configuration](https://github.com/harbor-framework/harbor/blob/09f5b964f8e5075f7e52fd08c9994dfc80ad5d24/src/harbor/models/trial/config.py#L63-L150): primary model, kwargs, environment and skills.
- [Custom agent imports](https://github.com/harbor-framework/harbor/blob/09f5b964f8e5075f7e52fd08c9994dfc80ad5d24/src/harbor/agents/factory.py#L175-L196): extension code runs in the controller process.
- [Aggregation identity](https://github.com/harbor-framework/harbor/blob/09f5b964f8e5075f7e52fd08c9994dfc80ad5d24/src/harbor/models/job/result.py#L59-L66): configuration kwargs are absent from the grouping key.
- [Verifier mode](https://github.com/harbor-framework/harbor/blob/09f5b964f8e5075f7e52fd08c9994dfc80ad5d24/src/harbor/models/task/verifier_mode.py#L28-L30): default is shared.
- [ATIF child trajectories](https://github.com/harbor-framework/harbor/blob/09f5b964f8e5075f7e52fd08c9994dfc80ad5d24/src/harbor/models/trajectories/trajectory.py#L87-L101): format support is not adapter coverage.

The owner-selected harness/model matrix and compatibility gates live in
[initial-campaign.md](initial-campaign.md); selection is not runtime validation.
Pending owner decisions: accept this component boundary; pin harness versions,
native settings and OpenCode agent definitions; choose task subset, credentials
mode and spend authorization; select repository visibility/license before publishing.
Runtime validation determines the actual supported capability set, not this design's
intended inventory.
