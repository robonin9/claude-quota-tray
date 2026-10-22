"""Regression tests for the 2026-10 review fixes."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _path  # noqa: F401  (adds src/ to sys.path)

import api_client as ac
import token_reader as tr


class AuthHeaderTests(unittest.TestCase):
    def test_oauth_token_uses_bearer_and_beta(self):
        h = ac.build_auth_headers("sk-ant-oat01-abc")
        self.assertEqual(h["Authorization"], "Bearer sk-ant-oat01-abc")
        self.assertEqual(h["anthropic-beta"], ac.OAUTH_BETA)
        self.assertNotIn("x-api-key", h)

    def test_api_key_uses_x_api_key(self):
        h = ac.build_auth_headers("sk-ant-api03-xyz")
        self.assertEqual(h["x-api-key"], "sk-ant-api03-xyz")
        self.assertNotIn("Authorization", h)
        self.assertNotIn("anthropic-beta", h)


class ExpiryTests(unittest.TestCase):
    def test_nested_claude_code_layout(self):
        data = {"claudeAiOauth": {"accessToken": "t", "expiresAt": 1_800_000_000_000}}
        self.assertEqual(tr.find_expiry_ms(data), 1_800_000_000_000.0)

    def test_top_level_and_seconds_scaled(self):
        self.assertEqual(tr.find_expiry_ms({"expires_at": 1_800_000_000}),
                         1_800_000_000_000.0)

    def test_missing_or_bool(self):
        self.assertIsNone(tr.find_expiry_ms({"source": "env"}))
        self.assertIsNone(tr.find_expiry_ms({"expiresAt": True}))


class SettingsCorruptBackupTests(unittest.TestCase):
    def test_corrupt_file_is_kept_not_overwritten(self):
        import settings as s
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            path = d / "settings.json"
            path.write_text("{not json", encoding="utf-8")
            with mock.patch.object(s, "SETTINGS_DIR", d), \
                 mock.patch.object(s, "SETTINGS_PATH", path), \
                 mock.patch.object(s, "_cache", None):
                data = s.load()
                self.assertTrue(data["accounts"])
                backups = list(d.glob("settings.corrupt-*.json"))
                self.assertEqual(len(backups), 1)
                self.assertEqual(backups[0].read_text(encoding="utf-8"), "{not json")
                json.loads(path.read_text(encoding="utf-8"))  # fresh valid file
            s._cache = None


class ScopedLimitTests(unittest.TestCase):
    def _snap(self, headers, status=200):
        import httpx
        return ac.snapshot_from_headers(
            httpx.Headers(headers), httpx.Response(status_code=status, headers=headers))

    def test_labels(self):
        self.assertEqual(ac.window_label("7d_oauth_opus"), "Opus")
        self.assertEqual(ac.window_label("7d_sonnet"), "Sonnet")
        self.assertEqual(ac.window_label("5h_opus"), "Opus (5h)")

    def test_every_model_limit_is_captured(self):
        snap = self._snap({
            "anthropic-ratelimit-unified-5h-utilization": "0.1",
            "anthropic-ratelimit-unified-7d-utilization": "0.2",
            "anthropic-ratelimit-unified-7d_sonnet-utilization": "0.5",
            "anthropic-ratelimit-unified-7d_oauth_opus-utilization": "0.9",
            "anthropic-ratelimit-unified-7d_fable-utilization": "0.3",
            "anthropic-ratelimit-unified-7d_fable-status": "allowed",
        })
        self.assertEqual(snap.weekly_pct, 20)
        self.assertEqual(snap.opus_pct, 90)  # legacy field kept
        self.assertEqual([s.label for s in snap.scoped], ["Opus", "Fable", "Sonnet"])
        self.assertEqual([s.pct for s in snap.scoped], [90, 30, 50])
        self.assertFalse(snap.limited)

    def test_limited_flag(self):
        snap = self._snap({
            "anthropic-ratelimit-unified-5h-utilization": "1",
            "anthropic-ratelimit-unified-5h-status": "rejected",
        }, 429)
        self.assertTrue(snap.ok)
        self.assertTrue(snap.limited)


class ModelFallbackTests(unittest.TestCase):
    def test_retired_model_falls_back(self):
        import httpx
        calls = []

        def handler(request):
            model = json.loads(request.content)["model"]
            calls.append(model)
            if model == "old-model":
                return httpx.Response(404, json={"error": {
                    "type": "not_found_error", "message": "model: old-model"}})
            return httpx.Response(200, json={}, headers={
                "anthropic-ratelimit-unified-5h-utilization": "0.42"})

        transport = httpx.MockTransport(handler)
        real = httpx.Client
        with mock.patch.object(ac.httpx, "Client",
                               lambda **kw: real(transport=transport, **kw)), \
             mock.patch.object(ac, "_working_model", None):
            snap = ac.fetch_usage("sk-ant-oat01-x", model="old-model")
            self.assertEqual(snap.session_pct, 42)
            self.assertEqual(calls[0], "old-model")
            self.assertEqual(ac._working_model, calls[-1])

    def test_auth_error_does_not_try_other_models(self):
        import httpx
        calls = []

        def handler(request):
            calls.append(1)
            return httpx.Response(401, json={"error": {
                "type": "authentication_error", "message": "invalid token"}})

        transport = httpx.MockTransport(handler)
        real = httpx.Client
        with mock.patch.object(ac.httpx, "Client",
                               lambda **kw: real(transport=transport, **kw)):
            snap = ac.fetch_usage("sk-ant-oat01-x")
        self.assertFalse(snap.ok)
        self.assertEqual(len(calls), 1)


class ExpiredTokenPreferenceTests(unittest.TestCase):
    def test_fresh_source_beats_expired_one(self):
        import time
        import auth_discovery as ad
        stale = {"token": "old", "raw": {"expiresAt": (time.time() - 60) * 1000}}
        fresh = {"token": "new", "raw": {"expiresAt": (time.time() + 3600) * 1000}}
        with mock.patch.object(ad, "_providers_for_platform",
                               return_value=[("a", lambda: dict(stale)),
                                             ("b", lambda: dict(fresh))]):
            self.assertEqual(ad.read_credentials()["token"], "new")

    def test_expired_used_as_last_resort(self):
        import time
        import auth_discovery as ad
        stale = {"token": "old", "raw": {"expiresAt": (time.time() - 60) * 1000}}
        with mock.patch.object(ad, "_providers_for_platform",
                               return_value=[("a", lambda: dict(stale)),
                                             ("b", lambda: None)]):
            self.assertEqual(ad.read_credentials()["token"], "old")


class TokenStoreTests(unittest.TestCase):
    TOK = "sk-ant-oat01-" + "A1b2_C3-d4" * 9

    def test_extract_plain_and_wrapped(self):
        import token_store as ts
        self.assertEqual(ts.extract_token(f"Your token:\n{self.TOK}\nStore it"), self.TOK)
        wrapped = f"{self.TOK[:50]}\n{self.TOK[50:]}\n\nUse this token"
        self.assertEqual(ts.extract_token(wrapped), self.TOK)
        self.assertIsNone(ts.extract_token("hello world"))
        self.assertIsNone(ts.extract_token("sk-ant-oat01-short"))

    def test_file_backend_roundtrip_and_provider(self):
        import sys as _sys
        import token_store as ts
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(ts, "_file_path", lambda: Path(tmp) / "token"), \
             mock.patch.object(_sys, "platform", "linux"):
            self.assertIsNone(ts.load_token())
            with self.assertRaises(ValueError):
                ts.save_token("not-a-token")
            ts.save_token(self.TOK)
            self.assertEqual(ts.load_token(), self.TOK)
            creds = ts.read_app_credentials()
            self.assertEqual(creds["token"], self.TOK)
            self.assertEqual(creds["source"], "app-token")
            ts.delete_token()
            self.assertIsNone(ts.load_token())

    def test_app_token_is_in_discovery_chain(self):
        import auth_discovery as ad
        names = [n for n, _ in ad._providers_for_platform()]
        self.assertEqual(names[:2], ["environment", "app-token"])


if __name__ == "__main__":
    unittest.main()
