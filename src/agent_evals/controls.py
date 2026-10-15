"""Approved deterministic controls only; no user agents, commands or endpoints."""

import hashlib
import io
import json
import os
import signal
import subprocess
import tarfile
import time
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import Field, model_validator

from .models import Contract, Digest
from .store import private_directory, write_new

HARBOR_REVISION = "09f5b964f8e5075f7e52fd08c9994dfc80ad5d24"
CONTROL_IMAGE = "python@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1"
ControlCase = Literal["sum-oracle", "sum-noop", "sort-oracle", "sort-noop", "transform-oracle", "transform-noop",
                      "fresh-a", "fresh-b", "timeout", "artifact-symlink", "artifact-oversize"]
CASES = ("sum-oracle", "sum-noop", "sort-oracle", "sort-noop", "transform-oracle", "transform-noop",
         "fresh-a", "fresh-b", "timeout", "artifact-symlink", "artifact-oversize")


class ControlRequest(Contract):
    schema_version: Literal[1] = 1
    run_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
    approval: Literal["local-docker-controls-v1"]
    harbor_revision: Literal["09f5b964f8e5075f7e52fd08c9994dfc80ad5d24"] = HARBOR_REVISION
    image: Literal["python@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1"] = CONTROL_IMAGE
    worker_lock_digest: Digest
    cases: Annotated[list[ControlCase], Field(min_length=1, max_length=12)]
    cpus: Literal[1] = 1
    memory_mb: Literal[512] = 512
    max_agent_seconds: Literal[60] = 60

    @model_validator(mode="after")
    def unique(self) -> "ControlRequest":
        if len(set(self.cases)) != len(self.cases):
            raise ValueError("control cases cannot repeat within a batch")
        return self


class ControlReceipt(Contract):
    case: ControlCase
    control_passed: bool
    execution: Literal["completed", "timeout", "error"]
    task_outcome: Literal["passed", "failed", "ungraded"]
    export_status: Literal["accepted", "missing", "rejected", "unavailable"]
    agent_wall_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    cleanup_succeeded: bool
    evidence_digest: Digest
    error: str | None = None


class ControlBatch(Contract):
    schema_version: Literal[1] = 1
    purpose: Literal["deterministic-boundary-controls-not-harness-evaluation"] = "deterministic-boundary-controls-not-harness-evaluation"
    request: ControlRequest
    receipts: list[ControlReceipt]
    complete: bool

    @model_validator(mode="after")
    def binding(self) -> "ControlBatch":
        cases = [receipt.case for receipt in self.receipts]
        if cases != self.request.cases[:len(cases)] or self.complete != (cases == self.request.cases):
            raise ValueError("control receipts do not bind the requested ordered cases/completeness")
        return self


def bytes_digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def compose_spec(image: str, project: str) -> dict:
    if image != CONTROL_IMAGE or not project.startswith("ae-controls-"):
        raise ValueError("only the fixed control image and scoped projects are permitted")
    return {"services": {"main": {
        "image": image, "pull_policy": "never", "command": ["sleep", "infinity"],
        "user": "1000:1000", "working_dir": "/workspace", "environment": {"HOME": "/home/agent"},
        "network_mode": "none", "read_only": True, "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"], "init": True,
        "cpus": 1, "mem_limit": "512m", "pids_limit": 64,
        "volumes": ["workspace:/workspace", "home:/home/agent"], "tmpfs": ["/tmp:rw,noexec,nosuid,size=16m"],
        "labels": {"agent-evals.control-project": project}
    }}, "volumes": {"workspace": {"labels": {"agent-evals.control-project": project}},
                    "home": {"labels": {"agent-evals.control-project": project}}}}


def inspect_isolation(container: dict, project: str, image_id: str, expected_image: str = CONTROL_IMAGE) -> None:
    host = container.get("HostConfig", {})
    config = container.get("Config", {})
    mounts = container.get("Mounts", [])
    volumes = [mount for mount in mounts if mount.get("Type") == "volume"]
    temporary_mounts = [mount for mount in mounts if mount.get("Type") == "tmpfs"]
    expected_mounts = {"/workspace": project + "_workspace", "/home/agent": project + "_home"}
    expected_bindings = {f"{project}_workspace:/workspace:rw", f"{project}_home:/home/agent:rw"}
    temporary_options = set(host.get("Tmpfs", {}).get("/tmp", "").split(","))
    safe = (
        host.get("NetworkMode") == "none" and host.get("ReadonlyRootfs") is True
        and host.get("Privileged") is False and host.get("NanoCpus") == 1_000_000_000
        and host.get("Memory") == 512 * 1024 * 1024 and host.get("PidsLimit") == 64
        and host.get("CapDrop") == ["ALL"] and not host.get("CapAdd")
        and "no-new-privileges:true" in host.get("SecurityOpt", [])
        # Docker serializes short-syntax named volumes in Binds too; Mounts.Type
        # and exact scoped names distinguish them from forbidden host bind mounts.
        and (not host.get("Binds") or set(host["Binds"]) == expected_bindings)
        and not host.get("PortBindings") and not host.get("Devices")
        and not host.get("DeviceRequests") and host.get("PidMode") != "host"
        and host.get("IpcMode") != "host" and config.get("User") == "1000:1000"
        and config.get("Image") in {expected_image, "docker.io/library/" + expected_image} and container.get("Image") == image_id
        and config.get("Labels", {}).get("agent-evals.control-project") == project
        and len(volumes) == 2 and len(volumes) + len(temporary_mounts) == len(mounts)
        and all(expected_mounts.get(m.get("Destination")) == m.get("Name") for m in volumes)
        and len({m.get("Destination") for m in volumes}) == 2
        and len(temporary_mounts) <= 1
        and all(m.get("Destination") == "/tmp" and not m.get("Source") for m in temporary_mounts)
        and set(host.get("Tmpfs", {})) == {"/tmp"}
        and {"rw", "noexec", "nosuid"} <= temporary_options
        and bool({"size=16m", "size=16777216"} & temporary_options)
        and not container.get("NetworkSettings", {}).get("Ports")
    )
    if not safe:
        raise ValueError("effective Docker isolation differs from the approved profile")


