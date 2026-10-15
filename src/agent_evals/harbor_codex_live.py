"""One native Codex smoke in the pinned Harbor/socket-proxy boundary."""

import asyncio
import base64
import fcntl
import io
import json
import os
import shlex
import signal
import sys
import tarfile
from pathlib import Path
from time import monotonic

from harbor.models.task.config import EnvironmentConfig
from harbor.models.trial.paths import TrialPaths

from .codex_live import CodexLiveRequest, CodexLiveResult, check_conformance, live_sources, smoke_compose
from .codex_smoke import native_config, prepare
from .controls import CONTROL_IMAGE, bytes_digest, decode_export
from .harbor_controls import ControlDocker, GRADER, docker, environment, inspect_container, provenance, write_text
from .harbor_network_controls import cleanup_project, empty_project, scoped_id
from .network_controls import inspect_proxy, inspect_subject, network_compose
from .planner import digest
from .smoke_evidence import native_usage, parse_device_prompt, strict_answer
from .store import private_directory, write_new

PROXY_ENV = {"HTTPS_PROXY": "http://127.0.0.1:8080", "HTTP_PROXY": "http://127.0.0.1:8080",
             "https_proxy": "http://127.0.0.1:8080", "http_proxy": "http://127.0.0.1:8080",
             "NO_PROXY": "", "no_proxy": ""}
HOME = "/home/agent/.codex"
VAULT = HOME + "/cleanup-vault"

SEAL = '''import json, os, shutil, stat
home='/home/agent/.codex'; vault=home+'/cleanup-vault'
auth=json.load(open(home+'/auth.json'))
assert auth.get('tokens') and not auth.get('OPENAI_API_KEY')
shutil.copyfile(home+'/auth.json', vault+'/auth.json')
os.chmod(vault+'/auth.json',0o600)
# Login-created writable noncredential state must be usable by UID 1000.
for name in os.listdir(home):
    if name in {'auth.json','config.toml','cleanup-vault','run-agent.py'}: continue
    path=home+'/'+name
    if os.path.islink(path): continue
    if os.path.isdir(path):
        for root,dirs,files in os.walk(path):
            os.chmod(root,0o1777)
            # Native Codex creates arg0 helper symlinks to the immutable binary.
            # Never chmod through those links into the read-only image.
            for f in files:
                child=root+'/'+f
                if not os.path.islink(child) and os.path.isfile(child): os.chmod(child,0o666)
    elif os.path.isfile(path): os.chmod(path,0o666)
os.chmod('/home/agent',0o1777); os.chmod(home,0o1777)
os.chmod(home+'/auth.json',0o444); os.chmod(home+'/config.toml',0o444)
os.chmod(vault,0o700)
assert os.stat(vault).st_uid==0 and stat.S_IMODE(os.stat(vault).st_mode)==0o700
assert os.stat(home).st_uid==0 and stat.S_IMODE(os.stat(home).st_mode)==0o1777
'''

DRIVER = '''import json, subprocess, sys
argv=json.loads(sys.argv[1]); maximum=4*1024*1024
process=subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
total=0
with open('/workspace/codex.jsonl','xb') as log:
    while chunk:=process.stdout.read(16384):
        total+=len(chunk)
        if total>maximum:
            process.kill(); process.wait(); print('native output limit exceeded'); sys.exit(125)
        log.write(chunk)
sys.exit(process.wait())
'''

REVOKE = '''import json, os, urllib.request
from urllib.error import HTTPError
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None
result={'attempted':False,'accepted':False,'statuses':[]}
try:
    tokens=json.load(open('/home/agent/.codex/cleanup-vault/auth.json'))['tokens']
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({'https':'http://127.0.0.1:8080'}),NoRedirect())
    for kind in ('refresh_token','access_token'):
        token=tokens.get(kind)
        if not token: continue
        result['attempted']=True
        payload={'token':token,'token_type_hint':kind}
        if kind=='refresh_token': payload['client_id']='app_EMoamEEZ73f0CkXaXp7hrann'
        req=urllib.request.Request('https://auth.openai.com/oauth/revoke',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'},method='POST')
        try:
            with opener.open(req,timeout=10) as response:
                status=response.status
                if response.geturl()!='https://auth.openai.com/oauth/revoke': status=0
        except HTTPError as error: status=error.code
        except Exception: status=0
        result['statuses'].append({'kind':kind,'http_status':status})
    result['accepted']=result['attempted'] and all(200<=x['http_status']<300 for x in result['statuses'])
except Exception: pass
print(json.dumps(result))
'''


