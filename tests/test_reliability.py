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
