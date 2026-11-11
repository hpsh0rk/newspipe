"""Offline acquisition, delivery gate, accounting and scheduling acceptance."""
from __future__ import annotations

import json
import sys
import unittest
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from newspipe import channel, cli, config, credentials, events, pipeline, policy, runtime, service, state
from test_news_pipeline import _item, _src

NOW = datetime(2026, 11, 11, 8, 15)
DAY = NOW.date().isoformat()


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = state.Store(Path(self.tmp.name))
        self.source = replace(_src(trigger="poll", interval_min=10), notify=policy.NotifyBinding("p"))
        self.cfg = config.Config(chat="oc_test", slots={"am": "08:15", "pm": "18:35"},
                                 sources={"src": self.source}, policies={"p": policy.NotificationPolicy()})
        self.addCleanup(patch.stopall)
        patch.object(runtime, "activate", side_effect=lambda *_: nullcontext()).start()
        self.adapter = patch.object(pipeline, "_adapter").start()
        self.adapter.return_value.fetch.return_value = [_item(1)]
        self.create = patch.object(channel, "create_entity", side_effect=lambda _: f"c{self.create.call_count}").start()
        self.send = patch.object(channel, "send_card", side_effect=lambda *a, **k: f"m{self.send.call_count}").start()
        self.update = patch.object(channel, "update_entity", return_value=True).start()

    def collect(self, **kw):
        fn = getattr(pipeline, "collect_source", None)
        self.assertIsNotNone(fn, "collection must have a no-send entrypoint")
        return fn(self.cfg, self.store, "src", now=kw.pop("now", NOW), **kw)

    def notify(self, **kw):
        fn = getattr(pipeline, "notify_source", None)
        self.assertIsNotNone(fn, "notification must have a no-fetch entrypoint")
        return fn(self.cfg, self.store, "src", now=kw.pop("now", NOW), **kw)

    def test_collect_persists_pending_and_cursor_without_enrich_or_send(self):
        with patch.object(pipeline, "_enrich_new", side_effect=AssertionError("collection enriched")):
            run = self.collect(force=True)
        self.assertEqual(run.status, "collected")
        self.assertEqual(self.store.pending_count("src"), 1)
        self.assertIsNotNone(self.store.cursor("src"))
        self.send.assert_not_called()

    def test_unforced_first_poll_is_baseline_not_history(self):
        run = self.collect(force=False)
        self.assertEqual(run.status, "baseline")
        self.adapter.return_value.fetch.assert_not_called()
        self.assertEqual(self.store.pending_count("src"), 0)

    def test_digest_notifies_pending_without_fetch(self):
        self.cfg = replace(self.cfg, policies={"p": policy.NotificationPolicy(mode="digest", slots=("am", "pm"))})
        self.collect(force=True)
        self.adapter.return_value.fetch.reset_mock()
        run = self.notify(slot="am")
        self.assertEqual(run.card, "sent")
        self.adapter.return_value.fetch.assert_not_called()
        self.assertEqual(self.store.pending_count("src"), 0)

    def test_digest_clock_is_not_bypassed_by_run_or_manual_acquisition(self):
        self.cfg = replace(self.cfg, policies={"p": policy.NotificationPolicy(mode="digest", slots=("pm",))})
        result = pipeline.run_source(self.cfg, self.store, "src", now=NOW)
        self.assertEqual(result[-1].status, "waiting_slot")
        self.assertEqual(self.store.pending_count("src"), 1)
        self.send.assert_not_called()
        self.assertEqual(self.notify(bypass_clock=True).card, "sent")

    def test_pause_and_quiet_continue_collection_then_pending_sends_without_fetch(self):
        self.cfg = replace(self.cfg, notifications=policy.NotificationGlobals(paused=True))
        self.collect(force=True)
        self.assertEqual(self.notify().status, "notifications_paused")
        self.cfg = replace(self.cfg, notifications=policy.NotificationGlobals(quiet_hours="08:00-09:00"))
        self.assertEqual(self.notify().status, "global_quiet_hours")
        self.adapter.return_value.fetch.reset_mock()
        self.assertEqual(self.notify(now=NOW.replace(hour=9)).card, "sent")
        self.adapter.return_value.fetch.assert_not_called()

    def test_separate_source_limits_and_hard_combined_global_cap(self):
        other = replace(self.source, name="other")
        self.cfg = replace(self.cfg, sources={"src": self.source, "other": other},
                           policies={"p": policy.NotificationPolicy(max_cards_per_day=1)},
                           notifications=policy.NotificationGlobals(max_cards_per_day=2))
        self.store.append_pending("src", [_item(1)])
        self.store.append_pending("other", [_item(2)])
        self.assertEqual(self.notify().card, "sent")
        self.assertEqual(pipeline.notify_source(self.cfg, self.store, "other", now=NOW).card, "sent")
        self.store.append_pending("src", [_item(3)])
        self.assertEqual(self.notify().status, "global_budget_exceeded")
        self.assertEqual(self.store.source_budget_state(DAY, "src")["cards"], 1)
        self.assertEqual(self.store.source_budget_state(DAY, "other")["cards"], 1)
        self.assertEqual(self.store.budget_state(DAY)["cards"], 2)

    def test_source_exception_never_bypasses_global_gates(self):
        self.cfg = replace(self.cfg, policies={"p": policy.NotificationPolicy(bypass_source_limits=True)},
                           notifications=policy.NotificationGlobals(max_cards_per_day=0))
        self.store.append_pending("src", [_item(1)])
        self.assertEqual(self.notify().status, "global_budget_exceeded")
        self.send.assert_not_called()

    def test_immediate_card_uses_new_batches_and_preserves_backlog(self):
        self.store.append_pending("src", [_item(i) for i in range(1, 21)])
        self.assertEqual(self.notify().card, "sent")
        self.assertEqual(self.notify(now=NOW + timedelta(minutes=1)).card, "sent")
        self.assertEqual(self.send.call_count, 2)
        self.assertEqual(self.store.pending_count("src"), 0)
        batches = list((self.store.root / "batches" / DAY).glob("*.json"))
        self.assertEqual(len(batches), 2)
        self.assertEqual(self.store.source_budget_state(DAY, "src")["cards"], 2)

    def test_append_update_counts_one_operation_and_uses_same_card(self):
        self.cfg = replace(self.cfg, policies={"p": policy.NotificationPolicy(form="append_card")})
        self.store.append_pending("src", [_item(1)])
        self.notify()
        self.store.append_pending("src", [_item(2)])
        self.assertEqual(self.notify(now=NOW + timedelta(minutes=1)).card, "updated")
        self.assertEqual(self.send.call_count, 1)
        self.assertEqual(self.update.call_count, 1)
        self.assertEqual(self.store.source_budget_state(DAY, "src")["cards"], 2)

    def test_collect_mode_consumes_locally_without_model_or_send(self):
        self.cfg = replace(self.cfg, policies={"p": policy.NotificationPolicy(mode="collect")})
        with patch.object(pipeline, "_enrich_new", side_effect=AssertionError("local collection enriched")):
            self.assertEqual(self.collect(force=True).status, "state_only")
        self.assertEqual(self.store.pending_count("src"), 0)
        self.assertEqual(self.store.pushed_ids("src"), {"e1"})
        self.cfg = replace(self.cfg, policies={"p": policy.NotificationPolicy()})
        self.assertEqual(self.notify().status, "nothing_new")
        self.send.assert_not_called()

    def test_source_budget_read_is_side_effect_free_and_incomplete_history_fails_closed(self):
        path = self.store._path("budget", f"{DAY}.json")
        path.write_text(json.dumps({"cards": 2, "operations": ["lost-1", "lost-2"]}))
        before = path.read_bytes()
        fn = getattr(self.store, "source_budget_state", None)
        self.assertIsNotNone(fn, "source accounting must expose a read-only view")
        self.assertFalse(fn(DAY, "src")["attribution_complete"])
        self.assertEqual(path.read_bytes(), before)
        self.store.append_pending("src", [_item(1)])
        self.assertEqual(self.notify().status, "budget_attribution_incomplete")
        self.send.assert_not_called()
        self.assertEqual(self.store.budget_state(DAY)["cards"], 2)

    def test_source_budget_bootstraps_confirmed_events_without_resetting_total(self):
        self.store._path("budget", f"{DAY}.json").write_text(json.dumps({"cards": 1, "operations": ["old"]}))
        events.append(self.store.news_dir, "delivered", source="src", digest=DAY,
                      event_id="ev_delivery_old", ts=NOW, payload={"card": "sent"})
        fn = getattr(self.store, "source_budget_state", None)
        self.assertIsNotNone(fn)
        self.assertEqual(fn(DAY, "src")["cards"], 1)
        self.assertTrue(fn(DAY, "src")["attribution_complete"])
        self.store.append_pending("src", [_item(1)])
        self.cfg = replace(self.cfg, policies={"p": policy.NotificationPolicy(max_cards_per_day=1)})
        self.assertEqual(self.notify().status, "budget_exceeded")

    def test_service_notifies_immediate_when_acquisition_is_throttled(self):
        self.store.set_cursor("src", NOW.timestamp(), at=NOW)
        self.store.append_pending("src", [_item(1)])
        service.run_once(self.cfg, self.store, now=NOW, logger=lambda _: None)
        self.assertEqual(self.send.call_count, 1)
        self.adapter.return_value.fetch.assert_not_called()

    def test_disabled_source_retains_pending(self):
        self.cfg = replace(self.cfg, sources={"src": replace(self.source, enabled=False)})
        self.store.append_pending("src", [_item(1)])
        self.assertEqual(self.collect(force=True).status, "disabled")
        self.assertEqual(self.notify().status, "disabled")
        self.assertEqual(self.store.pending_count("src"), 1)
        self.send.assert_not_called()

    def test_immediate_retry_is_bounded_and_new_content_or_edit_cannot_reset_it(self):
        self.send.side_effect = OSError("temporary send failure")
        self.store.append_pending("src", [_item(1)])
        self.assertEqual(self.notify().status, "send_failed")
        self.assertEqual(self.notify(now=NOW + timedelta(seconds=30)).status, "retry_wait")
        self.assertEqual(self.notify(now=NOW + timedelta(minutes=1)).status, "send_failed")
        self.assertEqual(self.notify(now=NOW + timedelta(minutes=2)).status, "send_failed")
        original = self.store.unfinished_batch("src")
        self.store.append_pending("src", [_item(2)])
        self.cfg = replace(self.cfg, policies={"p": policy.NotificationPolicy(label="Renamed")})
        self.assertEqual(self.notify(now=NOW + timedelta(minutes=3)).status, "retry_exhausted")
        self.assertEqual(self.send.call_count, 3)
        unfinished = self.store.unfinished_batch("src")
        self.assertEqual(unfinished["delivery_id"], original["delivery_id"])
        self.assertEqual(unfinished["send_uuid"], original["send_uuid"])
        self.assertEqual([i["ext_id"] for i in unfinished["items"]], ["e1"])

    def test_unknown_send_beyond_horizon_does_not_touch_remote_entities(self):
        self.send.side_effect = OSError("lost response")
        self.store.append_pending("src", [_item(1)])
        self.notify()
        self.update.reset_mock()
        self.assertEqual(self.notify(now=NOW + timedelta(hours=1)).status, "send_result_unknown")
        self.assertEqual(self.send.call_count, 1)
        self.update.assert_not_called()

    def test_remote_success_repairs_source_and_global_counts_once_while_disabled_and_paused(self):
        self.store.append_pending("src", [_item(1)])
        real_append = events.append
        fail = [True]
        def append(*args, **kwargs):
            if args[1] == "delivered" and fail[0]:
                fail[0] = False
                raise OSError("receipt storage interrupted")
            return real_append(*args, **kwargs)
        with patch.object(events, "append", side_effect=append):
            self.assertEqual(self.notify().status, "error")
        self.cfg = replace(self.cfg, notifications=policy.NotificationGlobals(paused=True),
                           sources={"src": replace(self.source, enabled=False)})
        self.assertEqual(self.notify().status, "ok")
        self.assertEqual(self.send.call_count, 1)
        self.assertEqual(self.store.source_budget_state(DAY, "src")["cards"], 1)
        self.assertEqual(self.store.budget_state(DAY)["cards"], 1)
        self.assertEqual(len(events.list_events(self.store.news_dir, days=None, types=["delivered"])), 1)

    def test_digest_progress_does_not_refetch_after_send_failure_or_repeat_after_slot_edit(self):
        self.source = replace(self.source, fetch=config.FetchCfg(trigger="slot", slots=("am",)))
        self.cfg = replace(self.cfg, sources={"src": self.source},
                           policies={"p": policy.NotificationPolicy(mode="digest", slots=("am",))})
        self.send.side_effect = [OSError("temporary"), "message"]
        service.run_once(self.cfg, self.store, now=NOW, logger=lambda _: None)
        service.run_once(self.cfg, self.store, now=NOW + timedelta(minutes=1), logger=lambda _: None)
        self.assertEqual(self.adapter.return_value.fetch.call_count, 1)
        self.assertEqual(self.send.call_count, 2)
        self.store.append_pending("src", [_item(2)])
        self.cfg = replace(self.cfg, slots={"am": "08:17", "pm": "18:35"})
        service.run_once(self.cfg, self.store, now=NOW + timedelta(minutes=2), logger=lambda _: None)
        self.assertEqual(self.send.call_count, 2)
        self.assertEqual(self.store.pending_count("src"), 1)

    def test_digest_blocked_slot_is_intentionally_complete_and_retains_pending(self):
        self.cfg = replace(self.cfg, policies={"p": policy.NotificationPolicy(mode="digest", slots=("am",))},
                           notifications=policy.NotificationGlobals(paused=True))
        self.store.set_cursor("src", NOW.timestamp(), at=NOW)
        self.store.append_pending("src", [_item(1)])
        service.run_once(self.cfg, self.store, now=NOW, logger=lambda _: None)
        self.assertTrue(service.load_runs(self.store.news_dir)["notification_slots"][DAY]["am"]["src"]["complete"])
        self.cfg = replace(self.cfg, notifications=None)
        service.run_once(self.cfg, self.store, now=NOW + timedelta(minutes=1), logger=lambda _: None)
        self.send.assert_not_called()
        self.assertEqual(self.store.pending_count("src"), 1)

    def test_service_attempts_only_one_new_immediate_operation_per_tick(self):
        self.store.set_cursor("src", NOW.timestamp(), at=NOW)
        self.store.append_pending("src", [_item(i) for i in range(1, 41)])
        service.run_once(self.cfg, self.store, now=NOW, logger=lambda _: None)
        self.assertEqual(self.send.call_count, 1)
        self.assertEqual(self.store.pending_count("src"), 27)

    def test_policy_and_destination_edit_do_not_redirect_unfinished_operation(self):
        self.store.append_pending("src", [_item(1)])
        self.send.side_effect = [OSError("lost response"), "message"]
        self.notify()
        before = self.store.unfinished_batch("src")
        self.cfg = replace(self.cfg, chat="oc_new", policies={"p": policy.NotificationPolicy(form="append_card")})
        self.notify(now=NOW + timedelta(minutes=1))
        after = self.store.read_batch(DAY, "src", before["slot"])
        self.assertEqual(after["delivery_id"], before["delivery_id"])
        self.assertEqual(after["form"], "card")
        self.assertEqual(after["delivery_chat"], "oc_test")
        self.assertEqual(self.send.call_args.args[0], "oc_test")

    def test_digest_confirmed_repair_completes_notification_slot_without_resending_new_content(self):
        self.cfg = replace(self.cfg, policies={"p": policy.NotificationPolicy(mode="digest", slots=("am",))})
        self.store.set_cursor("src", NOW.timestamp(), at=NOW)
        self.store.append_pending("src", [_item(1)])
        with patch.object(events, "append", side_effect=OSError("receipt interrupted")):
            service.run_once(self.cfg, self.store, now=NOW, logger=lambda _: None)
        self.store.append_pending("src", [_item(2)])
        service.run_once(self.cfg, self.store, now=NOW + timedelta(minutes=1), logger=lambda _: None)
        service.run_once(self.cfg, self.store, now=NOW + timedelta(minutes=2), logger=lambda _: None)
        self.assertEqual(self.send.call_count, 1)
        self.update.assert_not_called()
        self.assertTrue(service.load_runs(self.store.news_dir)["notification_slots"][DAY]["am"]["src"]["complete"])
        self.assertEqual(self.store.pending_count("src"), 1)

    def test_explicit_global_pause_also_constrains_legacy_high_priority(self):
        legacy = replace(_src(priority="high"), notify=None)
        self.cfg = replace(self.cfg, sources={"src": legacy}, notifications=policy.NotificationGlobals(paused=True))
        run = pipeline.run_slot(self.cfg, self.store, "am", now=NOW)[0]
        self.assertEqual(run.status, "notifications_paused")
        self.send.assert_not_called()

    def test_normal_digest_run_does_not_repeat_completed_slot(self):
        self.cfg = replace(self.cfg, policies={"p": policy.NotificationPolicy(mode="digest", slots=("am",))})
        self.store.append_pending("src", [_item(1)])
        self.assertEqual(self.notify(slot="am").card, "sent")
        self.store.append_pending("src", [_item(2)])
        self.assertEqual(self.notify(slot="am", now=NOW + timedelta(minutes=1)).status, "slot_complete")
        self.update.assert_not_called()
        self.assertEqual(self.store.pending_count("src"), 1)
        self.assertEqual(self.notify(bypass_clock=True, now=NOW + timedelta(minutes=1)).card, "sent")

    def test_temporary_doctor_card_budget_without_real_credentials(self):
        (self.store.news_dir / "sources.yaml").write_text(
            "chat: oc_test\nslots: {}\nsources: {}\npolicies: {p: {mode: immediate}}\n")
        with patch.object(credentials, "resolve_feishu", return_value=credentials.FeishuCreds("fake", "fake")):
            res = cli.run_subcommand(["doctor", "--json"], self.store.news_dir).as_dict()
        self.assertTrue(res["ok"], res)
        self.assertGreaterEqual(res["data"]["card_budget"]["headroom"], 0)
        self.assertEqual(res["data"]["inbound"], "none")
