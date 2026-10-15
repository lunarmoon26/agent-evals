import io
import json
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

from pydantic import ValidationError

from agent_evals.cli import main
from agent_evals.network_controls import NetworkControlRequest, check_result, inspect_proxy, inspect_subject, network_compose


class NetworkControlTests(unittest.TestCase):
    def test_profile_is_fixed_and_approval_required(self):
        base = {"run_id": "a" * 32, "approval": "socket-proxy-network-controls-v1",
                "sources_digest": "sha256:" + "1" * 64, "worker_lock_digest": "sha256:" + "2" * 64}
        for change in ({"cpus": 2}, {"cases": ["arbitrary"]}, {"base_image": "python:latest"},
                       {"max_test_seconds": 61}, {"provider_hosts": ["example.com"]}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                NetworkControlRequest.model_validate_json(json.dumps(base | change))
        with patch("subprocess.Popen", side_effect=AssertionError("must not launch")), patch("sys.stderr", io.StringIO()):
            self.assertEqual(main(["network-controls"]), 2)

    def test_subject_has_no_network_only_readonly_socket_route(self):
        spec = network_compose("sha256:" + "3" * 64, "ae-controls-net-fixture", ("chatgpt.com",))
        subject, proxy = spec["services"]["main"], spec["services"]["proxy"]
        self.assertEqual(subject["network_mode"], "none")
        self.assertNotIn("networks", subject)
        self.assertIn("egress:/egress:ro", subject["volumes"])
        self.assertEqual(proxy["volumes"], ["egress:/egress"])
        self.assertNotIn("environment", proxy)
        self.assertEqual(proxy["networks"], ["provider"])
        self.assertNotIn("ports", proxy)
        with self.assertRaises(ValueError):
            network_compose("python:latest", "ae-controls-net-fixture", ("chatgpt.com",))
        with self.assertRaises(ValueError):
            network_compose("sha256:" + "3" * 64, "ae-controls-net-fixture", ("*.openai.com",))

    def test_added_socket_must_be_readonly_named_not_host(self):
        container = {"Mounts": [{"Type": "volume", "Name": "ae-controls-net-fixture_egress", "Destination": "/egress", "RW": True}]}
        with self.assertRaisesRegex(ValueError, "read-only"):
            inspect_subject(container, "ae-controls-net-fixture", "sha256:" + "3" * 64)

    def test_proxy_inspection_fails_closed(self):
        with self.assertRaises(ValueError):
            inspect_proxy({"HostConfig": {"Privileged": True}}, "ae-controls-net-fixture", "sha256:" + "3" * 64)

    def test_zero_exit_worker_failed_cleanup_triggers_scoped_fallback(self):
        request = NetworkControlRequest(run_id="a" * 32, approval="socket-proxy-network-controls-v1",
                                        sources_digest="sha256:" + "1" * 64, worker_lock_digest="sha256:" + "2" * 64)
        result = {"purpose": "network-controls-not-harness-evaluation", "request": request.model_dump(mode="json"),
                  "image": "sha256:" + "3" * 64,
                  "receipts": [{"case": "auth-route", "control_passed": False, "execution": "completed",
                                "wall_seconds": 1.0, "cleanup_succeeded": False, "evidence_digest": "sha256:" + "4" * 64}]}
        with patch("agent_evals.network_controls.emergency_cleanup") as cleanup:
            with self.assertRaisesRegex(ValueError, "did not all pass"):
                check_result(result, request, Path("private-output"), {})
            cleanup.assert_called_once_with(Path("private-output"), request, {})

    def test_proxy_shutdown_reset_is_expected_denial_not_control_failure(self):
        from agent_evals.network_probe import probe
        relay = MagicMock()
        relay.poll.return_value = None
        with patch("agent_evals.network_probe.direct_denial"), \
             patch("agent_evals.network_probe.subprocess.Popen", return_value=relay), \
             patch("agent_evals.network_probe.socket.create_connection", return_value=MagicMock()), \
             patch("agent_evals.network_probe.connect", side_effect=ConnectionResetError), \
             patch("sys.stdout", io.StringIO()):
            probe("proxy-stop")
        relay.terminate.assert_called_once()