def compose(request: CodexLiveRequest, phase: str) -> dict:
    return smoke_compose(request, phase)


async def inspect_phase(env, request, phase, evidence):
    project = f"ae-controls-net-{request.run_id}-smoke"
    main_id, proxy_id = await scoped_id(project, "main"), await scoped_id(project, "proxy")
    main, proxy = await inspect_container(main_id), await inspect_container(proxy_id)
    inspect_subject(main, project, request.image)
    inspect_proxy(proxy, project, request.image)
    if proxy["Config"]["Cmd"] != compose(request, phase)["services"]["proxy"]["command"]:
        raise ValueError("proxy phase command drift")
    evidence.setdefault("phases", {})[phase] = {"subject": main, "proxy": proxy}
    ready = await env.exec("python -I -c " + shlex.quote(
        f"import os,time; p='/egress/{phase}.sock'; end=time.monotonic()+5\nwhile not os.path.exists(p) and time.monotonic()<end: time.sleep(.05)\nassert os.path.exists(p)"),
        user=1000, timeout_sec=6)
    if ready.return_code:
        raise ValueError("phase proxy socket not ready")
    return main_id


async def set_phase(env, request, phase, evidence):
    await env._run_docker_compose_command(["stop", "--timeout", "1", "proxy"], timeout_sec=5)
    spec = compose(request, phase)
    write_new(env.environment_dir / f"compose-{phase}.json", spec)
    write_text(env.environment_dir / "docker-compose.yaml", json.dumps(spec))
    await env._run_docker_compose_command(["up", "-d", "--no-deps", "--force-recreate", "proxy"], timeout_sec=10)
    await inspect_phase(env, request, phase, evidence)


async def start_relay(env, phase, previous_pid=None):
    if previous_pid is not None:
        command = ("python -I -c " + shlex.quote(
            f"import os,signal; p={previous_pid}; data=open('/proc/'+str(p)+'/cmdline','rb').read(); assert b'/opt/egress_proxy.py' in data and b'--relay' in data; os.kill(p,signal.SIGTERM)"))
        stopped = await env.exec(command, user="root", timeout_sec=5)
        if stopped.return_code:
            raise ValueError("trusted relay stop failed")
    result = await env.exec(f"python -I /opt/egress_proxy.py --relay /egress/{phase}.sock </dev/null >{VAULT}/relay-{phase}.log 2>&1 & echo $!",
                            user="root", timeout_sec=5)
    if result.return_code or not (result.stdout or "").strip().isdigit():
        raise ValueError("trusted relay did not start")
    ready = await env.exec("python -I -c " + shlex.quote(
        "import socket,time; end=time.monotonic()+3\nwhile True:\n try: s=socket.create_connection(('127.0.0.1',8080),timeout=.1); s.close(); break\n except OSError:\n  if time.monotonic()>end: raise\n  time.sleep(.05)"), user=1000, timeout_sec=4)
    if ready.return_code:
        raise ValueError("trusted relay did not become ready")
    pid = int(result.stdout.strip())
    checked = await env.exec("python -I -c " + shlex.quote(
        f"data=open('/proc/{pid}/cmdline','rb').read(); assert b'/opt/egress_proxy.py' in data and b'--relay' in data and b'/egress/{phase}.sock' in data"),
        user="root", timeout_sec=3)
    if checked.return_code:
        raise ValueError("trusted relay process verification failed")
    return pid


async def stop_subject(env, main_id, evidence):
    await env._run_docker_compose_command(["stop", "--timeout", "1", "main"], timeout_sec=5)
    state = (await inspect_container(main_id))["State"]
    evidence["stopped_before_collection"] = not state["Running"]
    if state["Running"]:
        raise ValueError("subject still running; refuse export/grading")


