# agent-evals

Status: **Offline core, Docker/network controls and one authenticated native Codex
diagnostic smoke verified; general campaign execution unavailable.**

Evaluate a versioned agent composition—harness, primary model, subagents,
instructions, skills, plugins, and tools—against reproducible tasks.

The proposed execution backend is Harbor. This project owns experiment identity,
configuration conformance, evidence requirements, and comparison reports; it does
not implement a replacement agent loop or container scheduler.

- [Major components and contracts](docs/design.md)
- [Implemented core and local walkthrough](docs/offline-core.md)
- [Verified Harbor/Docker and socket-proxy controls](docs/runtime-controls.md)
- [Selected initial harness campaign](docs/initial-campaign.md)
- [Accepted campaign evaluation protocol](docs/evaluation-protocol.md)
- [Delivery plan and acceptance criteria](PLAN.md)

The Python CLI validates declarative candidates/workspaces, creates deterministic
local locks/trial plans, ingests caller-supplied records and produces descriptive
reports. It rejects scored planning and fails closed on general campaign execution.
The separately approved `controls` command validates a pinned, no-network Harbor/
Docker profile; `network-controls` separately tests a restricted socket-proxy
boundary. Codex CLI version/configuration conformance is verified without login.
The single authorized native ChatGPT-login Codex smoke completed in 7.04 seconds
and passed independent grading. Token revocation was accepted and trial resources
were removed. Requested model: `gpt-6.1-sol`; observed model identity and monetary
cost remain unavailable. This diagnostic does not establish scored readiness or
authorize another run. Trial-private Hindsight integration remains unimplemented.

```sh
uv sync --offline --locked --python 3.12
uv run --offline --locked python -m unittest discover -s tests -v
uv run --offline --locked agent-evals validate examples/offline/campaign.json
```

The example uses synthetic, non-executable targets. Real-model evaluations,
container controls, remote publishing and extra integrations are separate steps.

Credentials, private tasks, native transcripts, and run artifacts are not Git
content. The GitHub source repository is public; runtime evidence stays owner-only and
local, without publication or backups.

GitHub: [lunarmoon26/agent-evals](https://github.com/lunarmoon26/agent-evals).
