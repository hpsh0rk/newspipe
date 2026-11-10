"""Durable, bounded card callback acceptance and replay."""
from __future__ import annotations

import json
import hashlib
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from newspipe import callback_jobs, channel, events, inbound, interaction, state


class CallbackAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.news = Path(self.tmp.name)
        self.batch = {"digest": "2026-11-10", "source": "src", "slot": "am", "form": "card",
                      "title": "test", "view": "list", "card_id": "card", "seq": 1,
                      "items": [{"ext_id": "e1", "id": "n01", "title": "test", "url": "https://example.com",
                                 "source": "src", "status": "unread"}]}
        state.Store(self.news).write_batch(self.batch)
        self.event = {"header": {"event_id": "platform-1", "event_type": inbound.CARD_ACTION_EVENT},
                      "event": {"action": {"value": {"domain": "news", "news_action": "wiki", "id": "n01",
                                                     "batch": self.batch["batch"]}}}}

    def test_acceptance_does_not_run_the_slow_handler(self):
        with patch.object(interaction, "handle_json", side_effect=AssertionError("must not run in callback")):
            result = inbound.dispatch(self.event, news_dir=self.news, defer=True)
        self.assertTrue(result["accepted"])
        self.assertEqual(events.queue(self.news), [])

    def test_platform_retry_and_restart_execute_one_favorite(self):
        from newspipe import callback_jobs
        first = inbound.dispatch(self.event, news_dir=self.news, defer=True)
        again = inbound.dispatch(self.event, news_dir=self.news, defer=True)
        self.assertEqual(first["job_id"], again["job_id"])
        self.assertEqual(callback_jobs.stats(self.news)["queued"], 1)
        with patch.object(channel, "update_entity", return_value=True):
            callback_jobs.drain(self.news)
            inbound.dispatch(self.event, news_dir=self.news, defer=True)
            callback_jobs.drain(self.news)
        self.assertEqual(len(events.queue(self.news)), 1)
        self.assertEqual(callback_jobs.stats(self.news)["done"], 1)

    def test_failed_update_is_visible_and_retry_does_not_duplicate_favorite(self):
        from newspipe import callback_jobs
        inbound.dispatch(self.event, news_dir=self.news, defer=True)
        with patch.object(channel, "update_entity", return_value=False):
            callback_jobs.drain(self.news)
        self.assertEqual(callback_jobs.stats(self.news)["queued"], 1)
        self.assertTrue(events.list_events(self.news, types=["failed"]))
        with patch.object(channel, "update_entity", return_value=True):
            callback_jobs.drain(self.news, now=10**12)
        self.assertEqual(len(events.queue(self.news)), 1)
        self.assertEqual(callback_jobs.stats(self.news)["done"], 1)

    def test_reused_event_id_with_different_action_is_rejected(self):
        from newspipe.errors import InboundError
        inbound.dispatch(self.event, news_dir=self.news, defer=True)
        self.event["event"]["action"]["value"]["news_action"] = "dismiss"
        with self.assertRaises(InboundError):
            inbound.dispatch(self.event, news_dir=self.news, defer=True)

    def test_interrupted_core_refresh_does_not_revert_a_later_action(self):
        job = callback_jobs.enqueue(self.news, self.event["event"]["action"]["value"], event_id="core-1")
        with patch.object(channel, "update_entity", return_value=True):
            interaction.handle(dict(job["payload"], _callback_id=job["id"]), news_dir=self.news)
            interaction.handle(dict(job["payload"], news_action="dismiss"), news_dir=self.news)
            job.update(state="running", attempts=1)
            state.write_json_atomic(callback_jobs.folder(self.news) / f"{job['id']}.json", job)
            callback_jobs.drain(self.news)
        saved = state.Store(self.news).read_batch("2026-11-10", "src", "am")
        self.assertEqual(saved["items"][0]["status"], "dismissed")
        self.assertEqual(len(events.queue(self.news)), 1)

    def test_interrupted_hook_is_not_replayed(self):
        job = callback_jobs.enqueue(self.news, {"domain": "news", "news_action": "external"}, event_id="hook-1")
        job.update(state="running", attempts=1)
        state.write_json_atomic(callback_jobs.folder(self.news) / f"{job['id']}.json", job)
        with patch.object(interaction, "handle_json", side_effect=AssertionError("unsafe replay")):
            callback_jobs.drain(self.news, logger=lambda _m: None)
        self.assertEqual(callback_jobs.stats(self.news)["failed"], 1)
        self.assertIn("不确定", events.list_events(self.news, types=["failed"])[0]["payload"]["error"])

    def test_jobs_execute_in_acceptance_order_not_hash_order(self):
        payload = self.event["event"]["action"]["value"]
        ids = sorted(["arrival-a", "arrival-b"], key=lambda v: hashlib.sha256(v.encode()).hexdigest(), reverse=True)
        first = callback_jobs.enqueue(self.news, dict(payload, news_action="open_detail"), event_id=ids[0])
        second = callback_jobs.enqueue(self.news, dict(payload, news_action="back_to_list"), event_id=ids[1])
        handled = []
        with patch.object(interaction, "handle_json", side_effect=lambda value, **kw: handled.append(json.loads(value)["_callback_id"]) or ""):
            callback_jobs.drain(self.news)
        self.assertEqual(handled, [first["id"], second["id"]])
