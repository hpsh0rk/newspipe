"""Only explicit bounded jobs evaluate saved proposals; delivery is untouched."""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from newspipe import cli, config, llm, state, story_compare
from newspipe.story_state import Ledger, StoryStorageError
from test_story_contracts import seed
from test_model_budget import Response

NOW = datetime(2026, 11, 13, 12)


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.news = Path(self.tmp.name)
        (self.news / "sources.yaml").write_text('chat: oc_test\nslots: {}\nsources: {}\nstories: {mode: observe, comparison: manual}\n')
        self.store = state.Store(self.news)
        self.drafts = seed(self.store)
        self.ref = llm.ModelRef("offline", "fixture", "https://model.invalid/v1", "fixture-key", "test")
        self.addCleanup(patch.stopall)
        patch.object(llm, "resolve_model", return_value=self.ref).start()
        for target in ("newspipe.pipeline._adapter", "newspipe.channel.send_card", "newspipe.credentials.resolve_feishu"):
            patch(target, side_effect=AssertionError("external side effect")).start()

    def evaluate(self, **kwargs):
        return story_compare.evaluate(self.store, config.load(self.news), self.drafts[2].observation_id, now=NOW, **kwargs)

    def response(self, relation="viewpoint"):
        data = {"relation": relation, "differences": [{"kind": "viewpoint", "current": "c1", "previous": "p1"}]}
        return Response({"choices": [{"message": {"content": json.dumps(data)}}],
                         "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}})

    def files(self):
        return {str(p.relative_to(self.news)): p.read_bytes() for p in self.news.rglob("*") if p.is_file()}

    def test_default_job_never_resolves_or_calls_a_model(self):
        with patch.object(llm, "chat_json", side_effect=AssertionError("model")):
            out = self.evaluate()
        self.assertEqual(out["status"], "recorded")
        self.assertGreater(len(out["pairs"]), 0)
        self.assertTrue(all(p["result"]["relation"] == "uncertain" for p in out["pairs"]))
        self.assertEqual(out["accounting"]["attempts"], 0)
        self.assertTrue(self.store.comparisons(self.drafts[2].observation_id)["receipts"])

    def test_explicit_model_result_and_cache_reuse_are_separate_receipts(self):
        with patch.object(llm, "_open_model", return_value=self.response()) as send:
            first = self.evaluate(allow_model=True, limit=1)
            second = self.evaluate(allow_model=True, limit=1)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(first["pairs"][0]["result"]["relation"], "viewpoint")
        self.assertEqual(first["accounting"]["measured_tokens"], 30)
        self.assertEqual(second["accounting"]["cache_hits"], 1)
        self.assertEqual(second["accounting"]["attempts"], 0)
        self.assertNotEqual(first["job_id"], second["job_id"])

    def test_latest_frozen_candidates_are_capped_and_cannot_be_arbitrary(self):
        for limit in (0, 6, True):
            with self.assertRaises(ValueError):
                self.evaluate(limit=limit)
        out = self.store.comparison_candidates(self.drafts[2].observation_id, limit=1)
        self.assertEqual(len(out["pairs"]), 1)
        self.assertEqual(out["pairs"][0]["proposal"]["relation"], "unclassified_change")
        self.assertEqual(out["capture_sequence"], 3)
        with self.assertRaises(ValueError):
            story_compare.evaluate(self.store, config.load(self.news), "ob_missing")

    def test_dry_run_and_billable_mode_require_explicit_matching_intent(self):
        ident = self.drafts[2].observation_id
        before = self.files()
        with patch.object(llm, "chat_json", side_effect=AssertionError("model")):
            out = cli.run_subcommand(["stories", "evaluate", "--observation", ident, "--allow-model", "--dry-run"], self.news)
        self.assertFalse(out.ok)
        self.assertEqual(out.error["code"], "E_VALIDATION")
        self.assertEqual(self.files(), before)
        cli.run_subcommand(["stories", "set", "--from-json", '{"comparison":"off"}'], self.news)
        with patch.object(llm, "chat_json", side_effect=AssertionError("model")), self.assertRaises(ValueError):
            self.evaluate(allow_model=True)

    def test_mode_or_unactivated_revision_blocks_even_local_jobs(self):
        cli.run_subcommand(["stories", "set", "--from-json", '{"window_days":30}'], self.news)
        with self.assertRaises(ValueError):
            self.evaluate()

    def test_disable_during_transport_records_cost_but_not_an_active_proposal(self):
        def transport(*args, **kwargs):
            cli.run_subcommand(["stories", "set", "--from-json", '{"mode":"off"}'], self.news)
            return self.response()
        with patch.object(llm, "_open_model", side_effect=transport) as send:
            out = self.evaluate(allow_model=True)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(out["status"], "stale_settings")
        self.assertEqual(out["accounting"]["attempts"], 1)
        self.assertFalse(self.store.comparisons(self.drafts[2].observation_id)["receipts"][0]["active"])

    def test_model_failure_is_safe_uncertainty_with_unknown_cost(self):
        with patch.object(llm, "_open_model", side_effect=TimeoutError("PRIVATE")):
            out = self.evaluate(allow_model=True, limit=1)
        self.assertEqual(out["pairs"][0]["result"]["relation"], "uncertain")
        self.assertGreater(out["accounting"]["unknown_tokens"], 0)
        self.assertNotIn("PRIVATE", json.dumps(out))

    def test_exact_copy_is_local_and_free_even_when_model_is_authorized(self):
        with patch.object(llm, "chat_json", side_effect=AssertionError("model")):
            out = story_compare.evaluate(self.store, config.load(self.news), self.drafts[1].observation_id, allow_model=True)
        self.assertEqual(out["pairs"][0]["result"]["relation"], "duplicate")
        self.assertEqual(out["accounting"]["attempts"], 0)

    def test_cli_contract_and_reads_preserve_frozen_versions_and_files(self):
        out = cli.run_subcommand(["stories", "evaluate", "--observation", self.drafts[2].observation_id, "--limit", "1"], self.news)
        self.assertTrue(out.ok, out.as_dict())
        self.assertTrue(out.changed)
        before = self.files()
        with patch.object(llm, "chat_json", side_effect=AssertionError("model")):
            saved = cli.run_subcommand(["stories", "comparisons", "--observation", self.drafts[2].observation_id], self.news)
        self.assertEqual(self.files(), before)
        self.assertFalse(saved.changed)
        pair = saved.data["receipts"][0]["pairs"][0]
        self.assertEqual(pair["result"]["evidence"]["current"]["version_hash"], self.drafts[2].version_hash)
        surface = {r["name"]: r for r in cli.CONTRACT}
        self.assertTrue(surface["stories evaluate"]["writes"])
        self.assertFalse(surface["stories comparisons"]["writes"])

    def test_read_only_v1_does_not_migrate_and_write_migration_is_atomic(self):
        db = self.store._story_ledger().path
        before = db.read_bytes()
        self.assertEqual(self.store.comparisons(self.drafts[2].observation_id)["receipts"], [])
        self.assertEqual(db.read_bytes(), before)
        with patch.object(Ledger, "_insert_comparison", side_effect=OSError("PRIVATE")), self.assertRaises(StoryStorageError):
            self.evaluate()
        with sqlite3.connect(db) as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='comparisons'").fetchone())
        out = self.evaluate()
        with sqlite3.connect(db) as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 2)
        originals = self.store.observations()
        self.assertEqual(len(originals["observations"]), 3)
        saved = deepcopy(self.store.comparisons(self.drafts[2].observation_id))
        out["job_id"] = "job_duplicate"
        with patch.object(Ledger, "_insert_comparison", side_effect=OSError("PRIVATE")), self.assertRaises(StoryStorageError):
            self.store.record_comparison(out)
        self.assertEqual(self.store.comparisons(self.drafts[2].observation_id), saved)

    def test_cli_storage_error_is_categorical_not_internal_or_secret(self):
        self.store._story_ledger().path.write_bytes(b"PRIVATE corrupt")
        out = cli.run_subcommand(["stories", "evaluate", "--observation", self.drafts[2].observation_id], self.news)
        self.assertEqual(out.error["code"], "E_RUNTIME", out.as_dict())
        self.assertNotIn("PRIVATE", json.dumps(out.as_dict()))

    def test_deep_provider_json_records_uncertainty_instead_of_escaping(self):
        content = '{"a":' + '[' * 1100 + '0' + ']' * 1100 + '}'
        response = Response({"choices": [{"message": {"content": content}}]})
        with patch.object(llm, "_open_model", return_value=response):
            out = self.evaluate(allow_model=True, limit=1)
        self.assertEqual(out["pairs"][0]["result"]["relation"], "uncertain")
        self.assertEqual(out["accounting"]["attempts"], 1)
        self.assertEqual(len(self.store.comparisons(self.drafts[2].observation_id)["receipts"]), 1)
