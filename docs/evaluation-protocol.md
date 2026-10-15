# Campaign evaluation protocol

Status: **Accepted methodological requirements; selected offline binding/reporting
checks implemented. Task audits and runtime requirements remain unvalidated.**
The owner approved these requirements on 2026-10-10. This approval does not
authorize provider calls, live containers, publication, or artifact deletion.
Architecture and metric definitions: [design.md](design.md).
Selected subjects: [initial-campaign.md](initial-campaign.md).
Current enforcement scope: [offline-core.md](offline-core.md).

## 1. Audit tasks and graders before scoring

Each task has a private review record binding the instruction, fixture, grader
and reference-solution revisions. The record covers:

- Clear, achievable requirements and a reference solution that meets them.
- Oracle passes and no-op fails; these controls are necessary, not sufficient.
- Alternative valid solutions where applicable, including different implementation
  strategies, to detect overly restrictive grading.
- Deliberately wrong/partial outputs, edge cases and unrelated regressions, to
  detect permissive grading or narrow tests. Each required behavior maps to checks.
- Agent-visible versus hidden material, artifact export and code-execution integrity.
- Task provenance, known public exposure, allowed network needs and answer-leak risks.

An unresolved requirement ambiguity or demonstrated grading defect blocks that
task from the scored suite. Imported tasks receive this review too; benchmark
popularity and a working reference patch do not establish grader validity.
Controls requiring execution run only after separate runtime approval.

Deterministic outcome graders remain the initial default. If subjective/model
grading is added, its rubric, judge version/settings and human calibration are
locked before scoring. Calibration includes known good/bad outputs and disagreement
review; the acceptance threshold is declared in advance. An LLM judge cannot
override mandatory deterministic integrity checks.

## 2. Human review and adjudication

The pilot is diagnostic, not part of scored results. Review all pilot non-passes
and a preselected sample of passes across every participating subject. The sample
rule/size and reviewer are chosen before viewing outcomes. Review final artifacts,
grades and available trajectories; coverage gaps remain explicit.

Classify findings as subject failure, task ambiguity/defect, grader error,
infrastructure failure, configuration drift, contamination, or unresolved.
Classifications carry evidence and a reviewer decision, not just an agent's claim.
Hide subject identity during artifact adjudication where practical; do not claim
blinding when trajectory inspection reveals it. Disagreements receive a second
review or remain unresolved and block readiness for the affected task/profile.

The scored campaign locks its review sampling and adjudication rules. Raw results
are immutable; decisions are linked annotations. Do not remove difficult tasks
because a candidate fails them. A discovered task/grader defect triggers a versioned
correction with impact assessment across all subjects and repeat slots of that task.
Preserve the original report, publish the correction/exclusions and changed
denominators, and compare only matched evidence. Changed graders require regrading
all retained eligible outputs for that task; changed instructions/fixtures require
a new campaign. Neither operation deletes original failures or authorizes reruns.

## 3. Reliability and uncertainty

Single-attempt success and the scheduled/valid denominators in the architecture
remain primary. Report each task's eligible successes, scheduled repetitions,
invalid/missing attempts and variability; repeats do not create new unique tasks.

Where requested, lock k and disjoint groups of k original repeat slots per task
before execution. Within each fully assessable group:

- `pass@k`: at least one eligible pass in k attempts (retry-opportunity performance).
- `pass^k`: all k attempts are eligible passes (consistent-success performance).

Report group counts, task weighting and unassessable groups. Groups with invalid,
missing or ungraded attempts are unassessable for capability estimates, not silently
dropped. An additional scheduled operational view treats those slots as non-passes
and includes every planned group; it is labeled separately from capability estimates.
Never select the best k runs or put diagnostic reruns into a scored group. `pass@k`
does not estimate single-attempt success and is not a free retry budget: its latency
and cost accounting covers all k attempts under a declared execution policy.

Declare the estimator, interval method, minimum sample requirements and primary
comparisons before scoring. Task-level paired comparisons preserve all repetitions
within each task (for example, a task-cluster bootstrap); do not treat repeated
attempts as independent task samples. No multiplication of estimated success
probabilities or claim of independent provider behavior is assumed. Small samples
receive descriptive counts rather than unsupported precision or winner claims.

## 4. Contamination controls and holdout

