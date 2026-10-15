"""Pinned Harbor Docker transport for the fixed no-provider control profile.

Imported only inside worker/.venv, never by offline planning.
"""

import asyncio
import importlib.metadata
import json
import os
import shlex
import signal
import sys
import time
from pathlib import Path

from harbor.environments.docker.docker import DockerEnvironment
from harbor.models.task.config import EnvironmentConfig
from harbor.models.trial.paths import TrialPaths

from .controls import (HARBOR_REVISION, ControlBatch, ControlReceipt, ControlRequest,
                       bytes_digest, compose_spec, decode_export, inspect_isolation)
from .planner import digest
from .store import private_directory, write_new

PROBE = '''import errno, json, os, socket, subprocess, sys
assert os.getuid() == 1000
assert os.listdir('/workspace') == [] and os.listdir('/home/agent') == []
assert [name for _, name in socket.if_nameindex()] == ['lo']
assert not any(os.path.exists(p) for p in ['/tests', '/solution', '/logs/verifier', '/var/run/docker.sock'])
assert not any('API_KEY' in k or 'HINDSIGHT' in k or 'ANTHROPIC' in k or 'OPENAI' in k for k in os.environ)
def blocked():
    s = socket.socket(); s.settimeout(1)
    try: s.connect(('192.0.2.1', 9))
    except OSError as e: return e.errno == errno.ENETUNREACH
    finally: s.close()
    return False
assert blocked()
child = subprocess.run([sys.executable, '-c', "import socket; s=socket.socket(); s.settimeout(1); s.connect(('192.0.2.1',9))"], capture_output=True)
assert child.returncode != 0 and b'Network is unreachable' in child.stderr
print(json.dumps({'fresh_home': True, 'fresh_workspace': True, 'hidden_absent': True, 'network_blocked': True, 'subprocess_network_blocked': True}))
'''

AGENT = '''import json, os, sys, time
case = sys.argv[1]
if case.endswith('-noop'): sys.exit(0)
if case == 'timeout': time.sleep(120); sys.exit(0)
if case == 'artifact-symlink': os.symlink('/etc/passwd', '/workspace/answer.json'); sys.exit(0)
if case == 'artifact-oversize': open('/workspace/answer.json','w').write('x'*8192); sys.exit(0)
if case == 'fresh-a': open('/home/agent/memory.json','w').write('synthetic-previous-answer-canary')
if case == 'fresh-b': assert not os.path.exists('/home/agent/memory.json')
inputs=json.load(open('/workspace/input.json'))
if case.startswith('sum-'): value=sum(inputs)
elif case.startswith('sort-'): value=sorted(inputs)
elif case.startswith('transform-'): value={k: v.upper() for k,v in inputs.items()}
else: value='fresh'
json.dump({'result':value}, open('/workspace/answer.json','w'))
'''

GRADER = '''import json
expected=json.load(open('/workspace/expected.json'))
answer=json.load(open('/workspace/deliverable.json'))
print(json.dumps({'passed': answer == {'result': expected}}))
'''


class ControlDocker(DockerEnvironment):
    """Use Harbor transport/lifecycle without its default bind mounts/overlays."""

    @property
    def _docker_compose_paths(self):
        return [self.environment_dir / "docker-compose.yaml"]

    def _write_mounts_compose_file(self):
        return None

    def _write_env_compose_file(self):
        return None

    async def _write_resources_compose_file(self):
        return None

    async def _run_docker_compose_command(self, command, check=True, timeout_sec=None, **kwargs):
        deadline = timeout_sec or 60
        try:
            return await super()._run_docker_compose_command(command, check=check, timeout_sec=deadline, **kwargs)
        except RuntimeError as error:
            if str(error) == f"Command timed out after {deadline} seconds":
                raise TimeoutError(str(error)) from error
            raise

    @staticmethod
    async def _collect_buffered_output(process, *, timeout_sec, stdin_data=None):
        try:
            return await DockerEnvironment._collect_buffered_output(process, timeout_sec=timeout_sec, stdin_data=stdin_data)
        except asyncio.CancelledError:
            await DockerEnvironment._terminate_process(process)
            raise

    async def remove_scoped(self):
        # Do not use Harbor's --rmi local: cached images are outside cleanup approval.
        await self._run_docker_compose_command(["down", "--volumes"])


async def docker(*arguments: str, limit=65536) -> tuple[int, bytes]:
    process = await asyncio.create_subprocess_exec("docker", *arguments, stdout=asyncio.subprocess.PIPE,
                                                   stderr=asyncio.subprocess.PIPE)
    async def read(stream, bound):
        data = bytearray()
        while True:
            chunk = await stream.read(min(4096, bound + 1 - len(data)))
            if not chunk:
                return bytes(data)
            data.extend(chunk)
            if len(data) > bound:
                raise ValueError("Docker output exceeds its bounded transfer limit")
    try:
        async with asyncio.timeout(30):
            async with asyncio.TaskGroup() as group:
                output = group.create_task(read(process.stdout, limit))
                error = group.create_task(read(process.stderr, 16384))
            await process.wait()
        if process.returncode and arguments[0] not in {"cp", "inspect"}:
            raise ValueError("Docker operation failed: " + error.result().decode(errors="replace")[:1000])
        if process.returncode and arguments[0] == "cp" and b"Could not find the file /workspace/answer.json" not in error.result():
            raise ValueError("output transfer failed for a reason other than a missing declared file")
        return process.returncode, output.result()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def container_id(project: str) -> str:
    _, output = await docker("ps", "-aq", "--filter", f"label=agent-evals.control-project={project}")
    ids = output.decode().split()
    if len(ids) != 1:
        raise ValueError("expected exactly one scoped control container")
    return ids[0]