def decode_export(archive: bytes) -> bytes:
    if len(archive) > 65536:
        raise ValueError("output archive exceeds 64 KiB")
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
            members = tar.getmembers()
            if len(members) != 1 or members[0].name not in {"answer.json", "./answer.json"}:
                raise ValueError("output archive contains undeclared paths")
            member = members[0]
            if not member.isfile() or member.size > 4096:
                raise ValueError("output must be one regular file of at most 4 KiB")
            stream = tar.extractfile(member)
            if stream is None:
                raise ValueError("missing archive data")
            data = stream.read(4097)
            if len(data) != member.size:
                raise ValueError("output size mismatch")
            return data
    except tarfile.TarError as error:
        raise ValueError("invalid output archive") from error


def emergency_cleanup(destination: Path, request: ControlRequest, environment: dict) -> None:
    """Fallback for worker cancellation/crash; only recorded, exact trusted projects."""
    results = {}
    for case in request.cases:
        for role in ("subject", "verifier"):
            folder = destination / case / role
            project = f"ae-controls-{request.run_id}-{case}-{role}"
            claim, compose = folder / "claimed.json", folder / "docker-compose.yaml"
            if not claim.exists():
                continue
            try:
                if claim.is_symlink() or compose.is_symlink() or json.loads(claim.read_text()) != {"project": project}:
                    raise ValueError("invalid cleanup claim")
                if json.loads(compose.read_text()) != compose_spec(request.image, project):
                    raise ValueError("cleanup composition drift")
                result = subprocess.run(["docker", "compose", "--project-name", project, "-f", str(compose.absolute()),
                                         "down", "--volumes"], env=environment, capture_output=True, timeout=60)
                results[project] = {"return_code": result.returncode, "stderr": result.stderr.decode(errors="replace")[:2000]}
            except Exception as error:
                results[project] = {"error": str(error)[:2000]}
    write_new(destination / "emergency-cleanup.json", results)


def launch_controls(project_root: Path, output_root: Path, cases: list[str], approved: bool) -> Path:
    if not approved:
        raise ValueError("controls require explicit --approve-local-controls; no container is launched")
    worker_python = project_root / "worker/.venv/bin/python"
    worker_lock = project_root / "worker/uv.lock"
    if not worker_python.is_file() or not worker_lock.is_file():
        raise ValueError("install the pinned worker environment before running controls")
    request = ControlRequest(run_id=uuid4().hex, approval="local-docker-controls-v1",
                             worker_lock_digest=bytes_digest(worker_lock.read_bytes()), cases=cases)
    private_directory(output_root)
    destination = output_root / request.run_id
    destination.mkdir(mode=0o700)
    write_new(destination / "request.json", request.model_dump(mode="json"))
    home = destination / "worker-home"
    private_directory(home)
    temporary = destination / "tmp"
    private_directory(temporary)
    environment = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "LANG": "C.UTF-8",
                   "HOME": str(home.absolute()), "DOCKER_CONFIG": str(home / ".docker"),
                   "TMPDIR": str(temporary.absolute()), "LITELLM_LOCAL_MODEL_COST_MAP": "True"}
    # No provider keys, proxies, Python path overrides or host agent homes are inherited.
    with (destination / "worker.log").open("xb") as log:
        os.chmod(log.name, 0o600)
        interrupted = False
        def interrupt(signum, frame):
            nonlocal interrupted
            interrupted = True  # Do not interrupt spawn or scoped cleanup midway.
        previous = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGINT)}
        for number in previous:
            signal.signal(number, interrupt)
        try:
            process = subprocess.Popen([str(worker_python), "-m", "agent_evals.harbor_controls", str(destination.absolute())],
                                       env=environment, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                deadline = time.monotonic() + 1800
                while True:
                    if interrupted:
                        raise InterruptedError("control launcher interrupted; scoped cleanup attempted")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(process.args, 1800)
                    try:
                        return_code = process.wait(timeout=min(1, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        continue
            except BaseException as failure:
                try:
                    if process.poll() is None:
                        process.send_signal(signal.SIGTERM)
                        process.wait(timeout=180)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                finally:
                    emergency_cleanup(destination, request, environment)
                if isinstance(failure, subprocess.TimeoutExpired):
                    raise ValueError(f"control worker deadline exceeded; private evidence: {destination}") from failure
                raise
        finally:
            for number, handler in previous.items():
                signal.signal(number, handler)
    if return_code:
        emergency_cleanup(destination, request, environment)
        raise ValueError(f"control worker failed; inspect private evidence at {destination}")
    batch = ControlBatch.model_validate_json((destination / "result.json").read_bytes())
    if batch.request != request or not batch.complete or not all(r.control_passed for r in batch.receipts):
        raise ValueError(f"control suite did not pass; inspect private evidence at {destination}")
    return destination
