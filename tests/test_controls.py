import io
import json
import signal
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from pydantic import ValidationError

from agent_evals.cli import main
from agent_evals.controls import (CONTROL_IMAGE, CASES, ControlBatch, ControlRequest, compose_spec,
                                  decode_export, inspect_isolation, launch_controls)


class ControlTests(unittest.TestCase):
    def request(self, **changes):
        values = {"run_id": "a" * 32, "approval": "local-docker-controls-v1",
                  "worker_lock_digest": "sha256:" + "1" * 64, "cases": list(CASES)}
        values.update(changes)
        return ControlRequest.model_validate_json(json.dumps(values))

    def test_approval_and_boundaries(self):
        self.assertEqual(len(self.request().cases), 11)
        for changes in ({"approval": "yes"}, {"cpus": 2}, {"max_agent_seconds": 61},
                        {"cases": ["arbitrary-command"]}, {"cases": ["sum-oracle", "sum-oracle"]},
                        {"image": "python:latest"}, {"provider_hosts": ["example.com"]}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.request(**changes)
        with tempfile.TemporaryDirectory() as root, patch("subprocess.run", side_effect=AssertionError("must not execute")):
            with self.assertRaisesRegex(ValueError, "approval|approve"):
                launch_controls(Path(root), Path(root) / "out", ["sum-oracle"], False)

    def test_compose_has_no_host_mounts_or_routes(self):
        spec = compose_spec(CONTROL_IMAGE, "ae-controls-fixture")
        main = spec["services"]["main"]
        self.assertEqual(main["network_mode"], "none")
        self.assertTrue(main["read_only"])
        self.assertEqual(main["volumes"], ["workspace:/workspace", "home:/home/agent"])
        self.assertEqual(main["environment"], {"HOME": "/home/agent"})
        self.assertNotIn("ports", main)
        self.assertEqual(main["cap_drop"], ["ALL"])

    def inspected(self):
        return {"HostConfig": {"NetworkMode": "none", "ReadonlyRootfs": True, "Privileged": False,
                               "NanoCpus": 1_000_000_000, "Memory": 512 * 1024 * 1024, "PidsLimit": 64,
                               "CapDrop": ["ALL"], "SecurityOpt": ["no-new-privileges:true"],
                               "Tmpfs": {"/tmp": "rw,noexec,nosuid,size=16m"}},
                "Config": {"User": "1000:1000", "Image": CONTROL_IMAGE,
                           "Labels": {"agent-evals.control-project": "ae-controls-fixture"}},
                "Image": "sha256:image-id", "Mounts": [
                    {"Type": "volume", "Destination": "/workspace", "Name": "ae-controls-fixture_workspace"},
                    {"Type": "volume", "Destination": "/home/agent", "Name": "ae-controls-fixture_home"}],
                "NetworkSettings": {"Ports": None}}

    def test_inspection_rejects_effective_widening(self):
        inspect_isolation(self.inspected(), "ae-controls-fixture", "sha256:image-id")
        for field, value in (("NetworkMode", "host"), ("Privileged", True), ("ReadonlyRootfs", False),
                             ("Binds", ["/host:/workspace"]), ("CapAdd", ["SYS_ADMIN"]), ("PidsLimit", 0)):
            data = self.inspected()
            data["HostConfig"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                inspect_isolation(data, "ae-controls-fixture", "sha256:image-id")
        data = self.inspected()
        data["Mounts"][0]["Name"] = "shared-memory"
        with self.assertRaises(ValueError):
            inspect_isolation(data, "ae-controls-fixture", "sha256:image-id")

    def test_docker_tmpfs_representation_is_explicitly_scoped(self):
        data = self.inspected()
        data["Mounts"].append({"Type": "tmpfs", "Destination": "/tmp", "Source": ""})
        inspect_isolation(data, "ae-controls-fixture", "sha256:image-id")
        data["Mounts"][-1]["Destination"] = "/unexpected"
        with self.assertRaises(ValueError):
            inspect_isolation(data, "ae-controls-fixture", "sha256:image-id")

    def test_named_volumes_in_docker_binds_are_not_host_binds(self):
        data = self.inspected()
        data["HostConfig"]["Binds"] = ["ae-controls-fixture_workspace:/workspace:rw", "ae-controls-fixture_home:/home/agent:rw"]
        inspect_isolation(data, "ae-controls-fixture", "sha256:image-id")
        data["HostConfig"]["Binds"][0] = "/host/workspace:/workspace:rw"
        with self.assertRaises(ValueError):
            inspect_isolation(data, "ae-controls-fixture", "sha256:image-id")

    @staticmethod
    def archive(name="answer.json", data=b'{"result":8}', kind=tarfile.REGTYPE):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as tar:
            info = tarfile.TarInfo(name)
            info.type = kind
            info.size = len(data) if kind == tarfile.REGTYPE else 0
            info.linkname = "/etc/passwd" if kind != tarfile.REGTYPE else ""
            tar.addfile(info, io.BytesIO(data))
        return buffer.getvalue()

    def test_bounded_export_never_extracts_paths(self):
        self.assertEqual(decode_export(self.archive()), b'{"result":8}')
        for archive in (self.archive(name="../answer.json"), self.archive(kind=tarfile.SYMTYPE),
                        self.archive(kind=tarfile.LNKTYPE), self.archive(data=b"x" * 4097),
                        b"x" * 65537, b"invalid-tar"):
            with self.subTest(length=len(archive)), self.assertRaises(ValueError):
                decode_export(archive)

    def test_controls_cli_is_fail_closed_without_approval(self):
        with patch("subprocess.run", side_effect=AssertionError("must not execute")):
            with patch("sys.stderr", io.StringIO()):
                self.assertEqual(main(["controls"]), 2)

    def test_empty_receipts_cannot_claim_complete(self):
        with self.assertRaises(ValidationError):
            ControlBatch(request=self.request(), receipts=[], complete=True)

    def test_launcher_sigterm_routes_through_scoped_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "worker/.venv/bin").mkdir(parents=True)
            (root / "worker/.venv/bin/python").write_text("fixture")
            (root / "worker/uv.lock").write_text("fixture")
            process = MagicMock()
            process.poll.return_value = None
            process.wait.return_value = 0
            def start(*args, **kwargs):
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
                return process
            original = signal.getsignal(signal.SIGTERM)
            with patch("subprocess.Popen", side_effect=start), patch("agent_evals.controls.emergency_cleanup") as cleanup:
                with self.assertRaises(InterruptedError):
                    launch_controls(root, root / "out", ["sum-oracle"], True)
                process.send_signal.assert_called_once_with(signal.SIGTERM)
                cleanup.assert_called_once()
            self.assertEqual(signal.getsignal(signal.SIGTERM), original)


if __name__ == "__main__":
    unittest.main()
