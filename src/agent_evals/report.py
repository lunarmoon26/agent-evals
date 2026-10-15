"""Descriptive summaries of caller-supplied evidence; no inferred missing usage."""

from collections import Counter
from statistics import median

from .models import CampaignLock, TrialRecord
from .planner import trial_plans
from .store import validate_records


def rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def summarize(lock: CampaignLock, records: list[TrialRecord]) -> dict:
    validate_records(lock, records)
    originals = {record.plan.slot_digest: record for record in records if record.purpose == "original"}
    campaign = lock.payload.campaign
    plans = trial_plans(lock)
    task_strata = {task.id: task.stratum for task in campaign.tasks}
    indexed = {}
    for slot in plans:
        indexed.setdefault((slot.subject_id, task_strata[slot.task_id]), {}).setdefault(slot.task_id, []).append(slot)
    rows = []
    # Do not mix tuning and holdout results, even within the same campaign.
    for subject in campaign.subjects:
        for stratum in sorted({task.stratum for task in campaign.tasks}):
            task_groups = indexed[(subject.id, stratum)]
            task_ids = set(task_groups)
            slots = [slot for group in task_groups.values() for slot in group]
            present = [originals[slot.slot_digest] for slot in slots if slot.slot_digest in originals]
            eligible = [record for record in present if record.eligible]
            passed = [record for record in eligible if record.outcome == "passed"]
            metrics = {}
            for name in sorted({name for record in present for name in record.metrics}):
                measurements = [record.metrics[name] for record in present if name in record.metrics]
                # Retain incompatible definitions/sources as different strata.
                grouped = {}
                for measurement in measurements:
                    key = (measurement.unit, measurement.definition, measurement.authority, measurement.pricing_revision)
                    grouped.setdefault(key, []).append(measurement)
                metrics[name] = [{"unit": key[0], "definition": key[1], "authority": key[2],
                                  "pricing_revision": key[3], "measured": len(values),
                                  "scheduled": len(slots), "missing_or_other_definition": len(slots) - len(values),
                                  "partial": sum(value.coverage == "partial" for value in values),
                                  "evidence_refs": sorted({value.evidence for value in values}),
                                  "sum_observed": sum(value.value for value in values),
                                  "median_observed": median(value.value for value in values)}
                                 for key, values in sorted(grouped.items(), key=lambda pair: str(pair[0]))]
            success_metrics = {}
            for name in metrics:
                values = [record.metrics[name] for record in passed if name in record.metrics]
                # Success-only values retain each definition/source, just like all-attempt views.
                success_metrics[name] = [{"value": value.value, "unit": value.unit,
                                          "definition": value.definition, "authority": value.authority,
                                          "coverage": value.coverage, "pricing_revision": value.pricing_revision,
                                          "evidence_ref": value.evidence}
                                         for value in values]
            costs = [record.metrics.get("subject_cost") for record in present]
            cost_groups = metrics.get("subject_cost", [])
            complete_cost = (len(present) == len(slots) and len(cost_groups) == 1
                             and all(cost is not None and cost.unit == "USD" and cost.coverage == "complete" for cost in costs))
            known_cost = sum(cost.value for cost in costs if cost is not None and cost.unit == "USD")
            groups = []
            per_task = []
            for task in campaign.tasks:
                if task.id not in task_ids:
                    continue
                task_slots = task_groups[task.id]
                task_records = [originals.get(slot.slot_digest) for slot in task_slots]
                per_task.append({"task_id": task.id, "task_digest": lock.payload.task_digests[task.id],
                                 "scheduled": len(task_slots), "eligible": sum(r is not None and r.eligible for r in task_records),
                                 "passed": sum(r is not None and r.eligible and r.outcome == "passed" for r in task_records)})
                k = campaign.policy.reliability_k
                for index in range(0, len(task_records), k):
                    members = task_records[index:index + k]
                    assessable = all(r is not None and r.eligible for r in members)
                    passes = sum(r is not None and r.eligible and r.outcome == "passed" for r in members)
                    groups.append((assessable, passes > 0, passes == k))
            assessed = [group for group in groups if group[0]]
            reliability = {"k": campaign.policy.reliability_k, "weighting": "equal disjoint groups per task",
                           "scheduled_groups": len(groups), "assessable_groups": len(assessed),
                           "unassessable_groups": len(groups) - len(assessed),
                           "pass_at_k_assessable": rate(sum(g[1] for g in assessed), len(assessed)),
                           "pass_all_k_assessable": rate(sum(g[2] for g in assessed), len(assessed)),
                           "pass_at_k_scheduled_operational": rate(sum(g[1] for g in groups), len(groups)),
                           "pass_all_k_scheduled_operational": rate(sum(g[2] for g in groups), len(groups))}
            rows.append({"subject_id": subject.id, "subject_digest": lock.payload.subject_digests[subject.id],
                         "stratum": stratum, "scheduled": len(slots), "recorded": len(present),
                         "missing": len(slots) - len(present), "eligible": len(eligible), "passed": len(passed),
                         "pass_rate_scheduled": rate(len(passed), len(slots)),
                         "pass_rate_valid_graded": rate(len(passed), len(eligible)),
                         "execution": dict(Counter(r.execution for r in present)),
                         "validity": dict(Counter(r.validity for r in present)),
                         "outcome": dict(Counter(r.outcome for r in present)),
                         "cleanup": dict(Counter(r.cleanup for r in present)),
                         "metrics": metrics, "successful_metrics": success_metrics,
                         "agent_wall_censored_trials": sum(r.execution in {"timeout", "budget-exhausted"} and "agent_wall" in r.metrics for r in present),
                         "known_subject_cost_usd": known_cost if len(cost_groups) == 1 else None,
                         "subject_cost_coverage_complete": complete_cost,
                         "cost_per_success_usd": known_cost / len(passed) if complete_cost and passed else None,
                         "trial_refs": [{"trial_id": r.trial_id, "slot_digest": r.plan.slot_digest,
                                         "grading_evidence": r.grading_evidence, "evidence": r.evidence} for r in present],
                         "missing_slot_refs": [slot.slot_digest for slot in slots if slot.slot_digest not in originals],
                         "reliability": reliability, "tasks": per_task})
    return {"schema_version": 1, "campaign_digest": lock.digest, "intent": campaign.policy.intent,
            "evidence_origin": "caller-supplied records; not execution attestation",
            "execution_blockers": lock.payload.blockers,
            "diagnostic_reruns_excluded": sum(record.purpose == "diagnostic" for record in records),
            "statistics": "descriptive only; no ranking or confidence intervals", "subjects": rows}


def markdown(report: dict) -> str:
    lines = ["# Agent evaluation report", "", f"Campaign: `{report['campaign_digest']}`", "",
             "Caller-supplied records; **not execution attestation**. Descriptive only; no ranking.", "",
             "| Subject | Stratum | Passed / scheduled | Eligible | Missing | Cost / success (USD) |",
             "| --- | --- | --- | --- | --- | --- |"]
    for row in report["subjects"]:
        cost = row["cost_per_success_usd"]
        cost_text = "unavailable" if cost is None else f"{cost:.6f}"
        lines.append(f"| {row['subject_id']} | {row['stratum']} | {row['passed']} / {row['scheduled']} | "
                     f"{row['eligible']} | {row['missing']} | {cost_text} |")
    lines += ["", f"Diagnostic reruns excluded: {report['diagnostic_reruns_excluded']}.", "",
              "Definitions, metric authority/coverage, failure distributions and reliability groups are in the JSON report.", ""]
    return "\n".join(lines)
