import json
import unittest
from unittest.mock import patch

from agent_evals.smoke_evidence import native_usage, parse_device_prompt, strict_answer


URL = "https://auth.openai.com/codex/device"
COUNTERS = {"input_tokens": 100, "cached_input_tokens": 20,
            "cache_write_input_tokens": 0, "output_tokens": 12,
            "reasoning_output_tokens": 4}


def completion(usage):
    return json.dumps({"type": "turn.completed", "usage": usage}).encode() + b"\n"


class DevicePromptTests(unittest.TestCase):
    def test_native_next_line_and_ansi(self):
        prompt = (f"1. Open this link:\n\n\x1b[34m{URL}\x1b[0m\n\n"
                  "2. Enter this one-time code\n\n\x1b[1mA1B2-C3D4\x1b[0m\n"
                  "session_id: 12345678\n")
        self.assertEqual(parse_device_prompt(prompt),
                         {"verification_url": URL, "user_code": "A1B2-C3D4"})

    def test_numeric_and_same_line_codes(self):
        for code in ("123456", "123456789012", "ABCD-1234", "ABCDE-1234", "ABCD-12345", "ABCDE-12345"):
            with self.subTest(code=code):
                self.assertEqual(parse_device_prompt(
                    f"{URL}\nEnter this one-time code: {code}"),
                    {"verification_url": URL, "user_code": code})

    def test_rejects_other_issuers_lookalikes_and_unrelated_ids(self):
        for url in ("http://auth.openai.com/codex/device", URL + "/extra",
                    URL + "?token=123456", URL + ".evil", "https://evil.test/device",
                    URL + "\nhttps://evil.test/device"):
            with self.subTest(url=url):
                self.assertIsNone(parse_device_prompt(f"{url}\nEnter this one-time code\nABCD-1234"))
        for body in ("session_id: 12345678", "token: ABCD-1234",
                     "Enter this one-time code\nsession_id: 12345678",
                     "Enter this one-time code\n12345",
                     "Enter this one-time code\n1234567890123",
                     "Enter this one-time code\nabcd-1234",
                     "Enter this one-time code\nABCD-1234-suffix",
                     "Enter this one-time code\nABCD-1234\nEnter this one-time code\n123456"):
            with self.subTest(body=body):
                self.assertIsNone(parse_device_prompt(f"{URL}\n{body}"))

    def test_partial_prompt_and_missing_url(self):
        self.assertIsNone(parse_device_prompt(f"{URL}\nEnter this one-time code\n"))
        self.assertIsNone(parse_device_prompt("Enter this one-time code\nABCD-1234"))


class NativeUsageTests(unittest.TestCase):
    def test_complete_native_usage_is_only_subject_reported(self):
        result = native_usage(completion(COUNTERS))
        self.assertEqual(result, {
            "authority": "subject-reported", "coverage": "complete",
            "definition": "codex-native-last-total-usage", "values": COUNTERS,
            "completed_turn_events": 1, "cost_usd": None, "cost_coverage": "unavailable",
        })

    def test_last_cumulative_total_not_sum_or_normalized_turns(self):
        last = {name: value * 2 for name, value in COUNTERS.items()}
        result = native_usage(completion(COUNTERS) + completion(last))
        self.assertEqual(result["values"], last)
        self.assertEqual(result["coverage"], "partial")
        self.assertEqual(result["completed_turn_events"], 2)
        self.assertNotIn("normalized_turns", result)
        self.assertNotIn("child_models", result)

    def test_missing_and_invalid_counters_never_become_zero(self):
        for value in (None, True, False, -1, 1.0, "12", [], {}):
            with self.subTest(value=value):
                usage = {**COUNTERS, "input_tokens": value}
                result = native_usage(completion(usage))
                self.assertIsNone(result["values"]["input_tokens"])
                self.assertEqual(result["coverage"], "partial")
        result = native_usage(completion({"input_tokens": 0}))
        self.assertEqual(result["values"]["input_tokens"], 0)
        self.assertIsNone(result["values"]["output_tokens"])
        self.assertEqual(result["coverage"], "partial")

    def test_unavailable_and_last_completion_missing_usage(self):
        for log in (b"", b"garbage", completion(None), completion({}),
                    b'{"type":"item.completed","usage":{"input_tokens":99}}\n',
                    completion(COUNTERS) + completion(None)):
            with self.subTest(log=log):
                result = native_usage(log)
                self.assertEqual(result["coverage"], "unavailable")
                self.assertTrue(all(value is None for value in result["values"].values()))
                self.assertIsNone(result["cost_usd"])

    def test_malformed_lines_preserve_diagnostic_partial_usage(self):
        for bad in (b"not-json\n", b'{"type":', b"\xff\n", b"[]\n",
                    b'{"x":1,"x":2}\n', b'{"x":NaN}\n'):
            with self.subTest(bad=bad):
                result = native_usage(completion(COUNTERS) + bad)
                self.assertEqual(result["values"], COUNTERS)
                self.assertEqual(result["coverage"], "partial")

    def test_log_bound_ignores_events_beyond_limit(self):
        first = completion(COUNTERS)
        log = first + b" " * (4 * 1024 * 1024 - len(first)) + completion({"input_tokens": 999})
        result = native_usage(log)
        self.assertEqual(result["values"], COUNTERS)
        self.assertEqual(result["completed_turn_events"], 1)
        self.assertEqual(result["coverage"], "partial")


class StrictAnswerTests(unittest.TestCase):
    def test_accepts_json_values_without_grading(self):
        for value in ({"wrong": True}, [1, "你好"], None, False, 12, "answer"):
            with self.subTest(value=value):
                self.assertEqual(strict_answer(json.dumps(value).encode()), value)
        self.assertEqual(strict_answer(b"0" + b" " * 4095), 0)

    def test_rejects_duplicates_nonfinite_malformed_and_unicode(self):
        for data in (b'{"a":1,"a":2}', b'{"x":{"a":1,"a":2}}',
                     b"NaN", b"Infinity", b"-Infinity", b"1e9999",
                     b"{} {}", b"", b"{", b"\xff", b'"\\ud800"',
                     b'{"\\udfff":1}', b'"\xed\xa0\x80"', b"\xef\xbb\xbf{}"):
            with self.subTest(data=data), self.assertRaises(ValueError):
                strict_answer(data)

    def test_rejects_oversize_and_deep_recursion(self):
        for data in (b"0" + b" " * 4096, b"[" * 65 + b"0" + b"]" * 65,
                     b"[" * 1500 + b"0" + b"]" * 1500):
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                strict_answer(data)

    def test_parsers_launch_nothing(self):
        with patch("subprocess.Popen", side_effect=AssertionError("no runtime")), \
             patch("subprocess.run", side_effect=AssertionError("no runtime")):
            strict_answer(b"null")
            native_usage(completion(COUNTERS))
            parse_device_prompt(f"{URL}\nEnter this one-time code\nABCD-1234")


if __name__ == "__main__":
    unittest.main()
