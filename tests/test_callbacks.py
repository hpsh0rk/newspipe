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

from newspipe import backends, callback_jobs, channel, config, events, inbound, interaction, runtime, state


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

    def test_worker_uses_last_good_service_config_not_rejected_disk_config(self):
        from newspipe.errors import ConfigError
        inbound.dispatch(self.event, news_dir=self.news, defer=True)
        (self.news / "sources.yaml").touch()
        cfg = config.Config(chat="oc_test", slots={}, sources={})
        worker = callback_jobs.Worker(self.news, logger=lambda _m: None)
        worker.config_provider = lambda: cfg
        with patch.object(config, "load", side_effect=ConfigError("rejected disk config")), \
             patch.object(channel, "update_entity", return_value=True), \
             patch.object(worker.stop_event, "wait", side_effect=lambda _seconds: worker.stop_event.set()):
            worker.run()
        self.assertEqual(callback_jobs.stats(self.news)["done"], 1)
        self.assertEqual(len(events.queue(self.news)), 1)

    def test_terminal_payload_leaves_active_queue_but_dedup_survives(self):
        payload = {"domain": "news", "news_action": "open_detail", "body": "large" * 1000}
        job = callback_jobs.enqueue(self.news, payload, event_id="terminal")
        with patch.object(interaction, "handle_json", return_value=""):
            callback_jobs.drain(self.news)
        self.assertEqual(list(callback_jobs.folder(self.news).glob("*.json")), [])
        duplicate = callback_jobs.enqueue(self.news, payload, event_id="terminal")
        self.assertEqual(duplicate["id"], job["id"])
        self.assertEqual(duplicate["state"], "done")
        self.assertNotIn("payload", duplicate)
        self.assertEqual(callback_jobs.stats(self.news)["done"], 1)
        from newspipe.errors import InboundError
        with self.assertRaises(InboundError):
            callback_jobs.enqueue(self.news, dict(payload, body="changed"), event_id="terminal")

    def test_drain_does_not_read_terminal_history(self):
        job = callback_jobs.enqueue(self.news, {"news_action": "open_detail"}, event_id="old")
        with patch.object(interaction, "handle_json", return_value=""):
            callback_jobs.drain(self.news)
        with patch.object(callback_jobs.state, "read_json", side_effect=AssertionError("history scan")):
            self.assertEqual(callback_jobs.drain(self.news), 0)

    def test_legacy_done_jobs_migrate_without_replaying(self):
        job = callback_jobs.enqueue(self.news, {"news_action": "open_detail"}, event_id="legacy")
        job.update(state="done")
        state.write_json_atomic(callback_jobs.folder(self.news) / f"{job['id']}.json", job)
        with patch.object(interaction, "handle_json", side_effect=AssertionError("replay")):
            callback_jobs.drain(self.news)
        self.assertEqual(list(callback_jobs.folder(self.news).glob("*.json")), [])
        self.assertEqual(callback_jobs.stats(self.news)["done"], 1)
        self.assertEqual(callback_jobs.enqueue(self.news, job["payload"], event_id="legacy")["state"], "done")

    def test_terminal_retirement_interruption_preserves_dedup_and_counts(self):
        job = callback_jobs.enqueue(self.news, {"news_action": "open_detail"}, event_id="interrupted")
        active = callback_jobs.folder(self.news) / f"{job['id']}.json"
        original_unlink = Path.unlink

        def interrupted_unlink(path, *args, **kw):
            if path == active:
                raise OSError("interrupted retirement")
            return original_unlink(path, *args, **kw)

        with patch.object(interaction, "handle_json", return_value=""), \
             patch.object(Path, "unlink", autospec=True, side_effect=interrupted_unlink):
            with self.assertRaises(OSError):
                callback_jobs.drain(self.news)
        self.assertEqual(callback_jobs.enqueue(self.news, job["payload"], event_id="interrupted")["state"], "done")
        self.assertEqual(callback_jobs.stats(self.news)["done"], 1)
        with patch.object(interaction, "handle_json", side_effect=AssertionError("replay")):
            callback_jobs.drain(self.news)
        self.assertEqual(callback_jobs.stats(self.news)["done"], 1)

    def test_corrupt_dedup_record_is_not_overwritten_or_replayed(self):
        from newspipe.errors import InboundError
        job = callback_jobs.enqueue(self.news, {"news_action": "external"}, event_id="corrupt")
        path = callback_jobs.folder(self.news) / f"{job['id']}.json"
        path.write_text("{corrupt", encoding="utf-8")
        with self.assertRaises(InboundError):
            callback_jobs.enqueue(self.news, job["payload"], event_id="corrupt")
        self.assertEqual(path.read_text(), "{corrupt")

    def test_failed_hook_input_is_retained_outside_active_queue(self):
        payload = {"news_action": "external", "batch": "test-batch", "argument": "needed for reconciliation"}
        job = callback_jobs.enqueue(self.news, payload, event_id="failed-hook")
        with patch.object(interaction, "handle_json", return_value="⚠️ uncertain effect"):
            callback_jobs.drain(self.news, logger=lambda _: None)
        self.assertEqual(list(callback_jobs.folder(self.news).glob("*.json")), [])
        receipt = callback_jobs.enqueue(self.news, payload, event_id="failed-hook")
        self.assertEqual(receipt["payload"], payload)
        self.assertEqual(receipt["state"], "failed")
        self.assertEqual(callback_jobs.stats(self.news)["failed"], 1)
        path = callback_jobs.receipt_path(self.news, job["id"], "failed")
        path.write_text("{corrupt", encoding="utf-8")
        from newspipe.errors import InboundError
        with self.assertRaises(InboundError):
            callback_jobs.enqueue(self.news, payload, event_id="failed-hook")

    def test_worker_binds_the_same_transport_as_its_config_snapshot(self):
        bound_channel = object()
        cfg = config.Config(chat="oc_test", slots={}, sources={})
        callback_jobs.enqueue(self.news, {"news_action": "open_detail"}, event_id="bound-worker")
        observed = []
        with patch.object(interaction, "handle_json", side_effect=lambda *a, **kw: observed.append(backends.get_channel()) or ""), \
             patch.object(backends, "make_channel", side_effect=AssertionError("must not re-resolve credentials")):
            callback_jobs.drain(self.news, runtime_provider=lambda: (cfg, bound_channel))
        self.assertEqual(observed, [bound_channel])

    def test_transport_binding_is_directory_scoped_and_none_preserves_outer_binding(self):
        first, other = object(), object()
        cfg = config.Config(chat="oc_test", slots={}, sources={})
        with patch.object(backends, "make_channel", return_value=other), runtime.bind_channel(self.news, first):
            with runtime.bind_channel(self.news, None), runtime.activate(cfg, self.news):
                self.assertIs(backends.get_channel(), first)
            with runtime.activate(cfg, self.news / "other"):
                self.assertIs(backends.get_channel(), other)
            with runtime.activate(cfg, self.news):
                self.assertIs(backends.get_channel(), first)
            with runtime.bind_channel(self.news / "other", other), runtime.activate(cfg, self.news):
                self.assertIs(backends.get_channel(), first)
