# Deterministic Harbor/Docker controls

Status: **Implemented and live-verified no-network deterministic profile.** Real harness/model
execution and scored campaigns remain blocked. Change: `chg-harbor-controls-20261011`.

Verification on 2026-10-11: 48 unit tests pass in the pinned worker environment;
the complete 11-control live batch passes, with successful cleanup for every case.
Private batch identity: `d6efbde32ab045228ce0b1ab38c320c1`. Evidence is retained at
`.eval/controls/<batch-id>/`; controller source digests and dependency/image pins
identify the tested closure. No tested-profile resources remain after teardown.
The exact amended budget is exhausted: two setup-only failures plus 11 completed
controls, 13 attempts total. No further live trials are authorized by this approval.

The first two attempts failed closed before subject execution because inspection
incorrectly treated Docker's short-syntax named-volume `HostConfig.Binds` entries
as host bind mounts. The fix validates exact trial-scoped names and `Mounts.Type`;
host paths remain forbidden. Failed evidence is preserved separately. The second
attempt captured inspection for diagnosis; no failed attempt is relabeled passing.

## Authorized scope

The owner approved up to 12 local deterministic trials on 2026-10-11, public
pinned setup downloads, and teardown only of resources created for these tests.
Each container is limited to 1 CPU/512 MiB; agent time is at most 60 seconds.
No model calls, host agent profiles, shared Hindsight access or publishing are
authorized. Private evidence remains under `.eval/` without automatic deletion.
Documentation of the command is not authorization for another batch.
The owner subsequently extended the total cap to 13 to include two preserved
setup-only inspection failures and the complete 11-control suite.

## Profile and trust boundary

The control worker uses pinned Harbor `0.24.0` at commit
`09f5b964f8e5075f7e52fd08c9994dfc80ad5d24`, installed separately from the offline
core through [worker/uv.lock](../worker/uv.lock). Controller code is first-party,
fixed code—not user-supplied agent classes or task commands.

Harbor owns Docker start/exec/upload/stop transport. A version-specific subclass
uses one evaluator-owned Compose definition rather than Harbor's default overlays.
This avoids implicit bind mounts, task-authored network overrides and verifier
overlay differences. Resource constraints are inspected after startup, before any
subject command. The pin is checked against installed VCS metadata.

Subject and verifier each get a unique project, container and new named volumes
for workspace/home. Root filesystem is read-only; capabilities are dropped,
no-new-privileges is set, execution UID is 1000, PID count is bounded, and network
mode is `none`. There are no host bind mounts, published ports, Docker sockets or
provider credentials. Fixed privileged-user setup only adjusts fixture permissions
inside the new volumes; subjects cannot choose execution users or setup commands.

Fresh named volumes retain outputs after container stop, allowing collection only
after all subject processes stop. They are not reused by another trial. The host
receives a bounded tar stream for one declared JSON file; links, extra paths and
oversized output are rejected without extracting files into the host filesystem.
Only sanitized JSON data goes to the fresh verifier; submitted code is not executed.
Expected results and grader code are uploaded only to the verifier.

The subprocess worker receives a minimal environment and a private home, not the
developer's API keys, proxies, Docker credential store or Python-path overrides.
The Docker daemon and host controller remain trusted. Docker shares the host kernel:
this is not a microVM guarantee or a hostile-code security certification.

## Controls and evidence

The default fixed suite has 11 trials:

- Sum, sorting and string transformation: oracle succeeds and no-op fails (six).
- A synthetic memory-file canary writer followed by a fresh-state reader (two).
- A deliberately timed-out agent (one).
- Symlink and oversized-artifact negative controls (two).

Every subject/verifier checks an empty initial workspace/home, hidden-grader/socket
absence, credential-variable absence and loopback-only network interfaces. A
TEST-NET address connection and a subprocess connection must fail as unreachable.
No probe contacts the developer's Hindsight service. This establishes denial of
external access in this no-network profile, not authorization/isolation correctness
inside a real trial-private Hindsight server.

Per-case private evidence includes effective Docker inspection, probes, agent
status, output identity, grading result, stop ordering and cleanup status. Worker
provenance binds Harbor revision, dependency lock and controller source digests.
Receipts distinguish **the control passing** from **the simulated task passing**:
a no-op failure/timeout/export rejection is expected control success.

The batch stops after a failed control rather than silently retrying. A run's
control receipts are separate from campaign TrialRecords and cannot be reported
as Codex/Claude/OpenCode/Antigravity scores. General `run` still fails closed.

Launcher/worker signal handling, graceful cancellation and scoped emergency cleanup
have mocked regression coverage; live cancellation was not an additional case in
this budget. RAM is capped at 512 MiB; Docker's swap defaults are recorded, not
claimed as a tested combined RAM+swap ceiling. Remote memory service authorization,
provider egress, malicious native plugins and general hostile submissions remain
outside this fixed-fixture verification scope.

## Setup and invocation

Run from the repository root, only with applicable approval:

```sh
uv sync --project worker --locked --python 3.12
docker pull python@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
uv run --offline --locked agent-evals controls --approve-local-controls
```

