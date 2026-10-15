"""One approved no-network Harbor CLI conformance control; no login/inference."""

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import signal
from pathlib import Path
from uuid import uuid4

from harbor.models.task.config import EnvironmentConfig
from harbor.models.trial.paths import TrialPaths

from agent_evals.codex_smoke import CodexSmokeRequest, native_config
from agent_evals.controls import CONTROL_IMAGE, HARBOR_REVISION, bytes_digest, compose_spec, inspect_isolation
from agent_evals.harbor_controls import ControlDocker, docker, inspect_container, provenance, write_text
from agent_evals.harbor_network_controls import cleanup_project, empty_project, scoped_id
from agent_evals.network_controls import NetworkControlRequest, source_digest
from agent_evals.store import private_directory, write_new


def verify_package(package: Path):
    receipt = json.loads((package / "receipt.json").read_bytes())
    from agent_evals.codex_smoke import PACKAGE_INTEGRITY
    if receipt["integrity"] != PACKAGE_INTEGRITY or receipt["status"] != "staged-not-executed":
        raise ValueError("package staging receipt mismatch")
    actual = set()
    for path in (package / "content").rglob("*"):
        if path.is_symlink():
            raise ValueError("symlink in staged package")
        if path.is_file():
            actual.add(path.relative_to(package / "content").as_posix())
        elif not path.is_dir():
            raise ValueError("special file in staged package")
    expected = {entry["path"] for entry in receipt["entries"]}
    if actual != expected or len(expected) != len(receipt["entries"]):
        raise ValueError("unlisted or duplicate staged package content")
    for entry in receipt["entries"]:
        # Only the previously confined stage is accepted; never an arbitrary package root.
        if not entry["path"].startswith("package/") or ".." in Path(entry["path"]).parts:
            raise ValueError("package receipt path escape")
        path = package / "content" / entry["path"]
        if path.is_symlink() or not path.is_file() or path.stat().st_size != entry["size"]:
            raise ValueError("staged package drift")
        sha = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                sha.update(chunk)
        if "sha256:" + sha.hexdigest() != entry["digest"]:
            raise ValueError("staged package content drift")
    return receipt


def verify_copied_vendor(vendor: Path, receipt: dict):
    prefix = "package/vendor/x86_64-unknown-linux-musl/"
    expected = {entry["path"][len(prefix):]: entry for entry in receipt["entries"] if entry["path"].startswith(prefix)}
    actual = set()
    for path in vendor.rglob("*"):
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise ValueError("unsafe copied vendor content")
        if path.is_file():
            name = path.relative_to(vendor).as_posix()
            actual.add(name)
            entry = expected.get(name)
            if entry is None or path.stat().st_size != entry["size"]:
                raise ValueError("unlisted copied vendor file")
            sha = hashlib.sha256()
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    sha.update(chunk)
            if "sha256:" + sha.hexdigest() != entry["digest"]:
                raise ValueError("copied vendor content drift")
    if actual != set(expected) or "bin/codex" not in actual:
        raise ValueError("incomplete copied vendor content")