async def inspect_container(cid: str) -> dict:
    code, output = await docker("inspect", cid)
    if code:
        raise ValueError("control container cannot be inspected")
    return json.loads(output)[0]


async def assert_cleanup(project: str):
    _, containers = await docker("ps", "-aq", "--filter", f"label=agent-evals.control-project={project}")
    _, volumes = await docker("volume", "ls", "-q", "--filter", f"label=agent-evals.control-project={project}")
    if containers.strip() or volumes.strip():
        raise ValueError("scoped container/volume cleanup incomplete")


def task_data(case: str):
    if case.startswith("sum-"):
        return [3, 1, 4], 8
    if case.startswith("sort-"):
        return ["b", "a", "c"], ["a", "b", "c"]
    if case.startswith("transform-"):
        return {"x": "a", "y": "b"}, {"x": "A", "y": "B"}
    return [], "fresh"


def write_text(path: Path, data: str):
    path.write_text(data)
    path.chmod(0o600)


def environment(directory: Path, project: str, image: str) -> ControlDocker:
    private_directory(directory)
    write_new(directory / "docker-compose.yaml", compose_spec(image, project))  # JSON is valid YAML.
    instance = ControlDocker(environment_dir=directory, environment_name=project, session_id=project,
                         trial_paths=TrialPaths(trial_dir=directory / "harbor"),
                         task_env_config=EnvironmentConfig(docker_image=image, cpus=1, memory_mb=512, workdir="/workspace"),
                         mounts=[], enable_environment_dir_upload=False)
    instance.control_claimed = False
    return instance


async def prepare(env: ControlDocker, project: str, image_id: str, evidence: dict):
    _, existing = await docker("ps", "-aq", "--filter", f"label=com.docker.compose.project={project}")
    if existing.strip():
        raise ValueError("control project already contains resources; refuse reuse/teardown")
    for name in ("home", "workspace"):
        _, names = await docker("volume", "ls", "-q", "--filter", f"name={project}_{name}")
        if names.strip():
            raise ValueError("control volume already exists; refuse reuse/teardown")
    write_new(env.environment_dir / "claimed.json", {"project": project})
    env.control_claimed = True
    await env.start(force_build=False)
    cid = await container_id(project)
    effective = await inspect_container(cid)
    evidence[project] = {"docker_inspect": effective}
    inspect_isolation(effective, project, image_id)
    # Trusted setup only. Subjects subsequently execute exclusively as UID 1000.
    setup = await env.exec("chmod 0777 /workspace /home/agent", user="root", timeout_sec=10)
    if setup.return_code:
        raise ValueError("cannot prepare fresh writable state")
    probe = await env.exec("python -c " + shlex.quote(PROBE), timeout_sec=10, user=1000)
    evidence[project]["probe"] = {"return_code": probe.return_code, "stdout": probe.stdout, "stderr": probe.stderr}
    if probe.return_code:
        raise ValueError("fresh-state/network/hidden-grader probe failed")
    return cid


