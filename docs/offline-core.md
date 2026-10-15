# Offline framework core

Status: **Implemented v0.1.0; planning and caller-supplied evidence only.**
Offline commands execute no harness, Docker invocation, candidate import or
host-profile loading. `run` fails closed and scored planning is rejected. The
separately approved [deterministic controls](runtime-controls.md) use a live-verified
no-network Docker profile. Provider/real-harness/Hindsight execution is still blocked.

## Contract owners

- [Typed models](../src/agent_evals/models.py) own authoring and persisted shapes.
- [Generated schemas](../schemas/) expose the core v1 contracts: campaign, lock,
  trial plan and trial record, plus the separate control-request/control-batch
  contracts. Unknown fields and wrong shared types are rejected.
  Native Codex smoke request/preparation contracts are separately exposed from
  [codex_smoke.py](../src/agent_evals/codex_smoke.py); they do not enable execution.
  Schemas expose structural shapes; typed validators also enforce cross-field and
  path semantics that JSON Schema alone does not express.
- [Planner](../src/agent_evals/planner.py) owns canonicalization/resolution.
- [Evaluation protocol](evaluation-protocol.md) owns methodological requirements,
  including gates not yet enforceable in this offline core.

The campaign contains explicit subject compositions, local component bundles,
task-package/initial-workspace/grader references and evaluator policy. This is the
base interface for an external RSI controller; proposal, lineage, selection and
activation remain outside this package. A new candidate gets a new lock; existing
locks and original trial slots are not edited to accommodate evolution.

OpenCode requires a primary-agent definition and explicit instructions-component
reference. Configured delegation requires explicit subagents. Codex/Antigravity
explicit agent-definition translation is unsupported and rejected, rather than
emulated. Native v1 settings are limited to requested effort/reasoning-effort/speed
strings. They are **not rendered or proven effective**. Model settings are requested
JSON inputs, not a promise of provider support. Adapter conformance is still blocked.
The selected real profiles remain unresolved in [initial-campaign.md](initial-campaign.md).

## Local resolution and identity

