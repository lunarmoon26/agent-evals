"""Explicitly approved no-inference network controls; offline contracts/launcher."""

import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import Field, model_validator

from .controls import CONTROL_IMAGE, HARBOR_REVISION, bytes_digest, compose_spec, inspect_isolation
from .models import Contract, Digest
from .store import private_directory, write_new

NetworkCase = Literal["auth-route", "agent-route", "proxy-stop", "timeout"]
NETWORK_CASES = ("auth-route", "agent-route", "proxy-stop", "timeout")
SOURCES = ("egress_proxy.py", "network_probe.py", "network_controls.py", "harbor_network_controls.py")


class NetworkControlRequest(Contract):
    schema_version: Literal[1] = 1
    run_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
    approval: Literal["socket-proxy-network-controls-v1"]
    harbor_revision: Literal["09f5b964f8e5075f7e52fd08c9994dfc80ad5d24"] = HARBOR_REVISION
    base_image: Literal["python@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1"] = CONTROL_IMAGE
    worker_lock_digest: Digest
    sources_digest: Digest
    cases: tuple[Literal["auth-route"], Literal["agent-route"], Literal["proxy-stop"], Literal["timeout"]] = NETWORK_CASES
    cpus: Literal[1] = 1
    memory_mb: Literal[512] = 512
    max_test_seconds: Literal[60] = 60


class NetworkControlReceipt(Contract):
    case: NetworkCase
    control_passed: bool
    execution: Literal["completed", "timeout", "error", "cancelled"]
    wall_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    cleanup_succeeded: bool
    evidence_digest: Digest
    error: str | None = None


class NetworkControlBatch(Contract):
    purpose: Literal["network-controls-not-harness-evaluation"] = "network-controls-not-harness-evaluation"
    request: NetworkControlRequest
    image: Digest
    receipts: list[NetworkControlReceipt]

    @model_validator(mode="after")
    def binding(self) -> "NetworkControlBatch":
        if [r.case for r in self.receipts] != list(self.request.cases[:len(self.receipts)]):
            raise ValueError("network receipts do not bind the requested ordered cases")
        return self


def source_digest(root: Path) -> str:
    from .planner import digest
    return digest({name: bytes_digest((root / "src/agent_evals" / name).read_bytes()) for name in SOURCES})


def network_compose(image_id: str, project: str, hosts: tuple[str, ...]) -> dict:
    if not project.startswith("ae-controls-net-") or hosts not in {("auth.openai.com",), ("chatgpt.com",)}:
        raise ValueError("unsupported network-control project/phase")
    if not image_id.startswith("sha256:") or len(image_id) != 71 or any(c not in "0123456789abcdef" for c in image_id[7:]):
        raise ValueError("network controls require an exact local image identity")
    result = compose_spec(CONTROL_IMAGE, project)
    subject = result["services"]["main"]
    subject["image"] = image_id
    subject["command"] = ["sleep", "60"]
    subject["volumes"].append("egress:/egress:ro")
    proxy = {key: value for key, value in subject.items() if key not in {"environment", "working_dir", "network_mode", "volumes"}}
    proxy.update({"user": "0:0", "volumes": ["egress:/egress"],
                  "command": ["python", "/opt/egress_proxy.py", "--lifetime", "60", "/egress/provider.sock", *hosts],
                  "networks": ["provider"]})
    result["services"]["proxy"] = proxy
    result["volumes"]["egress"] = {"labels": {"agent-evals.control-project": project}}
    result["networks"] = {"provider": {"labels": {"agent-evals.control-project": project}}}
    return result


def inspect_subject(container: dict, project: str, image_id: str):
    # Validate the added RO socket volume, then reuse the established main profile guard.
    mounts = container.get("Mounts", [])
    sockets = [mount for mount in mounts if mount.get("Destination") == "/egress"]
    if len(sockets) != 1 or sockets[0].get("Type") != "volume" or sockets[0].get("Name") != project + "_egress" or sockets[0].get("RW") is not False:
        raise ValueError("subject socket volume is not trial-private/read-only")
    bindings = container.get("HostConfig", {}).get("Binds") or []
    expected_socket = project + "_egress:/egress:ro"
    if expected_socket not in bindings:
        raise ValueError("missing read-only socket binding")
    narrowed = {**container, "Mounts": [mount for mount in mounts if mount.get("Destination") != "/egress"],
                "HostConfig": {**container["HostConfig"], "Binds": [binding for binding in bindings if binding != expected_socket]}}
    inspect_isolation(narrowed, project, image_id, expected_image=image_id)


