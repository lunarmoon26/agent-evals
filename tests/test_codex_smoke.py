import io
import json
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from agent_evals.cli import main
from agent_evals.codex_smoke import CodexSmokeRequest, prepare, write_preparation


class CodexSmokeTests(unittest.TestCase):
    def test_bounded_contract_rejects_scope_expansion_and_secrets(self):
        for changes in ({"cli_version": "latest"}, {"authentication": "host-auth-json"},
                        {"budget_enforcement": "hard-cap"}, {"credit_fallback": "disallowed"},
                        {"trials": 2}, {"agent_seconds": 61}, {"budget_usd": 11},
                        {"api_key": "synthetic-secret"}, {"provider_hosts": ["localhost"]}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                CodexSmokeRequest.model_validate_json(json.dumps({"credit_fallback": "allowed", **changes}))

    def test_configuration_is_native_requested_not_attested(self):
        prepared = prepare(CodexSmokeRequest(credit_fallback="allowed"))
        config = tomllib.loads(prepared.native_config_toml)
        self.assertEqual(config["model"], "gpt-6.1-sol")
        self.assertEqual(config["forced_login_method"], "chatgpt")
        self.assertEqual(config["cli_auth_credentials_store"], "file")
        self.assertEqual(config["service_tier"], "default")
        self.assertEqual(config["web_search"], "disabled")
        self.assertFalse(config["features"]["memories"])
        self.assertFalse(config["features"]["plugins"])
        self.assertNotIn("multi_agent", config["features"])
        self.assertEqual(prepared.status, "blocked-preparation-only")
        self.assertTrue(prepared.blockers)
        self.assertEqual(prepared.login_argv, ["codex", "login", "--device-auth"])
        self.assertNotIn("resume", prepared.execute_argv)
        self.assertEqual(prepared.execute_argv[-2:], ["--", prepared.instruction])
        self.assertEqual(set(prepared.subject_environment), {"HOME", "CODEX_HOME"})
        self.assertEqual(prepared.request.delegation, "native-defaults-unverified")

    def test_deterministic_preparation_and_exclusive_private_storage(self):
        request = CodexSmokeRequest(credit_fallback="allowed")
        self.assertEqual(prepare(request), prepare(request))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private" / "preparation.json"
            with patch("subprocess.Popen", side_effect=AssertionError("must not launch")), \
                 patch("subprocess.run", side_effect=AssertionError("must not launch")):
                with self.assertRaisesRegex(ValueError, "allow-credit-fallback"):
                    write_preparation(path, False)
                self.assertFalse(path.exists())
                write_preparation(path, True)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                with self.assertRaises(FileExistsError):
                    write_preparation(path, True)

    def test_cli_prepares_without_runtime_imports_or_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private" / "preparation.json"
            with patch("subprocess.Popen", side_effect=AssertionError("must not launch")), \
                 patch("subprocess.run", side_effect=AssertionError("must not launch")), \
                 patch("sys.stdout", io.StringIO()):
                self.assertEqual(main(["prepare-codex-smoke", "--allow-credit-fallback", "--out", str(path)]), 0)
            self.assertEqual(json.loads(path.read_bytes())["status"], "blocked-preparation-only")


if __name__ == "__main__":
    unittest.main()