All artifact paths are relative to one explicit input root (by default the campaign
file's directory). Absolute/parent/dot/control-character paths, symlink traversal,
special files, Git metadata and `.env` files are rejected. Each snapshot is limited
to 64 MiB, 10000 entries and 64 directory levels. Use selected artifact directories,
not a whole host home. Descriptor-based traversal rejects intermediate symlink
replacement rather than relying only on pathname checks.

Files bind SHA-256 bytes, size and executable status; directory entries are retained.
Snapshot identity is SHA-256 of its entry array. Optional expected digests are checked.
Subject/task identities bind requested settings and resolved artifact identities.
Lock identity binds the resolved campaign, snapshots, identities, planner version
and execution blockers. Canonical JSON sorts object keys, preserves array order,
uses compact UTF-8 serialization and rejects nonfinite values. Reordering arrays
can change identity. Digests are integrity references, not authentication.

`verify` re-resolves local inputs and rejects drift. Locks retain relative source
references, not copies of input bytes; retain the source artifacts privately if
reproduction is needed. Snapshotting is not workspace assembly or an immutable
filesystem service; concurrent mutation of author-owned inputs is not supported.
No arbitrary candidate code runs in the planner.

Memory is disabled or a requested trial-private service with a digest-pinned image
and optional seed plus audit artifact. No shared-service URL or developer memory
credential is accepted as a memory binding. Seed audit content is digest-bound,
not independently adjudicated. Stateful sequences are unsupported in v1.
Provider host declarations bind intent only: DNS/network isolation is not enforced
here. A syntactically valid host is not proof it resolves to a safe public endpoint.

Credential references are logical names, not values. Recognizable credential
fields/token patterns are rejected in settings; this is **not universal secret
detection**. Component contents remain private operator-reviewed inputs. Do not
print/export manifests or artifacts containing sensitive data without review.

## Records and reports

Trial plans expand task → repeat → subject, with disjoint reliability groups fixed
by k. Actual executions use new UUIDv4 IDs supplied by the caller. The record store
checks exact planned-slot binding, mandatory evidence references, one original per
slot, and diagnostic linkage to that original. Writes are serialized and exclusive;
private directories/files use `0700`/`0600`. Existing broad-permission output
directories are rejected, not silently changed. This is Linux local storage, not
a distributed artifact service or protection from the account owner editing files.

An imported `controller` authority label is a caller assertion. This package checks
structure/references, not the truth, existence or trusted production of that evidence.
Reports explicitly say **caller-supplied records; not execution attestation**. Real
scored reporting needs the future trusted worker and evidence store integration.

JSON reports separate subject digest and tuning/holdout strata, scheduled/eligible
denominators, failure/validity/cleanup distributions, missing trials and diagnostic
reruns. Eligible passes require valid controller grading and successful cleanup.
Timeouts can have valid failed/partial grades; their measured wall times are marked
as censored counts, not successful-completion speed. Successful-attempt metric
values are separate from all-attempt observed summaries.

Metric rollups preserve definition, unit, authority and pricing revision. Missing
and partial coverage are explicit. Token categories/parent-child usage are never
automatically summed or guessed. Supply an already deduplicated `subject_cost` total
including failures/retries/children; cost-per-success is unavailable unless all
scheduled originals have complete compatible cost totals and at least one pass.
Different definitions/sources are not silently combined into that efficiency grade
or a single known-spend total. Per-definition observed costs remain available.

Repeated-trial reporting is descriptive: fully assessable disjoint groups provide
`pass@k`/`pass^k`; all planned groups provide separately labeled operational views
with unknown/invalid slots treated as non-passes. No inferential intervals, paired
winner claims, automatic exclusions, latency tail percentiles or weighted ranking
are implemented. Markdown is a compact summary; JSON retains detailed coverage.

## Verified local walkthrough

Run from the repository root. Dependencies are pinned in `uv.lock`; the example is
synthetic and its model/image/version labels are not executable targets.

```sh
uv sync --offline --locked --python 3.12
uv run --offline --locked python -m unittest discover -s tests -v
uv run --offline --locked python scripts/generate_schemas.py
uv run --offline --locked agent-evals validate examples/offline/campaign.json
uv run --offline --locked agent-evals plan examples/offline/campaign.json --out .eval/offline.lock.json
uv run --offline --locked agent-evals verify .eval/offline.lock.json --root examples/offline
uv run --offline --locked agent-evals slots .eval/offline.lock.json
uv run --offline --locked agent-evals report .eval/offline.lock.json --store .eval/records --format markdown
uv run --offline --locked agent-evals schema campaign
```

Planning produces four original slots, two distinct subject identities and explicit
execution blockers. The empty-store report shows zero passes, four missing trials
and unavailable cost. Re-running `plan` with the same output path fails rather than
overwriting evidence. On a machine without cached dependencies, offline sync fails;
online installation is a separate operator choice.

`agent-evals run .eval/offline.lock.json` returns exit code 2 without starting anything.
`ingest LOCK RECORD --store DIRECTORY` accepts externally produced trial-record JSON;
unit tests exercise this path with synthetic records, not real evaluations.

## Next runtime gate

The pinned worker now verifies fixed no-network deterministic controls, including
oracle/no-op, timeout, fresh state, independent artifact grading and malicious
exports. General candidate/workspace assembly, provider egress, trial-private memory
services and real CLI adapters remain next gates. No-network control success does
not remove a real profile's blockers or authorize a model smoke test. The current
blockers cannot be removed merely by editing the lock.

## Offline Codex native-login smoke preparation

The owner selected native ChatGPT login, one 60-second Codex smoke and a $10
monitoring target (not a hard cap), with paid-credit fallback explicitly allowed.
The offline command below creates a private, exclusive preparation record; it
starts no installs, login, containers, network traffic or provider inference:

```sh
uv run --offline --locked agent-evals prepare-codex-smoke --allow-credit-fallback --out .eval/codex-smoke/preparation-v1.json
```

The record binds requested Codex `0.162.1`, npm linux-x64 package integrity,
`gpt-6.1-sol`, medium reasoning and Standard service tier, a fixed artifact task,
requested native configuration and argv, and explicit runtime blockers. Its status
is always `blocked-preparation-only`. Repeating the command refuses overwrite;
this artifact is neither an executable campaign lock nor credential storage.
Requested configuration is not installed/effective/observed evidence. Native
delegation remains unverified. No host auth file is accepted. Smoke preparation
does not resolve OpenCode profiles or claim a tested general Codex adapter.
Preparation artifacts stay on the evaluator side; only public task inputs and
native configuration are appropriate subject uploads, never the entire plan.

The [socket-proxy controls and CLI conformance](runtime-controls.md#socket-proxy-network-controls-and-codex-cli-conformance)
now pass; they do not prove authenticated native execution. This preparation's
historical blockers remain unchanged in its stored artifact. Current verified
status and outstanding login/runtime work are in [PLAN.md](../PLAN.md); no consumer
may treat an edited preparation or blocker list as permission to execute.
