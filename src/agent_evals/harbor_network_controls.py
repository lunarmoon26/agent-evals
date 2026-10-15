"""Pinned Harbor lifecycle for the approved fixed socket-proxy controls."""

import asyncio
import json
import os
import signal
import sys
from pathlib import Path
from time import monotonic

from harbor.models.task.config import EnvironmentConfig
from harbor.models.trial.paths import TrialPaths

from .harbor_controls import ControlDocker, docker, inspect_container, provenance, write_text
from .network_controls import (NetworkControlReceipt, NetworkControlRequest, inspect_proxy,
                               inspect_subject, network_compose, source_digest)
from .planner import digest
from .store import private_directory, write_new

CLEANUP_SECONDS = 15


async def cleanup_project(env, project: str) -> tuple[bool, str | None]:
    try:
        async with asyncio.timeout(CLEANUP_SECONDS):
            await env._run_docker_compose_command(["down", "--volumes", "--timeout", "1"], timeout_sec=10)
            await empty_project(project)
        return True, None
    except asyncio.CancelledError:
        return False, "cleanup cancelled; scoped parent fallback required"
    except Exception as failure:
        return False, f"{type(failure).__name__}: {failure}"[:2000]


async def scoped_id(project: str, role: str) -> str:
    _, output = await docker("ps", "-aq", "--filter", f"label=com.docker.compose.project={project}",
                             "--filter", f"label=com.docker.compose.service={role}")
    ids = output.decode().split()
    if len(ids) != 1:
        raise ValueError("expected one exact project/service container")
    return ids[0]


async def empty_project(project: str):
    for arguments in (("ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"),
                      ("volume", "ls", "-q", "--filter", f"name={project}_"),
                      ("network", "ls", "-q", "--filter", f"name={project}_")):
        _, output = await docker(*arguments)
        if output.strip():
            raise ValueError("project contains existing resources; refuse reuse or cleanup")


async def build_image(output: Path, request: NetworkControlRequest) -> str:
    source = Path(__file__).parent
    build = output / "build"
    private_directory(build)
    for name in ("egress_proxy.py", "network_probe.py"):
        (build / name).write_bytes((source / name).read_bytes())
        (build / name).chmod(0o600)
    write_text(build / "Dockerfile", f"FROM {request.base_image}\nCOPY --chmod=0555 egress_proxy.py network_probe.py /opt/\n")
    iid = output / "image-id.txt"
    code, logs = await docker("build", "--network=none", "--pull=false", "--iidfile", str(iid), str(build))
    write_text(output / "build.log", logs.decode(errors="replace"))
    if code or not iid.exists():
        raise ValueError("fixed control-image assembly failed")
    iid.chmod(0o600)
    image_id = iid.read_text().strip()
    # Validate the exact image syntax before any compose generation.
    network_compose(image_id, "ae-controls-net-validation", ("chatgpt.com",))
    return image_id


