"""Private exclusive-create storage. Imported records are not execution attestation."""

import json
import fcntl
import os
import stat
from pathlib import Path

from .models import CampaignLock, TrialRecord
from .planner import trial_plans


def private_directory(path: Path) -> None:
    # Check existing ancestors; never traverse a symlink while writing artifacts.
    current = Path(path.anchor) if path.is_absolute() else Path(".")
    for part in path.parts[1:] if path.is_absolute() else path.parts:
        current /= part
        if current.is_symlink():
            raise ValueError("storage cannot traverse symlinks")
        if not current.exists():
            current.mkdir(mode=0o700, exist_ok=True)
    if not path.is_dir() or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError("artifact directory must have owner-only permissions")


def write_new(path: Path, value: object) -> None:
    private_directory(path.parent)
    data = (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)  # Only our newly created, incomplete file.
        raise


def validate_records(lock: CampaignLock, records: list[TrialRecord]) -> None:
    plans = {plan.slot_digest: plan for plan in trial_plans(lock)}
    ids: dict[str, TrialRecord] = {}
    originals: set[str] = set()
    for record in records:
        if record.trial_id in ids:
            raise ValueError("duplicate execution ID")
        ids[record.trial_id] = record
        if plans.get(record.plan.slot_digest) != record.plan:
            raise ValueError("record does not bind a planned campaign slot")
        if record.validity == "valid" and record.outcome != "ungraded":
            required = lock.payload.campaign.policy.required_evidence
            if any(name not in record.evidence for name in required):
                raise ValueError("valid graded record lacks mandatory evidence references")
            if "grading" in required and record.evidence["grading"] != record.grading_evidence:
                raise ValueError("grading evidence references disagree")
        if record.purpose == "original":
            if record.plan.slot_digest in originals:
                raise ValueError("multiple original executions for one planned slot")
            originals.add(record.plan.slot_digest)
    for record in records:
        if record.retry_of is not None:
            parent = ids.get(record.retry_of)
            if parent is None or parent.purpose != "original" or parent.plan != record.plan:
                raise ValueError("diagnostic rerun must reference the original execution of the same slot")


class RecordStore:
    def __init__(self, root: Path, lock: CampaignLock):
        self.lock = lock
        self.root = root / lock.digest.removeprefix("sha256:")

    def read(self) -> list[TrialRecord]:
        if self.root.is_symlink():
            raise ValueError("storage cannot traverse symlinks")
        if not self.root.exists():
            return []
        private_directory(self.root)
        with self._mutex() as stream:
            fcntl.flock(stream, fcntl.LOCK_SH)
            return self._read_unlocked()

    def _mutex(self):
        descriptor = os.open(self.root / ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        if stat.S_IMODE(os.fstat(descriptor).st_mode) & 0o077:
            os.close(descriptor)
            raise ValueError("storage mutex must be private")
        return os.fdopen(descriptor, "r+b")

    def _read_unlocked(self) -> list[TrialRecord]:
        records = []
        for folder in ("originals", "diagnostics"):
            directory = self.root / folder
            if directory.is_symlink():
                raise ValueError("storage cannot traverse symlinks")
            if not directory.exists():
                continue
            private_directory(directory)
            for path in sorted(directory.glob("*.json")):
                if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) & 0o077:
                    raise ValueError("record must be a private regular file")
                record = TrialRecord.model_validate_json(path.read_bytes())
                expected_folder = "originals" if record.purpose == "original" else "diagnostics"
                expected_name = record.plan.slot_digest.removeprefix("sha256:") if folder == "originals" else record.trial_id
                if folder != expected_folder or path.stem != expected_name:
                    raise ValueError("record storage path does not match its identity")
                records.append(record)
        validate_records(self.lock, records)
        return records

    def append(self, record: TrialRecord) -> Path:
        # Original filename is the slot identity: concurrent writes cannot replace it.
        private_directory(self.root)
        with self._mutex() as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            validate_records(self.lock, self._read_unlocked() + [record])
            folder = "originals" if record.purpose == "original" else "diagnostics"
            filename = record.plan.slot_digest.removeprefix("sha256:") if folder == "originals" else record.trial_id
            destination = self.root / folder / (filename + ".json")
            write_new(destination, record.model_dump(mode="json"))
            return destination
