# Delivery plan

Status: **Offline core, Docker/network controls, Codex CLI conformance and one
authenticated native Codex diagnostic smoke verified. General campaign execution remains blocked.**
Architecture owner: [docs/design.md](docs/design.md).

## Active increment: Codex native-login smoke preparation

Status: **Single authorized smoke completed and passed; authorization consumed.**
Change ID: `chg-codex-native-smoke-20261013`.
Next increment: select remaining runtime/campaign work; do not repeat the consumed smoke.

Closure (2026-10-14): native ChatGPT login and actual `codex exec` completed in
`ca3475cc3e5743db868f8f74feb3a48d`. Agent time: 7.037s within the 60s bound.
The independent no-network verifier observed numeric result 8 and graded passed.
Requested model: `gpt-6.1-sol`; observed model identity remains unavailable.
Native subject-reported usage: 22,770 input tokens, including 20,736 cached input,
159 output; coverage partial. Subscription monetary cost remains unavailable,
not zero and not a demonstrated $10 hard cap. Refresh/access token revocations
each received HTTP 200 (provider accepted, not independent invalidation proof).
Subject stopped before collection; cleanup succeeded, credential volumes removed,
and scoped container/volume absence verified. Private evidence is retained under
`.eval/codex-smoke/live/ca3475cc3e5743db868f8f74feb3a48d/`; no raw transcript/auth
export. Original setup failures remain historical evidence, not model retries.
The intervening `e1f6ff396b754edea1a3f3c999fe950e` login expired without a saved
grant/model invocation and cleaned successfully. The exclusive single-invocation
claim is now consumed and must not be removed. This diagnostic is not scored
campaign readiness, complete accounting or validation of other harness profiles.

Owner selections: Codex, native ChatGPT login, initial $10 ceiling; subsequent
approval narrows the first smoke to one task, at most 60 seconds agent time,
included allowance only and no paid credits. Superseding owner approval on
2026-10-13 permits credit fallback for that one 60-second smoke, with $10 a
monitoring target, explicitly not a guaranteed hard cap. Private account/balance
details are omitted from repository documentation. No API-key
substitution, credit purchase, automatic retry or additional model trial is authorized.
Requested model remains `gpt-6.1-sol`; CLI `0.162.1` and configuration parsing are
conformance-verified below. Account/model availability and actual inference remain
unverified.

Source inspection (2026-10-13): pinned Harbor's native Codex adapter supports
auth JSON but its existing-login route reads a host file before uploading it.
That route is not accepted for this smoke: login uses fresh isolated state, never
the developer's `~/.codex/auth.json`. ACP authentication is not substituted.
Native invocation bypasses its inner sandbox; the tested outer Docker authority
boundary remains required, including provider-only egress and fresh verifier state.

