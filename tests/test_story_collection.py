"""Prospective observation capture never replays delivery, queues or favorites."""
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import yaml

from newspipe import channel, cli, config, events, pipeline, runtime, state
from newspipe.source_contract import FetchResult, SubscriptionReport
from newspipe.story_state import StoryStorageError
from test_observations import article, TEXT

NOW = datetime(2026, 11, 12, 12)
OLD = NOW - timedelta(hours=1)


class StoryCollectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.news = Path(self.tmp.name)
        self.store = state.Store(self.news)
        raw = {"chat": "oc_test", "slots": {"am": "08:15"}, "sources": {"src": {
            "adapter": "hn", "fetch": {"trigger": "poll", "interval_min": 10},
            "enrich": {"summary": "off"}, "notify": {"policy": "p"}}}, "policies": {"p": {"mode": "immediate"}}}
        (self.news / "sources.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
        self.cfg = config.load(self.news)
        self.store.set_cursor("src", OLD.timestamp(), at=OLD)
        self.addCleanup(patch.stopall)
        patch.object(runtime, "activate", side_effect=lambda *_: nullcontext()).start()
        patch("time.monotonic", return_value=0).start()
        self.adapter = patch.object(pipeline, "_adapter").start().return_value
        self.adapter.fetch.return_value = [article(pub_ts=NOW.timestamp())]
        patch.object(channel, "create_entity", return_value="card").start()
        patch.object(channel, "send_card", return_value="message").start()
        patch.object(channel, "update_entity", return_value=True).start()

    def setting(self, mode, refresh=True):
        out = cli.run_subcommand(["stories", "set", "--from-json", json.dumps({"mode": mode})], self.news)
        self.assertTrue(out.ok, out.as_dict())
        if refresh:
            self.cfg = config.load(self.news)

    def collect(self, **kw):
        return pipeline.collect_source(self.cfg, self.store, "src", now=NOW, **kw)

    def test_default_off_and_dry_collection_do_not_initialize_or_write_history(self):
        self.assertEqual(self.collect().status, "collected")
        self.assertEqual(self.store.story_status()["availability"], "not_initialized")
        self.setting("observe")
        before = {p: p.read_bytes() for p in self.news.rglob("*") if p.is_file()}
        self.collect(dry=True)
        self.assertEqual(before, {p: p.read_bytes() for p in self.news.rglob("*") if p.is_file()})

    def test_google_article_identity_survives_multiple_queries_and_search_setting_changes(self):
        from newspipe.adapters import google_news
        from test_google_news_source import search_feed, search_entry, STAMP
        changed = cli.run_subcommand(["source", "patch", "src", "--from-json",
            '{"adapter":"google_news","settings":{"queries":["AI","Models"]}}'], self.news)
        self.assertTrue(changed.ok, changed.as_dict())
        self.setting("observe")
        def response(settings, title="Article title"):
            with patch.object(google_news, "request", return_value=search_feed(search_entry(title=title))), patch.object(
                    google_news.time, "time", return_value=STAMP + 7200):
                return google_news.fetch({"settings": settings})
        self.adapter.fetch.return_value = response({"queries": ["AI", "Models"]})
        self.collect()
        self.assertEqual(self.store.story_status()["observation_count"], 1)
        first = self.store.observations()["observations"][0]
        for settings in ({"queries": ["Different query"], "max_results": 100},
                         {"queries": ["Models"], "language": "en-US", "region": "US", "lookback_days": 30}):
            changed = cli.run_subcommand(["source", "patch", "src", "--from-json",
                                         json.dumps({"settings": settings})], self.news)
            self.assertTrue(changed.ok, changed.as_dict())
            self.cfg = config.load(self.news)
            self.adapter.fetch.return_value = response(settings)
            self.collect()
            self.assertEqual(self.store.story_status()["observation_count"], 1)
            self.assertEqual(self.store.observations()["observations"][0]["identity_hash"], first["identity_hash"])
        self.adapter.fetch.return_value = response(self.cfg.sources["src"].extra["settings"], title="Corrected article title")
        self.collect()
        rows = self.store.observations()["observations"]
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["identity_hash"] for row in rows}, {first["identity_hash"]})
        self.assertEqual(self.store.pending_count("src"), 1)
        self.assertEqual(self.store.cursor("src"), OLD.timestamp())
        self.assertEqual(self.store.usage(NOW.date().isoformat())["calls"], 0)

    def test_originals_are_captured_even_when_filtered_out(self):
        self.setting("observe")
        source = self.cfg.sources["src"]
        self.cfg = replace(self.cfg, sources={"src": replace(source, filter=replace(source.filter, include_keywords=["NEVER_MATCH"]))})
        run = self.collect()
        self.assertEqual(run.kept, 0)
        self.assertEqual(self.store.pending_count("src"), 0)
        self.assertEqual(self.store.story_status()["observation_count"], 1)
        row = self.store.observations()["observations"][0]
        self.assertEqual(row["evidence"]["text"], TEXT)

    def test_same_id_after_delivery_retains_new_version_without_historic_replay(self):
        self.setting("observe")
        self.collect()
        pipeline.notify_source(self.cfg, self.store, "src", now=NOW)
        preserved = {p: p.read_bytes() for sub in ("batches", "pushed", "pending") for p in (self.store.root / sub).rglob("*") if p.is_file()}
        self.adapter.fetch.return_value = [article(source_text=TEXT + " Later correction.", pub_ts=NOW.timestamp())]
        self.collect()
        self.assertEqual(self.store.story_status()["observation_count"], 2)
        self.assertEqual(self.store.pushed_ids("src"), {"1"})
        self.assertEqual(self.store.pending_count("src"), 0)
        self.assertEqual(preserved, {p: p.read_bytes() for p in preserved})
        self.assertEqual(self.store.usage(NOW.date().isoformat())["calls"], 0)
        self.assertEqual(self.store.usage(NOW.date().isoformat())["chars"], 0)

    def test_notify_is_no_fetch_and_never_initializes_or_compares(self):
        self.collect()
        self.setting("observe")
        self.adapter.fetch.side_effect = AssertionError("notification fetched")
        run = pipeline.notify_source(self.cfg, self.store, "src", now=NOW)
        self.assertEqual(run.card, "sent")
        self.assertEqual(self.store.story_status()["availability"], "not_initialized")

    def test_stale_observe_task_cannot_reactivate_old_mode(self):
        self.setting("observe")
        self.collect()
        active = self.store.story_status()["activation_id"]
        def changed_during_transport(*_, **kw):
            self.setting("off", refresh=False)
            return [article(2, pub_ts=NOW.timestamp())]
        self.adapter.fetch.side_effect = changed_during_transport
        run = self.collect()
        self.assertEqual(run.observations["status"], "stale_settings")
        self.assertEqual(run.observations["proposal_count"], 0)
        self.assertEqual(self.store.story_status()["activation_id"], active)
        self.assertEqual(self.store.story_status()["observation_count"], 2)

    def test_off_keeps_collection_running_when_old_history_is_corrupt(self):
        self.setting("observe")
        self.collect()
        self.setting("off")
        self.store._story_ledger().path.write_bytes(b"corrupt")
        self.adapter.fetch.return_value = [article(2, pub_ts=NOW.timestamp())]
        run = self.collect()
        self.assertEqual(run.status, "collected")
        self.assertEqual(run.observations["status"], "unavailable")
        self.assertEqual({i["ext_id"] for i in self.store.peek_pending("src")}, {1, 2})
        self.assertEqual(self.store._story_ledger().path.read_bytes(), b"corrupt")

    def test_off_history_failure_during_transport_is_only_a_warning(self):
        self.setting("observe")
        self.collect()
        self.setting("off")
        def break_history(*args, **kwargs):
            self.store._story_ledger().path.write_bytes(b"corrupt")
            return [article(2, pub_ts=NOW.timestamp())]
        self.adapter.fetch.side_effect = break_history
        run = self.collect()
        self.assertEqual(run.status, "collected")
        self.assertEqual(run.observations["status"], "unavailable")

    def test_modes_are_prospective_and_do_not_migrate_old_pending(self):
        self.store.append_pending("src", [article(99)])
        self.setting("observe")
        self.collect()
        active = self.store.story_status()["activation_id"]
        self.setting("off")
        self.adapter.fetch.return_value = [article(2)]
        self.collect()
        self.assertEqual(self.store.story_status()["activated_mode"], "off")
        self.assertEqual(self.store.story_status()["observation_count"], 1)
        self.setting("observe")
        self.collect()
        self.assertNotEqual(self.store.story_status()["activation_id"], active)
        self.assertEqual({item["ext_id"] for item in self.store.peek_pending("src")}, {1, 2, 99})
        self.assertIsNone(next((r for r in self.store.observations()["observations"] if r["origin"] != "source"), None))

    def test_ledger_failure_keeps_pending_but_not_cursor(self):
        self.setting("observe")
        with patch.object(self.store, "record_observations", side_effect=StoryStorageError("corrupt")):
            run = self.collect()
        self.assertEqual(run.status, "error")
        self.assertEqual(run.observations["status"], "error")
        self.assertEqual(self.store.cursor("src"), OLD.timestamp())
        self.assertEqual(self.store.peek_pending("src"), [article(pub_ts=NOW.timestamp())])
        self.assertIn("原始版本", self.store.status("src")["note"])

    def test_legacy_boundary_captures_before_state_only_completion(self):
        self.setting("observe")
        source = self.cfg.sources["src"]
        self.cfg = replace(self.cfg, sources={"src": replace(source, notify=None, deliver=replace(source.deliver, form="state_only"))})
        run = pipeline.run_source(self.cfg, self.store, "src", now=NOW)[0]
        self.assertEqual(self.store.story_status()["observation_count"], 1)
        self.assertEqual(self.store.pushed_ids("src"), {"1"})
        self.assertEqual(run.card, "none")

    def test_legacy_capture_keeps_original_material_before_display_enrichment(self):
        self.setting("observe")
        source = self.cfg.sources["src"]
        self.cfg = replace(self.cfg, sources={"src": replace(source, notify=None)})
        def display(cfg, store, scfg, items, now, **kw):
            for item in items:
                item.update(title_zh="generated title", summary="generated summary", enrich_state="ok")
            return items, {"ok": len(items)}
        with patch.object(pipeline, "_enrich_new", side_effect=display):
            self.assertEqual(pipeline.run_source(self.cfg, self.store, "src", now=NOW)[0].card, "sent")
        original = self.store.observations()["observations"][0]["evidence"]
        self.assertEqual(original["title"], article()["title"])
        self.assertEqual(original["text"], TEXT)

    def test_partial_results_capture_good_items_without_advancing_cursor(self):
        self.setting("observe")
        self.adapter.fetch.return_value = FetchResult([article(pub_ts=NOW.timestamp())], (
            SubscriptionReport("good", "success", 1, watermark=NOW.timestamp()),
            SubscriptionReport("bad", "failure", code="network")))
        self.collect()
        self.assertEqual(self.store.story_status()["observation_count"], 1)
        self.assertEqual(self.store.cursor("src"), OLD.timestamp())

    def test_legacy_frozen_recovery_survives_observation_failure(self):
        source = self.cfg.sources["src"]
        self.cfg = replace(self.cfg, sources={"src": replace(source, notify=None)})
        self.adapter.fetch.return_value = FetchResult([article(pub_ts=OLD.timestamp())], (
            SubscriptionReport("src", "success", 1),))
        channel.send_card.side_effect = OSError("lost response")
        pipeline.run_source(self.cfg, self.store, "src", now=NOW)
        before = self.store.unfinished_batch("src")
        self.assertIsNotNone(before)
        self.setting("observe")
        self.cfg = replace(self.cfg, sources={"src": replace(self.cfg.sources["src"], notify=None)})
        channel.send_card.side_effect = None
        self.adapter.fetch.return_value = [article(2, pub_ts=NOW.timestamp())]
        with patch.object(self.store, "record_observations", side_effect=StoryStorageError("corrupt")):
            run = pipeline.run_source(self.cfg, self.store, "src", now=NOW)[0]
        self.assertEqual(run.card, "sent")
        self.assertEqual(run.observations["status"], "error")
        self.assertEqual(self.store.cursor("src"), OLD.timestamp())
        self.assertEqual([i["ext_id"] for i in self.store.peek_pending("src")], [2])
        after = self.store.read_batch(before["digest"], "src", before["slot"])
        for key in ("delivery_id", "send_uuid", "delivery_payload", "delivery_chat", "items"):
            self.assertEqual(after[key], before[key])

    def test_empty_success_activates_but_total_failure_does_not(self):
        self.setting("observe")
        self.adapter.fetch.return_value = FetchResult([], (SubscriptionReport("src", "failure", code="network"),))
        self.collect()
        self.assertEqual(self.store.story_status()["availability"], "not_initialized")
        self.adapter.fetch.return_value = []
        self.collect()
        self.assertEqual(self.store.story_status()["activated_mode"], "observe")
        self.assertEqual(self.store.story_status()["observation_count"], 0)

    def test_metric_only_retry_and_restart_do_not_add_versions_or_change_favorites(self):
        self.setting("observe")
        self.collect()
        favorite = events.append(self.news, "favorite", source="src", payload={"title": "saved"},
                                 ts=NOW - timedelta(days=1))
        favorite_path = self.store.root / "events" / (NOW - timedelta(days=1)).strftime("%Y-%m-%d.jsonl")
        before = favorite_path.read_bytes()
        self.store = state.Store(self.news)
        self.cfg = config.load(self.news)
        self.adapter.fetch.return_value = [article(pub_ts=NOW.timestamp(), score=999, comments=123)]
        self.collect()
        self.assertEqual(self.store.story_status()["observation_count"], 1)
        self.assertEqual(favorite_path.read_bytes(), before)
        self.assertEqual(events.queue(self.news)[0]["id"], favorite["id"])

    def test_pending_write_interruption_retries_history_idempotently(self):
        self.setting("observe")
        with patch.object(self.store, "append_pending", side_effect=OSError("synthetic write failure")):
            with self.assertRaises(OSError):
                self.collect()
        self.assertEqual(self.store.cursor("src"), OLD.timestamp())
        self.collect()
        self.assertEqual(self.store.story_status()["observation_count"], 1)
        self.assertEqual(self.store.pending_count("src"), 1)

    def test_settings_aba_during_transport_is_stale_without_advancing_activation(self):
        self.setting("observe")
        self.collect()
        active = self.store.story_status()["activation_id"]
        def aba(*args, **kwargs):
            self.setting("off", refresh=False)
            self.setting("observe", refresh=False)
            return [article(2)]
        self.adapter.fetch.side_effect = aba
        run = self.collect()
        self.assertEqual(run.observations["status"], "stale_settings")
        self.assertEqual(self.store.story_status()["activation_id"], active)
