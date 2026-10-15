"""Worker-only unit checks; no Docker processes or containers are launched."""

import asyncio
import importlib.util
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


@unittest.skipUnless(importlib.util.find_spec("harbor"), "requires pinned worker environment")
class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_credential_seal_does_not_chmod_arg0_symlink_targets(self):
        import json
        import os
        import stat
        from agent_evals.harbor_codex_live import SEAL
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)/'home'
            home = base/'.codex'
            vault = home/'cleanup-vault'
            vault.mkdir(parents=True)
            (home/'auth.json').write_text(json.dumps({'tokens':{'access_token':'synthetic-access','refresh_token':'synthetic-refresh'},'OPENAI_API_KEY':None}))
            (home/'config.toml').write_text('model="requested"')
            helpers = home/'tmp/arg0'
            helpers.mkdir(parents=True)
            immutable = Path(directory)/'immutable-native-binary'
            immutable.write_bytes(b'synthetic-public-binary')
            immutable.chmod(0o400)
            (helpers/'apply_patch').symlink_to(immutable)
            original_stat = os.stat
            def root_owned(path, *args, **kwargs):
                value = original_stat(path, *args, **kwargs)
                if str(path) in {str(home),str(vault)}:
                    fields = list(value); fields[4] = 0
                    return os.stat_result(fields)
                return value
            with patch('os.stat', root_owned):
                exec(compile(SEAL.replace('/home/agent', str(base)), '<credential-seal>', 'exec'), {})
            self.assertEqual(stat.S_IMODE(immutable.stat().st_mode),0o400)
            self.assertTrue((vault/'auth.json').exists())

    async def test_smoke_phase_profiles_keep_subject_off_network(self):
        from agent_evals.codex_live import CodexLiveRequest
        from agent_evals.codex_smoke import CodexSmokeRequest
        from agent_evals.harbor_codex_live import compose
        request = CodexLiveRequest(run_id='a'*32, approval='one-codex-native-smoke-20261013',
                                  profile=CodexSmokeRequest(credit_fallback='allowed'),
                                  worker_lock_digest='sha256:'+'1'*64, source_digest='sha256:'+'2'*64,
                                  conformance_digest='sha256:'+'3'*64)
        for phase, host in [('login', 'auth.openai.com'), ('agent', 'chatgpt.com'), ('revoke', 'auth.openai.com')]:
            spec = compose(request, phase)
            self.assertEqual(spec['services']['main']['network_mode'], 'none')
            self.assertIn('egress:/egress:ro', spec['services']['main']['volumes'])
            self.assertEqual(spec['services']['proxy']['command'][-1], host)
            self.assertNotIn('environment', spec['services']['proxy'])

    async def test_smoke_export_rejects_log_symlinks(self):
        import io
        import tarfile
        from agent_evals.harbor_codex_live import decode_log
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w') as archive:
            member = tarfile.TarInfo('codex.jsonl')
            member.type, member.linkname = tarfile.SYMTYPE, '/home/agent/.codex/auth.json'
            archive.addfile(member)
        with self.assertRaisesRegex(ValueError, 'unsafe'):
            decode_log(stream.getvalue())

    async def test_smoke_collector_never_exports_raw_auth_or_transcripts(self):
        import io
        import json
        import os
        from agent_evals import smoke_evidence
        from agent_evals.harbor_codex_live import COLLECTOR
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            secret = 'synthetic-private-access-token'
            (workspace/'answer.json').write_text(json.dumps({'result':secret}))
            (workspace/'codex.jsonl').write_text(json.dumps({'type':'item.completed','item':{'text':secret}})+'\n')
            original_open = os.open
            def confined_open(name, flags):
                self.assertTrue(name.startswith('/subject-workspace/'))
                return original_open(workspace/Path(name).name, flags)
            output = io.StringIO()
            with patch.dict('sys.modules', {'smoke_evidence':smoke_evidence}), patch('os.open', confined_open), patch('sys.stdout', output):
                exec(compile(COLLECTOR, '<trusted-collector>', 'exec'), {})
            self.assertNotIn(secret, output.getvalue())
            receipt = json.loads(output.getvalue())
            self.assertFalse(receipt['passed'])
            self.assertIsNone(receipt['numeric_answer'])

    async def test_trusted_relay_readiness_ignores_subject_python_modules(self):
        import subprocess
        import sys
        from agent_evals.harbor_codex_live import start_relay
        env = SimpleNamespace(exec=AsyncMock(side_effect=[
            SimpleNamespace(return_code=0, stdout='123\n'),
            SimpleNamespace(return_code=0, stdout=''),
            SimpleNamespace(return_code=0, stdout='')]))
        await start_relay(env, 'revoke')
        for call in env.exec.call_args_list:
            self.assertIn('python -I ', call.args[0])
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory)/'socket.py').write_text("raise RuntimeError('subject code imported by cleanup')\n")
            result = subprocess.run([sys.executable, '-I', '-c', 'import socket,time; print(socket.__name__)'],
                                    cwd=directory, capture_output=True, timeout=3)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), b'socket')

    async def test_trusted_public_files_are_created_root_owned_not_copied(self):
        from agent_evals.harbor_codex_live import install_public
        env = SimpleNamespace(exec=AsyncMock(return_value=SimpleNamespace(return_code=0)))
        await install_public(env, '/home/agent/.codex/config.toml', b'model="requested"', 0o444)
        call = env.exec.call_args
        self.assertEqual(call.kwargs['user'], 'root')
        self.assertIn('python -I ', call.args[0])
        self.assertIn('os.O_EXCL|os.O_NOFOLLOW', call.args[0])
        self.assertIn('st_uid==0', call.args[0])


    async def test_harbor_timeout_normalization(self):
        from agent_evals.harbor_controls import ControlDocker, DockerEnvironment
        env = object.__new__(ControlDocker)
        with patch.object(DockerEnvironment, "_run_docker_compose_command", AsyncMock(side_effect=RuntimeError("Command timed out after 1 seconds"))):
            with self.assertRaises(TimeoutError):
                await env._run_docker_compose_command(["exec"], timeout_sec=1)
        with patch.object(DockerEnvironment, "_run_docker_compose_command", AsyncMock(side_effect=RuntimeError("daemon failure"))):
            with self.assertRaisesRegex(RuntimeError, "daemon failure"):
                await env._run_docker_compose_command(["exec"], timeout_sec=1)

    async def test_cancellation_terminates_cli_child(self):
        from agent_evals.harbor_controls import ControlDocker, DockerEnvironment
        with patch.object(DockerEnvironment, "_collect_buffered_output", AsyncMock(side_effect=asyncio.CancelledError)), \
             patch.object(DockerEnvironment, "_terminate_process", AsyncMock()) as terminate:
            child = object()
            with self.assertRaises(asyncio.CancelledError):
                await ControlDocker._collect_buffered_output(child, timeout_sec=1)
            terminate.assert_awaited_once_with(child)

    async def test_volume_query_failure_does_not_claim_resources(self):
        from agent_evals.harbor_controls import prepare
        with tempfile.TemporaryDirectory() as directory:
            env = SimpleNamespace(environment_dir=Path(directory), control_claimed=False, start=AsyncMock())
            with patch("agent_evals.harbor_controls.docker", AsyncMock(side_effect=[(0, b""), ValueError("daemon failure")])):
                with self.assertRaisesRegex(ValueError, "daemon failure"):
                    await prepare(env, "ae-controls-fixture", "sha256:image", {})
            self.assertFalse(env.control_claimed)
            env.start.assert_not_awaited()
            self.assertFalse((Path(directory) / "claimed.json").exists())

    async def test_network_cleanup_deadline_and_cancellation_are_visible(self):
        from agent_evals.harbor_network_controls import cleanup_project
        env = SimpleNamespace(_run_docker_compose_command=AsyncMock())
        async def stalled(*args):
            await asyncio.Future()
        with patch("agent_evals.harbor_network_controls.CLEANUP_SECONDS", 0.01), \
             patch("agent_evals.harbor_network_controls.empty_project", stalled):
            succeeded, reason = await cleanup_project(env, "ae-controls-net-fixture")
            self.assertFalse(succeeded)
            self.assertIn("TimeoutError", reason)
        env._run_docker_compose_command = AsyncMock(side_effect=asyncio.CancelledError)
        succeeded, reason = await cleanup_project(env, "ae-controls-net-fixture")
        self.assertFalse(succeeded)
        self.assertIn("cancelled", reason)

    async def test_copied_codex_vendor_rejects_unlisted_and_mutated_files(self):
        from scripts.check_codex_image import verify_copied_vendor
        from agent_evals.controls import bytes_digest
        with tempfile.TemporaryDirectory() as temporary:
            vendor = Path(temporary)
            (vendor / "bin").mkdir()
            (vendor / "bin/codex").write_bytes(b"synthetic-public-binary")
            receipt = {"entries": [{"path": "package/vendor/x86_64-unknown-linux-musl/bin/codex",
                                    "size": 23, "digest": bytes_digest(b"synthetic-public-binary")} ]}
            verify_copied_vendor(vendor, receipt)
            (vendor / "unlisted").write_text("synthetic-unlisted")
            with self.assertRaisesRegex(ValueError, "unlisted"):
                verify_copied_vendor(vendor, receipt)
            (vendor / "unlisted").unlink()  # Only this test's temporary fixture.
            (vendor / "bin/codex").write_bytes(b"synthetic-public-change")
            with self.assertRaisesRegex(ValueError, "drift"):
                verify_copied_vendor(vendor, receipt)
