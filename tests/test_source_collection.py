"""Offline acquisition boundary and durable-content safety."""
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from newspipe import channel, cli, config, events, pipeline, policy, runtime, service, state, view
from types import SimpleNamespace
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

    def test_window_limited_success_retains_content_without_advancing_cursor(self):
        self.adapter.fetch.return_value = FetchResult([_item(1, pub_ts=NOW.timestamp())], (
            SubscriptionReport("bounded", "success", 1, diagnostics={"window_limited": True}),))
        run = self.collect()
        self.assertEqual(run.collection["status"], "success")
        self.assertEqual(self.store.pending_count("src"), 1)
        self.assertEqual(self.store.cursor("src"), OLD.timestamp())

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

    def test_failure_and_partial_poll_throttle_after_restart_then_retry(self):
        for partial in (False, True):
            with self.subTest(partial=partial):
                self.store = state.Store(Path(self.tmp.name) / str(partial))
                self.store.set_cursor("src", OLD.timestamp(), at=OLD)
                self.adapter.fetch.reset_mock()
                self.adapter.fetch.return_value = (self.result(failed=True) if partial else
                    FetchResult([], (SubscriptionReport("bad", "failure", code="timeout"),)))
                first = pipeline.run_poll(self.cfg, self.store, now=NOW, collect_only=True)[0]
                self.assertIn(first.status, {"error", "partial_failure"})
                self.store = state.Store(self.store.news_dir)
                second = pipeline.run_poll(self.cfg, self.store, now=NOW + timedelta(minutes=1), collect_only=True)[0]
                self.assertEqual(second.status, "throttled")
                self.assertEqual(self.adapter.fetch.call_count, 1)
                self.assertEqual(self.store.cursor("src"), OLD.timestamp())
                self.assertEqual(self.store.pending_count("src"), int(partial))
                pipeline.run_poll(self.cfg, self.store, now=NOW + timedelta(minutes=10), collect_only=True)
                self.assertEqual(self.adapter.fetch.call_count, 2)

    def test_manual_force_bypasses_only_acquisition_interval(self):
        self.adapter.fetch.side_effect = TimeoutError("sensitive-input")
        self.collect(force=False)
        self.assertEqual(self.collect(force=False, now=NOW + timedelta(minutes=1)).status, "throttled")
        self.collect(force=True, now=NOW + timedelta(minutes=1))
        self.assertEqual(self.adapter.fetch.call_count, 2)
        self.send.assert_not_called()

    def test_attempt_clock_falls_back_to_legacy_cursor_and_handles_timezones(self):
        age = getattr(self.store, "collection_attempt_age_min", None)
        record = getattr(self.store, "record_collection_attempt", None)
        self.assertIsNotNone(age)
        self.assertIsNotNone(record)
        self.assertEqual(age("src", NOW), 60)
        aware = NOW.replace(tzinfo=timezone(timedelta(hours=8)))
        record("src", at=aware)
        self.assertEqual(age("src", NOW + timedelta(minutes=3)), 3)
        self.assertEqual(age("src", aware + timedelta(minutes=3)), 3)
        record("src", at=NOW)
        self.assertEqual(age("src", aware + timedelta(minutes=3)), 3)
        self.assertEqual(self.store.cursor("src"), OLD.timestamp())

    def test_baseline_and_dry_run_are_not_transport_attempts(self):
        self.store = state.Store(Path(self.tmp.name) / "baseline")
        self.assertEqual(self.collect(force=False).status, "baseline")
        self.adapter.fetch.assert_not_called()
        paths = {p.relative_to(self.store.root) for p in self.store.root.rglob("*") if p.is_file()}
        self.collect(force=True, dry=True)
        self.assertEqual(paths, {p.relative_to(self.store.root) for p in self.store.root.rglob("*") if p.is_file()})
        self.assertFalse(any("attempt" in str(p) for p in paths))

    def test_attempt_is_recorded_before_transport_not_completion(self):
        elapsed = [0]
        def fetch(*args, **kwargs):
            age = getattr(self.store, "collection_attempt_age_min", None)
            self.assertIsNotNone(age)
            self.assertEqual(age("src", NOW), 0)
            elapsed[0] += 600
            raise TimeoutError("slow request")
        self.adapter.fetch.side_effect = fetch
        with patch("time.monotonic", side_effect=lambda: elapsed[0]):
            self.collect()
        self.assertEqual(self.store.collection_attempt_age_min("src", NOW + timedelta(minutes=11)), 11)

    def test_legacy_error_poll_uses_attempt_clock(self):
        self.cfg = replace(self.cfg, sources={"src": replace(self.source, notify=None)})
        self.adapter.fetch.side_effect = TimeoutError("slow request")
        pipeline.run_poll(self.cfg, self.store, now=NOW)
        second = pipeline.run_poll(self.cfg, self.store, now=NOW + timedelta(minutes=1))[0]
        self.assertEqual(second.status, "throttled")
        self.assertEqual(self.adapter.fetch.call_count, 1)

    def test_report_survives_notification_throttle_and_restart(self):
        self.adapter.fetch.return_value = self.result(failed=True)
        self.collect()
        pipeline.notify_source(self.cfg, self.store, "src", now=NOW)
        pipeline.run_poll(self.cfg, self.store, now=NOW + timedelta(minutes=1), collect_only=True)
        self.store = state.Store(self.store.news_dir)
        row = view._source_row(self.source, self.store, now=NOW + timedelta(minutes=1))
        self.assertEqual(row["status"], "partial_failure")
        self.assertEqual(row["activity_status"], "throttled")
        self.assertEqual(row["collection"]["failed"], 1)
        self.assertEqual(row["collection"]["fetched"], 1)
        self.assertEqual(row["status_tone"], "warn")
        self.adapter.fetch.return_value = [_item(2)]
        self.collect()
        row = view._source_row(self.source, self.store, now=NOW)
        self.assertNotIn("collection", row)
        self.assertEqual(row["status"], "collected")

    def test_partial_result_is_a_cli_warning(self):
        self.adapter.fetch.return_value = self.result(failed=True)
        with patch.object(cli, "_load", return_value=(self.cfg, self.store)), patch.object(cli, "_now", return_value=NOW):
            result = cli._sub_phase(SimpleNamespace(cmd="collect", source="src", dry=False), self.store.news_dir)
        self.assertTrue(result.ok)
        self.assertTrue(result.warnings, "partial success needs a warning, not quiet success")

    def test_partial_result_does_not_complete_slot(self):
        self.adapter.fetch.return_value = self.result(failed=True)
        for bound in (False, True):
            with self.subTest(bound=bound):
                self.store = state.Store(Path(self.tmp.name) / ("slot" + str(bound)))
                source = replace(_src(trigger="slot"), notify=policy.NotifyBinding("p") if bound else None)
                cfg = replace(self.cfg, slots={"am": "10:00"}, sources={"src": source})
                self.adapter.fetch.reset_mock()
                service.run_once(cfg, self.store, now=NOW, logger=lambda _: None)
                progress = service.load_runs(self.store.news_dir)["slot_progress"][NOW.date().isoformat()]["am"]["src"]
                self.assertFalse(progress["complete"])
                self.assertEqual(progress["status"], "partial_failure")
                service.run_once(cfg, self.store, now=NOW + timedelta(minutes=2), logger=lambda _: None)
                self.assertEqual(self.adapter.fetch.call_count, 2)

    def test_integer_id_zero_is_durable_delivered_once_and_never_rewritten(self):
        item = _item(1, ext_id=0, pub_ts=NOW.timestamp())
        self.adapter.fetch.return_value = self.result([item], watermark=NOW.timestamp())
        self.assertEqual(self.collect().new, 1)
        self.assertEqual(self.store.peek_pending("src", 1)[0]["ext_id"], 0)
        self.assertEqual(pipeline.notify_source(self.cfg, self.store, "src", now=NOW).card, "sent")
        rows = self.store.root / "pushed" / "src.jsonl"
        before = rows.read_bytes()
        self.assertIs(type(json.loads(before.decode().strip())["ext_id"]), int)
        self.assertEqual(self.collect().new, 0)
        self.assertEqual(self.store.pending_count("src"), 0)
        self.assertEqual(rows.read_bytes(), before)
        self.assertEqual(self.send.call_count, 1)

    def test_existing_integer_pushed_id_is_not_replayed_or_rewritten(self):
        self.store.record_pushed("src", [_item(1, ext_id=7)], NOW.date().isoformat())
        path = self.store.root / "pushed" / "src.jsonl"
        before = path.read_bytes()
        self.adapter.fetch.return_value = self.result([_item(1, ext_id=7)], watermark=NOW.timestamp())
        self.assertEqual(self.collect().new, 0)
        self.assertEqual(path.read_bytes(), before)

    def test_malformed_known_fields_and_surrogates_record_safe_failure(self):
        self.cfg = replace(self.cfg, sources={"src": replace(self.source, filter=replace(self.source.filter, rank="score_desc"))})
        for index, value in enumerate((self.result([_item(1, score={"bad": 1})]), self.result([_item(1, summary="\ud800")]),
                      FetchResult([], (SubscriptionReport("\ud800", "empty"),)))):
            with self.subTest(value=value):
                self.store = state.Store(Path(self.tmp.name) / ("malformed" + str(index)))
                self.store.set_cursor("src", OLD.timestamp(), at=OLD)
                self.adapter.fetch.return_value = value
                run = self.collect()
                self.assertEqual(run.status, "error")
                self.assertEqual(run.collection["reports"][0]["code"], "invalid_response")
                self.assertEqual(self.store.status("src")["status"], "error")
                self.assertEqual(self.store.cursor("src"), OLD.timestamp())
                self.assertEqual(self.store.pending_count("src"), 0)

    def test_confirmed_partial_slot_repair_still_retries_failed_acquisition(self):
        source = _src(trigger="slot")
        self.cfg = replace(self.cfg, slots={"am": "10:00"}, sources={"src": source})
        self.adapter.fetch.return_value = self.result(failed=True)
        append = events.append
        def interrupted(news_dir, event_type, **kw):
            if event_type == "delivered":
                raise OSError("local receipt interruption")
            return append(news_dir, event_type, **kw)
        with patch.object(events, "append", side_effect=interrupted):
            service.run_once(self.cfg, self.store, now=NOW, logger=lambda _: None)
        before = self.store.unfinished_batch("src")
        self.assertTrue(before["delivery_effect_done"])
        self.adapter.fetch.return_value = self.result([_item(2)], watermark=NOW.timestamp())
        service.run_once(self.cfg, self.store, now=NOW + timedelta(minutes=2), logger=lambda _: None)
        self.assertEqual(self.adapter.fetch.call_count, 2, "receipt repair cannot complete a partially acquired slot")
        self.assertEqual(self.store.pushed_ids("src"), {"e1", "e2"})
        self.assertEqual(self.send.call_count, 1, "confirmed remote send must never be repeated")

    def test_cleared_newer_diagnostic_cannot_resurrect_older_inflight_failure(self):
        report = FetchResult([], (SubscriptionReport("feed", "failure", code="network"),)).summary()
        self.store.record_collection_report("src", report, at=NOW)
        self.store.clear_collection_report("src", at=NOW + timedelta(minutes=1))
        self.store.record_collection_report("src", report, at=NOW)
        self.assertIsNone(self.store.collection_report("src"))


if __name__ == "__main__":
    unittest.main()
