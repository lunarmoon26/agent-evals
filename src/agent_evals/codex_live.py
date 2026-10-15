"""Single authorized diagnostic smoke; immutable request and isolated worker launch."""

import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import Field

from .codex_smoke import CodexSmokeRequest
from .controls import HARBOR_REVISION, bytes_digest
from .models import Contract, Digest
from .network_controls import NetworkControlBatch, source_digest, network_compose
from .planner import digest
from .store import private_directory, write_new

NATIVE_IMAGE = "sha256:0e10ce839937f6509c7dc6474c509b00f7a3c1261e075003a324879c355d49ec"
NETWORK_BATCH = "fe039c33490641e483f44c04ea181069"
VERSION_BATCH = "version-4ad6f45767ae4a559381225da9fc5b65"
PHASES = {"login": ("auth.openai.com", 900), "agent": ("chatgpt.com", 120), "revoke": ("auth.openai.com", 40)}
SOURCES = ("codex_live.py", "harbor_codex_live.py", "codex_smoke.py", "smoke_evidence.py",
           "controls.py", "harbor_controls.py", "network_controls.py", "harbor_network_controls.py",
           "egress_proxy.py", "planner.py", "store.py", "models.py")


class CodexLiveRequest(Contract):
    schema_version: Literal[1] = 1
    run_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
    purpose: Literal["diagnostic-native-smoke-not-scored"] = "diagnostic-native-smoke-not-scored"
    approval: Literal["one-codex-native-smoke-20261013"]
    profile: CodexSmokeRequest
    harbor_revision: Literal["09f5b964f8e5075f7e52fd08c9994dfc80ad5d24"] = HARBOR_REVISION
    image: Literal["sha256:0e10ce839937f6509c7dc6474c509b00f7a3c1261e075003a324879c355d49ec"] = NATIVE_IMAGE
    worker_lock_digest: Digest
    source_digest: Digest
    conformance_digest: Digest
    network_batch: Literal["fe039c33490641e483f44c04ea181069"] = NETWORK_BATCH
    cli_batch: Literal["version-4ad6f45767ae4a559381225da9fc5b65"] = VERSION_BATCH
    retention: Literal["owner-only-local-no-backup-or-publication"] = "owner-only-local-no-backup-or-publication"
    credential_cleanup: Literal["revoke-original-grant-and-remove-trial-volumes"] = "revoke-original-grant-and-remove-trial-volumes"
    login_seconds: Literal[900] = 900


class CodexLiveResult(Contract):
    schema_version: Literal[1] = 1
    purpose: Literal["diagnostic-native-smoke-not-scored"] = "diagnostic-native-smoke-not-scored"
    run_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
    request_digest: Digest
    execution: Literal["completed", "timeout", "agent-error", "setup-error", "cancelled"]
    agent_started: bool
    task_outcome: Literal["passed", "failed", "ungraded"]
    agent_wall_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    cleanup_succeeded: bool
    revocation: Literal["not-required", "provider-accepted", "failed", "unverified"]
    revocation_followup_required: bool
    evidence_digest: Digest
    cost_usd: None = None
    cost_coverage: Literal["unavailable"] = "unavailable"
    error: str | None = None


def smoke_compose(request: CodexLiveRequest, phase: str) -> dict:
    host, lifetime = PHASES[phase]
    project = f"ae-controls-net-{request.run_id}-smoke"
    spec = network_compose(request.image, project, (host,))
    spec["services"]["main"]["command"] = ["sleep", "1200"]
    spec["services"]["main"]["environment"]["CODEX_HOME"] = "/home/agent/.codex"
    spec["services"]["proxy"]["command"] = ["python", "/opt/egress_proxy.py", "--lifetime", str(lifetime), f"/egress/{phase}.sock", host]
    return spec


def fallback_stop(output: Path, request: CodexLiveRequest, environment: dict):
    # An unconfirmed grant must remain recoverable, but no subject process may
    # survive a forced worker termination. Never delete its credential volume.
    folder = output / "subject"
    claim = folder / "claimed.json"
    receipt = {"revocation_followup_required": True, "credential_volumes_quarantined": True, "stopped": False}
    try:
        if claim.exists():
            if claim.is_symlink() or (folder / "docker-compose.yaml").is_symlink():
                raise ValueError("symlink in cleanup claim")
            project = f"ae-controls-net-{request.run_id}-smoke"
            if json.loads(claim.read_bytes()) != {"project": project, "image": request.image}:
                raise ValueError("cleanup claim mismatch")
            spec = json.loads((folder / "docker-compose.yaml").read_bytes())
            if not any(spec == smoke_compose(request, phase) for phase in PHASES):
                raise ValueError("cleanup compose drift")
            result = subprocess.run(["docker", "compose", "--project-name", project, "-f", str(folder / "docker-compose.yaml"),
                                     "down", "--timeout", "1"], env=environment, capture_output=True, timeout=20)
            remaining = subprocess.run(["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"],
                                       env=environment, capture_output=True, timeout=5)
            receipt["stopped"] = result.returncode == 0 and remaining.returncode == 0 and not remaining.stdout.strip()
        else:
            receipt["stopped"] = True
    except Exception:
        receipt["cleanup_followup_required"] = True
    write_new(output / "forced-stop.json", receipt)