[OpenAI credit guidance](https://help.openai.com/en/articles/12642688) states that
usage draws from an available credit balance after included limits, and automatic
reload can purchase more credits. Disabling reload alone does not prevent spending
existing credits. No verified per-run CLI switch for included-allowance-only use
has been established. [Codex pricing](https://developers.openai.com/codex/pricing/)
distinguishes subscription usage from API prices; neither a 60-second timeout nor
an API-price estimate proves a USD cap or prevents credit fallback.

The prior included-only billing blocker is superseded, not falsely resolved by
account settings. Keep subscription credit use separate from API-price estimates;
unavailable monetary cost is not zero. Do not request secrets or auth-file contents
in chat, purchase credits, clear balances, or change account settings.

Registry/source preparation: npm reports Codex `0.162.1`; pin that exact release
for conformance rather than installing `latest`. Source at `rust-v0.162.1` supports
`codex login --device-auth`; this flow requests/polls codes at auth.openai.com,
then persists tokens only inside the isolated CODEX_HOME. No localhost browser
callback port or host-auth import is required. Source inspection is not installed
binary verification. The login step needs the owner present for browser authorization.

Preparation implemented: `prepare-codex-smoke --allow-credit-fallback --out PATH`
creates an exclusive private, deterministic preparation artifact without importing
Harbor, installing, logging in, or running a model. Exact shapes belong to
`src/agent_evals/codex_smoke.py` and generated schemas. The requested baseline is
medium reasoning, Standard (`service_tier = "default"`), disabled web search,
remote-memory/connector/plugin/hook/telemetry requests, with native delegation
left unverified rather than falsely claimed disabled. This is a smoke baseline,
not a resolved general candidate composition or scored campaign.
Package preparation binds npm's exact linux-x64 tarball and SHA-512 integrity.
Remaining effective/observed controls still block execution, regardless of the
preparation artifact's contents. Verification: 52 tests pass in worker environment;
the offline walkthrough creates `.eval/codex-smoke/preparation-v1.json` privately.

Network source finding: Harbor's pinned egress sidecar permits unfiltered DNS and
ICMP, exempts namespace-local destinations and marked traffic, and does not enforce
public-IP/destination-host binding. Its allowlist cannot establish the required
provider-only isolation. Do not enable it and relabel it sufficient.

Accepted boundary (owner approved implementation and controls on 2026-10-13): keep the subject at
`network_mode: none`; a local loopback relay reaches a trusted HTTPS CONNECT proxy
through one read-only trial-private Unix-socket volume. The separately isolated
proxy has public egress but no credentials or shared memory bindings. It permits
only exact phase-authorized provider names on port 443, validates DNS answers as
public/non-local before dialing those exact numeric addresses, and validates TLS
ClientHello SNI against the approved destination. No direct subject DNS/UDP/ICMP,
LAN/host routes, wildcard domains or arbitrary CONNECT destinations. Provider
TLS remains end-to-end; there is no credential interception or TLS trust override.
Proposed routes: auth.openai.com during login/logout; chatgpt.com during agent
execution. Runtime/source evidence may require additional exact endpoints; changes
are reviewed rather than silently widening to `*.openai.com` or ordinary networking.
Harbor still owns environment lifecycle/exec/upload; no delegation loop is recreated.

Required negative controls before login: rejected host/IPliteral/port, private and
mixed DNS answers, rebinding, mismatched/missing SNI, direct socket/DNS and subprocess
bypass, proxy shutdown, timeout/cancellation and scoped cleanup. No-network oracle
controls are not repeated under their exhausted approval. Bounded setup downloads
and a new no-model network-control budget require explicit approval.

Network-control approval: up to eight no-model container-control attempts, at most
60s test time and 1 CPU/512 MiB per container; pinned public setup downloads; cleanup
only of newly created resources; private retained evidence under `.eval/`. This is
independent of the exhausted 13 no-network attempts and the single approved model
smoke. Login waits for the network controls to pass. The initial fixed suite has
four attempts (auth route, agent route, proxy shutdown, timeout), each with fresh
subject/proxy containers. Provider positives perform certificate-verified TLS
handshakes only, not HTTP/authenticated/model requests. Unit mocks cover malformed
protocol, DNS-answer/rebinding and SNI cases that need controlled resolvers.

Network/control closure (2026-10-13): complete four-case batch
`fe039c33490641e483f44c04ea181069` passes. Earlier batch
`3502fd09aeb34aa99a6f468cc496d8da` retains two provider-route passes and a failed
proxy-shutdown probe (an expected connection reset was not recognized); its test
fix has regression coverage. Seven network attempts plus the separate one-attempt
Codex CLI conformance consume exactly eight approved no-model attempts. No more
controls are authorized under this approval. All created containers/volumes/networks
are removed; images/public-package artifacts and private evidence are retained.
Implemented boundary and limits: [docs/runtime-controls.md](docs/runtime-controls.md).

Codex CLI conformance: `version-4ad6f45767ae4a559381225da9fc5b65` passes inside
Harbor's disposable no-network boundary. Its image is
`sha256:0e10ce839937f6509c7dc6474c509b00f7a3c1261e075003a324879c355d49ec`.
Native `--version`, `login --help`, `exec --help`, `features list` and unauthenticated
`login status` run successfully with expected status codes. The exact public npm
tarball is downloaded, SHA-512-verified, confined, staged without host execution,
then content-checked before image assembly. The build performs no network-enabled
RUN steps. Requested configuration is parsed; selected disabled feature flags are
reported disabled. Native delegation reports enabled by default; no delegation
or model invocation is observed. This does not prove account availability, billing,
or all effective/observed settings during an authenticated run.

Verification: 91 tests pass in the pinned worker environment. Static review issues
on deadlines, teardown fallback, archive acquisition, unlisted copied files and
root ownership changes under dropped capabilities are fixed and regression-tested.
No shared Hindsight access, host profiles, API keys, login or inference occurred.

Owner credential-lifecycle approval is now accepted: locally retained owner-only
logs, no publication/backups, no token export, trial-login revocation followed by
credential-volume removal, and visible operator follow-up on failed revocation.
If revocation is unconfirmed, stop/remove the containers but quarantine their
credential volumes for follow-up instead of destroying the only recoverable grant.
The owner delegates integration, checks, execution and cleanup; only browser login
requires human participation. Do not request repeated approvals for this unchanged
single-smoke scope. Auth token files
must not be exported with task/log artifacts. A failed revocation must be recorded
and require operator follow-up, not hidden by volume deletion. The owner must be
available to complete the device login in their browser when its integration is ready.

Accepted implementation delta: a dedicated single-smoke worker uses the verified
local CLI image and socket-proxy profile. Login, agent and revocation routes are
separate phases. Authentication runs as trusted root before subject execution;
the model task runs as UID 1000. Fresh root-owned sticky home/config directories
protect an original-grant cleanup vault from the subject; only trial credentials
are present, never the host profile. Root owns the immutable requested config and
read-only native auth file; writable sessions/caches are still fresh trial state.
The worker starts one `codex exec`, bounded to 60s, then stops every subject process
before separate no-network artifact grading. The trusted verifier mounts only the
stopped subject workspace read-only; no auth/home volume. It reads bounded regular
files without following symlinks and exports only numeric usage/answer and grading
receipts, never raw JSONL/transcripts or arbitrary JSON. Root cleanup Python helpers
use isolated mode to prevent subject-controlled imports. Relay logs stay in the
root-only vault, inaccessible to UID 1000. A provider-accepted revocation receipt
means HTTP 2xx, not independent verification that every token is invalidated.
It restores only the auth route for trusted original-grant revocation, records any
failure, and removes its newly created containers/networks/volumes. A private
exclusive authorization claim prevents a second model invocation. Native in-harness
requests/retries/delegation remain inside the same budget and are not replaced by
an evaluator loop. Subscription billing remains unavailable unless independently
observed; token events are native evidence, not invoice or exact-child coverage.

Dedicated diagnostic runner implemented in `codex_live.py`/`harbor_codex_live.py`:
`codex-smoke --approve-single-smoke` starts isolated device login and returns only
the browser URL/code; after the owner authorizes it, the same bounded worker runs
one model task, grades independently and cleans up. `codex-smoke-wait OUTPUT` reads
the private result. General campaign `run` remains unavailable; this narrow path
does not grant additional trials or scored readiness. 113 worker tests pass;
focused review found no remaining issues before login. Actual native proxy login,
account/model access, task performance and revocation still require live evidence.

Native setup attempts (not additional model trials): `9899daf718744d6394f9e98d0013e2a5`
and `a4b32168ae79463a959e18d93fc1ac49` failed before a device code because Compose
copy ownership plus 0600 files prevented capability-restricted root from reading
configuration. Public controller files now use root-owned exclusive creation via
Harbor exec, without adding capabilities or exporting credentials. Setup
`0cf9e64052ef4da5a7dced3435327381` then reached native login but did not expose a
validated prompt within the readiness window; cancelled, cleaned, no saved grant.
All three retain original setup evidence. No `codex exec` invocation or approval
claim has been consumed. Native auth proxy-routing/boot behavior is under diagnosis;
do not relabel these setups as successful model runs or repeat exhausted controls.

Readiness diagnosis closed: setup `13a4365a7bc946b5bd8a46e48ab85565` established
that native login did issue a code, but the parser assumed four characters on both
sides of the separator; the service returns four/five-character groups. Shape-only
diagnostics retained no credential/transcript. The parser now accepts the native
format with regression coverage. Both readiness-timeout setups were cancelled and
fully cleaned without a saved grant or model invocation.

Current live smoke `9d63f2c199b6465699740f0e44a02efa` has exposed the native device
prompt through the isolated auth-only route and is waiting for owner browser login.
That login succeeded, but credential sealing failed before any model task. Provider
accepted original-grant revocation and all trial resources were removed; the
single model-invocation claim remains unused. The sealing loop followed native
arg0 helper symlinks while changing writable-state permissions; it now skips links
and special files, with a synthetic immutable-target regression test. The original
failure remains retained, not relabeled a successful smoke.

Current live smoke is `e1f6ff396b754edea1a3f3c999fe950e`, waiting for replacement
browser authorization. The worker will automatically execute the single task,
grade and revoke/clean up after authorization, within its overall deadline.
115 worker unit tests pass.
Do not change worker source files while this worker is live. Device codes belong
only to the private prompt artifact/human login message, never public documentation.

Remaining implementation gates: isolated interactive login flow, phase-specific
proxy integration into the authenticated lifecycle, hidden-safe fixed task,
bounded log/output collection, credential revocation/retention policy, and separate
grading. Authentication endpoints are setup-only, not an unrestricted agent route.
No login or inference has occurred. The preceding approved controls include live
container launches and public-package/image setup only. The real `run` command
remains fail-closed; no authenticated harness worker is claimed shipped.

## Completed increment: approved deterministic Harbor/Docker controls

Change ID: `chg-harbor-controls-20261011`.
Implemented scope and runtime evidence: [docs/runtime-controls.md](docs/runtime-controls.md).

Owner approval: up to 12 deterministic local trials, at most 60s agent time and
1 CPU/512 MiB per container; pinned public dependency/image downloads during setup;
cleanup only of this increment's containers/networks/volumes. No provider calls,
host-profile mounts, shared Hindsight access, or remote publication. Evidence stays
private under `.eval/`; no automatic artifact deletion is authorized.

Budget amendment: owner approved one extra trial after two preserved setup-only
inspection failures; total authorized cap is 13. No resource/time/network/credential
permissions changed. Failed attempts remain evidence, not replaced successful slots.

Closure (2026-10-11): 48 worker-environment unit tests pass; all 11 controls pass
in batch `d6efbde32ab045228ce0b1ab38c320c1`. Two earlier setup-only failures remain
preserved. Exactly 13 attempts consumed the amended approval; no further live runs
are authorized. Every created container/volume is removed, private evidence remains,
and shared Hindsight/host profiles/model providers remain untouched. Review found
no remaining code issues; documentation is synchronized to the verified scope.

Next increment: one approved real CLI smoke after provider-only egress enforcement,
exact native adapter/version/auth routing and bounded task/spend approval. The owner
can provide the credentials route (not secrets in chat) and OpenCode agent settings.
No-network controls do not authorize or establish those properties.

Scope: separate pinned Harbor worker, deterministic trusted control agents/tasks,
network-none disposable Docker subject/verifier environments, bounded artifact
inspection, private control receipts, and explicit approval-aware CLI. These
controls are not executions of Codex/Claude/OpenCode/Antigravity and cannot enter
their scored reports or erase their existing readiness blockers.

Acceptance:

- Oracle passes/no-op fails on three independent artifact tasks; hidden graders
  are absent from subject containers and grading occurs after subject stop.
- Docker inspection proves network-none, fresh home/state, no host bind mounts,
  resource limits and no privileged/host-network/socket access.
- Fresh-state canaries, attempted network connections and hidden-grader probes
  fail to expose prior-trial or evaluator material. Shared memory remains untouched.
- A timed-out agent is stopped before bounded output collection and grading.
- Created resources are scoped by unique identities; cleanup results and partial
  failure evidence remain visible. Unrelated Docker resources are never removed.
- Unknown controls/approval mismatch/worker revision drift fail before launch.
- Unit tests plus the approved live controls establish only this no-network
  deterministic profile. Provider egress and real Hindsight service isolation remain
  separate future gates.

## Completed increment: offline framework core

Change ID: `chg-offline-framework-core-20261010` (owner-authorized implementation).
Implemented scope and exact limitations: [docs/offline-core.md](docs/offline-core.md).

Scope: strict typed JSON contracts/generated schemas, local candidate/workspace
resolution, deterministic campaign/trial locks, drift verification, append-only
private records, descriptive JSON/Markdown reports, CLI and synthetic fixtures.
RSI controllers can consume these contracts without sharing a repository.
No provider calls, live containers, trusted candidate-code imports, or publication.

Acceptance:

- Same inputs produce the same lock; settings/content changes alter identity.
- Unknown fields, incomplete OpenCode profiles, unpinned versions, conflicting
  settings, obvious embedded credentials, unsafe paths and symlinks are rejected.
- Lock/content tampering, duplicate original slots and mismatched records fail.
- Memory policy and initial-state inputs are explicit; shared-service URLs are
  not supported. Planning does not establish runtime isolation.
- Records are created exclusively with owner-only permissions; reruns cannot
  replace originals or enter primary scores. Missing trials remain scheduled.
- Reports separate profile identities, outcomes, validity, cleanup, efficiency,
  missing coverage, and descriptive repeated-trial reliability.
- All execution requests fail closed until a tested Harbor/isolation worker exists.

Verification: unit tests, CLI example walkthrough, generated-schema consistency,
and focused review. Runtime conformance and statistical inference remain future work.

Closure evidence (2026-10-10): 36 unit tests pass under Python 3.12.13; compile checks
pass; offline dependency lock/sync and CLI validate/plan/verify/slots/schema/report
walkthrough pass. The run command returns exit 2 without starting a process.
Review findings on snapshot limits/symlink races, malformed TOML, mixed cost totals
and quadratic report scans are fixed with regression coverage; final review has no
remaining findings. Private synthetic planning output is ignored under `.eval/`.
No provider/container runs, publishing, or modifications to sibling projects occur.

## Completed design baseline: major pieces

Change ID: `chg-major-piece-design-20261010`.

Original scope: local documentation only, without dependencies or external runs.
The subsequent offline-core increment above installs pinned project dependencies
from the local cache; the external-run restrictions remain unchanged.

Acceptance criteria:

- The major components identify inputs, responsibilities, trust boundaries, and
  evidence outputs without treating proposed behavior as implemented.
- Harness adapters and execution backends have distinct responsibilities.
- Unsupported subagent constraints, partial traces, retries, hidden grading,
  credentials, and result denominators have explicit handling.
- The first implementation increment has bounded observable acceptance criteria.

The owner authorized framework implementation after the design/protocol discussion.
Repository visibility remains undecided. Runtime and native-adapter work remains
subject to the independent conformance and external-run gates.

## Selected initial campaign

Change ID: `chg-initial-harness-targets-20261010`.
Target and readiness owner: [docs/initial-campaign.md](docs/initial-campaign.md).

The owner selected four CLI harnesses and five harness/model profiles. No profile
is execution-ready. OpenCode primary-agent/subagent settings remain owner-supplied;
the planner does not insert defaults while waiting for them. Model execution is
blocked on task/budget/credentials approval and the conformance gates.

Documentation acceptance: preserve the five requested candidates, documented model
IDs versus pending routing/variant choices, OpenCode's pending settings, and the
distinction between CLI selection and proved runtime compatibility.

## 1. Offline profile/suite planner

Accepted methodology: [docs/evaluation-protocol.md](docs/evaluation-protocol.md).
Change ID: `chg-evaluation-protocol-20261010`. Documentation is written; enforcement,
task audits and pilot evidence are not implemented or produced.

Typed JSON authoring/lock/plan/record models, generated schemas and local resolution
are implemented by the offline core. Verified native translation and scored-readiness
checks below remain pending; scored planning is rejected rather than claiming them.

Acceptance:

- Identical inputs produce identical plan digests.
- Content/version drift, unknown shared fields, native/shared conflicts, unsupported
  requirements, unsafe paths, and embedded credential values fail validation.
- Planning starts no containers, installers, network requests, or provider calls.
- Distinct subject settings produce distinct identities even with the same harness/model.
- Locks bind the protocol's audit/review, holdout/network, repeat-group, scheduling,
  exclusion and reporting policies; scored planning rejects unresolved requirements.

Remaining: replace opaque protocol-artifact references with reviewed scored readiness,
native configuration conformance, and trusted runtime evidence. The core does not
establish those properties from user-supplied manifest fields.

## 2. Harbor worker and deterministic task controls

Change ID: `chg-trial-memory-isolation-20261010`. Accepted isolation contract:
[docs/design.md](docs/design.md#trial-state-and-external-service-isolation).
The fixed no-network controls verify disposable Docker state and independent
artifact grading. General real-harness/provider/service enforcement is still pending.

Select and lock a Harbor dependency in an isolated worker environment. Prepare
three small coding/terminal tasks with separate trusted verifiers. Use oracle and
no-op subjects; keep automatic retries disabled.

Initial graders inspect output artifacts without executing submitted code. A
code-execution grader needs a separate integrity review before benchmark expansion.

Acceptance:

- Oracle passes and no-op fails under the same fixture/grader identities.
- Hidden tests/reference solutions are unavailable to the subject.
- Repeated trials start clean, retain independent IDs, and preserve artifacts.
- Every harness/child/plugin runs in the disposable subject boundary with fresh
  local state; shared Hindsight/MCP stores, host profiles and unauthorized service
  routes are inaccessible. Required memory uses a conforming trial-private binding,
  never silent disablement or a reset of the developer's shared store.
- Cross-trial canaries, direct-access/namespace negative controls, seed identities
  and runtime denial evidence prove the declared state scope. Missing assurance
  and confirmed contamination are distinguishable.
- Service writers are stopped, evidence captured and scoped access revoked under
  the approved cleanup policy; failures cannot affect another trial's state.
- Diagnostic reruns never replace original repeat slots or enter primary scores.
- Timeout/crash/cancellation, artifact-export violations, grader failure, and
  cleanup failure produce accurate records and readable partial evidence.
- Tampered locks and input drift fail before subject execution.
- Each scored task has reviewed positive/negative/alternative-solution controls,
  requirement coverage and an integrity/provenance audit; broken tasks are blocked.
- Holdout access, cross-trial isolation and declared agent-network restrictions
  have enforcement evidence, not just configuration-file assertions.

Live Docker runs require separate approval; static/unit checks do not prove these
runtime properties.

## 3. Selected harness compatibility and delegation conformance

Validate the selected campaign incrementally, beginning with one completed profile
after design review. Establish native-config/version compatibility before paid
execution. OpenCode V2 compatibility and owner-supplied agent settings are explicit
gates; completing another harness does not authorize default OpenCode profiles.
Disabled/native/configured delegation ablations remain follow-up experiments,
not assumptions about the initial cross-harness campaign.

Acceptance:

- Applied settings are captured with sensitive values removed.
- Required configuration fields have conformance evidence, not merely file writes.
- Actual child-model/delegation evidence and missing-coverage cases are distinguishable.
- Subject-reported traces are distinguishable from controller/provider observations.
- A profile cannot claim disabled delegation without tested enforcement evidence.
- Real-model runs use explicitly approved credentials, task scope and budgets.
- Raw artifacts have owner-only access and an approved retention/backup policy.
- The diagnostic pilot reviews all non-passes and a preselected pass sample per
  subject; unresolved task/grader/configuration/integrity issues block affected scope.

## 4. Reporting and benchmark expansion

Descriptive local JSON/Markdown reports over caller-supplied records are implemented.
Trusted worker-produced evidence, declared efficiency gates, paired inferential
statistics and benchmark expansion remain pending. Add a pinned coding benchmark
subset and additional harnesses only after controls pass.

Acceptance:

- Fixtures prove profile separation, scheduled/valid denominators, partial scores,
  unavailable cost, parent/child cost deduplication, and exclusion of diagnostic
  reruns, invalid passing rewards, and cleanup failures from pass numerators.
- Mismatched task/environment/budget strata are not silently ranked together.
- Reports link outcome and observability claims to the relevant evidence coverage.
- Fixtures prove token-category overlap handling, parallel latency accounting,
  logical turns versus tool calls/retries, missing efficiency evidence, and
  cost-per-success including failed-attempt spend with complete coverage.
- Correctness and declared cost/latency/turn gates have separate results; cheap
  failures cannot receive a successful task outcome.
- Raw artifacts remain private; publication is a separate reviewed export.
- Fixtures distinguish single-attempt success, `pass@k`, `pass^k`, unassessable
  groups and scheduled operational views without cherry-picking repeat slots.
- Task-cluster uncertainty, matched comparisons and small-sample limitations
  follow the predeclared policy; reports separate tuning and holdout results.
- Review corrections preserve original records/reports, apply consistently across
  affected subjects and disclose changed denominators; reruns remain diagnostic.

## Deferred

Second evaluator backend, services/dashboard, cloud execution, hostile-workload
isolation, hard USD enforcement, peer-agent task topologies, and automated promotion.
Each needs a concrete requirement and a focused contract before implementation.
