"""Model attempts are durably charged before transport, including retries."""
from __future__ import annotations

import json
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from newspipe import llm, state

NOW = datetime(2026, 11, 10, 10)
GOOD = {"choices": [{"finish_reason": "stop", "message": {"content": '{"ok": true}'}}]}
EMPTY = {"choices": [{"finish_reason": "length", "message": {"content": ""}}]}


class Response:
    def __init__(self, data=GOOD):
        self.data = data

    def read(self):
        return json.dumps(self.data).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class ModelBudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = state.Store(Path(self.tmp.name))
        self.ref = llm.ModelRef("test-model", "test-provider", "https://model.invalid/v1", "test-key", "test")
        resolver = patch.object(llm, "resolve_model", return_value=self.ref)
        resolver.start()
        self.addCleanup(resolver.stop)
        self.budget = {"max_calls_per_day": 10, "max_calls_per_hour": 1, "max_chars_per_call": 100}

    def call(self, user="user", **kwargs):
        return llm.chat_json("summarize", system="system", user=user, prompt_version="v1",
                             store=self.store, models_cfg={}, budget=self.budget, now=kwargs.pop("now", NOW),
                             **kwargs)

    def test_real_usage_enforces_the_hourly_limit(self):
        with patch.object(llm.urllib.request, "urlopen", return_value=Response()) as send:
            self.assertTrue(self.call().ok)
            blocked = self.call("different", now=NOW + timedelta(minutes=1))
        self.assertEqual(send.call_count, 1)
        self.assertIn("hourly_calls", blocked.note)
        self.assertEqual(self.store.usage(NOW.date().isoformat())["recent"], [NOW.timestamp()])

    def test_cached_result_is_free_at_the_budget_limit(self):
        self.budget["max_calls_per_day"] = 1
        with patch.object(llm.urllib.request, "urlopen", return_value=Response()) as send:
            self.assertTrue(self.call().ok)
            again = self.call()
        self.assertEqual(again.state, "reused")
        self.assertEqual(send.call_count, 1)

    def test_reasoning_retry_must_reserve_another_attempt(self):
        with patch.object(llm.urllib.request, "urlopen", return_value=Response(EMPTY)) as send:
            result = self.call()
        self.assertEqual(send.call_count, 1)
        self.assertIn("hourly_calls", result.note)
        self.assertEqual(self.store.usage(NOW.date().isoformat())["calls"], 1)

    def test_interruption_after_transport_starts_is_still_charged(self):
        class Interrupted(BaseException):
            pass
        with patch.object(llm.urllib.request, "urlopen", side_effect=Interrupted):
            with self.assertRaises(Interrupted):
                self.call()
        self.assertEqual(self.store.usage(NOW.date().isoformat())["calls"], 1)

    def test_oversized_input_degrades_without_transport_or_truncation(self):
        with patch.object(llm.urllib.request, "urlopen", return_value=Response()) as send:
            result = self.call("x" * 100)
        self.assertFalse(result.ok)
        send.assert_not_called()
        usage = self.store.usage(NOW.date().isoformat())
        self.assertEqual(usage["calls"], 0)
        self.assertEqual(usage["reasons"], {"max_chars_per_call": 1})

    def test_default_usage_date_and_receipt_timestamp_use_injected_clock(self):
        with patch.object(llm.urllib.request, "urlopen", return_value=Response()):
            self.assertTrue(self.call(now=NOW - timedelta(days=20)).ok)
        stamp = NOW - timedelta(days=20)
        self.assertEqual(self.store.usage(stamp.date().isoformat())["calls"], 1)
        key = self.store.receipt_key("v1", self.ref.model, "system\x00user")
        self.assertEqual(self.store.receipt(key)["ts"], stamp.isoformat(timespec="seconds"))

    def test_rolling_hour_budget_survives_midnight(self):
        before = NOW.replace(hour=23, minute=55)
        after = (NOW + timedelta(days=1)).replace(hour=0, minute=5)
        with patch.object(llm.urllib.request, "urlopen", return_value=Response()) as send:
            self.assertTrue(self.call(now=before).ok)
            result = self.call("different", now=after)
        self.assertEqual(send.call_count, 1)
        self.assertIn("hourly_calls", result.note)

    def test_concurrent_requests_cannot_both_spend_the_last_call(self):
        start = threading.Barrier(2)
        def call(user):
            start.wait(timeout=3)
            return self.call(user)
        with patch.object(llm.urllib.request, "urlopen", return_value=Response()) as send:
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(call, ("first", "second")))
        self.assertEqual(send.call_count, 1)
        self.assertEqual(sum(r.ok for r in results), 1)

    def test_each_attempt_accounts_the_entire_input(self):
        self.budget["max_calls_per_hour"] = 10
        with patch.object(llm.urllib.request, "urlopen", side_effect=[Response(EMPTY), Response()]):
            self.assertTrue(self.call().ok)
        usage = self.store.usage(NOW.date().isoformat())
        self.assertEqual(usage["calls"], 2)
        self.assertEqual(usage["chars"], 2 * len("systemuser"))

    def test_concurrent_identical_requests_share_one_paid_receipt(self):
        start = threading.Barrier(2)
        def call(_):
            start.wait(timeout=3)
            return self.call()
        with patch.object(llm.urllib.request, "urlopen", return_value=Response()) as send:
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(call, range(2)))
        self.assertEqual(send.call_count, 1)
        self.assertEqual(sorted(r.state for r in results), ["ok", "reused"])

    def test_legacy_counters_fail_closed_for_the_recent_hour(self):
        stamp = NOW.isoformat(timespec="seconds")
        state.write_json_atomic(self.store.llm_root / "usage" / f"{NOW.date()}.json",
                                {"calls": 1, "updated": stamp})
        with patch.object(llm.urllib.request, "urlopen", return_value=Response()) as send:
            result = self.call()
        send.assert_not_called()
        self.assertIn("hourly_calls", result.note)