def inspect_proxy(container: dict, project: str, image_id: str):
    host, config = container.get("HostConfig", {}), container.get("Config", {})
    mounts = container.get("Mounts", [])
    if not (host.get("NetworkMode") == project + "_provider" and host.get("Privileged") is False
            and host.get("ReadonlyRootfs") is True and host.get("CapDrop") == ["ALL"] and not host.get("CapAdd")
            and "no-new-privileges:true" in host.get("SecurityOpt", []) and host.get("PidsLimit") == 64
            and host.get("Memory") == 512 * 1024 * 1024 and host.get("NanoCpus") == 1_000_000_000
            and not host.get("PortBindings") and not host.get("Devices") and not host.get("DeviceRequests")
            and host.get("PidMode") != "host" and host.get("IpcMode") != "host"
            and config.get("User") == "0:0" and config.get("Image") == image_id and container.get("Image") == image_id
            and config.get("Labels", {}).get("agent-evals.control-project") == project
            and set(container.get("NetworkSettings", {}).get("Networks", {})) == {project + "_provider"}
            and host.get("Binds") == [project + "_egress:/egress:rw"]
            and len(mounts) == 1 and mounts[0].get("Type") == "volume"
            and mounts[0].get("Name") == project + "_egress" and mounts[0].get("Destination") == "/egress"
            and mounts[0].get("RW") is True):
        raise ValueError("effective proxy authority differs from the approved profile")


def emergency_cleanup(output: Path, request: NetworkControlRequest, environment: dict):
    receipts = {}
    for case in request.cases:
        folder = output / case
        claim = folder / "claimed.json"
        if not claim.exists():
            continue
        project = f"ae-controls-net-{request.run_id}-{case}"
        try:
            if claim.is_symlink() or (folder / "docker-compose.yaml").is_symlink():
                raise ValueError("symlink cleanup claim")
            value = json.loads(claim.read_bytes())
            if value.get("project") != project or set(value) != {"project", "image"}:
                raise ValueError("invalid cleanup claim")
            hosts = ("auth.openai.com",) if case == "auth-route" else ("chatgpt.com",)
            if json.loads((folder / "docker-compose.yaml").read_bytes()) != network_compose(value["image"], project, hosts):
                raise ValueError("cleanup compose drift")
            result = subprocess.run(["docker", "compose", "--project-name", project, "-f", str((folder / "docker-compose.yaml").absolute()),
                                     "down", "--volumes"], env=environment, capture_output=True, timeout=60)
            receipts[case] = {"return_code": result.returncode, "stderr": result.stderr.decode(errors="replace")[:2000]}
        except Exception as error:
            receipts[case] = {"error": str(error)[:2000]}
    write_new(output / "emergency-cleanup.json", receipts)


def check_result(result: dict, request: NetworkControlRequest, output: Path, environment: dict):
    batch = NetworkControlBatch.model_validate_json(json.dumps(result))
    if any(not receipt.cleanup_succeeded for receipt in batch.receipts):
        emergency_cleanup(output, request, environment)
    if batch.request != request or [r.case for r in batch.receipts] != list(request.cases) or not all(r.control_passed for r in batch.receipts):
        raise ValueError(f"network controls did not all pass; private evidence: {output}")


def launch_network_controls(root: Path, destination: Path, approved: bool) -> Path:
    if not approved:
        raise ValueError("network controls require --approve-network-controls; no container is launched")
    worker = root / "worker/.venv/bin/python"
    if not worker.is_file():
        raise ValueError("install the pinned worker environment first")
    request = NetworkControlRequest(run_id=uuid4().hex, approval="socket-proxy-network-controls-v1",
                                    worker_lock_digest=bytes_digest((root / "worker/uv.lock").read_bytes()),
                                    sources_digest=source_digest(root))
    output = destination / request.run_id
    private_directory(destination)
    output.mkdir(mode=0o700)
    write_new(output / "request.json", request.model_dump(mode="json"))
    for name in ("worker-home", "tmp"):
        private_directory(output / name)
    environment = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "LANG": "C.UTF-8",
                   "HOME": str((output / "worker-home").absolute()), "DOCKER_CONFIG": str((output / "worker-home/.docker").absolute()),
                   "TMPDIR": str((output / "tmp").absolute()), "LITELLM_LOCAL_MODEL_COST_MAP": "True"}
    interrupted = False
    def interrupt(signum, frame):
        nonlocal interrupted
        interrupted = True
    previous = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGINT)}
    for number in previous:
        signal.signal(number, interrupt)
    try:
        with (output / "worker.log").open("xb") as log:
            os.chmod(log.name, 0o600)
            process = subprocess.Popen([str(worker), "-m", "agent_evals.harbor_network_controls", str(output.absolute())],
                                       env=environment, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                deadline = time.monotonic() + 900
                while True:
                    if interrupted:
                        raise InterruptedError("network control launcher interrupted")
                    if time.monotonic() >= deadline:
                        raise TimeoutError("network control worker exceeded total deadline")
                    try:
                        code = process.wait(timeout=1)
                        break
                    except subprocess.TimeoutExpired:
                        pass
            except BaseException:
                try:
                    if process.poll() is None:
                        process.send_signal(signal.SIGTERM)
                        process.wait(timeout=120)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                finally:
                    emergency_cleanup(output, request, environment)
                raise
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
    if code:
        emergency_cleanup(output, request, environment)
        raise ValueError(f"network worker failed; inspect private evidence: {output}")
    result = json.loads((output / "result.json").read_bytes())
    check_result(result, request, output, environment)
    return output
