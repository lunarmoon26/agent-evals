"""Local resolution only. Never import a candidate or invoke a runner."""

import hashlib
import json
import os
import stat
import tomllib
from contextlib import contextmanager
from pathlib import Path

from .models import Artifact, Campaign, CampaignLock, FileEntry, LockPayload, Snapshot, TrialPlan


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


@contextmanager
def open_confined(root: Path, relative: str):
    """Anchor traversal in directory descriptors; no intermediate symlink follows."""
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = relative.split("/")
        for index, part in enumerate(parts):
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if index < len(parts) - 1:
                flags |= os.O_DIRECTORY
            try:
                child = os.open(part, flags, dir_fd=descriptor)
            except OSError as error:
                raise ValueError(f"missing artifact or symlink/non-directory traversal: {relative}") from error
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def snapshot(root: Path, artifact: Artifact) -> Snapshot:
    entries: list[FileEntry] = []
    total = 0

    def check_name(relative: str) -> None:
        if any(part == ".git" or part == ".env" or part.startswith(".env.") for part in relative.split("/")):
            raise ValueError("Git metadata and environment credential files cannot be snapshotted")

    def collect(descriptor: int, relative: str, info: os.stat_result) -> None:
        nonlocal total
        if len(entries) >= 10000:
            raise ValueError("artifact exceeds 10000 entries")
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("only regular files/directories can be snapshotted")
        if info.st_size > 64 * 1024 * 1024 or total + info.st_size > 64 * 1024 * 1024:
            raise ValueError("artifact exceeds the 64 MiB snapshot limit")
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            contents = stream.read(64 * 1024 * 1024 + 1)
            after = os.fstat(stream.fileno())
        if len(contents) != info.st_size or (after.st_size, after.st_mtime_ns) != (info.st_size, info.st_mtime_ns):
            raise ValueError("artifact changed while snapshotting")
        total += len(contents)
        entries.append(FileEntry(path=relative, kind="file", digest="sha256:" + hashlib.sha256(contents).hexdigest(),
                                 size=len(contents), executable=bool(info.st_mode & 0o111)))

    def walk(descriptor: int, prefix: str) -> None:
        if prefix.count("/") >= 64:
            raise ValueError("artifact exceeds 64 directory levels")
        before = os.fstat(descriptor)
        # scandir is streamed and capped before sorting, including directory-only trees.
        with os.scandir(descriptor) as iterator:
            names = []
            for entry in iterator:
                if len(names) + len(entries) >= 10000:
                    raise ValueError("artifact exceeds 10000 entries")
                names.append(entry.name)
        for name in sorted(names):
            relative = f"{prefix}/{name}" if prefix else name
            check_name(relative)
            info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                raise ValueError("symlinks are forbidden")
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise ValueError("only regular files/directories can be snapshotted")
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if stat.S_ISDIR(info.st_mode):
                flags |= os.O_DIRECTORY
            try:
                child = os.open(name, flags, dir_fd=descriptor)
            except OSError as error:
                raise ValueError("artifact changed or symlink encountered while snapshotting") from error
            try:
                opened = os.fstat(child)
                if (opened.st_dev, opened.st_ino, opened.st_mode) != (info.st_dev, info.st_ino, info.st_mode):
                    raise ValueError("artifact changed while snapshotting")
                if stat.S_ISDIR(info.st_mode):
                    if len(entries) >= 10000:
                        raise ValueError("artifact exceeds 10000 entries")
                    entries.append(FileEntry(path=relative, kind="directory"))
                    walk(child, relative)
                else:
                    collect(child, relative, info)
            finally:
                os.close(child)
        if os.fstat(descriptor).st_mtime_ns != before.st_mtime_ns:
            raise ValueError("directory changed while snapshotting")

    check_name(artifact.path)
    with open_confined(root, artifact.path) as source:
        info = os.fstat(source)
        if stat.S_ISDIR(info.st_mode):
            walk(source, "")
        else:
            collect(source, artifact.path.split("/")[-1], info)
    entries.sort(key=lambda entry: entry.path)
    identity = digest([entry.model_dump(mode="json") for entry in entries])
    if artifact.expected_digest and artifact.expected_digest != identity:
        raise ValueError(f"artifact digest mismatch: {artifact.path}")
    return Snapshot(source=artifact.path, digest=identity, entries=entries)


def references(campaign: Campaign) -> dict[str, Artifact]:
    refs: dict[str, Artifact] = {}
    for subject in campaign.subjects:
        for component in subject.components:
            refs[f"subject/{subject.id}/component/{component.id}"] = component.artifact
        for key in ("seed", "seed_audit"):
            artifact = getattr(subject.memory, key)
            if artifact:
                refs[f"subject/{subject.id}/memory/{key}"] = artifact
    for task in campaign.tasks:
        for key in ("package", "initial_workspace", "grader"):
            refs[f"task/{task.id}/{key}"] = getattr(task, key)
    for key, artifact in campaign.policy.protocol_artifacts.items():
        refs[f"protocol/{key}"] = artifact
    return refs


def execution_blockers(campaign: Campaign) -> list[str]:
    blockers = ["execution-worker-unavailable", "sandbox-network-memory-isolation-unverified",
                "task-audit-and-pilot-review-unverified", "harness-configuration-conformance-unverified"]
    if any(subject.harness == "opencode-v2" for subject in campaign.subjects):
        blockers.append("harbor-opencode-v2-adapter-unverified")
    return blockers


