"""Store-owned history is durable, read-only to consumers, and legacy-safe."""
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from newspipe import state
from newspipe.observations import prepare_observation
from newspipe.story_matching import rank_candidates
from newspipe.story_state import Ledger, StoryStorageError
from test_observations import article, TEXT

NOW = datetime(2026, 11, 12, 12)


class StoryStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = state.Store(Path(self.tmp.name))

    def draft(self, native=1, source="one", **changes):
        return prepare_observation(source, "rsshub", article(native, **changes))

    def snapshot(self, drafts, at=NOW, scan_limit=500):
        return self.store.observation_capture_snapshot(drafts, at=at, window_days=14,
                                                      candidate_limit=5, scan_limit=scan_limit)

    def record(self, drafts, *, snapshot=None, at=NOW, mode="observe", revision="r1", current=None, baselines=None):
        snapshot = self.snapshot(drafts, at) if snapshot is None else snapshot
        proposals = []
        for draft in drafts:
            previous = snapshot.latest_by_identity.get(draft.identity_hash)
            legacy = (baselines or {}).get(str(draft.native_id), {}).get("draft")
            proposals += rank_candidates(draft, list(snapshot.candidates) + ([previous or legacy] if previous or legacy else []), limit=5)
        return self.store.record_observations(drafts, at=at, mode=mode, config_revision=revision,
                                             current_revision=current or revision,
                                             expected_activation_id=snapshot.activation_id,
                                             snapshot=snapshot, proposals=proposals, baselines=baselines or {})

    def db(self):
        return self.store.root / "stories" / "history.sqlite3"

    def test_read_missing_history_creates_no_files(self):
        self.assertEqual(self.store.story_status()["availability"], "not_initialized")
        self.assertEqual(self.store.observations()["observations"], [])
        self.assertEqual(self.store.story_preview()["receipts"], [])
        self.assertIsNone(self.store.observation("ob_missing"))
        self.assertEqual(list(Path(self.tmp.name).rglob("*")), [])

    def test_repeated_version_keeps_original_and_advances_capture(self):
        first = self.record([self.draft(score=1)])
        with sqlite3.connect(self.db()) as conn:
            original = conn.execute("SELECT original_json FROM observations").fetchone()[0]
        second = self.record([self.draft(score=900)])
        with sqlite3.connect(self.db()) as conn:
            self.assertEqual(conn.execute("SELECT original_json FROM observations").fetchone()[0], original)
        self.assertEqual(second["inserted"], 0)
        self.assertEqual(second["repeated"], 1)
        self.assertGreater(second["capture_sequence"], first["capture_sequence"])
        self.assertEqual(self.store.story_status()["observation_count"], 1)

    def test_changed_and_late_versions_keep_ingestion_order_after_restart(self):
        a, b = self.draft(pub_ts=200), self.draft(source_text=TEXT + " Correction.", pub_ts=0)
        self.record([a])
        self.record([b])
        self.store = state.Store(self.store.news_dir)
        first = self.store.observations(limit=1)
        second = self.store.observations(after=first["next_after"], limit=1)
        self.assertTrue(first["has_more"])
        self.assertEqual(first["observations"][0]["observation_id"], a.observation_id)
        self.assertEqual(second["observations"][0]["observation_id"], b.observation_id)
        self.assertEqual(second["observations"][0]["pub_ts"], 0)
        self.assertEqual(self.store.story_preview()["receipts"][1]["proposals"][0]["relation"], "unclassified_change")

    def test_old_same_native_baseline_is_not_limited_by_candidate_window(self):
        first = self.draft()
        self.record([first], at=NOW - timedelta(days=200))
        snapshot = self.snapshot([self.draft(source_text=TEXT + " Update.")])
        self.assertEqual(snapshot.candidates, ())
        self.assertEqual(snapshot.latest_by_identity[first.identity_hash].observation_id, first.observation_id)
        self.assertEqual(self.store.story_status()["observation_count"], 1)

    def test_candidate_scan_and_text_are_bounded_without_losing_original_evidence(self):
        drafts = [self.draft(n, source_text=TEXT + "word " * 5000 + str(n)) for n in range(60)]
        self.record(drafts)
        snapshot = self.snapshot([self.draft(100)], scan_limit=50)
        self.assertEqual(len(snapshot.candidates), 50)
        self.assertIn("scan_limit_reached", snapshot.limitations)
        self.assertTrue(all(len(d.evidence.text) <= 20000 for d in snapshot.candidates))
        detail = self.store.observation(drafts[0].observation_id)
        self.assertTrue(detail["evidence"]["text_truncated"])
        self.assertEqual(self.store.story_status()["observation_count"], 60)

    def test_stale_settings_and_history_do_not_restore_frontiers_or_save_proposals(self):
        self.record([self.draft()])
        snapshot = self.snapshot([self.draft(2)])
        self.record([], mode="off", revision="r2")
        active = self.store.story_status()["activation_id"]
        stale = self.record([self.draft(2)], snapshot=snapshot, current="r2")
        self.assertEqual(stale["status"], "stale_settings")
        self.assertEqual(stale["proposal_count"], 0)
        self.assertEqual(self.store.story_status()["activation_id"], active)
        self.record([self.draft()], revision="r1")
        self.assertNotEqual(self.store.story_status()["activation_id"], snapshot.activation_id)
        snapshot = self.snapshot([self.draft(source_text=TEXT + " Third.")])
        self.record([self.draft(source_text=TEXT + " Second.")])
        stale = self.record([self.draft(source_text=TEXT + " Third.")], snapshot=snapshot)
        self.assertEqual(stale["status"], "stale_history")
        self.assertEqual(stale["proposal_count"], 0)

    def test_transaction_interruption_leaves_no_partial_versions_or_activation(self):
        original = Ledger._insert_observation
        def interrupted(ledger, conn, draft, at, origin):
            if draft.native_id == 2:
                raise OSError("PRIVATE failure")
            return original(ledger, conn, draft, at, origin)
        with patch.object(Ledger, "_insert_observation", autospec=True, side_effect=interrupted):
            with self.assertRaises(StoryStorageError) as error:
                self.record([self.draft(1), self.draft(2)])
        self.assertNotIn("PRIVATE", str(error.exception))
        self.assertEqual(self.store.story_status()["observation_count"], 0)
        self.assertIsNone(self.store.story_status()["activation_id"])

    def test_unknown_id_is_not_storage_failure_and_reads_never_repair(self):
        self.record([self.draft()])
        before = {p: p.read_bytes() for p in self.store.root.rglob("*") if p.is_file()}
        self.assertIsNone(self.store.observation("ob_missing"))
        self.store.observations()
        self.store.story_preview()
        self.assertEqual(before, {p: p.read_bytes() for p in self.store.root.rglob("*") if p.is_file()})
        self.db().write_bytes(b"not a database PRIVATE")
        with self.assertRaises(StoryStorageError) as error:
            self.store.observation("ob_missing")
        self.assertEqual(error.exception.code, "corrupt")
        self.assertNotIn(str(self.db()), str(error.exception))
        self.assertEqual(self.db().read_bytes(), b"not a database PRIVATE")

    def test_locked_and_future_version_databases_fail_loudly(self):
        self.record([self.draft()])
        with sqlite3.connect(self.db()) as conn:
            conn.execute("BEGIN EXCLUSIVE")
            with self.assertRaises(StoryStorageError) as error:
                self.store.story_status()
            self.assertEqual(error.exception.code, "locked")
            conn.rollback()
            conn.execute("PRAGMA user_version=99")
        with self.assertRaises(StoryStorageError) as error:
            self.store.story_preview()
        self.assertEqual(error.exception.code, "version")

    def test_read_snapshot_keeps_one_sql_revision(self):
        self.record([self.draft()])
        with self.store._story_ledger()._connection() as reader:
            before = reader.execute("SELECT captures FROM metadata").fetchone()[0]
            with sqlite3.connect(self.db(), timeout=0.001) as writer:
                writer.execute("UPDATE metadata SET captures=captures+1")
                with self.assertRaises(sqlite3.OperationalError):
                    writer.commit()
                writer.rollback()
            self.assertEqual(reader.execute("SELECT captures FROM metadata").fetchone()[0], before)

    def test_truncated_urls_are_not_offered_as_original_links(self):
        draft = self.draft(url="https://example.com/" + "x" * 3000,
                           referenced_urls=[{"role": "article", "url": "https://example.com/" + "y" * 3000}])
        self.record([draft])
        evidence = self.store.observation(draft.observation_id)["evidence"]
        self.assertTrue(evidence["permalink"]["redacted"])
        self.assertFalse(evidence["permalink"]["clickable"])
        self.assertTrue(evidence["references"][0]["redacted"])
        self.assertFalse(evidence["references"][0]["clickable"])

    def test_legacy_original_material_is_reused_without_touching_existing_ledgers(self):
        old = {"ext_id": 1, "title": "Toolkit version 1", "summary": "Original version one material."}
        self.store.record_pushed("one", [old], "2026-11-01")
        self.store.write_batch({"digest": "2026-11-01", "source": "one", "adapter": "rsshub", "slot": "am", "items": [old]})
        before = {p: p.read_bytes() for p in self.store.root.rglob("*") if p.is_file()}
        baselines = self.store.original_observation_baselines("one", {"1"})
        self.assertEqual(baselines["1"]["availability"], "limited_original")
        self.record([self.draft()], baselines=baselines)
        receipt = self.store.story_preview()["receipts"][0]
        self.assertEqual(receipt["proposals"][0]["relation"], "unclassified_change")
        self.assertEqual({p: p.read_bytes() for p in before}, before)

    def test_generated_truncated_and_missing_legacy_material_make_silent_uncertain_baselines(self):
        for native, item in ((1, {"ext_id": 1, "title_zh": "生成标题", "summary_zh": TEXT}),
                             (2, {"ext_id": 2, "title": "Title", "summary": TEXT, "source_text_truncated": True}),
                             (3, None)):
            self.store.record_pushed("one", [{"ext_id": native}], "2026-11-01")
            if item:
                self.store.write_batch({"digest": "2026-11-01", "source": "one", "adapter": "rsshub", "slot": str(native), "items": [item]})
            baselines = self.store.original_observation_baselines("one", {str(native)})
            self.record([self.draft(native)], baselines=baselines)
            receipt = self.store.story_preview(observation_id=self.draft(native).observation_id)["receipts"][0]
            self.assertEqual(receipt["proposals"], [])
            self.assertIn("migration_baseline_missing_material", receipt["limitations"])
        self.record([self.draft(3, source_text=TEXT + " Later development.")])
        self.assertEqual(self.store.story_preview()["receipts"][-1]["proposals"][0]["relation"], "unclassified_change")

    def test_public_contracts_never_expose_original_extras_or_secret_links(self):
        draft = self.draft(url="https://user:PRIVATE@example.com/a?token=PRIVATE", headers={"Authorization": "PRIVATE"})
        self.record([draft])
        out = self.store.observation(draft.observation_id)
        self.assertNotIn("PRIVATE", json.dumps(out))
        self.assertNotIn("original_json", out)
        self.assertTrue(out["evidence"]["permalink"]["redacted"])
        self.assertFalse(out["evidence"]["permalink"]["clickable"])
        for after, limit in ((-1, 50), (0, 0), (0, 101), (0, True)):
            with self.assertRaises(ValueError):
                self.store.observations(after=after, limit=limit)