async def install_public(env, target: str, contents: bytes, mode: int):
    # Compose cp may assign the configured UID 1000. With CAP_CHOWN/DAC_OVERRIDE
    # dropped, root cannot take ownership/read a 0600 upload. Create these public
    # evaluator files directly as root via Harbor exec; credentials never pass here.
    encoded = base64.b64encode(contents).decode('ascii')
    command = "python -I -c " + shlex.quote(
        f"import os,base64; p={target!r}; fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,{mode});\nwith os.fdopen(fd,'wb') as output: output.write(base64.b64decode({encoded!r}))\nos.chmod(p,{mode}); assert os.stat(p).st_uid==0")
    result = await env.exec(command, user="root", timeout_sec=5)
    if result.return_code:
        raise ValueError("trusted public-file installation failed")


def decode_log(archive: bytes) -> bytes:
    if len(archive) > 4 * 1024 * 1024 + 65536:
        raise ValueError("native log archive limit exceeded")
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
        members = tar.getmembers()
        if len(members) != 1 or members[0].name not in {"codex.jsonl", "./codex.jsonl"} or not members[0].isfile() or members[0].size > 4 * 1024 * 1024:
            raise ValueError("unsafe native log export")
        stream = tar.extractfile(members[0])
        if stream is None:
            raise ValueError("missing native log data")
        return stream.read(4 * 1024 * 1024 + 1)


COLLECTOR = '''import json, os, stat
from smoke_evidence import strict_answer, native_usage
def read(name,limit):
    fd=os.open('/subject-workspace/'+name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        metadata=os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size>limit: raise ValueError('unsafe artifact')
        with os.fdopen(fd,'rb',closefd=False) as stream: data=stream.read(limit+1)
        if len(data)>limit: raise ValueError('oversized artifact')
        return data
    finally: os.close(fd)
receipt={'passed':False,'numeric_answer':None,'native_usage':native_usage(b'')}
try:
    answer=strict_answer(read('answer.json',4096))
    if isinstance(answer,dict) and set(answer)=={'result'} and type(answer['result']) is int and -(2**63)<=answer['result']<2**63:
        receipt['numeric_answer']=answer['result']; receipt['passed']=answer['result']==8
except Exception: pass
try: receipt['native_usage']=native_usage(read('codex.jsonl',4*1024*1024))
except Exception: pass
print(json.dumps(receipt))
'''


async def grade(output, request, evidence):
    folder = output / "verifier"
    project = f"ae-controls-{request.run_id}-smoke-verifier"
    verifier = environment(folder, project, CONTROL_IMAGE)
    subject_project = f"ae-controls-net-{request.run_id}-smoke"
    spec = json.loads((folder / "docker-compose.yaml").read_bytes())
    spec["services"]["main"]["volumes"].append("subject_workspace:/subject-workspace:ro")
    spec["volumes"]["subject_workspace"] = {"external": True, "name": subject_project + "_workspace"}
    write_text(folder / "docker-compose.yaml", json.dumps(spec))
    claimed = False
    outcome = "ungraded"
    try:
        async with asyncio.timeout(20):
            await empty_project(project)
            write_new(folder / "claimed.json", {"project": project})
            claimed = True
            await verifier.start(force_build=False)
            vid = await scoped_id(project, "main")
            from .controls import inspect_isolation
            effective = await inspect_container(vid)
            code, image = await docker("image", "inspect", CONTROL_IMAGE)
            if code:
                raise ValueError("verifier image unavailable")
            external = [m for m in effective["Mounts"] if m["Destination"] == "/subject-workspace"]
            binding = subject_project + "_workspace:/subject-workspace:ro"
            if len(external) != 1 or external[0].get("Type") != "volume" or external[0].get("Name") != subject_project + "_workspace" or external[0].get("RW") is not False or binding not in effective["HostConfig"]["Binds"]:
                raise ValueError("trusted collector subject volume is not exact/read-only")
            narrowed = {**effective, "Mounts": [m for m in effective["Mounts"] if m["Destination"] != "/subject-workspace"],
                        "HostConfig": {**effective["HostConfig"], "Binds": [b for b in effective["HostConfig"]["Binds"] if b != binding]}}
            inspect_isolation(narrowed, project, json.loads(image)[0]["Id"])
            evidence["verifier_inspect"] = effective
            setup = await verifier.exec("chmod 0777 /workspace", user="root", timeout_sec=5)
            if setup.return_code:
                raise ValueError("verifier fixture setup failed")
            write_text(folder / "collector.py", COLLECTOR)
            write_text(folder / "smoke_evidence.py", Path(__file__).with_name("smoke_evidence.py").read_text())
            for name in ("collector.py", "smoke_evidence.py"):
                await install_public(verifier, "/workspace/" + name, (folder / name).read_bytes(), 0o444)
            result = await verifier.exec("python /workspace/collector.py", user="root", timeout_sec=5)
            if result.return_code:
                raise ValueError("independent verifier failed")
            evidence["grade"] = json.loads(result.stdout)
            evidence["native_usage"] = evidence["grade"]["native_usage"]
            outcome = "passed" if evidence["grade"]["passed"] else "failed"
    finally:
        if claimed:
            clean, reason = await cleanup_project(verifier, project)
            evidence["verifier_cleanup"] = {"succeeded": clean, "error": reason}
    return outcome


