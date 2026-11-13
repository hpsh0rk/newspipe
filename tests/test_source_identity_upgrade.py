"""Full RSS identities recognize old receipts without merging new prefix collisions."""
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import yaml

from newspipe import channel, cli, config, dedup, events, pipeline, runtime, state, view
from newspipe.adapters import rsshub
from newspipe.source_contract import SourceContractError, normalize_fetch
from test_feedparse import rss

PREFIX = "p" * 120
FIRST, SECOND = PREFIX + "-first", PREFIX + "-second"
NOW = datetime(2026, 11, 13, 12)


def feed(ids=(FIRST, SECOND), text="Original source " * 30):
    entries = []
    for native in ids:
        entries.append(rss(text, guid=native).split(b'<item>')[1].split(b'</item>')[0])
    return b'<rss xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>' + b''.join(b'<item>' + e + b'</item>' for e in entries) + b'</channel></rss>'


class SourceIdentityUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.news = Path(self.tmp.name)
        self.store = state.Store(self.news)
        raw = {"chat": "oc_test", "slots": {}, "stories": {"mode": "observe"}, "sources": {"src": {
            "adapter": "rsshub", "feeds": ["/rss"], "fetch": {"trigger": "manual"},
            "enrich": {"summary": "off"}, "notify": {"policy": "p"}}}, "policies": {"p": {"mode": "immediate"}}}
        (self.news / "sources.yaml").write_text(yaml.safe_dump(raw))
        self.cfg = config.load(self.news)

    def fetched(self, ids=(FIRST, SECOND)):
        with patch("newspipe.net.request", return_value=feed(ids)):
            return normalize_fetch(rsshub.fetch({"feeds": ["/rss"]}), source="src", adapter="rsshub").items

    def collect(self, ids=(FIRST, SECOND), **kw):
        with patch("newspipe.net.request", return_value=feed(ids, **kw)), \
                patch.object(pipeline, "_adapter", return_value=rsshub), \
                patch.object(runtime, "activate", side_effect=lambda *_: nullcontext()), \
                patch("newspipe.channel.send_card", side_effect=AssertionError("live send")), \
                patch.object(pipeline, "_enrich_new", side_effect=AssertionError("model access")):
            return pipeline.collect_source(self.cfg, self.store, "src", now=NOW)

    def test_colliding_legacy_prefixes_keep_distinct_full_native_ids(self):
        items = self.fetched()
        self.assertEqual([i["ext_id"] for i in items], [FIRST, SECOND])
        self.assertEqual([i["legacy_ext_id"] for i in items], [PREFIX, PREFIX])
        self.assertTrue(all(i["identity_version"] == "rss-full-v2" for i in items))
        self.assertEqual(len(dedup.fresh(items, self.store, "src")), 2)

    def test_new_exact_prefix_and_long_guid_remain_distinct_in_either_order(self):
        for ids in ((PREFIX, FIRST), (FIRST, PREFIX)):
            with self.subTest(ids=ids):
                items = self.fetched(ids)
                self.assertEqual([i["ext_id"] for i in dedup.fresh(items, self.store, "src")], list(ids))

    def test_new_exact_prefix_receipt_and_pending_are_not_legacy_aliases(self):
        items = self.fetched((PREFIX, FIRST))
        self.store.record_pushed("src", items[:1], "2026-11-13")
        self.assertEqual([i["ext_id"] for i in dedup.fresh(items[1:], self.store, "src")], [FIRST])
        self.store.append_pending("src", items[:1])
        self.collect((FIRST,))
        self.assertEqual([i["ext_id"] for i in self.store.peek_pending("src")], [PREFIX, FIRST])
        self.assertNotIn("migration_baseline_missing_material", self.store.story_preview()["receipts"][0]["limitations"])

    def test_dedup_never_copies_the_entire_history_for_each_item(self):
        class History(set):
            def __or__(self, other):
                raise AssertionError("per-item history copying")
        with patch.object(self.store, "pushed_ids", return_value=History({"old"})):
            self.assertEqual(len(dedup.fresh([{"ext_id": "new"}], self.store, "src")), 1)

    def test_old_completed_prefix_suppresses_replay_but_is_an_uncertain_baseline(self):
        self.store.record_pushed("src", [{"ext_id": PREFIX}], "2026-11-12")
        before = {p: p.read_bytes() for p in (self.store.root / "pushed").rglob("*") if p.is_file()}
        self.assertEqual(self.collect().status, "collected")
        self.assertEqual(self.store.pending_count("src"), 0)
        self.assertEqual(self.store.pushed_ids("src"), {PREFIX})
        self.assertEqual(self.store.story_status()["observation_count"], 2)
        receipt = self.store.story_preview()["receipts"][0]
        self.assertIn("migration_baseline_missing_material", receipt["limitations"])
        self.assertEqual(receipt["proposals"], [])
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_old_pending_identity_is_not_rewritten_or_duplicated(self):
        self.store.append_pending("src", [{"ext_id": PREFIX, "title": "Old original"}])
        before = (self.store.root / "pending/src.jsonl").read_bytes()
        self.collect()
        self.assertEqual(self.store.pending_count("src"), 1)
        self.assertEqual((self.store.root / "pending/src.jsonl").read_bytes(), before)

    def test_collection_preserves_frozen_payload_and_favorite_intent(self):
        self.store.append_pending("src", [{"ext_id": PREFIX, "title": "Old original", "url": "https://publisher.example.invalid/old"}])
        with patch.object(runtime, "activate", side_effect=lambda *_: nullcontext()), \
                patch.object(channel, "create_entity", return_value="card"), \
                patch.object(channel, "send_card", side_effect=OSError("lost response")):
            pipeline.notify_source(self.cfg, self.store, "src", now=NOW)
        before = self.store.unfinished_batch("src")
        self.assertIsNotNone(before)
        favorite = events.append(self.news, "favorite", source="src", ts=NOW,
                                 payload={"item_id": PREFIX, "title": "Old original", "url": "https://publisher.example.invalid/old"})
        self.collect()
        self.assertEqual(self.store.unfinished_batch("src"), before)
        self.assertEqual([e for e in events.list_events(self.news, days=None) if e["type"] == "favorite"], [favorite])

    def test_partial_real_adapter_collection_retains_cursor_then_recovers_after_restart(self):
        source = replace(self.cfg.sources["src"], extra={"feeds": ["/good", "/bad"]})
        self.cfg = replace(self.cfg, sources={"src": source})
        self.store.set_cursor("src", 50, at=NOW)
        import urllib.error
        def request(url, **_):
            if url.endswith("/bad"):
                raise urllib.error.HTTPError("SECRET", 403, "SECRET", {}, None)
            return rss("Original " * 80, guid="good", date="Thu, 01 Jan 1970 00:01:40 +0000")
        with patch("newspipe.net.request", side_effect=request), patch.object(pipeline, "_adapter", return_value=rsshub), patch.object(runtime, "activate", side_effect=lambda *_: nullcontext()):
            run = pipeline.collect_source(self.cfg, self.store, "src", now=NOW)
        self.assertEqual(run.status, "partial_failure")
        self.assertEqual(self.store.cursor("src"), 50)
        self.assertEqual(self.store.pending_count("src"), 1)
        self.assertNotIn("SECRET", json.dumps(run.as_dict()))
        self.store = state.Store(self.news)
        def recovered(url, **_):
            native, date = ("bad", "Thu, 01 Jan 1970 00:01:50 +0000") if url.endswith("/bad") else ("good", "Thu, 01 Jan 1970 00:01:40 +0000")
            return rss("Original " * 80, guid=native, date=date)
        with patch("newspipe.net.request", side_effect=recovered), patch.object(pipeline, "_adapter", return_value=rsshub), patch.object(runtime, "activate", side_effect=lambda *_: nullcontext()):
            run = pipeline.collect_source(self.cfg, self.store, "src", now=NOW)
        self.assertEqual(run.status, "collected")
        self.assertEqual(self.store.cursor("src"), 100)
        self.assertEqual([i["ext_id"] for i in self.store.peek_pending("src")], ["good", "bad"])
        self.assertEqual(self.store.story_status()["observation_count"], 2)

    def test_synthetic_doctor_and_view_do_not_collect_or_resolve_real_credentials(self):
        class Credentials:
            def describe(self):
                return {"available": True}
        with patch("newspipe.credentials.resolve_feishu", return_value=Credentials()), \
                patch("newspipe.net.request", side_effect=AssertionError("network access")):
            out = cli.run_subcommand(["doctor", "--json"], self.news).as_dict()
            self.assertTrue(out["ok"], out)
            self.assertLessEqual(out["data"]["card_budget"]["elements"], 200)
            self.assertEqual(view.build(self.news)["stories"]["saved_mode"], "observe")

    def test_new_full_completion_never_registers_ambiguous_prefix_as_completed(self):
        items = self.fetched()
        self.store.record_pushed("src", items[:1], "2026-11-13")
        self.assertEqual(self.store.pushed_ids("src"), {FIRST})
        self.assertEqual([i["ext_id"] for i in dedup.fresh(items, self.store, "src")], [SECOND])

    def test_new_pending_collisions_survive_restart_and_retry_without_duplicates(self):
        self.collect()
        self.store = state.Store(self.news)
        self.collect()
        self.assertEqual([i["ext_id"] for i in self.store.peek_pending("src")], [FIRST, SECOND])

    def test_alias_validation_is_narrow_and_unknown_legacy_extras_stay_compatible(self):
        item = {"ext_id": FIRST, "identity_version": "rss-full-v2", "legacy_ext_id": "wrong"}
        with self.assertRaises(SourceContractError):
            normalize_fetch([item], source="src", adapter="rsshub")
        legacy = {"ext_id": "short", "legacy_ext_id": {"unknown": "keep"}}
        self.assertEqual(normalize_fetch([legacy], source="src", adapter="custom").items, [legacy])


if __name__ == "__main__":
    unittest.main()
