# Initial harness campaign

Status: **Harness/model targets selected by the project owner; execution blocked
on unresolved inputs and implementation.** No evaluations are executed.

Change ID: `chg-initial-harness-targets-20261010`.
Architecture owner: [design.md](design.md).
Delivery owner: [../PLAN.md](../PLAN.md).

## Target matrix

These are five distinct subject profiles, not a four-harness x all-models grid.
Model labels and documented IDs are not proof of access through the chosen account,
provider route, or harness version.

| Profile label | Requested harness surface | Requested primary model | Documented model selection | Pending inputs |
| --- | --- | --- | --- | --- |
| Codex / Sol 6.1 | Codex CLI | 6.1-sol | `gpt-6.1-sol` | Smoke selects CLI 0.162.1/native login/medium/Standard; authenticated adapter/model/usage conformance and final campaign composition pending |
| Claude Code / Sonnet 5.5 | Claude Code CLI | sonnet-5.5 | `claude-sonnet-5-5` | CLI version, effort, credentials route, permissions and native settings |
| OpenCode / Sol 6.1 | OpenCode V2 CLI | 6.1-sol | Provider/model handle pending; target model is `gpt-6.1-sol` | V2 version/adapter, provider route, primary agent, subagents and other owner-supplied settings |
| OpenCode / Sonnet 5.5 | OpenCode V2 CLI | sonnet-5.5 | Provider/model handle pending; target model is `claude-sonnet-5-5` | V2 version/adapter, provider route, primary agent, subagents and other owner-supplied settings |
| Antigravity / Gemini 3.8 Flash High | Antigravity CLI (`agy`), not the SDK | Gemini 3.8 Flash High | `gemini-3.8-flash-high` (owner-selected) | CLI version, credentials route, permissions and native settings |

OpenCode profiles remain incomplete until the owner supplies primary-agent,
subagent, and related configuration. The planner rejects incomplete profiles;
it does not substitute the built-in agent, silently disable delegation, or use
the developer's host configuration.

For the other harnesses, a stock baseline is a proposal, not a locked profile.
Native delegation/background-model defaults are captured and labeled when used.
Primary-model selection does not assert that every child/background request uses
the same model. Do not change those defaults into a solo-agent ablation silently.

Codex preparation update (2026-10-13): the owner selects native ChatGPT login and
one 60-second diagnostic task, with paid-credit fallback allowed and $10 a monitoring
target, not a hard cap. The fixed smoke requests medium reasoning/Standard speed;
its CLI, selected configuration flags and restricted socket-proxy controls pass
no-inference conformance. The owner has not logged in to this isolated environment,
and no model run or scored profile is established. Current evidence, exhausted
control budgets and credential-lifecycle gates belong to [PLAN.md](../PLAN.md) and
[runtime-controls.md](runtime-controls.md).

## Execution and comparison policy

- Exercise the requested unattended CLI surfaces through Harbor, not replacement
  agent loops or SDK-only implementations with different behavior.
- Lock exact CLI and Harbor/adapter versions before model execution; no `latest`
  installations during scored runs.
- Validate model availability and routing in the approved isolated runtime.
  Unsupported or inaccessible model selections fail, never silently substitute.
- Unrequested model fallback is disabled where controllable. If the harness or
  provider switches models, preserve evidence and exclude that attempt from the
  exact-model comparison rather than relabeling it as the requested subject.
- Use matched task revisions, environments, outcome graders and comparable
  evaluator budgets. Record native effort/speed settings; equal setting names
  across providers are not proof of equal compute budgets.
- Compare Codex against OpenCode on Sol 6.1, and Claude Code against OpenCode on
  Sonnet 5.5, as complete system comparisons. OpenCode agent differences remain
  part of the subject. Antigravity/Gemini is a different-model system comparison,
  not evidence isolating the harness's causal effect.
- Report correctness, tokens/cost, latency and turns under the definitions in
  [design.md](design.md). Preserve native metrics alongside normalized ones.

## Compatibility findings (static inspection)

Harbor source baseline:
`09f5b964f8e5075f7e52fd08c9994dfc80ad5d24`. Public documentation checked
2026-10-10; docs are mutable and not runtime conformance evidence.

| Integration | Finding | Required validation |
| --- | --- | --- |
| Codex | Official model docs name `gpt-6.1-sol`; CLI supports explicit model selection. | Pinned unattended CLI, effective model, account access, usage/child evidence. |
| Claude Code | CLI exposes print/stream-json, explicit models, subagent definitions and subagent text forwarding. | Pinned CLI and Harbor option wiring; Sonnet 5.5 access and effective model; usage coverage. |
| OpenCode V2 | V2 command docs install `@opencode/cli` and support standalone runs. Inspected Harbor installs `opencode-ai` and generates legacy-shaped settings. | Validate or adapt installation, configuration and NDJSON/session evidence. Do not assume V2 works through the existing adapter. |
| Antigravity CLI | Headless docs expose model slugs, stream-json usage and subagent references. `num_turns` counts user turns, not normalized logical model generations. Session usage counters are cumulative. | Pinned model/effort selection and Harbor invocation; counter scope/overlap and event-schema compatibility. |

CLI output is subject-reported evidence unless stronger attribution is present.
In particular, Antigravity's exit-0/`SUCCESS` means response completion, not task
success; permission-denied tools can leave the process successful. Grader evidence
determines task outcome. Repeated cumulative usage envelopes are not summed.

## Readiness gates

1. Accept typed profile/plan/result contracts and implement the offline planner.
2. Pass deterministic oracle/no-op, fresh-state, timeout and hidden-grader controls.
3. Resolve exact harness versions, routing, delegation policy, native settings and
   evidence requirements. Validate OpenCode V2 compatibility explicitly.
4. Select a common task subset, repetition count and wall-clock/resource budgets.
   Code-execution task graders require the integrity review in the architecture.
5. Obtain explicit model-run approval covering the credentials route, task data,
   network policy, spend scope and private-artifact retention. No host profile mount.
6. Perform a bounded compatibility smoke before the scored campaign; record smoke
   evidence separately from scored attempts.
7. Meet the [evaluation protocol](evaluation-protocol.md): audit every selected task,
   adjudicate the diagnostic pilot, separate tuning/holdout tasks, validate network
   policy, and lock reliability/reporting, review and exclusion rules before scoring.

These gates do not authorize live runs, credential extraction, host CLI changes,
repository publication, or installing dependencies into other workspace projects.

## Primary sources

- [Owner-supplied Codex developer commands](https://learn.chatgpt.com/docs/developer-commands?surface=cli). The fetched static page renders desktop commands; the linked model docs provide explicit Codex CLI selection.
- [OpenAI model selection](https://learn.chatgpt.com/docs/models).
- [Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference).
- [Sonnet 5.5 ID and availability](https://platform.claude.com/docs/en/models/sonnet-5-5/whats-new-sonnet-5-5).
- [OpenCode V2 commands](https://opencode.ai/v2/docs/cli/commands/).
- [Antigravity CLI usage](https://antigravity.google/docs/cli/using/).
- [Antigravity headless model selection and counters](https://antigravity.google/docs/cli/headless/).
- [Harbor OpenCode installation](https://github.com/harbor-framework/harbor/blob/09f5b964f8e5075f7e52fd08c9994dfc80ad5d24/src/harbor/agents/installed/opencode.py#L137-L150).
- [Harbor OpenCode generated configuration](https://github.com/harbor-framework/harbor/blob/09f5b964f8e5075f7e52fd08c9994dfc80ad5d24/src/harbor/agents/installed/opencode.py#L535-L574).