async def run(output: Path):
    request = CodexLiveRequest.model_validate_json((output / "request.json").read_bytes())
    root = Path(__file__).resolve().parents[2]
    if output.name != request.run_id or request.source_digest != live_sources(root) or request.conformance_digest != check_conformance(root):
        raise ValueError("smoke request/source/conformance drift")
    provenance(output, request)
    project = f"ae-controls-net-{request.run_id}-smoke"
    subject_folder = output / "subject"
    private_directory(subject_folder)
    write_new(subject_folder / "docker-compose.yaml", compose(request, "login"))
    env = ControlDocker(environment_dir=subject_folder, environment_name=project, session_id=project,
                        trial_paths=TrialPaths(trial_dir=subject_folder / "harbor"),
                        task_env_config=EnvironmentConfig(docker_image=request.image, cpus=1, memory_mb=512, workdir="/workspace"),
                        mounts=[], enable_environment_dir_upload=False)
    evidence = {"request_digest": digest(request.model_dump(mode="json")), "requested_model": request.profile.model,
                "observed_model": None, "billing": "unavailable-subscription-usage-not-api-invoice"}
    execution, outcome, revocation = "setup-error", "ungraded", "not-required"
    claimed, auth_possible, vault_ready, agent_started, cleanup = False, False, False, False, True
    main_id, relay_pid, login_task = None, None, None
    elapsed, error = 0.0, None
    try:
        async with asyncio.timeout(990):
            await empty_project(project)
            write_new(subject_folder / "claimed.json", {"project": project, "image": request.image})
            claimed = True
            await env.start(force_build=False)
            main_id = await inspect_phase(env, request, "login", evidence)
            setup = await env.exec(f"mkdir -p {HOME} {VAULT}; chmod 1777 /home/agent {HOME}; chmod 0700 {VAULT}; chmod 0777 /workspace",
                                   user="root", timeout_sec=5)
            if setup.return_code:
                raise ValueError("fresh sticky home/vault setup failed")
            for name, content, target in (("config.toml", native_config(request.profile), HOME + "/config.toml"),
                                           ("run-agent.py", DRIVER, HOME + "/run-agent.py"),
                                           ("revoke.py", REVOKE, VAULT + "/revoke.py")):
                write_text(output / name, content)
                await install_public(env, target, content.encode(), 0o500 if name == "revoke.py" else 0o444)
            relay_pid = await start_relay(env, "login")
            auth_possible = True
            login_task = asyncio.create_task(env.exec(f"umask 077; codex login --device-auth > /home/agent/login.log 2>&1",
                                                      user="root", env=PROXY_ENV, timeout_sec=880))
            prompt_written = False
            while not login_task.done():
                reading = await env.exec("python -I -c " + shlex.quote(
                    "from pathlib import Path; p=Path('/home/agent/login.log'); print(p.read_text()[:16384] if p.exists() else '')"),
                    user="root", timeout_sec=3)
                prompt = parse_device_prompt(reading.stdout or "")
                if not (output / "login-diagnostic.json").exists() and (reading.stdout or '').strip():
                    import re
                    text = reading.stdout
                    urls = re.findall(r"https?://[^\s\x1b]+", text)
                    labels = [name for name in ('Enter this one-time code', 'expires in 15 minutes', 'Error logging in', 'Error loading configuration') if name in text]
                    # No transcript or arbitrary URL/token retained; only shape hints.
                    write_new(output / "login-diagnostic.json", {"characters": len(text), "labels": labels,
                              "expected_url_present": 'https://auth.openai.com/codex/device' in text,
                              "url_count": len(urls), "candidate_code_lengths": [len(x) for x in re.findall(r'(?m)^\s*([A-Z0-9]+-[A-Z0-9]+)\s*$', re.sub(r'\x1b\[[0-9;]*m', '', text))]})
                if prompt and not prompt_written:
                    write_new(output / "login-prompt.json", prompt)
                    prompt_written = True
                await asyncio.sleep(0.5)
            login_result = await login_task
            evidence["login_exit_code"] = login_result.return_code
            if login_result.return_code:
                diagnostic = await env.exec("python -I -c " + shlex.quote(
                    "import json; from pathlib import Path; text=Path('/home/agent/login.log').read_text()[:16384].lower(); labels=['permission denied','read-only file system','operation not permitted','connection refused','connection reset','error sending request','error loading configuration','certificate','403','429','404','timed out','command not found','no such file','device code login is not enabled']; print(json.dumps([x for x in labels if x in text]))"),
                    user="root", timeout_sec=3)
                evidence["login_failure_classification"] = json.loads(diagnostic.stdout or "[]")
                raise ValueError("native device login failed; no model task was started")
            sealed = await env.exec("python -I -c " + shlex.quote(SEAL), user="root", timeout_sec=5)
            if sealed.return_code:
                # SEAL contains only fixed controller paths and no token output.
                # Preserve exception kind/line, not native auth contents.
                import re
                message=(sealed.stdout or '')+(sealed.stderr or '')
                evidence['seal_failure']={'line_numbers':re.findall(r'line ([0-9]+)',message),
                                          'categories':[name for name in ('AssertionError','PermissionError','Read-only file system','OSError') if name in message]}
                raise ValueError("original-grant credential sealing failed")
            vault_ready = True
            evidence["original_grant_sealed"] = True
            await set_phase(env, request, "agent", evidence)
            relay_pid = await start_relay(env, "agent", relay_pid)
            public = prepare(request.profile)
            write_new(output / "input.json", public.input_json)
            await install_public(env, "/workspace/input.json", (output / "input.json").read_bytes(), 0o444)
            # One native task invocation across this owner's authorization, not per PID.
            write_new(root / ".eval/codex-smoke/one-smoke-invocation.claim.json",
                      {"run_id": request.run_id, "request_digest": evidence["request_digest"]})
            agent_started = True
            write_new(output / "agent-started.json", {"run_id": request.run_id})
            started = monotonic()
            try:
                result = await env.exec("python -I " + HOME + "/run-agent.py " + shlex.quote(json.dumps(public.execute_argv)),
                                        user=1000, env=PROXY_ENV, timeout_sec=request.profile.agent_seconds)
                execution = "completed" if result.return_code == 0 else "agent-error"
                evidence["agent_exit_code"] = result.return_code
            except TimeoutError:
                execution = "timeout"
            finally:
                elapsed = monotonic() - started
                await stop_subject(env, main_id, evidence)
            # Read stopped subject artifacts only inside the independent trusted
            # no-network verifier; export neither transcripts nor arbitrary JSON.
            outcome = await grade(output, request, evidence)
    except asyncio.CancelledError:
        execution, error = "cancelled", "worker cancelled; no automatic retry"
    except Exception as failure:
        error = f"{type(failure).__name__}: {failure}"[:1500]
    finally:
        if login_task and not login_task.done():
            login_task.cancel()
            try:
                await login_task
            except (asyncio.CancelledError, Exception):
                pass
        if auth_possible and claimed:
            revocation = "unverified"
            try:
                async with asyncio.timeout(45):
                    # Stop all subject writers, then restart ONLY for trusted cleanup.
                    await env._run_docker_compose_command(["stop", "--timeout", "1", "main"], timeout_sec=5)
                    await env._run_docker_compose_command(["start", "main"], timeout_sec=5)
                    await set_phase(env, request, "revoke", evidence)
                    relay_pid = await start_relay(env, "revoke")
                    if not vault_ready:
                        # Partial setup may have persisted a grant; copy only inside
                        # the protected in-container vault, never into host artifacts.
                        recovery = await env.exec("python -I -c " + shlex.quote(
                            f"import os,shutil; p='{HOME}/auth.json'; v='{VAULT}/auth.json';\nif os.path.exists(p): shutil.copyfile(p,v); os.chmod(v,0o600)"),
                            user="root", timeout_sec=5)
                        if recovery.return_code:
                            raise ValueError("partial grant recovery failed")
                    exists = await env.exec(f"test -f {VAULT}/auth.json", user="root", timeout_sec=3)
                    if exists.return_code:
                        revocation = "not-required"
                    else:
                        result = await env.exec(f"python -I {VAULT}/revoke.py", user="root", env=PROXY_ENV, timeout_sec=25)
                        receipt = json.loads(result.stdout) if result.return_code == 0 else {}
                        evidence["revocation"] = receipt
                        revocation = "provider-accepted" if receipt.get("accepted") else "failed"
            except (Exception, asyncio.CancelledError):
                evidence["revocation_failure"] = "original-grant revocation not confirmed; operator follow-up required"
        if claimed:
            if revocation in {"not-required", "provider-accepted"}:
                cleanup, reason = await cleanup_project(env, project)
                if reason:
                    evidence["cleanup_failure"] = reason
                    # Scoped retry is cleanup, not a model/control retry.
                    retry, retry_reason = await cleanup_project(env, project)
                    evidence["cleanup_retry"] = {"succeeded": retry, "error": retry_reason}
                    cleanup = retry
            else:
                cleanup = False
                evidence["credential_volumes_quarantined"] = True
                try:
                    await env._run_docker_compose_command(["down", "--timeout", "1"], timeout_sec=15)
                    evidence["quarantine_subjects_removed"] = True
                except (Exception, asyncio.CancelledError):
                    evidence["cleanup_failure"] = "quarantine stop failed; operator follow-up required"
        cleanup = cleanup and evidence.get("verifier_cleanup", {}).get("succeeded", True)
        evidence["credential_volumes_removed"] = cleanup
        write_new(output / "evidence.json", evidence)
        result = CodexLiveResult(run_id=request.run_id, request_digest=evidence["request_digest"], execution=execution,
                                 agent_started=agent_started, task_outcome=outcome, agent_wall_seconds=elapsed,
                                 cleanup_succeeded=cleanup, revocation=revocation,
                                 revocation_followup_required=revocation in {"failed", "unverified"},
                                 evidence_digest=digest(evidence), error=error)
        write_new(output / "result.json", result.model_dump(mode="json"))


async def main(output):
    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, task.cancel)
    loop.add_signal_handler(signal.SIGINT, task.cancel)
    root = Path(__file__).resolve().parents[2]
    scope = root / ".eval/codex-smoke"
    private_directory(scope)
    fd = os.open(scope / "live-scope.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (scope / "one-smoke-invocation.claim.json").exists():
            raise ValueError("the single authorized native invocation was already claimed")
        await run(output)
    finally:
        os.close(fd)


if __name__ == "__main__":
    os.umask(0o077)
    asyncio.run(main(Path(sys.argv[1])))