def live_sources(root: Path) -> str:
    return digest({name: bytes_digest((root / "src/agent_evals" / name).read_bytes()) for name in SOURCES})


def check_conformance(root: Path) -> str:
    network_path = root / ".eval/network-controls" / NETWORK_BATCH / "result.json"
    network = NetworkControlBatch.model_validate_json(network_path.read_bytes())
    if network.request.sources_digest != source_digest(root) or len(network.receipts) != 4 or not all(r.control_passed and r.cleanup_succeeded for r in network.receipts):
        raise ValueError("network control closure drift or incomplete evidence")
    folder = root / ".eval/codex-smoke" / VERSION_BATCH
    version = json.loads((folder / "evidence.json").read_bytes())
    if (not version.get("control_passed") or not version.get("cleanup_succeeded") or version.get("image") != NATIVE_IMAGE
            or version.get("proxy_source_digest") != bytes_digest((root / "src/agent_evals/egress_proxy.py").read_bytes())
            or version.get("version", {}).get("stdout") != "codex-cli 0.162.1\n"):
        raise ValueError("CLI/image conformance closure drift")
    # Retain byte identities, not merely caller-provided success labels.
    return digest({"network": bytes_digest(network_path.read_bytes()),
                   "version": bytes_digest((folder / "evidence.json").read_bytes()),
                   "config": bytes_digest((folder / "config.toml").read_bytes())})


def launch_smoke(root: Path, approved: bool) -> Path:
    if not approved:
        raise ValueError("native smoke requires --approve-single-smoke; no login or model call starts")
    worker = root / "worker/.venv/bin/python"
    if not worker.is_file():
        raise ValueError("pinned worker environment is unavailable")
    request = CodexLiveRequest(run_id=uuid4().hex, approval="one-codex-native-smoke-20261013",
                               profile=CodexSmokeRequest(credit_fallback="allowed"),
                               worker_lock_digest=bytes_digest((root / "worker/uv.lock").read_bytes()),
                               source_digest=live_sources(root), conformance_digest=check_conformance(root))
    base = root / ".eval/codex-smoke/live"
    private_directory(base)
    output = base / request.run_id
    output.mkdir(mode=0o700)
    write_new(output / "request.json", request.model_dump(mode="json"))
    for name in ("worker-home", "tmp"):
        private_directory(output / name)
    environment = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "LANG": "C.UTF-8",
                   "HOME": str(output / "worker-home"), "DOCKER_CONFIG": str(output / "worker-home/.docker"),
                   "TMPDIR": str(output / "tmp"), "LITELLM_LOCAL_MODEL_COST_MAP": "True"}
    with (output / "worker.log").open("xb") as log:
        os.chmod(log.name, 0o600)
        process = subprocess.Popen([str(worker), "-m", "agent_evals.harbor_codex_live", str(output)],
                                   env=environment, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    write_new(output / "worker.json", {"pid": process.pid, "run_id": request.run_id})
    # The worker owns a bounded lifecycle after readiness, while the human completes
    # device login. Readiness waits in this foreground invocation, not via tool polling.
    deadline = time.monotonic() + 90
    try:
        while time.monotonic() < deadline:
            if (output / "login-prompt.json").exists():
                return output
            if process.poll() is not None:
                raise ValueError(f"smoke setup stopped before device login; private evidence: {output}")
            time.sleep(0.1)
        raise ValueError(f"device login did not become ready; private evidence: {output}")
    except BaseException:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=90)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                # A forced kill may lose provider revocation; never silently claim it.
                fallback_stop(output, request, environment)
        raise


def wait_smoke(output: Path, seconds: int = 120) -> CodexLiveResult:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if (output / "result.json").exists():
            return CodexLiveResult.model_validate_json((output / "result.json").read_bytes())
        time.sleep(0.2)
    raise ValueError(f"smoke is still waiting/running; private evidence: {output}")
