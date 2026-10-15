import copy
import hashlib
import io
import json
import os
import shutil
import stat
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from pydantic import ValidationError

from agent_evals.cli import SCHEMAS, main
from agent_evals.models import Artifact, Campaign, CampaignLock, TrialRecord
from agent_evals.planner import digest, plan, request_execution, snapshot, trial_plans, verify_inputs, verify_lock
from agent_evals.report import markdown, summarize
from agent_evals.store import RecordStore, validate_records, write_new

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = "sha256:" + "1" * 64


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / "examples/offline", self.root / "inputs")
        self.inputs = self.root / "inputs"
        self.raw = json.loads((self.inputs / "campaign.json").read_text())
        self.campaign = self.parse(self.raw)
        self.lock = plan(self.campaign, self.inputs)
        self.slots = trial_plans(self.lock)

    @staticmethod
    def parse(value):
        return Campaign.model_validate_json(json.dumps(value))

    def record(self, slot=None, **changes):
        values = {"trial_id": str(uuid4()), "plan": (slot or self.slots[0]).model_dump(mode="json"),
                  "execution": "completed", "validity": "valid", "outcome": "passed", "cleanup": "succeeded",
                  "grading_authority": "controller", "grading_evidence": EVIDENCE,
                  "evidence": {"grading": EVIDENCE, "configuration": EVIDENCE, "isolation": EVIDENCE}}
        values.update(changes)
        return TrialRecord.model_validate_json(json.dumps(values))

    def metric(self, value, unit="USD", **changes):
        result = {"value": value, "unit": unit, "authority": "provider", "coverage": "complete",
                  "definition": "subject total including children and retries; deduplicated", "evidence": EVIDENCE}
        result.update(changes)
        return result

    def test_determinism_and_round_trip(self):
        self.assertEqual(plan(self.campaign, self.inputs), self.lock)
        verify_inputs(self.lock, self.inputs)
        restored = CampaignLock.model_validate_json(self.lock.model_dump_json())
        self.assertEqual(trial_plans(restored), self.slots)
        self.assertEqual(len(self.slots), 4)
        self.assertEqual([s.subject_id for s in self.slots], ["fixture-base", "fixture-variant"] * 2)
        self.assertEqual(len({s.slot_digest for s in self.slots}), 4)

    def test_profile_and_content_identity(self):
        self.assertNotEqual(*self.lock.payload.subject_digests.values())
        changed = copy.deepcopy(self.raw)
        changed["subjects"][0]["version"] = "0.0.1-fixture"
        self.assertNotEqual(plan(self.parse(changed), self.inputs).digest, self.lock.digest)
        (self.inputs / "candidate/instructions.md").write_text("changed")
        with self.assertRaisesRegex(ValueError, "drift"):
            verify_inputs(self.lock, self.inputs)

    def test_lock_tampering(self):
        values = self.lock.model_dump(mode="json")
        values["payload"]["campaign"]["repetitions"] = 4
        with self.assertRaisesRegex(ValueError, "digest"):
            verify_lock(CampaignLock.model_validate_json(json.dumps(values)))

    def test_resealed_inconsistent_identity(self):
        values = self.lock.model_dump(mode="json")
        values["payload"]["subject_digests"]["fixture-base"] = EVIDENCE
        values["digest"] = digest(values["payload"])
        with self.assertRaisesRegex(ValueError, "identity"):
            verify_lock(CampaignLock.model_validate_json(json.dumps(values)))

    def test_resealed_blocker_removal_rejected(self):
        values = self.lock.model_dump(mode="json")
        values["payload"]["blockers"] = []
        values["digest"] = digest(values["payload"])
        with self.assertRaisesRegex(ValueError, "blockers"):
            verify_lock(CampaignLock.model_validate_json(json.dumps(values)))

    def test_bad_shared_fields(self):
        for path, value in [("version", "latest"), ("version", "^1.0"), ("unexpected", True),
                            ("native_settings", {"model": "other"}), ("native_settings", {"hindsight_url": "http://host"}),
                            ("native_settings", {"api_key": "secret"})]:
            with self.subTest(path=path, value=value):
                raw = copy.deepcopy(self.raw)
                raw["subjects"][0][path] = value
                with self.assertRaises(ValidationError):
                    self.parse(raw)

    def test_embedded_credentials(self):
        for settings in ({"api_key": "value"}, {"nested": {"Authorization": "value"}},
                         {"value": "Bearer example-secret"}):
            raw = copy.deepcopy(self.raw)
            raw["subjects"][0]["model"]["settings"] = settings
            with self.assertRaises(ValidationError):
                self.parse(raw)

    def test_incomplete_opencode(self):
        raw = copy.deepcopy(self.raw)
        raw["subjects"][0]["harness"] = "opencode-v2"
        with self.assertRaisesRegex(ValidationError, "primary agent"):
            self.parse(raw)

    def test_complete_opencode_is_still_blocked(self):
        raw = copy.deepcopy(self.raw)
        subject = raw["subjects"][0]
        subject["harness"] = "opencode-v2"
        subject["primary_agent"] = {"id": "builder", "model": subject["model"], "instructions_component": "instructions"}
        lock = plan(self.parse(raw), self.inputs)
        self.assertIn("harbor-opencode-v2-adapter-unverified", lock.payload.blockers)
        with self.assertRaisesRegex(ValueError, "execution is unavailable"):
            request_execution(lock)

    def test_memory_policy(self):
        raw = copy.deepcopy(self.raw)
        raw["subjects"][0]["memory"] = {"mode": "trial-private", "service_image": "hindsight:latest"}
        with self.assertRaises(ValidationError):
            self.parse(raw)
        raw["subjects"][0]["memory"] = {"mode": "trial-private", "service_image": "fixture/hindsight@sha256:" + "2" * 64,
                                       "seed": {"path": "workspace"}}
        with self.assertRaisesRegex(ValidationError, "audit"):
            self.parse(raw)
        raw["subjects"][0]["memory"]["seed_audit"] = {"path": "grader/criteria.json"}
        lock = plan(self.parse(raw), self.inputs)
        self.assertIn("subject/fixture-base/memory/seed", lock.payload.snapshots)

    def test_path_confinement(self):
        for path in ("../outside", "/etc/passwd", "a/../b", "a//b", "a\\b", "a\nb"):
            with self.subTest(path=path), self.assertRaises(ValidationError):
                Artifact(path=path)
        (self.inputs / "alias").symlink_to(self.inputs / "candidate", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            snapshot(self.inputs, Artifact(path="alias/instructions.md"))
        (self.inputs / "candidate/link").symlink_to(self.inputs / "workspace/input.txt")
        with self.assertRaisesRegex(ValueError, "symlink"):
            snapshot(self.inputs, Artifact(path="candidate"))

    def test_snapshot_limits_and_special_files(self):
        path = self.inputs / "large"
        with path.open("wb") as stream:
            stream.truncate(64 * 1024 * 1024 + 1)
        with self.assertRaisesRegex(ValueError, "limit"):
            snapshot(self.inputs, Artifact(path="large"))
        os.mkfifo(self.inputs / "pipe")
        with self.assertRaisesRegex(ValueError, "regular"):
            snapshot(self.inputs, Artifact(path="pipe"))

    def test_expected_digest(self):
        with self.assertRaisesRegex(ValueError, "mismatch"):
            snapshot(self.inputs, Artifact(path="workspace", expected_digest=EVIDENCE))

    def test_directory_only_snapshot_limit(self):
        directory = self.inputs / "many"
        directory.mkdir()
        # Mock the streamed enumeration to avoid creating thousands of real directories.
        class Entry:
            name = "directory"
        class Entries:
            def __enter__(self):
                return iter([Entry()] * 10001)
            def __exit__(self, *args):
                return False
        with patch("agent_evals.planner.os.scandir", return_value=Entries()):
            with self.assertRaisesRegex(ValueError, "10000"):
                snapshot(self.inputs, Artifact(path="many"))

    def test_ancestor_symlink_replacement_cannot_escape(self):
        directory = self.inputs / "transient"
        directory.mkdir()
        (directory / "data.txt").write_text("inside")
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "data.txt").write_text("outside-secret")
        original_open = os.open
        def replace_after_directory_open(path, flags, *args, **kwargs):
            descriptor = original_open(path, flags, *args, **kwargs)
            if path == "transient":
                directory.rename(self.inputs / "detached")
                directory.symlink_to(outside, target_is_directory=True)
            return descriptor
        with patch("agent_evals.planner.os.open", side_effect=replace_after_directory_open):
            result = snapshot(self.inputs, Artifact(path="transient/data.txt"))
        self.assertEqual(result.entries[0].digest, "sha256:" + hashlib.sha256(b"inside").hexdigest())

    def test_deep_snapshot_limit(self):
        directory = self.inputs / "deep"
        directory.mkdir()
        current = directory
        for _ in range(66):
            current /= "d"
            current.mkdir()
        with self.assertRaisesRegex(ValueError, "directory levels"):
            snapshot(self.inputs, Artifact(path="deep"))

    def test_model_native_effort_conflict(self):
        raw = copy.deepcopy(self.raw)
        raw["subjects"][0]["model"]["settings"] = {"reasoning_effort": "high"}
        raw["subjects"][0]["native_settings"] = {"effort": "low"}
        with self.assertRaisesRegex(ValidationError, "overlap"):
            self.parse(raw)

    def test_destination_collision(self):
        raw = copy.deepcopy(self.raw)
        component = copy.deepcopy(raw["subjects"][0]["components"][0])
        component.update(id="other", destination="AGENTS.md/subfile")
        raw["subjects"][0]["components"].append(component)
        with self.assertRaisesRegex(ValidationError, "overlap"):
            self.parse(raw)

    def test_scored_and_shared_verification_rejected(self):
        raw = copy.deepcopy(self.raw)
        raw["policy"]["intent"] = "scored"
        with self.assertRaisesRegex(ValueError, "scored planning"):
            plan(self.parse(raw), self.inputs)
        (self.inputs / "task/task.toml").write_text('[verifier]\nenvironment_mode = "shared"\n')
        with self.assertRaisesRegex(ValueError, "separate"):
            plan(self.campaign, self.inputs)

    def test_malformed_verifier_table(self):
        (self.inputs / "task/task.toml").write_text('verifier = "separate"\n')
        with self.assertRaisesRegex(ValueError, "separate"):
            plan(self.campaign, self.inputs)

    def test_network_authoring(self):
        for host in ("http://localhost:8888", "127.0.0.1", "memory.local", "*.example.com"):
            raw = copy.deepcopy(self.raw)
            raw["policy"]["provider_hosts"] = [host]
            with self.subTest(host=host), self.assertRaises(ValidationError):
                self.parse(raw)

    def test_planning_has_no_external_effects(self):
        with patch("subprocess.Popen", side_effect=AssertionError("process")), patch("socket.socket", side_effect=AssertionError("network")):
            self.assertEqual(plan(self.campaign, self.inputs), self.lock)
            with self.assertRaises(ValueError):
                request_execution(self.lock)

    def test_records_binding_and_original_uniqueness(self):
        first = self.record()
        with self.assertRaisesRegex(ValueError, "multiple original"):
            validate_records(self.lock, [first, self.record()])
        bad = first.model_dump(mode="json")
        bad["plan"]["repeat"] = 99
        with self.assertRaisesRegex(ValueError, "planned"):
            validate_records(self.lock, [TrialRecord.model_validate_json(json.dumps(bad))])
        rerun = self.record(purpose="diagnostic", retry_of=first.trial_id)
        validate_records(self.lock, [first, rerun])
        with self.assertRaisesRegex(ValueError, "original execution"):
            validate_records(self.lock, [rerun])

    def test_record_authority_required_evidence_and_units(self):
        with self.assertRaises(ValidationError):
            self.record(grading_authority="subject")
        with self.assertRaisesRegex(ValueError, "mandatory"):
            validate_records(self.lock, [self.record(evidence={"grading": EVIDENCE})])
        with self.assertRaises(ValidationError):
            self.record(metrics={"subject_cost": self.metric(1, unit="tokens")})
        with self.assertRaises(ValidationError):
            self.record(metrics={"subject_cost": self.metric(1, authority="estimate")})

    def test_exclusive_private_storage(self):
        store = RecordStore(self.root / "private", self.lock)
        record = self.record()
        path = store.append(record)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.root / "private").stat().st_mode), 0o700)
        self.assertEqual(store.read(), [record])
        with self.assertRaises(ValueError):
            store.append(record)
        path.chmod(0o644)
        with self.assertRaises(ValueError):
            store.read()

    def test_concurrent_original_append(self):
        store = RecordStore(self.root / "private", self.lock)
        def append():
            try:
                store.append(self.record())
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(lambda _: append(), range(2))), 1)
        self.assertEqual(len(store.read()), 1)

    def test_store_symlink_and_immutable_lock_output(self):
        (self.root / "alias").symlink_to(self.inputs, target_is_directory=True)
        with self.assertRaises(ValueError):
            write_new(self.root / "alias" / "lock.json", {})
        output = self.root / "private" / "lock.json"
        write_new(output, self.lock.model_dump(mode="json"))
        with self.assertRaises(FileExistsError):
            write_new(output, {})

    def test_report_denominators_and_diagnostic_exclusion(self):
        first = self.record()
        failure = self.record(self.slots[2], outcome="failed")
        rerun = self.record(purpose="diagnostic", retry_of=first.trial_id)
        report = summarize(self.lock, [first, failure, rerun])
        row = report["subjects"][0]
        self.assertEqual((row["scheduled"], row["eligible"], row["passed"]), (2, 2, 1))
        self.assertEqual(row["pass_rate_valid_graded"], .5)
        self.assertEqual(row["reliability"]["pass_at_k_assessable"], 1)
        self.assertEqual(row["reliability"]["pass_all_k_assessable"], 0)
        self.assertEqual(report["diagnostic_reruns_excluded"], 1)
        self.assertEqual(report["subjects"][1]["missing"], 2)
        self.assertIsNone(report["subjects"][1]["pass_rate_valid_graded"])

    def test_invalid_pass_and_cleanup_failure(self):
        records = [self.record(validity="contaminated", reasons=["shared-memory"]),
                   self.record(self.slots[2], cleanup="failed")]
        row = summarize(self.lock, records)["subjects"][0]
        self.assertEqual(row["passed"], 0)
        self.assertEqual(row["eligible"], 0)
        self.assertEqual(row["reliability"]["unassessable_groups"], 1)
        self.assertEqual(row["reliability"]["pass_at_k_scheduled_operational"], 0)

    def test_cost_includes_failed_spend_and_missing_is_not_zero(self):
        first = self.record(metrics={"subject_cost": self.metric(2)})
        failure = self.record(self.slots[2], outcome="failed", metrics={"subject_cost": self.metric(3)})
        row = summarize(self.lock, [first, failure])["subjects"][0]
        self.assertEqual(row["cost_per_success_usd"], 5)
        row = summarize(self.lock, [first])["subjects"][0]
        self.assertFalse(row["subject_cost_coverage_complete"])
        self.assertIsNone(row["cost_per_success_usd"])
        self.assertEqual(row["known_subject_cost_usd"], 2)
        self.assertIn("unavailable", markdown(summarize(self.lock, [first])))

    def test_partial_cost_and_mixed_definitions(self):
        first = self.record(metrics={"subject_cost": self.metric(2, coverage="partial")})
        second = self.record(self.slots[2], metrics={"subject_cost": self.metric(3)})
        self.assertIsNone(summarize(self.lock, [first, second])["subjects"][0]["cost_per_success_usd"])
        first = self.record(metrics={"subject_cost": self.metric(2, definition="different scope")})
        row = summarize(self.lock, [first, second])["subjects"][0]
        self.assertEqual(len(row["metrics"]["subject_cost"]), 2)
        self.assertIsNone(row["known_subject_cost_usd"])
        self.assertIsNone(row["cost_per_success_usd"])

    def test_token_categories_not_blindly_summed_and_latency_censored(self):
        first = self.record(execution="timeout", outcome="failed", metrics={
            "input_tokens": self.metric(100, unit="tokens", definition="input includes cached tokens"),
            "cache_read_tokens": self.metric(80, unit="tokens", definition="subset of input"),
            "agent_wall": self.metric(60, unit="seconds", authority="controller", definition="task wall time")})
        row = summarize(self.lock, [first])["subjects"][0]
        self.assertNotIn("total_tokens", row["metrics"])
        self.assertEqual(row["agent_wall_censored_trials"], 1)
        self.assertEqual(row["successful_metrics"]["agent_wall"], [])

    def test_holdout_not_mixed(self):
        raw = copy.deepcopy(self.raw)
        task = copy.deepcopy(raw["tasks"][0])
        task.update(id="heldout", stratum="holdout")
        raw["tasks"].append(task)
        lock = plan(self.parse(raw), self.inputs)
        rows = summarize(lock, [])["subjects"]
        self.assertEqual(len(rows), 4)
        self.assertEqual({row["stratum"] for row in rows}, {"tuning", "holdout"})

    def test_many_subjects_and_tasks(self):
        for subject_count, task_count in ((200, 1), (1, 200), (30, 30)):
            with self.subTest(subjects=subject_count, tasks=task_count):
                raw = copy.deepcopy(self.raw)
                raw["subjects"] = [dict(copy.deepcopy(raw["subjects"][0]), id=f"subject-{i}") for i in range(subject_count)]
                raw["tasks"] = [dict(copy.deepcopy(raw["tasks"][0]), id=f"task-{i}") for i in range(task_count)]
                lock = plan(self.parse(raw), self.inputs)
                rows = summarize(lock, [])["subjects"]
                self.assertEqual(len(rows), subject_count)
                self.assertEqual(sum(row["scheduled"] for row in rows), subject_count * task_count * 2)
                self.assertTrue(all(len(row["tasks"]) == task_count for row in rows))

    def test_cli_workflow_and_redaction(self):
        output = self.root / "private" / "lock.json"
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["validate", str(self.inputs / "campaign.json")]), 0)
            self.assertEqual(main(["plan", str(self.inputs / "campaign.json"), "--out", str(output)]), 0)
            self.assertEqual(main(["verify", str(output), "--root", str(self.inputs)]), 0)
            self.assertEqual(main(["slots", str(output)]), 0)
            record_file = self.root / "record.json"
            record_file.write_text(self.record().model_dump_json())
            self.assertEqual(main(["ingest", str(output), str(record_file), "--store", str(self.root / "records")]), 0)
            self.assertEqual(main(["report", str(output), "--store", str(self.root / "records")]), 0)
        with redirect_stderr(io.StringIO()) as error:
            self.assertEqual(main(["run", str(output)]), 2)
        self.assertIn("execution is unavailable", error.getvalue())
        raw = copy.deepcopy(self.raw)
        raw["subjects"][0]["model"]["settings"] = {"api_key": "sensitive-do-not-print"}
        (self.root / "bad.json").write_text(json.dumps(raw))
        with redirect_stderr(io.StringIO()) as error:
            self.assertEqual(main(["validate", str(self.root / "bad.json")]), 2)
        self.assertNotIn("sensitive-do-not-print", error.getvalue())

    def test_generated_schemas(self):
        for name, model in SCHEMAS.items():
            with self.subTest(name=name):
                stored = json.loads((ROOT / "schemas" / f"{name}.v1.schema.json").read_text())
                self.assertEqual(stored, model.model_json_schema())


if __name__ == "__main__":
    unittest.main()