async def check(root: Path):
    request = NetworkControlRequest(run_id=uuid4().hex, approval="socket-proxy-network-controls-v1",
                                    worker_lock_digest=bytes_digest((root / "worker/uv.lock").read_bytes()),
                                    sources_digest=source_digest(root))
    output = root / ".eval/codex-smoke" / ("version-" + request.run_id)
    private_directory(output)
    write_new(output / "request.json", {"purpose": "single-codex-version-control-no-login-no-inference",
                                       "harbor_revision": HARBOR_REVISION, "network_request_provenance": request.model_dump(mode="json")})
    provenance(output, request)
    package = root / ".eval/codex-smoke/codex-0.162.1-linux-x64"
    package_receipt = verify_package(package)
    build = output / "build"
    private_directory(build)
    vendor = package / "content/package/vendor/x86_64-unknown-linux-musl"
    shutil.copytree(vendor, build / "vendor")
    verify_copied_vendor(build / "vendor", package_receipt)
    (build / "egress_proxy.py").write_bytes((root / "src/agent_evals/egress_proxy.py").read_bytes())
    write_text(build / "Dockerfile", f"FROM {CONTROL_IMAGE}\nCOPY --chmod=0555 vendor/ /opt/vendor/\nCOPY --chmod=0555 egress_proxy.py /opt/egress_proxy.py\nENV PATH=/opt/vendor/bin:/usr/local/bin:/usr/bin:/bin\n")
    image_file = output / "image-id.txt"
    _, build_log = await docker("build", "--network=none", "--pull=false", "--iidfile", str(image_file), str(build))
    write_text(output / "build.log", build_log.decode(errors="replace"))
    image_file.chmod(0o600)
    image = image_file.read_text().strip()
    project = "ae-controls-codex-version-" + request.run_id
    compose = compose_spec(CONTROL_IMAGE, project)
    compose["services"]["main"]["image"] = image
    compose["services"]["main"]["command"] = ["sleep", "60"]
    compose["services"]["main"]["environment"]["CODEX_HOME"] = "/home/agent/.codex"
    write_new(output / "docker-compose.yaml", compose)
    env = ControlDocker(environment_dir=output, environment_name=project, session_id=project,
                        trial_paths=TrialPaths(trial_dir=output / "harbor"),
                        task_env_config=EnvironmentConfig(docker_image=image, cpus=1, memory_mb=512, workdir="/workspace"),
                        mounts=[], enable_environment_dir_upload=False)
    claimed, passed, cleanup = False, False, True
    evidence = {"image": image, "project": project, "proxy_source_digest": bytes_digest((build / "egress_proxy.py").read_bytes()),
                "package_integrity": json.loads((package / "receipt.json").read_bytes())["integrity"], "inference_requests": 0}
    try:
        async with asyncio.timeout(45):
            await empty_project(project)
            write_new(output / "claimed.json", {"project": project, "image": image})
            claimed = True
            await env.start(force_build=False)
            cid = await scoped_id(project, "main")
            effective = await inspect_container(cid)
            evidence["docker_inspect"] = effective
            inspect_isolation(effective, project, image, expected_image=image)
            setup = await env.exec("mkdir -p /home/agent/.codex; chmod 0777 /home/agent /home/agent/.codex", user="root", timeout_sec=5)
            if setup.return_code:
                raise ValueError("fresh CODEX_HOME setup failed")
            write_text(output / "config.toml", native_config(CodexSmokeRequest(credit_fallback="allowed")))
            await env.upload_file(output / "config.toml", "/home/agent/.codex/config.toml")
            await env.exec("chmod 0444 /home/agent/.codex/config.toml", user="root", timeout_sec=5)
            for name, command in (("version", "codex --version"), ("login-help", "codex login --help"),
                                  ("exec-help", "codex exec --help"), ("features", "codex features list"),
                                  ("login-status", "codex login status")):
                result = await env.exec(command, user=1000, timeout_sec=5)
                evidence[name] = {"return_code": result.return_code, "stdout": result.stdout, "stderr": result.stderr}
                if name == "login-status":
                    if result.return_code != 1 or "Not logged in" not in (result.stdout or "") + (result.stderr or ""):
                        raise ValueError("fresh CLI is unexpectedly authenticated")
                elif result.return_code:
                    raise ValueError(f"native CLI conformance command failed: {name}")
            if "codex-cli 0.162.1" not in evidence["version"]["stdout"] or "--device-auth" not in evidence["login-help"]["stdout"]:
                raise ValueError("installed CLI version/device login surface differs from pin")
            await env.stop_service("main")
            evidence["stopped"] = not (await inspect_container(cid))["State"]["Running"]
            passed = evidence["stopped"]
    except asyncio.CancelledError:
        evidence["error"] = "cancelled"
    except Exception as error:
        evidence["error"] = f"{type(error).__name__}: {error}"[:2000]
    finally:
        if claimed:
            cleanup, reason = await cleanup_project(env, project)
            if reason:
                evidence["cleanup_error"] = reason
        evidence["cleanup_succeeded"] = cleanup
        evidence["control_passed"] = passed and cleanup
        write_new(output / "evidence.json", evidence)
    print(output)
    if not evidence["control_passed"]:
        raise SystemExit(2)


async def main(root: Path):
    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, task.cancel)
    loop.add_signal_handler(signal.SIGINT, task.cancel)
    await check(root)


if __name__ == "__main__":
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("--approve-version-control", action="store_true")
    args = parser.parse_args()
    if not args.approve_version_control:
        parser.error("one approved no-model control attempt is required")
    # Clean environment: no host Codex credentials, API keys, proxies or keyrings.
    root = Path(__file__).resolve().parents[1]
    home = root / ".eval/codex-smoke/version-worker-home"
    private_directory(home)
    allowed_path = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    os.environ.clear()
    os.environ.update({"PATH": allowed_path, "HOME": str(home), "DOCKER_CONFIG": str(home / ".docker"),
                       "LITELLM_LOCAL_MODEL_COST_MAP": "True"})
    asyncio.run(main(root))