def artifact_groups(snapshots: dict[str, Snapshot]) -> dict[tuple[str, str], dict[str, str]]:
    groups: dict[tuple[str, str], dict[str, str]] = {}
    for key, value in snapshots.items():
        parts = key.split("/")
        if parts[0] in {"subject", "task"}:
            groups.setdefault((parts[0], parts[1]), {})[key] = value.digest
    return groups


def plan(campaign: Campaign, root: Path) -> CampaignLock:
    root = root.resolve(strict=True)
    if campaign.policy.intent == "scored":
        raise ValueError("scored planning is blocked: runtime conformance/audit gates are not implemented")
    snapshots = {}
    cached = {}
    for key, artifact in references(campaign).items():
        if artifact.path not in cached:
            cached[artifact.path] = snapshot(root, artifact)
        resolved = cached[artifact.path]
        if artifact.expected_digest and artifact.expected_digest != resolved.digest:
            raise ValueError(f"artifact digest mismatch: {artifact.path}")
        snapshots[key] = resolved
    for task in campaign.tasks:
        files = {entry.path: entry for entry in snapshots[f"task/{task.id}/package"].entries if entry.kind == "file"}
        if "task.toml" not in files or "instruction.md" not in files:
            raise ValueError("task package requires Harbor task.toml and instruction.md")
        with open_confined(root, task.package.path + "/task.toml") as descriptor:
            with os.fdopen(os.dup(descriptor), "rb") as stream:
                contents = stream.read(64 * 1024 * 1024 + 1)
        if "sha256:" + hashlib.sha256(contents).hexdigest() != files["task.toml"].digest:
            raise ValueError("task configuration changed during planning")
        config_data = tomllib.loads(contents.decode())
        verifier = config_data.get("verifier")
        if not isinstance(verifier, dict) or verifier.get("environment_mode") != "separate":
            raise ValueError("initial profile requires explicit separate verification in task.toml")
    subject_digests = {}
    grouped = artifact_groups(snapshots)
    for subject in campaign.subjects:
        inputs = grouped.get(("subject", subject.id), {})
        subject_digests[subject.id] = digest({"subject": subject.model_dump(mode="json"), "artifacts": inputs})
    task_digests = {}
    for task in campaign.tasks:
        inputs = grouped.get(("task", task.id), {})
        task_digests[task.id] = digest({"task": task.model_dump(mode="json"), "artifacts": inputs})
    payload = LockPayload(campaign=campaign, snapshots=snapshots, subject_digests=subject_digests,
                          task_digests=task_digests, blockers=execution_blockers(campaign))
    return CampaignLock(digest=digest(payload.model_dump(mode="json")), payload=payload)


def verify_lock(lock: CampaignLock) -> None:
    if lock.digest != digest(lock.payload.model_dump(mode="json")):
        raise ValueError("campaign lock digest mismatch")
    campaign = lock.payload.campaign
    if campaign.policy.intent != "pilot" or lock.payload.blockers != execution_blockers(campaign):
        raise ValueError("lock cannot remove execution/scoring readiness blockers")
    if set(lock.payload.snapshots) != set(references(campaign)):
        raise ValueError("lock artifact references are inconsistent")
    for key, artifact in references(campaign).items():
        stored = lock.payload.snapshots[key]
        if stored.source != artifact.path or stored.digest != digest([e.model_dump(mode="json") for e in stored.entries]):
            raise ValueError("lock snapshot is inconsistent")
        if artifact.expected_digest and artifact.expected_digest != stored.digest:
            raise ValueError("lock expected artifact digest mismatch")
    grouped = artifact_groups(lock.payload.snapshots)
    for kind, values, identities in (("subject", campaign.subjects, lock.payload.subject_digests),
                                    ("task", campaign.tasks, lock.payload.task_digests)):
        if set(identities) != {value.id for value in values}:
            raise ValueError("lock identities are inconsistent")
        for value in values:
            inputs = grouped.get((kind, value.id), {})
            if identities[value.id] != digest({kind: value.model_dump(mode="json"), "artifacts": inputs}):
                raise ValueError("lock identity digest mismatch")


def verify_inputs(lock: CampaignLock, root: Path) -> None:
    verify_lock(lock)
    rebuilt = plan(lock.payload.campaign, root)
    if rebuilt != lock:
        raise ValueError("input content, version or plan drift detected")


def trial_plans(lock: CampaignLock) -> list[TrialPlan]:
    verify_lock(lock)
    campaign = lock.payload.campaign
    slots = []
    for task in campaign.tasks:
        for repeat in range(campaign.repetitions):
            for subject in campaign.subjects:
                values = {"campaign_digest": lock.digest, "subject_id": subject.id,
                          "subject_digest": lock.payload.subject_digests[subject.id],
                          "task_id": task.id, "task_digest": lock.payload.task_digests[task.id],
                          "repeat": repeat, "reliability_group": repeat // campaign.policy.reliability_k}
                slots.append(TrialPlan(**values, slot_digest=digest(values)))
    return slots


def request_execution(lock: CampaignLock) -> None:
    verify_lock(lock)
    raise ValueError("execution is unavailable: a tested Harbor worker with enforced isolation and explicit run approval is required")