async def _trial(output: Path, request: NetworkControlRequest, case: str, image: str) -> NetworkControlReceipt:
    folder = output / case
    private_directory(folder)
    project = f"ae-controls-net-{request.run_id}-{case}"
    hosts = ("auth.openai.com",) if case == "auth-route" else ("chatgpt.com",)
    write_new(folder / "docker-compose.yaml", network_compose(image, project, hosts))
    env = ControlDocker(environment_dir=folder, environment_name=project, session_id=project,
                        trial_paths=TrialPaths(trial_dir=folder / "harbor"),
                        task_env_config=EnvironmentConfig(docker_image=image, cpus=1, memory_mb=512, workdir="/workspace"),
                        mounts=[], enable_environment_dir_upload=False)
    evidence = {"case": case, "project": project, "image": image, "allowed_hosts": hosts,
                "sources_digest": request.sources_digest, "inference_requests": 0}
    claimed = False
    cleanup, passed, execution = True, False, "error"
    elapsed = 0.0
    error = None
    try:
        await empty_project(project)
        write_new(folder / "claimed.json", {"project": project, "image": image})
        claimed = True
        await env.start(force_build=False)
        subject_id, proxy_id = await scoped_id(project, "main"), await scoped_id(project, "proxy")
        subject, proxy = await inspect_container(subject_id), await inspect_container(proxy_id)
        evidence.update({"subject_inspect": subject, "proxy_inspect": proxy})
        inspect_subject(subject, project, image)
        inspect_proxy(proxy, project, image)
        # Fixed fixtures only, no auth or agent profile in either container.
        fresh = await env.exec("python -c \"import os; assert os.listdir('/workspace') == []; assert os.listdir('/home/agent') == []; assert not os.path.exists('/var/run/docker.sock'); assert not os.path.exists('/tests'); assert not any('API_KEY' in k or 'HINDSIGHT' in k for k in os.environ)\"",
                               user=1000, timeout_sec=5)
        if fresh.return_code:
            raise ValueError("fresh-state/profile-absence check failed")
        ready = await env.exec("python -c \"import os,time; p='/egress/provider.sock'; end=time.monotonic()+5;\nwhile not os.path.exists(p) and time.monotonic()<end: time.sleep(.05)\nassert os.path.exists(p)\"",
                               user=1000, timeout_sec=6)
        if ready.return_code:
            raise ValueError("proxy socket not ready")
        if case == "proxy-stop":
            await env.stop_service("proxy")
        started = monotonic()
        try:
            result = await env.exec(f"python /opt/network_probe.py {case}", user=1000,
                                    timeout_sec=1 if case == "timeout" else request.max_test_seconds)
            elapsed = monotonic() - started
            execution = "completed" if result.return_code == 0 else "error"
            evidence["probe"] = {"return_code": result.return_code, "stdout": result.stdout, "stderr": result.stderr}
            passed = result.return_code == 0 and case != "timeout"
        except TimeoutError:
            elapsed = monotonic() - started
            execution = "timeout"
            passed = case == "timeout"
        finally:
            await env.stop_service("main")
        stopped = await inspect_container(subject_id)
        evidence["subject_stop_state"] = stopped["State"]
        if stopped["State"]["Running"]:
            raise ValueError("subject still running after stop")
        await env.stop_service("proxy")
        _, logs = await docker("logs", "--tail", "128", proxy_id)
        write_text(folder / "proxy.log", logs.decode(errors="replace"))
    except asyncio.CancelledError:
        execution, error, passed = "cancelled", "network worker cancelled; no more cases launched", False
    except Exception as failure:
        error, passed = f"{type(failure).__name__}: {failure}"[:2000], False
    finally:
        if claimed:
            cleanup, cleanup_error = await cleanup_project(env, project)
            if cleanup_error:
                evidence["cleanup_error"] = cleanup_error
        evidence["cleanup_succeeded"] = cleanup
        write_new(folder / "evidence.json", evidence)
    return NetworkControlReceipt(case=case, control_passed=passed and cleanup, execution=execution,
                                 wall_seconds=elapsed, cleanup_succeeded=cleanup, evidence_digest=digest(evidence), error=error)


async def trial(output: Path, request: NetworkControlRequest, case: str, image: str) -> NetworkControlReceipt:
    # Shared deadline includes creation, inspection, readiness and the probe.
    # _trial captures cancellation and preserves a failed receipt after bounded cleanup.
    return await asyncio.wait_for(_trial(output, request, case, image), timeout=55)


async def run(output: Path):
    request = NetworkControlRequest.model_validate_json((output / "request.json").read_bytes())
    if output.name != request.run_id:
        raise ValueError("network-control request/run-directory mismatch")
    root = Path(__file__).resolve().parents[2]
    if source_digest(root) != request.sources_digest:
        raise ValueError("network-control source drift")
    provenance(output, request)
    code, _ = await docker("image", "inspect", request.base_image)
    if code:
        raise ValueError("pinned base image is not installed; approved setup pull is separate")
    image = await build_image(output, request)
    write_new(output / "image.json", {"image": image, "base_image": request.base_image, "sources_digest": request.sources_digest})
    receipts = []
    for case in request.cases:
        receipt = await trial(output, request, case, image)
        receipts.append(receipt)
        write_new(output / f"receipt-{case}.json", receipt.model_dump(mode="json"))
        if not receipt.control_passed:
            break
    write_new(output / "result.json", {"purpose": "network-controls-not-harness-evaluation", "request": request.model_dump(mode="json"),
                                       "image": image, "receipts": [r.model_dump(mode="json") for r in receipts]})


async def main(output: Path):
    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, task.cancel)
    loop.add_signal_handler(signal.SIGINT, task.cancel)
    try:
        await run(output)
    except asyncio.CancelledError:
        write_new(output / "cancelled.json", {"cancelled": True})
        raise


if __name__ == "__main__":
    os.umask(0o077)
    asyncio.run(main(Path(sys.argv[1])))
