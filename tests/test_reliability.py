"""Offline regressions for workflow recovery and shared state contracts."""
from __future__ import annotations

import json
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from newspipe import backends, channel, cli, config, edit, events, interaction, pipeline, service, state
from test_news_pipeline import _item, _src, _write_registry

NOW = datetime(2026, 11, 10, 10)


def cfg_for(source):
    return SimpleNamespace(chat="oc_test", sources={source.name: source},
                           slot_sources=lambda slot: [source], hooks=None,
                           models={}, budget=lambda: {})


class WorkflowRecoveryTests(unittest.TestCase):
    def test_cross_day_local_repair_does_not_skip_todays_only_slot(self):
        with TemporaryDirectory() as tmp:
            news = Path(tmp)
            store = state.Store(news)
            cfg = config.Config(chat="oc_test", slots={"am": "08:15"}, sources={"src": _src()})
            before, after = NOW.replace(day=9, hour=8, minute=15), NOW.replace(hour=8, minute=16)
            actual_append = events.append
            failed = [False]
            def append(*args, **kwargs):
                if args[1] == "delivered" and not failed[0]:
                    failed[0] = True
                    raise OSError("temporary event storage failure")
                return actual_append(*args, **kwargs)
            with patch.object(pipeline, "_adapter") as adapter, patch.object(events, "append", side_effect=append), \
                 patch.object(channel, "create_entity", return_value="card"), \
                 patch.object(channel, "send_card", return_value="message") as send, \
                 patch.object(channel, "update_entity", return_value=True) as update:
                adapter.return_value.fetch.return_value = [_item(1)]
                pipeline.run_slot(cfg, store, "am", now=before)
                adapter.return_value.fetch.return_value = [_item(2)]
                service.run_once(cfg, store, now=after, news_dir=news, logger=lambda _m: None)
            self.assertEqual(adapter.return_value.fetch.call_count, 2)
            self.assertEqual(store.pushed_ids("src"), {"e1", "e2"})
            self.assertEqual(send.call_count, 2)
            self.assertEqual(update.call_count, 0)
            self.assertIsNotNone(store.read_batch("2026-11-10", "src", "am"))
            self.assertEqual(service.load_runs(news)["slots"]["2026-11-10"], ["am"])

    def test_successful_delivery_recovers_each_local_completion_step_once(self):
        for fault in ("pushed", "budget", "event", "heartbeat", "cursor", "finalize"):
            with self.subTest(fault=fault), TemporaryDirectory() as tmp:
                store = state.Store(Path(tmp))
                source = _src(trigger="poll")
                cfg = cfg_for(source)
                store.set_cursor("src", NOW.timestamp() - 3600, at=NOW)
                fail = [True]
                originals = {"pushed": store.record_pushed, "budget": store.note_card,
                             "event": events.append, "heartbeat": store.heartbeat,
                             "cursor": store.set_cursor, "finalize": store.write_batch}
                def interrupted(*args, **kwargs):
                    is_completion = (fault != "event" or args[1] == "delivered")
                    if fault == "finalize":
                        is_completion = args[0].get("delivery_pending") is False
                    if is_completion and fail[0]:
                        fail[0] = False
                        if fault != "finalize":
                            originals[fault](*args, **kwargs)
                        raise OSError(f"interrupted {fault}")
                    return originals[fault](*args, **kwargs)
                target, method = ((events, "append") if fault == "event" else
                                  (store, {"pushed": "record_pushed", "budget": "note_card",
                                           "heartbeat": "heartbeat", "cursor": "set_cursor",
                                           "finalize": "write_batch"}[fault]))
                with patch.object(pipeline, "_adapter", return_value=SimpleNamespace(fetch=lambda *a, **k: [_item(1)])), \
                     patch.object(channel, "create_entity", return_value="card"), \
                     patch.object(channel, "send_card", return_value="message") as send, \
                     patch.object(channel, "update_entity", return_value=True) as update, \
                     patch.object(target, method, side_effect=interrupted):
                    try:
                        pipeline._process(cfg, store, source, digest="2026-11-10", slot="alert", dry=False, now=NOW, mode="poll")
                    except OSError:
                        pass
                    recovered = pipeline._process(cfg, store, source, digest="2026-11-10", slot="alert", dry=False, now=NOW, mode="poll")
                self.assertFalse(fail[0], "fault injection did not reach the completion step")
                self.assertEqual(recovered.status, "ok")
                self.assertEqual(send.call_count, 1)
                self.assertEqual(update.call_count, 0)
                self.assertEqual(store.budget_state("2026-11-10")["cards"], 1)
                self.assertEqual(len(events.list_events(store.news_dir, types=["delivered"])), 1)
                self.assertFalse(store.read_batch("2026-11-10", "src", "alert")["delivery_pending"])

    def test_pending_survives_below_minimum(self):
        with TemporaryDirectory() as tmp:
            store = state.Store(Path(tmp))
            store.append_pending("src", [_item(1)])
            with patch.object(pipeline, "_adapter", return_value=SimpleNamespace(fetch=lambda *a, **k: [])):
                run = pipeline.run_slot(cfg_for(_src(min_items=2)), store, "am", now=NOW)[0]
            self.assertEqual(run.status, "below_min_items")
            self.assertEqual(store.pending_count("src"), 1)

    def test_send_identity_survives_post_send_write_failure(self):
        with TemporaryDirectory() as tmp:
            store = state.Store(Path(tmp))
            actual_write = store.write_batch
            fail = True
            messages = {}
            entities = []
            keys = []

            def save(batch):
                nonlocal fail
                if batch.get("message_id") and fail:
                    fail = False
                    raise OSError("interrupted after remote send")
                return actual_write(batch)

            def create(card):
                entities.append(card)
                return f"card-{len(entities)}"

            def send(chat, card, *, idempotency_key=""):
                keys.append(idempotency_key)
                key = idempotency_key or f"unkeyed-{len(keys)}"
                return messages.setdefault(key, f"message-{len(messages) + 1}")

            cfg = cfg_for(_src())
            with patch.object(pipeline, "_adapter", return_value=SimpleNamespace(fetch=lambda *a, **k: [_item(1)])), patch.object(channel, "create_entity", side_effect=create), patch.object(channel, "send_card", side_effect=send), patch.object(channel, "update_entity", return_value=True), patch.object(store, "write_batch", side_effect=save):
                try:
                    pipeline.run_slot(cfg, store, "am", now=NOW)
                except OSError:
                    pass
                pipeline.run_slot(cfg, store, "am", now=NOW)
            self.assertEqual(len(entities), 1)
            self.assertEqual(len(messages), 1)
            self.assertTrue(keys[0])
            self.assertEqual(store.pushed_ids("src"), {"e1"})
            self.assertEqual(store.pending_count("src"), 0)

    def test_unfinished_delivery_is_recovered_across_batch_dates(self):
        with TemporaryDirectory() as tmp:
            store = state.Store(Path(tmp))
            batch = {"digest": "2026-11-09", "source": "src", "slot": "am", "form": "card",
                     "title": "test", "view": "list", "card_id": "old-card", "seq": 1,
                     "message_id": "already-sent", "delivery_pending": True,
                     "items": [dict(_item(1), id="n01", status="unread")]}
            store.write_batch(batch)
            store.append_pending("src", [_item(1)])
            with patch.object(pipeline, "_adapter", return_value=SimpleNamespace(fetch=lambda *a, **k: [])), patch.object(channel, "update_entity", return_value=True), patch.object(channel, "create_entity", side_effect=AssertionError("must not create another card")):
                run = pipeline.run_slot(cfg_for(_src()), store, "am", now=NOW)[0]
            self.assertEqual(run.status, "ok")
            self.assertEqual(store.pushed_ids("src"), {"e1"})
            self.assertFalse(store.read_batch("2026-11-09", "src", "am")["delivery_pending"])

    def test_concurrent_favorites_preserve_both_states_and_sequences(self):
        with TemporaryDirectory() as tmp:
            news = Path(tmp)
            store = state.Store(news)
            batch = {"digest": "2026-11-10", "source": "src", "slot": "am", "form": "card",
                     "title": "test", "view": "list", "card_id": "card", "seq": 1,
                     "items": [dict(_item(n), id=f"n0{n}", status="unread") for n in (1, 2)]}
            store.write_batch(batch)
            start = threading.Barrier(2)
            original_read = state.read_json
            sequences = []

            def read(path):
                value = original_read(path)
                if str(path).endswith("src-am.json"):
                    time.sleep(0.02)
                return value

            def update(card, seq, content):
                sequences.append(seq)
                time.sleep(0.02)
                return True

            def click(n):
                start.wait(timeout=5)
                return interaction.handle_json(json.dumps({"domain": "news", "news_action": "wiki",
                                                           "id": f"n0{n}", "batch": batch["batch"]}), news_dir=news)

            with patch.object(state, "read_json", side_effect=read), patch.object(channel, "update_entity", side_effect=update):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    list(pool.map(click, (1, 2)))
            saved = store.read_batch("2026-11-10", "src", "am")
            self.assertEqual([i["status"] for i in saved["items"]], ["wiki", "wiki"])
            self.assertEqual(sequences, [2, 3])

    def test_pushed_ledger_does_not_count_same_item_twice(self):
        with TemporaryDirectory() as tmp:
            store = state.Store(Path(tmp))
            store.record_pushed("src", [_item(1)], "2026-11-10")
            store.record_pushed("src", [_item(1), _item(2)], "2026-11-10")
            self.assertEqual(store.pushed_by_day(), {"2026-11-10": {"src": 2}})


