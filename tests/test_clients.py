import io
import json
import types
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from brief_agent.clients import JevClient, ProviderError, WriterClient, decision_schema


QUESTIONS = {
    "relation": {"type": "choice", "instructions": "Relation?", "criteria": {
        "supports": "Same assertion.", "says_nothing": "Does not address it."}},
    "match": {"type": "score", "instructions": "Same entity?",
              "criteria": ["different", "unclear", "same"]},
    "promo": {"type": "noul", "instructions": "Promotional?"},
}
ANSWERS = {
    "relation": {"type": "choice", "choice": "supports", "confidence": 0.9},
    "match": {"type": "score", "score": 1.7, "confidence": 0.8},
    "promo": {"type": "noul", "noul": 0.1},
}


def http_response(data):
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = json.dumps(data).encode()
    return response


def completion(content, finish="stop", refusal=None):
    response = MagicMock()
    response.model_dump.return_value = {
        "model": "returned-model", "usage": {"prompt_tokens": 11, "completion_tokens": 9},
        "choices": [{"finish_reason": finish,
                     "message": {"content": content, "refusal": refusal}}],
    }
    return response


class WriterTests(unittest.TestCase):
    def setUp(self):
        self.factory = MagicMock()
        self.module = patch.dict("sys.modules", {"openai": types.SimpleNamespace(OpenAI=self.factory)})
        self.module.start()
        self.addCleanup(self.module.stop)
        self.api = self.factory.return_value.chat.completions.create

    def writer(self, gemini=False):
        url = ("https://generativelanguage.googleapis.com/v1beta/openai/" if gemini
               else "https://api.groq.com/openai/v1")
        return WriterClient(url, "secret-test-key", "chosen-model")

    def test_groq_request_strict_schema_and_safe_log(self):
        self.api.return_value = completion('{"snapshot": []}')
        client = self.writer()
        schema = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
        self.assertEqual(client.generate("Write facts", {"text": "document"}, schema), {"snapshot": []})
        request = self.api.call_args.kwargs
        self.assertEqual(request["reasoning_effort"], "none")
        self.assertEqual(request["extra_body"], {"reasoning_format": "hidden"})
        self.assertTrue(request["response_format"]["json_schema"]["strict"])
        self.assertEqual(request["max_tokens"], 2500)
        self.assertEqual(request["temperature"], 0.2)
        self.assertEqual(self.factory.call_args.kwargs["max_retries"], 0)
        self.assertEqual(client.calls[0]["model"], "returned-model")
        self.assertEqual(client.calls[0]["usage"]["prompt_tokens"], 11)
        self.assertNotIn("secret-test-key", json.dumps(client.calls))
        self.assertNotIn("headers", client.calls[0])

    def test_gemini_is_explicit_and_uses_minimal_reasoning(self):
        self.api.return_value = completion("A brief.")
        client = self.writer(gemini=True)
        self.assertEqual(client.generate("Write", {}), {"markdown": "A brief."})
        self.assertEqual(self.api.call_args.kwargs["reasoning_effort"], "minimal")
        self.assertNotIn("extra_body", self.api.call_args.kwargs)
        self.assertNotIn("response_format", self.api.call_args.kwargs)

    def test_decisions_have_strict_shapes_and_label_confidence(self):
        self.api.return_value = completion(json.dumps({"answers": ANSWERS}))
        client = self.writer()
        self.assertEqual(client.decide({"claim": "A result"}, QUESTIONS), ANSWERS)
        self.assertEqual(client.calls[0]["confidence_kind"], "llm_stated_uncalibrated")
        schema = decision_schema(QUESTIONS)
        def check_objects(node):
            if isinstance(node, dict):
                if node.get("type") == "object":
                    self.assertFalse(node["additionalProperties"])
                    self.assertEqual(set(node["required"]), set(node["properties"]))
                for value in node.values():
                    check_objects(value)
            elif isinstance(node, list):
                for value in node:
                    check_objects(value)
        check_objects(schema)

    def test_invalid_confidence_cannot_enter_decisions(self):
        answers = {**ANSWERS, "relation": {**ANSWERS["relation"], "confidence": 1.5}}
        self.api.return_value = completion(json.dumps({"answers": answers}))
        client = self.writer()
        with self.assertRaisesRegex(ProviderError, "invalid_answers"):
            client.decide({}, QUESTIONS)
        self.assertEqual(client.calls[-1]["status"], "error")

    def test_refusal_truncation_and_invalid_json_are_failures(self):
        for response in (completion("{}", finish="length"), completion(None, refusal="no"),
                         completion("not JSON"), completion("[]")):
            with self.subTest(response=response):
                self.api.return_value = response
                client = self.writer()
                with self.assertRaises(ProviderError):
                    client.generate("Write", {}, {})
                self.assertEqual(client.calls[0]["status"], "error")

    def test_sdk_error_message_and_key_are_not_logged(self):
        self.api.side_effect = RuntimeError("request secret-test-key failed")
        client = self.writer()
        with self.assertRaises(ProviderError) as raised:
            client.generate("Write", {})
        self.assertNotIn("secret-test-key", str(raised.exception) + json.dumps(client.calls))
        self.assertEqual(self.api.call_count, 1)

    def test_provider_error_code_is_logged_without_message_or_400_retry(self):
        for body in ({"code": "json_validate_failed", "message": "secret-test-key org_privatevalue"},
                     {"error": {"code": "json_validate_failed", "message": "secret-test-key org_privatevalue"}},
                     {"code": "invalid_request_error", "message": "secret-test-key org_privatevalue"}):
            expected_code = body.get('error', body)['code']
            error = RuntimeError("secret-test-key org_privatevalue")
            error.status_code, error.body = 400, body
            self.api.side_effect = error
            client = self.writer()
            with patch("brief_agent.clients.time.sleep") as sleep:
                with self.assertRaises(ProviderError) as raised:
                    client.generate("Write", {})
            sleep.assert_not_called()
            self.assertEqual(len(client.calls), 1)
            self.assertEqual(client.calls[0]["provider_error_code"], expected_code)
            self.assertEqual(raised.exception.provider_code, expected_code)
            self.assertEqual(raised.exception.status, 400)
            self.assertNotIn("secret-test-key", json.dumps(client.calls))
            self.assertNotIn("org_privatevalue", json.dumps(client.calls))

    def test_unstructured_or_sensitive_error_codes_are_not_logged(self):
        for code in (None, {}, "secret-test-key", "org_privatevalue", "unexpected details here"):
            error = RuntimeError("private")
            error.status_code, error.body = 400, {"code": code}
            self.api.side_effect = error
            client = self.writer()
            with self.assertRaises(ProviderError) as raised:
                client.generate("Write", {})
            self.assertNotIn("provider_error_code", client.calls[0])
            self.assertIsNone(raised.exception.provider_code)

    def test_raw_response_key_echo_is_redacted(self):
        self.api.return_value = completion("echo secret-test-key")
        client = self.writer()
        client.generate("Write", {})
        self.assertNotIn("secret-test-key", json.dumps(client.calls))
        self.assertIn("[redacted]", json.dumps(client.calls))

    def test_short_rate_limit_headers_do_not_bypass_minute_cooldown(self):
        cases = [
            ({"retry-after": "3"}, 3),
            ({"retry-after-ms": "1250", "retry-after": "9"}, 1.25),
            ({"x-ratelimit-reset-tokens": "7.66s"}, 7.66),
            ({"x-ratelimit-reset-tokens": "1m"}, 60),
            ({"x-ratelimit-reset-tokens": "500ms"}, 0.5),
            ({}, None),
            ({"retry-after": "nan", "x-ratelimit-reset-tokens": "invalid"}, None),
        ]
        for headers, requested_wait in cases:
            with self.subTest(headers=headers):
                error = RuntimeError("secret-test-key must not be logged")
                error.status_code = 429
                error.response = types.SimpleNamespace(headers=headers)
                self.api.side_effect = [error, completion("A brief.")]
                client = self.writer()
                with patch("brief_agent.clients.time.sleep") as sleep:
                    self.assertEqual(client.generate("Write", {}), {"markdown": "A brief."})
                sleep.assert_called_once_with(60)
                self.assertEqual(client.calls[0]["retry_reason"], "minute_window_cooldown")
                self.assertEqual(client.calls[0].get("provider_requested_wait_s"), requested_wait)
                if requested_wait is None:
                    self.assertNotIn("provider_requested_wait_s", client.calls[0])
                self.assertNotIn("secret-test-key", json.dumps(client.calls))
                self.assertNotIn('"headers"', json.dumps(client.calls))

    def test_daily_exhaustion_and_excessive_wait_stop_without_early_retry(self):
        cases = [
            ({"x-ratelimit-remaining-requests": "0", "x-ratelimit-reset-requests": "23h59m"},
             "daily_request_limit_exhausted", 86340),
            ({"x-ratelimit-remaining-requests": "0"}, "daily_request_limit_exhausted", None),
            ({"x-ratelimit-reset-tokens": "2m59.56s"}, "retry_wait_exceeds_60s_bound", 179.56),
            ({"retry-after": "90"}, "retry_wait_exceeds_60s_bound", 90),
        ]
        for headers, reason, requested_wait in cases:
            with self.subTest(reason=reason):
                error = RuntimeError("private provider details")
                error.status_code = 429
                error.response = types.SimpleNamespace(headers=headers)
                self.api.side_effect = error
                client = self.writer()
                with patch("brief_agent.clients.time.sleep") as sleep:
                    with self.assertRaisesRegex(ProviderError, reason):
                        client.generate("Write", {})
                sleep.assert_not_called()
                self.assertEqual(len(client.calls), 1)
                self.assertEqual(client.calls[0]["retry_reason"], reason)
                self.assertEqual(client.calls[0]["retry_delay_s"], 0)
                self.assertEqual(client.calls[0].get("provider_requested_wait_s"), requested_wait)
                if requested_wait is None:
                    self.assertNotIn("provider_requested_wait_s", client.calls[0])

    def test_near_daily_reset_uses_cooldown_and_long_nonempty_daily_bucket_is_ignored(self):
        for headers in [
            {"x-ratelimit-remaining-requests": "0", "x-ratelimit-reset-requests": "10s"},
            {"x-ratelimit-remaining-requests": "900", "x-ratelimit-reset-requests": "23h", "x-ratelimit-reset-tokens": "8s"},
        ]:
            error = RuntimeError("private")
            error.status_code = 429
            error.response = types.SimpleNamespace(headers=headers)
            self.api.side_effect = [error, completion("A brief.")]
            client = self.writer()
            with patch("brief_agent.clients.time.sleep") as sleep:
                client.generate("Write", {})
            sleep.assert_called_once_with(60)
            self.assertEqual(client.calls[0]["retry_reason"], "minute_window_cooldown")

    def test_near_zero_token_reset_cannot_trigger_rapid_rpm_retries(self):
        error = RuntimeError("private")
        error.status_code = 429
        error.response = types.SimpleNamespace(headers={
            "x-ratelimit-reset-tokens": "1ms", "x-ratelimit-remaining-requests": "900"})
        self.api.side_effect = error
        client = self.writer()
        with patch("brief_agent.clients.time.sleep") as sleep:
            with self.assertRaises(ProviderError):
                client.generate("Write", {})
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [60, 60])
        self.assertEqual(len(client.calls), 3)
        self.assertEqual([call['retry_reason'] for call in client.calls],
                         ['minute_window_cooldown', 'minute_window_cooldown', 'attempt_limit_reached'])

    def test_headerless_rate_limit_is_still_limited_to_three_attempts(self):
        error = RuntimeError("private")
        error.status_code = 429
        error.response = types.SimpleNamespace(headers={})
        self.api.side_effect = error
        client = self.writer()
        with patch("brief_agent.clients.time.sleep") as sleep:
            with self.assertRaises(ProviderError):
                client.generate("Write", {})
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [60, 60])
        self.assertEqual(len(client.calls), 3)
        self.assertEqual(client.calls[-1]["retry_reason"], "attempt_limit_reached")
        self.assertEqual(client.calls[-1]["retry_delay_s"], 0)


