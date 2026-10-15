import io
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_evals.cli import main
from agent_evals.codex_live import check_conformance, launch_smoke, fallback_stop, CodexLiveRequest


class LiveTests(unittest.TestCase):
    def test_no_approval_does_not_launch(self):
        with patch('subprocess.Popen', side_effect=AssertionError('must not launch')):
            with self.assertRaisesRegex(ValueError, 'approve-single-smoke'):
                launch_smoke(Path('missing'), False)
            with patch('sys.stderr', io.StringIO()):
                self.assertEqual(main(['codex-smoke']), 2)

    def test_missing_conformance_fails_closed(self):
        with self.assertRaises(OSError):
            check_conformance(Path('missing-conformance'))

    def test_exact_release_device_prompt_expiry_label(self):
        from agent_evals.smoke_evidence import parse_device_prompt
        prompt = '\n1. Open this link in your browser and sign in to your account\n   https://auth.openai.com/codex/device\n\n2. Enter this one-time code (expires in 15 minutes)\n   ABCD-1234\n\nContinue only if you started this login in Codex.'
        self.assertEqual(parse_device_prompt(prompt), {'verification_url': 'https://auth.openai.com/codex/device', 'user_code': 'ABCD-1234'})

    def test_fallback_refuses_unscoped_cleanup(self):
        import tempfile
        import json
        from agent_evals.codex_smoke import CodexSmokeRequest
        request = CodexLiveRequest(run_id='a'*32, approval='one-codex-native-smoke-20261013',
                                  profile=CodexSmokeRequest(credit_fallback='allowed'),
                                  worker_lock_digest='sha256:'+'1'*64, source_digest='sha256:'+'2'*64,
                                  conformance_digest='sha256:'+'3'*64)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output/'subject').mkdir()
            (output/'subject/claimed.json').write_text(json.dumps({'project':'unrelated','image':request.image}))
            with patch('subprocess.run', side_effect=AssertionError('must not clean unrelated resources')):
                fallback_stop(output, request, {})
            receipt = json.loads((output/'forced-stop.json').read_bytes())
            self.assertFalse(receipt['stopped'])
            self.assertTrue(receipt['revocation_followup_required'])