Separate public/tuning tasks from a private or newly authored final holdout; report
them as separate strata. Holdout instructions are revealed to the subject only at
execution, while grading material/reference solutions stay evaluator-only. Private
does not prove absence of training contamination. Record provenance, exposure and
any uncertainty; do not publish private fixtures through reports.

Freeze the holdout before profile tuning is complete and restrict access. Once its
results guide configuration changes, label it used-for-tuning and select a fresh
holdout for subsequent confirmatory claims. Never market a repeatedly optimized
benchmark result as untouched validation.

Initial coding/terminal runs deny general internet access during solving, apart
from approved provider endpoints and explicitly scoped trial-private services under
the [trial-state isolation contract](design.md#trial-state-and-external-service-isolation).
Shared Hindsight/MCP memory and host/LAN services are not permitted by those exceptions.
Dependencies are materialized during separately
controlled setup. Exceptions declare destinations, purpose, enforcement and network
evidence requirements in the lock. Browser/research tasks need a different reviewed
policy; network restrictions cannot silently change their task semantics.

Subjects cannot access benchmark solutions, other trial records or evaluator stores.
Inspect available evidence for answer retrieval, cross-trial state and evaluation
awareness; recognition alone is not contamination. Confirmed answer leakage marks
the affected attempt contaminated; uncertain access gets an explicit unresolved
integrity annotation. Reports state detection coverage rather than claiming all
leakage is observable. Do not silently substitute a different network policy when
the runtime cannot enforce the approved one.

Cold-start readiness includes a cross-trial canary control: trial A writes a unique
synthetic answer-like marker into its permitted local/remote memory; trial B starts
from the declared initial snapshot and cannot retrieve A's marker. Controls also
attempt direct shared-service access and cross-namespace reads/writes through
subprocesses and configured tools. Check declared initial-state identities and
runtime access-denial evidence; a failed recall alone does not prove isolation.
These are synthetic controls, not permission to write to the developer's memory.
Stateful-sequence experiments report their retained state and exposure separately.

## 5. Freeze the comparison before execution

The campaign lock binds task/holdout revisions, subject profiles, native settings,
grader/audit revisions, repeats/groups, budgets, network policy, evidence requirements,
review/adjudication rules, exclusion criteria, scoring/uncertainty methods and
primary comparisons. It also binds a scheduling policy that interleaves subjects
within matched task/repeat blocks to reduce time/load confounding, with declared
concurrency/rate limits and recorded execution timestamps. Cache treatment and
provider-side caching uncertainties are reported with latency and usage results.

Pilot results can change the next lock, not the definition of an existing scored
campaign. Unplanned analyses are labeled exploratory. Corrections and reruns follow
the rules above and the architecture; no results-driven weights or quiet exclusions.

Correctness, cost, latency and turns remain separate dimensions. Compare their
tradeoffs on matched tasks with coverage, not a post-hoc weighted leaderboard.
Exact primary-agent and subagent settings are mandatory subject inputs; incomplete
OpenCode profiles remain blocked, not filled with convenient defaults.

## Readiness evidence

A scored campaign requires a locked task audit, pilot review/adjudication record,
holdout/access and network policy, predeclared statistical/reporting policy, and
subject conformance evidence. Unresolved integrity findings block the affected
scope. These artifacts are evidence requirements, not artifacts already produced.
Typed models/generated schemas own their eventual exact shapes.

## Rationale sources

- [Anthropic: Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents), 2026-01-09: tasks/trials/outcomes, grader calibration, trajectory review and repeated-trial reliability.
- [Anthropic: Eval awareness in BrowseComp](https://www.anthropic.com/engineering/eval-awareness-browsecomp), 2026-03-06: answer leakage and evaluation-aware retrieval.
- [OpenAI: Trustworthy third-party evaluations](https://openai.com/index/trustworthy-third-party-evaluations-foundations/), 2026-05-29: appropriate harnesses and valid evaluation conditions.
- [OpenAI: Separating signal from noise in coding evaluations](https://openai.com/index/separating-signal-from-noise-coding-evaluations/), 2026-07-08: task defects and benchmark audits. Findings concern the audited benchmark, not all tasks.

These sources motivate the protocol; they do not certify this unimplemented system
or establish a universal industry standard.