async def trial(request: ControlRequest, case: str, output: Path, image_id: str) -> ControlReceipt:
    folder = output / case
    private_directory(folder)
    subject_project = f"ae-controls-{request.run_id}-{case}-subject"
    verifier_project = f"ae-controls-{request.run_id}-{case}-verifier"
    subject = environment(folder / "subject", subject_project, request.image)
    verifier = environment(folder / "verifier", verifier_project, request.image)
    evidence = {"case": case, "subject_project": subject_project, "verifier_project": verifier_project,
                "harbor_revision": request.harbor_revision, "image": request.image}
    status, outcome, export_status = "error", "ungraded", "unavailable"
    passed = False
    elapsed = 0.0
    error = None
    cleanup = True
    try:
        cid = await prepare(subject, subject_project, image_id, evidence)
        inputs, expected = task_data(case)
        write_new(folder / "input.json", inputs)
        write_text(folder / "agent.py", AGENT)
        await subject.upload_file(folder / "input.json", "/workspace/input.json")
        await subject.upload_file(folder / "agent.py", "/workspace/agent.py")
        # Docker cp creates root-owned files; make only these known fixture files readable.
        await subject.exec("chmod 0444 /workspace/input.json /workspace/agent.py", user="root", timeout_sec=10)
        started = time.monotonic()
        try:
            result = await subject.exec("python /workspace/agent.py " + shlex.quote(case), user=1000,
                                        timeout_sec=1 if case == "timeout" else request.max_agent_seconds)
            status = "completed" if result.return_code == 0 else "error"
            evidence["agent"] = {"return_code": result.return_code, "stdout": result.stdout, "stderr": result.stderr}
        except TimeoutError:
            status = "timeout"
        finally:
            elapsed = time.monotonic() - started
            await subject.stop_service("main")
        stopped = await inspect_container(cid)
        if stopped["State"]["Running"]:
            raise ValueError("subject is still running; refuse collection and grading")
        evidence["subject_stopped_before_export"] = True
        code, archive = await docker("cp", f"{cid}:/workspace/answer.json", "-")
        answer = None
        if code:
            export_status = "missing"
        else:
            try:
                contents = decode_export(archive)
                answer = json.loads(contents)
                export_status = "accepted"
                evidence["output_digest"] = bytes_digest(contents)
            except (ValueError, UnicodeError):
                export_status = "rejected"
        await prepare(verifier, verifier_project, image_id, evidence)
        write_new(folder / "expected.json", expected)
        write_new(folder / "deliverable.json", answer)
        write_text(folder / "grader.py", GRADER)
        for filename in ("expected.json", "deliverable.json", "grader.py"):
            await verifier.upload_file(folder / filename, "/workspace/" + filename)
        await verifier.exec("chmod 0444 /workspace/*.json /workspace/grader.py", user="root", timeout_sec=10)
        grade = await verifier.exec("python /workspace/grader.py", user=1000, timeout_sec=10)
        if grade.return_code:
            raise ValueError("trusted verifier failed")
        evidence["grade"] = json.loads(grade.stdout)
        outcome = "passed" if evidence["grade"]["passed"] else "failed"
        expected_export = "rejected" if case.startswith("artifact-") else "missing" if case.endswith("-noop") or case == "timeout" else "accepted"
        expected_outcome = "failed" if expected_export != "accepted" else "passed"
        passed = (export_status == expected_export and outcome == expected_outcome
                  and status == ("timeout" if case == "timeout" else "completed"))
    except asyncio.CancelledError:
        error = "worker cancelled; remaining cases were not launched"
    except Exception as failure:
        error = f"{type(failure).__name__}: {failure}"[:2000]
    finally:
        for env, project in ((subject, subject_project), (verifier, verifier_project)):
            if not env.control_claimed:
                continue
            try:
                await env.remove_scoped()
                await assert_cleanup(project)
            except Exception as failure:
                cleanup = False
                evidence.setdefault("cleanup_errors", []).append(f"{project}: {failure}")
        evidence["cleanup_succeeded"] = cleanup
        write_new(folder / "evidence.json", evidence)
    return ControlReceipt(case=case, control_passed=passed and cleanup, execution=status,
                          task_outcome=outcome, export_status=export_status, agent_wall_seconds=elapsed,
                          cleanup_succeeded=cleanup, evidence_digest=digest(evidence), error=error)


def provenance(output: Path, request: ControlRequest):
    root = Path(__file__).resolve().parents[2]
    if bytes_digest((root / "worker/uv.lock").read_bytes()) != request.worker_lock_digest:
        raise ValueError("worker dependency lock drift")
    distribution = importlib.metadata.distribution("harbor")
    source = json.loads(distribution.read_text("direct_url.json") or "{}")
    if distribution.version != "0.24.0" or source.get("vcs_info", {}).get("commit_id") != HARBOR_REVISION:
        raise ValueError("installed Harbor revision does not match the approved pin")
    closure = {name: bytes_digest((Path(__file__).parent / name).read_bytes())
               for name in ("harbor_controls.py", "controls.py", "models.py", "planner.py", "store.py")}
    write_new(output / "provenance.json", {"harbor": source, "worker_lock_digest": request.worker_lock_digest,
                                          "controller_sources": closure})


async def run(output: Path):
    request = ControlRequest.model_validate_json((output / "request.json").read_bytes())
    if output.name != request.run_id:
        raise ValueError("control request identity does not match its private run directory")
    provenance(output, request)
    code, image = await docker("image", "inspect", request.image)
    if code:
        raise ValueError("pull the pinned public control image during approved setup before execution")
    image_id = json.loads(image)[0]["Id"]
    receipts = []
    for case in request.cases:
        receipt = await trial(request, case, output, image_id)
        receipts.append(receipt)
        write_new(output / f"receipt-{case}.json", receipt.model_dump(mode="json"))
        if not receipt.control_passed:
            break  # Preserve the budget and inspect failures rather than retry automatically.
    batch = ControlBatch(request=request, receipts=receipts, complete=len(receipts) == len(request.cases))
    write_new(output / "result.json", batch.model_dump(mode="json"))


async def main(output: Path):
    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, task.cancel)
    loop.add_signal_handler(signal.SIGINT, task.cancel)
    try:
        await run(output)
    except asyncio.CancelledError:
        write_new(output / "cancelled.json", {"cancelled": True, "reason": "worker cancelled before/after a trial"})
        raise


if __name__ == "__main__":
    os.umask(0o077)
    asyncio.run(main(Path(sys.argv[1])))
