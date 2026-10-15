# Project instructions

Read `docs/design.md`, `docs/offline-core.md` and `PLAN.md` before changing behavior.
The offline core, Docker/network controls and no-inference Codex CLI conformance
are verified; authenticated harness execution and external-service integration
remain targets. Obtain
agreement on consequential contracts before implementing them; update their status
only with supporting evidence.

- Keep the evaluation controller separate from the harness under test.
- Reuse Harbor execution; do not recreate a harness's delegation loop.
- Reject unsupported configuration; distinguish requested, effective, and observed inputs.
- Keep hidden graders and reference solutions out of the subject environment.
- Never commit credentials, raw transcripts, private fixtures, or run artifacts.
- Do not mount host agent profiles, Docker sockets into subjects, or host credentials.
- Require explicit approval for provider calls, live container runs, remote writes,
  or publication. Documentation of a command is not approval to execute it.
- Use Python 3.12+ and `uv`. Pin runtime dependencies.
- Keep proposed work in `PLAN.md`; exact implemented shapes belong in typed models
  and their generated schemas, not independent handwritten copies.

Local checks: `uv run --offline --locked python -m unittest discover -s tests -v`.
After contract changes: `uv run --offline --locked python scripts/generate_schemas.py`.
General campaign execution is intentionally unavailable; never bypass it with host
CLI invocations. The owner-approved one-task native Codex smoke has a dedicated
isolated worker; its exclusive invocation claim must never be removed to retry.
`controls`, `network-controls` and the version-conformance script need
explicit per-batch approval and must not be run by default tests. Their previously
approved live budgets are exhausted; consult `PLAN.md` before another launch.
Worker-only unit checks:
`uv run --project worker --offline --locked python -m unittest discover -s tests -q`
(does not launch containers).
