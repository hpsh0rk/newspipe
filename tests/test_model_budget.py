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
from newspipe import enrich, llm, state
from test_news_pipeline import _item, _src

NOW = datetime(2026, 11, 10, 10)
GOOD = {"choices": [{"finish_reason": "stop", "message": {"content": '{"ok": true}'}}]}
EMPTY = {"choices": [{"finish_reason": "length", "message": {"content": ""}}]}


class Response:
    def __init__(self, data=GOOD):
        self.data = data

    def read(self, limit=None):
        body = json.dumps(self.data).encode()
        return body if limit is None else body[:limit]

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
        identity = json.dumps([self.ref.provider, self.ref.base_url, self.ref.model], separators=(",", ":"))
        key = self.store.receipt_key("v1", identity, "system\x00user")
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

    def test_production_enrichment_accounts_retry_at_actual_execution_time(self):
        before = NOW.replace(hour=23, minute=55)
        after = (NOW + timedelta(days=1)).replace(hour=0, minute=5)
        self.budget.update(max_calls_per_day=1, max_calls_per_hour=10, max_chars_per_call=8000)
        with patch.object(llm, "datetime", wraps=datetime) as model_clock, \
             patch.object(llm.urllib.request, "urlopen", side_effect=[Response(EMPTY), Response()]) as send:
            model_clock.now.side_effect = [before, before, after, after]
            enrich.enrich([_item(1)], _src(summary="title"), self.store, {}, self.budget, now=before)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(self.store.usage(before.date().isoformat())["calls"], 1)
        self.assertEqual(self.store.usage(after.date().isoformat())["calls"], 1)

    def scope(self, **changes):
        return {"name": "comparison", "max_calls_per_day": 10, "max_calls_per_hour": 3,
                "max_tokens_per_day": 50000, "reserve_calls_per_day": 0,
                "reserve_calls_per_hour": 0, "reserve_tokens_per_day": 0, **changes}

    def test_scope_headroom_protects_summaries(self):
        self.budget.update(max_calls_per_day=2, max_calls_per_hour=10)
        with patch.object(llm.urllib.request, "urlopen", return_value=Response()) as send:
            first = self.call(spend_scope=self.scope(reserve_calls_per_day=1))
            blocked = self.call("compare again", spend_scope=self.scope(reserve_calls_per_day=1))
            summary = self.call("summary")
        self.assertTrue(first.ok)
        self.assertFalse(blocked.ok)
        self.assertIn("headroom", blocked.note)
        self.assertTrue(summary.ok)
        self.assertEqual(send.call_count, 2)

    def test_scoped_token_reservation_blocks_transport(self):
        with patch.object(llm.urllib.request, "urlopen", return_value=Response()) as send:
            result = self.call(spend_scope=self.scope(max_tokens_per_day=10), max_tokens=100)
        self.assertFalse(result.ok)
        send.assert_not_called()
        self.assertEqual(result.accounting["attempts"], 0)

    def test_overall_token_headroom_and_legacy_usage_survive_first_new_attempt(self):
        self.budget.update(max_calls_per_hour=10, max_tokens_per_day=1000, max_chars_per_call=100, max_tokens_ceiling=100)
        state.write_json_atomic(self.store.llm_root / "usage" / f"{NOW.date()}.json",
                                {"calls": 1, "recent": []})
        with patch.object(llm.urllib.request, "urlopen", return_value=Response()) as send:
            first = self.call(max_tokens=100)
            blocked = self.call("second", max_tokens=100)
        self.assertTrue(first.ok)
        self.assertFalse(blocked.ok)
        self.assertEqual(send.call_count, 1)
        self.assertIn("daily_tokens", blocked.note)

    def test_overall_token_headroom_can_disable_comparison_without_disabling_summary(self):
        self.budget.update(max_calls_per_hour=10, max_tokens_per_day=1000)
        with patch.object(llm.urllib.request, "urlopen", return_value=Response()) as send:
            blocked = self.call(spend_scope=self.scope(reserve_tokens_per_day=900), max_tokens=100)
            summary = self.call("summary", max_tokens=100)
        self.assertFalse(blocked.ok)
        self.assertTrue(summary.ok)
        self.assertEqual(send.call_count, 1)

    def test_measured_and_unknown_usage_are_distinct_and_reasoning_counts_once(self):
        self.budget["max_calls_per_hour"] = 10
        measured = {**GOOD, "usage": {"prompt_tokens": 7, "completion_tokens": 15,
                    "total_tokens": 22, "completion_tokens_details": {"reasoning_tokens": 10}}}
        with patch.object(llm.urllib.request, "urlopen", return_value=Response(measured)):
            result = self.call(spend_scope=self.scope(), max_tokens=100)
        self.assertEqual(result.accounting["measured_tokens"], 22)
        self.assertEqual(result.accounting["unknown_tokens"], 0)
        with patch.object(llm.urllib.request, "urlopen", side_effect=TimeoutError("PRIVATE response")):
            timed = self.call("different", spend_scope=self.scope(), max_tokens=100)
        self.assertGreater(timed.accounting["unknown_tokens"], 100)
        self.assertNotIn("PRIVATE", timed.note)
        usage = self.store.usage(str(NOW.date()))["scopes"]["comparison"]
        self.assertEqual(usage["measured_tokens"], 22)
        self.assertEqual(usage["unknown_tokens"], timed.accounting["unknown_tokens"])

    def test_cache_is_free_and_isolated_by_endpoint(self):
        self.budget["max_calls_per_hour"] = 10
        with patch.object(llm.urllib.request, "urlopen", return_value=Response()) as send:
            self.call(spend_scope=self.scope())
            reused = self.call(spend_scope=self.scope())
            other = llm.ModelRef(self.ref.model, self.ref.provider, "https://second.invalid/v1", "test-key", "test")
            with patch.object(llm, "resolve_model", return_value=other):
                self.call(spend_scope=self.scope())
        self.assertEqual(send.call_count, 2)
        self.assertTrue(reused.accounting["cache_hit"])
        self.assertEqual(reused.accounting["attempts"], 0)

    def test_comparison_has_no_implicit_reasoning_retry(self):
        self.budget["max_calls_per_hour"] = 10
        with patch.object(llm.urllib.request, "urlopen", return_value=Response(EMPTY)) as send:
            result = self.call(spend_scope=self.scope(), retry_empty=False)
        self.assertFalse(result.ok)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(result.accounting["attempts"], 1)

    def test_oversized_response_remains_unknown_and_does_not_leak_body(self):
        with patch.object(llm.urllib.request, "urlopen", return_value=Response({**GOOD, "extra": "PRIVATE" * 100})):
            result = self.call(response_limit=100)
        self.assertFalse(result.ok)
        self.assertNotIn("PRIVATE", result.note)

    def test_malformed_provider_choices_fail_safely_after_reservation(self):
        self.budget["max_calls_per_hour"] = 10
        for raw in ({"choices": [None]}, {"choices": {"PRIVATE": "bad"}},
                    {"choices": [{"message": "PRIVATE"}]}):
            with self.subTest(raw=raw), patch.object(llm.urllib.request, "urlopen", return_value=Response(raw)):
                result = self.call(str(raw), retry_empty=False)
                self.assertFalse(result.ok)
                self.assertEqual(result.accounting["attempts"], 1)
                self.assertNotIn("PRIVATE", result.note)

    def test_scoped_concurrency_and_midnight_share_reservations(self):
        self.budget["max_calls_per_hour"] = 10
        before = NOW.replace(hour=23, minute=55)
        after = (NOW + timedelta(days=1)).replace(hour=0, minute=5)
        scope = self.scope(max_calls_per_hour=1)
        with patch.object(llm.urllib.request, "urlopen", return_value=Response()) as send:
            self.call(spend_scope=scope, now=before)
            blocked = self.call("after midnight", spend_scope=scope, now=after)
        self.assertFalse(blocked.ok)
        self.assertEqual(send.call_count, 1)
        start = threading.Barrier(2)
        def call(user):
            start.wait(timeout=3)
            return self.call(user, spend_scope=self.scope(max_calls_per_day=1), now=after + timedelta(hours=2))
        with patch.object(llm.urllib.request, "urlopen", return_value=Response()) as send:
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(call, ("third", "fourth")))
        self.assertEqual(sum(r.ok for r in results), 1)
        self.assertEqual(send.call_count, 1)