class JevTests(unittest.TestCase):
    def test_http_shape_and_custom_user_agent(self):
        raw = {"model": "jev-pinned", "answers": ANSWERS, "usage": {"input_tokens": 30}}
        with patch("brief_agent.clients.urllib.request.urlopen", return_value=http_response(raw)) as call:
            client = JevClient("https://example.test/systemone", "secret-test-key", "jev-free")
            self.assertEqual(client.decide({"document": "Evidence"}, QUESTIONS), ANSWERS)
        request = call.call_args.args[0]
        self.assertEqual(json.loads(request.data), {
            "model": "jev-free", "state": {"document": "Evidence"}, "questions": QUESTIONS})
        self.assertEqual(request.get_header("User-agent"), "research-brief-agent/1.0")
        self.assertEqual(request.get_header("Authorization"), "Bearer secret-test-key")
        self.assertEqual(call.call_args.kwargs["timeout"], 60)
        self.assertEqual(client.calls[0]["model"], "jev-pinned")
        self.assertNotIn("secret-test-key", json.dumps(client.calls))

    def test_rate_limit_retry_records_every_attempt(self):
        error = urllib.error.HTTPError("https://example.test", 429, "secret-test-key", {"retry-after": "3"}, io.BytesIO())
        raw = {"model": "jev-pinned", "answers": ANSWERS, "usage": {}}
        with patch("brief_agent.clients.urllib.request.urlopen", side_effect=[error, http_response(raw)]), \
                patch("brief_agent.clients.time.sleep") as sleep:
            client = JevClient("https://example.test/systemone", "secret-test-key", "jev-free")
            self.assertEqual(client.decide({}, QUESTIONS), ANSWERS)
        sleep.assert_called_once_with(60)
        self.assertEqual([row["attempt"] for row in client.calls], [1, 2])
        self.assertEqual([row["status"] for row in client.calls], ["error", "ok"])
        self.assertEqual(client.calls[0]["retry_reason"], "minute_window_cooldown")
        self.assertEqual(client.calls[0]["provider_requested_wait_s"], 3)
        self.assertNotIn("secret-test-key", json.dumps(client.calls))

    def test_long_rate_limit_wait_is_logged_without_retry_or_private_headers(self):
        error = urllib.error.HTTPError("https://example.test", 429, "secret-test-key", {
            "retry-after": "90", "private-header": "private-provider-value",
        }, io.BytesIO(b"private-error-body"))
        with patch("brief_agent.clients.urllib.request.urlopen", side_effect=error) as call, \
                patch("brief_agent.clients.time.sleep") as sleep:
            client = JevClient("https://example.test/systemone", "secret-test-key", "jev-free")
            with self.assertRaisesRegex(ProviderError, "retry_wait_exceeds_60s_bound"):
                client.decide({}, QUESTIONS)
        sleep.assert_not_called()
        self.assertEqual(call.call_count, 1)
        self.assertEqual(client.calls[0]["provider_requested_wait_s"], 90)
        self.assertIsInstance(client.calls[0]["provider_requested_wait_s"], (int, float))
        self.assertEqual(client.calls[0]["retry_delay_s"], 0)
        logged = json.dumps(client.calls)
        for private in ("secret-test-key", "private-provider-value", "private-error-body", '"headers"'):
            self.assertNotIn(private, logged)

    def test_retry_limit_and_permanent_error(self):
        for status, expected in ((503, 3), (403, 1)):
            with self.subTest(status=status):
                error = urllib.error.HTTPError("https://example.test", status, "secret-test-key", {}, io.BytesIO())
                with patch("brief_agent.clients.urllib.request.urlopen", side_effect=error) as call, \
                        patch("brief_agent.clients.time.sleep"):
                    client = JevClient("https://example.test/systemone", "secret-test-key", "jev-free")
                    with self.assertRaises(ProviderError) as raised:
                        client.decide({}, QUESTIONS)
                self.assertEqual(call.call_count, expected)
                self.assertEqual(raised.exception.status, status)
                self.assertNotIn("secret-test-key", str(raised.exception))

    def test_missing_answer_and_nan_are_rejected(self):
        for answers in ({}, {**ANSWERS, "promo": {"type": "noul", "noul": float("nan")}}):
            with self.subTest(answers=answers):
                with patch("brief_agent.clients.urllib.request.urlopen", return_value=http_response({
                        "model": "jev-pinned", "answers": answers, "usage": {}})):
                    client = JevClient("https://example.test/systemone", "public", "jev-free")
                    with self.assertRaisesRegex(ProviderError, "invalid_answers"):
                        client.decide({}, QUESTIONS)


if __name__ == "__main__":
    unittest.main()