Without `--approve-local-controls`, the command fails before any container launch.
`--case` selects only known fixed controls; it does not accept shell commands.
The image is pre-pulled during setup; the worker cannot pull at runtime.
The control-request/control-batch schemas are generated from
[controls.py](../src/agent_evals/controls.py). The current receipt is controller
evidence on this trusted host, not third-party cryptographic attestation.

## What the owner supplies next

After the controls pass, choose the first real CLI smoke's authentication route
and authorize its exact scope/budget. Prefer a separately scoped API credential
when available; otherwise design an explicit isolated native-login workflow.
Do not paste secrets into chat or mount an existing host agent home. Select a
bounded task/repetition count and spending ceiling; token/USD limits are not hard
caps until tested enforcement exists.

OpenCode primary-agent/subagent settings remain owner-supplied. A memory-dependent
profile needs an independently reviewed trial-private Hindsight service and seed
policy before it can execute. Provider-only egress and required remote services
need a new tested network profile; enabling ordinary Docker networking is not an
acceptable shortcut. No real profile's blockers are removed by these controls alone.

## Socket-proxy network controls and Codex CLI conformance

Status: **Implemented and live-verified fixed network controls; isolated native
login and model execution are not implemented/enabled.** Change:
`chg-codex-native-smoke-20261013`. The owner separately approved eight no-model
attempts, pinned public setup downloads and cleanup of newly created resources.
This budget is now exhausted: seven network attempts and one CLI conformance.
No login/inference or access to shared Hindsight/host agent profiles occurs.

The subject remains UID 1000, capability-dropped, read-only-root,
`network_mode: none`, with new home/workspace volumes. An additional read-only
trial-private named volume exposes the HTTPS proxy's Unix socket. A subject-local
127.0.0.1 relay reaches that socket; no subject DNS, UDP, ICMP or ordinary IP egress
is enabled. The proxy has only that volume, no credentials/task outputs, a separate
Docker provider network, no published ports and the same 1 CPU/512 MiB/PID limits.
Its root UID is needed only to create the socket in a new root-owned volume;
capabilities are dropped and no-new-privileges/read-only-root remain enforced.

[egress_proxy.py](../src/agent_evals/egress_proxy.py) accepts only exact lowercase
host names on port 443. It rejects malformed CONNECT headers, other hosts/ports,
IP literals, private/mixed DNS answers and IPv6 transition address forms. It checks
all DNS answers, resolves once, dials a validated numeric address and confirms the
peer. Before dialing, it parses bounded fragmented TLS ClientHello records and
requires matching SNI, rejecting absent/duplicate SNI and encrypted ClientHello.
TLS stays end-to-end: the proxy does not decrypt, log or inspect request payloads.
This is a destination-route boundary, not URL/path restrictions, a billing proxy
or prevention of provider-side retrieval/SSRF. Provider infrastructure and public
DNS remain trusted; local-address enumeration is scoped to the proxy namespace.

The exact phase controls permit either auth.openai.com or chatgpt.com, never both
in the fixed suite. Provider positives perform certificate-verified TLS handshakes
only, with no HTTP payload. Live negatives check forbidden destinations, alternate
ports, suffix-confusable hosts, mismatched SNI, direct TCP/DNS/raw-socket/subprocess
bypass, shutdown of the only proxy, and timeout termination. DNS rebinding/mixed
answers, malformed protocol, IPv6 transition and cancellation tests use unit mocks,
not live malicious DNS infrastructure. These results do not prove Codex's native
HTTPS_PROXY compatibility or every endpoint needed for authenticated operation.

The control subject exits at 60s; the proxy has an independent kernel-alarm process
deadline and begins graceful shutdown earlier. A 55s shared trial deadline includes
creation, inspection, readiness and the probe. Separate 15s cleanup includes teardown
and resource-absence checks. A failed cleanup triggers exact-claim parent fallback;
the original failed receipt is preserved rather than converted into a passing case.
The fixed suite stops at the first failure and never automatically retries.

Complete passing batch: `fe039c33490641e483f44c04ea181069` under
`.eval/network-controls/`. Earlier batch `3502fd09aeb34aa99a6f468cc496d8da` retains
three attempts, including the incorrect proxy-reset negative-control failure.
Conformance batch: `.eval/codex-smoke/version-4ad6f45767ae4a559381225da9fc5b65/`.
It verifies `codex-cli 0.162.1`, device-login flags, configuration parsing/feature
reporting and a fresh unauthenticated home; it performs no login or inference.
91 worker-environment unit tests pass. No test resources remain after cleanup.
Public staged binaries are never executed on the host; retained local images are
identified by their image IDs, not treated as registry-published immutable digests.

Commands (documentation is not another run approval):

```sh
uv run --offline --locked agent-evals network-controls --approve-network-controls
uv run --offline --locked python scripts/stage_codex.py --approve-public-download --out .eval/codex-smoke/codex-0.162.1-linux-x64
uv run --project worker --offline --locked python scripts/check_codex_image.py --approve-version-control
```

The network control request/batch schemas own exact record shapes; these receipts
are separate from scored TrialRecords. General `run` still fails closed. The
single owner-approved model smoke is not consumed by these no-model controls;
it remains gated on native-login/runtime integration, credential lifecycle and
the artifact-retention policy.
