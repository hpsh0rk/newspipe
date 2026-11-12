"""Offline acquisition boundary and durable-content safety."""
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from newspipe import channel, config, pipeline, policy, runtime, state
from newspipe.source_contract import FetchResult, SubscriptionReport
from test_news_pipeline import _item, _src

NOW = datetime(2026, 11, 12, 10)
OLD = NOW - timedelta(hours=1)


class SourceCollectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = state.Store(Path(self.tmp.name))
        self.source = replace(_src(trigger="poll", interval_min=10), notify=policy.NotifyBinding("p"))
        self.cfg = config.Config(chat="oc_test", slots={"am": "08:15"}, sources={"src": self.source},
                                 policies={"p": policy.NotificationPolicy()})
        self.addCleanup(patch.stopall)
        patch("time.monotonic", return_value=0).start()
        patch.object(runtime, "activate", side_effect=lambda *_: nullcontext()).start()
        self.adapter = patch.object(pipeline, "_adapter").start().return_value
        self.adapter.fetch.return_value = [_item(1)]
        self.create = patch.object(channel, "create_entity", return_value="card").start()
        self.send = patch.object(channel, "send_card", return_value="message").start()
        self.update = patch.object(channel, "update_entity", return_value=True).start()
        self.store.set_cursor("src", OLD.timestamp(), at=OLD)

    def result(self, items=None, *, failed=False, watermark=None):
        items = [_item(1, pub_ts=NOW.timestamp())] if items is None else items
        reports = [SubscriptionReport("good", "success" if items else "empty", len(items),
                                      watermark=watermark)]
        if failed:
            reports.append(SubscriptionReport("bad", "failure", code="network"))
        return FetchResult(items, tuple(reports))

    def collect(self, **kw):
        return pipeline.collect_source(self.cfg, self.store, "src", now=kw.pop("now", NOW), **kw)

    def legacy(self, **kw):
        self.cfg = replace(self.cfg, sources={"src": replace(self.source, notify=None)})
        return pipeline.run_source(self.cfg, self.store, "src", now=NOW, **kw)[0]

    def test_partial_success_retains_originals_without_advancing_cursor(self):
        item = {**_item(1), "extra": {"facts": [1, 2]}}
        self.adapter.fetch.return_value = self.result([item], failed=True)
        run = self.collect()
        self.assertEqual(run.status, "partial_failure")
        self.assertEqual(run.collection["status"], "partial_failure")
        self.assertEqual(self.store.cursor("src"), OLD.timestamp())
        self.assertEqual(self.store.peek_pending("src", 10), [item])
        self.assertEqual(self.store.pushed_ids("src"), set())
        self.send.assert_not_called()

    def test_failed_and_partial_empty_are_not_empty_success(self):
        for result, status in (
            (FetchResult([], (SubscriptionReport("bad", "failure", code="permission"),)), "error"),
            (self.result([], failed=True), "partial_failure"),
        ):
            with self.subTest(status=status):
                self.adapter.fetch.return_value = result
                run = self.collect()
                self.assertEqual(run.status, status)
                self.assertEqual(self.store.status("src")["status"], status)
                self.assertEqual(self.store.cursor("src"), OLD.timestamp())

    def test_complete_result_uses_partition_low_watermark_clamped_to_clock(self):
        for watermark, want in ((OLD.timestamp() - 10, OLD.timestamp()),
                                (NOW.timestamp() + 100, NOW.timestamp()),
                                (OLD.timestamp() + 10, OLD.timestamp() + 10)):
            with self.subTest(watermark=watermark):
                self.store.set_cursor("src", OLD.timestamp(), at=OLD)
                self.adapter.fetch.return_value = self.result(watermark=watermark)
                self.collect()
                self.assertEqual(self.store.cursor("src"), want)
        self.adapter.fetch.return_value = FetchResult([], (
            SubscriptionReport("a", "empty", watermark=OLD.timestamp() + 20),
            SubscriptionReport("b", "empty", watermark=OLD.timestamp() + 10)))
        self.store.set_cursor("src", OLD.timestamp(), at=OLD)
        self.collect()
        self.assertEqual(self.store.cursor("src"), OLD.timestamp() + 10)

    def test_unknown_or_equal_dates_do_not_fabricate_publication_time(self):
        self.adapter.fetch.return_value = self.result([_item(1, pub_ts=0)])
        run = self.collect()
        self.assertEqual(self.store.cursor("src"), OLD.timestamp())
        self.assertEqual(self.store.peek_pending("src", 1)[0]["pub_ts"], 0)
        self.assertEqual(run.collection["status"], "success")
        self.adapter.fetch.return_value = self.result([_item(2, pub_ts=OLD.timestamp())])
        self.collect()
        self.assertEqual(self.store.pending_count("src"), 2)

    def test_explicit_empty_watermark_advances_but_unknown_empty_does_not(self):
        self.adapter.fetch.return_value = self.result([])
        self.assertEqual(self.collect().status, "collected")
        self.assertEqual(self.store.cursor("src"), OLD.timestamp())
        self.adapter.fetch.return_value = self.result([], watermark=NOW.timestamp())
        self.collect()
        self.assertEqual(self.store.cursor("src"), NOW.timestamp())

    def test_malformed_and_provider_errors_have_safe_actionable_diagnostics(self):
        for bad in ({"Authorization": "sensitive-input"}, [_item(1, ext_id=None)]):
            with self.subTest(value=bad):
                self.adapter.fetch.return_value = bad
                run = self.collect()
                self.assertEqual(run.status, "error")
                self.assertNotIn("sensitive-input", json.dumps(run.as_dict()))
                self.assertEqual(self.store.cursor("src"), OLD.timestamp())
        self.adapter.fetch.side_effect = OSError("Authorization: Bearer sensitive-input")
        run = self.collect()
        saved = "".join(p.read_text() for p in self.store.root.rglob("*") if p.is_file())
        self.assertNotIn("sensitive-input", saved + json.dumps(run.as_dict()))
        self.assertTrue(run.note)

    def test_dry_run_does_not_write_enrich_or_send(self):
        before = {p: p.read_bytes() for p in self.store.root.rglob("*") if p.is_file()}
        self.adapter.fetch.return_value = self.result(failed=True)
        with patch.object(pipeline, "_enrich_new", side_effect=AssertionError("model access")):
            self.assertEqual(self.collect(dry=True).status, "dry")
        self.assertEqual(before, {p: p.read_bytes() for p in self.store.root.rglob("*") if p.is_file()})
        self.send.assert_not_called()

    def test_notification_never_normalizes_or_fetches_existing_content(self):
        self.store.append_pending("src", [_item(1)])
        with patch("newspipe.source_contract.normalize_fetch", side_effect=AssertionError("acquisition")):
            self.assertEqual(pipeline.notify_source(self.cfg, self.store, "src", now=NOW).card, "sent")
        self.adapter.fetch.assert_not_called()

    def test_collection_cannot_mutate_frozen_operation(self):
        self.store.append_pending("src", [_item(1)])
        self.send.side_effect = OSError("lost response")
        pipeline.notify_source(self.cfg, self.store, "src", now=NOW)
        before = self.store.unfinished_batch("src")
        self.adapter.fetch.return_value = self.result([_item(2)], failed=True)
        self.collect()
        self.assertEqual(self.store.unfinished_batch("src"), before)
        self.assertEqual(self.store.pending_count("src"), 2)

    def test_legacy_partial_delivery_cannot_advance_global_cursor(self):
        self.adapter.fetch.return_value = self.result(failed=True)
        run = self.legacy()
        self.assertEqual(run.card, "sent")
        self.assertEqual(run.collection["status"], "partial_failure")
        self.assertEqual(self.store.cursor("src"), OLD.timestamp())

    def test_legacy_total_failure_still_recovers_frozen_send(self):
        self.send.side_effect = OSError("lost response")
        self.legacy()
        before = self.store.unfinished_batch("src")
        self.send.side_effect = None
        self.adapter.fetch.return_value = FetchResult([], (SubscriptionReport("bad", "failure", code="network"),))
        run = self.legacy()
        after = self.store.read_batch(before["digest"], "src", before["slot"])
        self.assertEqual(run.card, "sent")
        for key in ("send_uuid", "delivery_payload", "delivery_chat", "delivery_id", "items"):
            self.assertEqual(after[key], before[key])

    def test_transport_is_outside_writer_lock_at_both_entrypoints(self):
        depth = [0]
        transaction = self.store.transaction
        @contextmanager
        def tracked():
            with transaction():
                depth[0] += 1
                try:
                    yield
                finally:
                    depth[0] -= 1
        def fetch(*args, **kwargs):
            self.assertEqual(depth[0], 0, "transport cannot block unrelated state writers")
            return self.result()
        self.adapter.fetch.side_effect = fetch
        with patch.object(self.store, "transaction", side_effect=tracked):
            self.assertEqual(self.collect().status, "collected")
            self.assertEqual(self.legacy().card, "sent")

    def test_interleaved_collection_commit_never_rolls_back_newer_cursor(self):
        newer = NOW.timestamp() - 10
        def fetch(*args, **kwargs):
            self.store.set_cursor("src", newer, at=NOW)
            return self.result(watermark=OLD.timestamp() + 10)
        self.adapter.fetch.side_effect = fetch
        self.collect()
        self.assertEqual(self.store.cursor("src"), newer)

    def test_interrupted_pending_write_keeps_cursor_and_retry_is_idempotent(self):
        self.adapter.fetch.return_value = self.result([_item(1), _item(2)], watermark=NOW.timestamp())
        append = self.store.append_pending
        def interrupted(source, items):
            append(source, items[:1])
            raise OSError("local interruption")
        with patch.object(self.store, "append_pending", side_effect=interrupted):
            with self.assertRaises(OSError):
                self.collect()
        self.assertEqual(self.store.cursor("src"), OLD.timestamp())
        self.collect()
        self.assertEqual(self.store.pending_count("src"), 2)
        self.assertEqual(self.store.pushed_ids("src"), set())

    def test_legacy_results_keep_serialized_run_shape(self):
        self.assertNotIn("collection", self.collect().as_dict())


if __name__ == "__main__":
    unittest.main()