class FavoriteContractTests(unittest.TestCase):
    def test_old_favorite_is_visible_and_not_pruned(self):
        with TemporaryDirectory() as tmp:
            news = Path(tmp)
            favorite = events.append(news, "favorite", ts=datetime.now() - timedelta(days=100))
            self.assertEqual([e["id"] for e in events.queue(news)], [favorite["id"]])
            events.prune(news, keep_days=30)
            self.assertEqual([e["id"] for e in events.queue(news)], [favorite["id"]])

    def test_queue_ack_requires_receipt_and_correct_type(self):
        with TemporaryDirectory() as tmp:
            news = Path(tmp)
            favorite = events.append(news, "favorite")
            click = events.append(news, "clicked")
            for args in (["queue", "ack", favorite["id"]],
                         ["queue", "ack", click["id"], "--note", "wiki/page"],
                         ["events", "ack", favorite["id"], "--by", "test"]):
                with self.subTest(args=args):
                    result = cli.run_subcommand([*args, "--json"], news)
                    self.assertFalse(result.ok)
                    self.assertEqual(events.acked_ids(news), set())
            result = cli.run_subcommand(["queue", "ack", favorite["id"], "--note", "wiki/page", "--json"], news)
            self.assertTrue(result.ok)
            self.assertEqual(events.queue(news), [])


class EnrichOnlyReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.news = Path(self.tmp.name)
        self.store = state.Store(self.news)
        source = _src(summary="title")
        self.cfg = cfg_for(source)
        self.batch = {"digest": "2026-11-10", "source": "src", "slot": "am", "form": "card",
                      "title": "test", "view": "list", "card_id": "card", "seq": 1,
                      "items": [dict(_item(1), id="n01", status="unread")]}
        self.store.write_batch(self.batch)

    def run_enrichment(self):
        return cli.cmd_enrich_only(self.store, self.cfg, date="2026-11-10", dry=False,
                                   verbose=False, json_out=False)

    def test_dry_enrichment_does_not_create_a_writer_lock_or_any_state(self):
        before = {str(p.relative_to(self.news)): p.read_bytes() for p in self.news.rglob("*") if p.is_file()}
        with patch.object(pipeline, "_enrich_new", side_effect=AssertionError("dry run must not enrich")):
            cli.cmd_enrich_only(self.store, self.cfg, date="2026-11-10", dry=True,
                                verbose=False, json_out=False)
        after = {str(p.relative_to(self.news)): p.read_bytes() for p in self.news.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_failed_update_still_reserves_sequence_before_the_request(self):
        def enrich(cfg, store, scfg, items, now, **kwargs):
            return items, {"ok": 1}
        reserved = []
        def update(card_id, seq, card):
            reserved.append(self.store.read_batch("2026-11-10", "src", "am")["seq"])
            return False
        with patch.object(pipeline, "_enrich_new", side_effect=enrich), patch.object(channel, "update_entity", side_effect=update):
            self.run_enrichment()
        self.assertEqual(reserved, [2])
        self.assertEqual(self.store.read_batch("2026-11-10", "src", "am")["seq"], 2)

    def test_enrichment_cannot_overwrite_a_concurrent_favorite(self):
        enriching, release, clicked = threading.Event(), threading.Event(), threading.Event()
        def enrich(cfg, store, scfg, items, now, **kwargs):
            enriching.set()
            if not release.wait(timeout=3):
                raise AssertionError("test did not release enrichment")
            return [dict(item, title_zh="translated", enrich_state="ok") for item in items], {"ok": 1}
        def click():
            interaction.handle({"domain": "news", "news_action": "wiki", "id": "n01",
                                "batch": self.batch["batch"]}, news_dir=self.news)
            clicked.set()
        with patch.object(pipeline, "_enrich_new", side_effect=enrich), patch.object(channel, "update_entity", return_value=True):
            with ThreadPoolExecutor(max_workers=2) as pool:
                enrich_future = pool.submit(self.run_enrichment)
                self.assertTrue(enriching.wait(timeout=3))
                click_future = pool.submit(click)
                clicked.wait(timeout=0.1)
                release.set()
                enrich_future.result(timeout=3)
                click_future.result(timeout=3)
        saved = self.store.read_batch("2026-11-10", "src", "am")
        self.assertEqual(saved["items"][0]["status"], "wiki")
        self.assertEqual(saved["items"][0].get("title_zh"), "translated")

    def test_enrichment_uses_the_configured_channel(self):
        self.cfg.service = config.ServiceCfg(channel="feishu_lark_cli")
        selected = []
        def make(name, **kwargs):
            selected.append(name)
            return SimpleNamespace(update_entity=lambda *a: True)
        with patch.object(backends, "make_channel", side_effect=make), patch.object(pipeline, "_enrich_new", return_value=(self.batch["items"], {"ok": 1})), patch.dict("os.environ", {"NEWSPIPE_CHANNEL": "feishu_direct"}):
            self.run_enrichment()
        self.assertEqual(selected, ["feishu_lark_cli"])


class RuntimeAssemblyTests(unittest.TestCase):
    def tearDown(self):
        backends.set_channel(None)
        backends.set_default_channel_name(None)

    def test_switching_channel_clears_previous_override(self):
        with TemporaryDirectory() as tmp:
            news = Path(tmp)
            direct = cfg_for(_src())
            direct.service = config.ServiceCfg(channel="feishu_direct")
            delegated = cfg_for(_src())
            delegated.service = config.ServiceCfg(channel="feishu_lark_cli")
            service.prepare_channel(direct, news_dir=news)
            service.prepare_channel(delegated, news_dir=news)
            self.assertIsInstance(backends.get_channel(), backends.LarkCliChannel)

    def test_cli_pipeline_uses_configured_channel_not_environment(self):
        with TemporaryDirectory() as tmp:
            news = Path(tmp)
            cfg = cfg_for(_src())
            cfg.service = config.ServiceCfg(channel="feishu_lark_cli")
            selected = []

            def make(name, **kwargs):
                selected.append(name)
                return SimpleNamespace(create_entity=lambda card: "card", send_card=lambda *a, **k: "message")

            with patch.object(cli, "_load", return_value=(cfg, state.Store(news))), patch.object(pipeline, "_adapter", return_value=SimpleNamespace(fetch=lambda *a, **k: [_item(1)])), patch.object(backends, "make_channel", side_effect=make), patch.dict("os.environ", {"NEWSPIPE_CHANNEL": "feishu_direct"}):
                result = cli.run_subcommand(["run", "--slot", "am", "--json"], news)
            self.assertTrue(result.ok)
            self.assertEqual(selected, ["feishu_lark_cli"])

    def test_default_service_is_standalone(self):
        self.assertEqual(config.ServiceCfg().channel, "feishu_direct")

    def test_legacy_card_uses_explicit_directory_and_configured_channel(self):
        with TemporaryDirectory() as tmp:
            news = Path(tmp)
            cfg = cfg_for(_src())
            cfg.service = config.ServiceCfg(channel="feishu_lark_cli")
            store = state.Store(news)
            batch = {"digest": "2026-11-10", "source": "src", "slot": "am", "title": "test",
                     "form": "card", "view": "list", "card_id": "card", "seq": 1,
                     "items": [dict(_item(1), id="n01", status="unread")]}
            store.write_batch(batch)
            selected = []
            def make(name, **kwargs):
                selected.append(name)
                return SimpleNamespace(update_entity=lambda *a: True)
            with patch.object(config, "load", return_value=cfg), \
                 patch.object(config, "default_news_dir", return_value=news / "wrong"), \
                 patch.object(backends, "make_channel", side_effect=make):
                code = cli.main(["--news-dir", str(news), "--card", json.dumps({"domain": "news",
                                 "news_action": "wiki", "id": "n01", "batch": batch["batch"]})])
            self.assertEqual(code, 0)
            self.assertEqual(len(events.queue(news)), 1)
            self.assertEqual(selected, ["feishu_lark_cli"])

    def test_legacy_service_passes_the_explicit_directory(self):
        with TemporaryDirectory() as tmp:
            news = Path(tmp)
            with patch.object(config, "load", return_value=cfg_for(_src())), \
                 patch.object(service, "run_forever", return_value=0) as serve:
                self.assertEqual(cli.main(["--news-dir", str(news), "--serve", "--once"]), 0)
            self.assertEqual(serve.call_args.kwargs.get("news_dir"), news)

    def test_doctor_reports_known_ws_conflict_as_configuration_error(self):
        from newspipe import credentials
        with TemporaryDirectory() as tmp:
            news = _write_registry(Path(tmp), {"src": {"adapter": "aihot", "fetch": {"trigger": "slot", "slots": ["am"]}}})
            with patch.object(service, "shared_app_ws_conflict", return_value="same-app ws conflict"), \
                 patch.object(credentials, "resolve_feishu", return_value=credentials.FeishuCreds("cli_test", "test")):
                result = cli.run_subcommand(["doctor", "--json"], news)
            self.assertFalse(result.ok)
            self.assertEqual(result.error["code"], "E_CONFIG")
            self.assertIn("same-app ws conflict", result.error["message"])

    def test_scoped_channels_are_isolated_between_threads(self):
        from newspipe import runtime
        with TemporaryDirectory() as tmp:
            start = threading.Barrier(2)
            def run(name):
                cfg = cfg_for(_src())
                cfg.service = config.ServiceCfg(channel=name)
                with runtime.activate(cfg, Path(tmp)):
                    start.wait(timeout=5)
                    return backends.get_channel().name
            with ThreadPoolExecutor(max_workers=2) as pool:
                self.assertEqual(list(pool.map(run, ("feishu_direct", "feishu_lark_cli"))),
                                 ["feishu_direct", "feishu_lark_cli"])

    def test_concurrent_config_edits_reject_stale_base_hash(self):
        with TemporaryDirectory() as tmp:
            news = _write_registry(Path(tmp), {name: {"adapter": "aihot", "fetch": {"trigger": "slot", "slots": ["am"]}} for name in ("first", "second")})
            base = edit.file_hash(news / "sources.yaml")
            start = threading.Barrier(2)
            actual_apply = edit._apply
            def apply(*args, **kwargs):
                time.sleep(0.02)
                return actual_apply(*args, **kwargs)
            def change(name):
                start.wait(timeout=5)
                return edit.set_source_enabled(news, name, False, base_hash=base)
            with patch.object(edit, "_apply", side_effect=apply):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(change, ("first", "second")))
            self.assertEqual(sum(bool(r.get("ok")) for r in results), 1)
            self.assertEqual(sum(r.get("code") == "E_CONFLICT" for r in results), 1)

    def test_invalid_bulk_ack_writes_nothing(self):
        with TemporaryDirectory() as tmp:
            news = Path(tmp)
            click = events.append(news, "clicked")
            result = cli.run_subcommand(["events", "ack", click["id"], "ev_unknown", "--by", "test", "--json"], news)
            self.assertFalse(result.ok)
            self.assertEqual(events.acked_ids(news), set())

    def test_old_favorite_can_be_acked_with_a_receipt(self):
        with TemporaryDirectory() as tmp:
            news = Path(tmp)
            favorite = events.append(news, "favorite", ts=datetime.now() - timedelta(days=100))
            result = cli.run_subcommand(["queue", "ack", favorite["id"], "--note", "wiki/page", "--json"], news)
            self.assertTrue(result.ok)
            self.assertEqual(events.queue(news), [])
