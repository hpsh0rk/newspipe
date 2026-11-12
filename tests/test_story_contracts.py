"""Read contracts distinguish absent history from failed storage without effects."""
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from newspipe import cli, config, state
from newspipe.observations import prepare_observation
from newspipe.story_matching import rank_candidates
from test_observations import article, TEXT

NOW = datetime(2026, 11, 12, 12)
SOURCES = "chat: oc_test\nslots: {}\nsources: {}\npolicies: {}\n"


def seed(store):
    settings, revision = config.story_settings_snapshot(store.news_dir)
    drafts = [prepare_observation("one", "rsshub", article()),
              prepare_observation("two", "rsshub", article(2)),
              prepare_observation("one", "rsshub", article(source_text=TEXT + " Later correction."))]
    for draft in drafts:
        snapshot = store.observation_capture_snapshot([draft], at=NOW, window_days=14, candidate_limit=5, scan_limit=500)
        store.record_observations([draft], at=NOW, mode="observe", config_revision=revision,
            current_revision=revision, expected_activation_id=snapshot.activation_id, snapshot=snapshot,
            proposals=rank_candidates(draft, list(snapshot.candidates), limit=5), baselines={})
    return drafts


class StoryContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.news = Path(self.tmp.name)
        (self.news / "sources.yaml").write_text(SOURCES, encoding="utf-8")
        self.store = state.Store(self.news)
        self.addCleanup(patch.stopall)
        for target in ("newspipe.pipeline._adapter", "newspipe.channel.send_card", "newspipe.credentials.resolve_feishu"):
            patch(target, side_effect=AssertionError("read attempted external or comparison work")).start()

    def files(self):
        return {str(p.relative_to(self.news)): p.read_bytes() for p in self.news.rglob("*") if p.is_file()}

    def run_cli(self, *args):
        return cli.run_subcommand([*args, "--json"], self.news)

    def test_describe_registers_all_read_commands_and_parameters(self):
        described = {entry["name"]: entry for entry in cli.CONTRACT}
        for name in ("observations list", "observations show", "stories preview"):
            self.assertIn(name, described)
            self.assertFalse(described[name]["writes"])
        self.assertIn("--observation", described["stories preview"]["args"])
        self.assertIn("observations", cli.SUBCOMMANDS)

    def test_missing_history_is_explicit_and_not_initialized_by_reads(self):
        before = self.files()
        for argv, key in ((["observations", "list"], "observations"), (["stories", "preview"], "receipts")):
            out = self.run_cli(*argv)
            self.assertTrue(out.ok, out.as_dict())
            self.assertFalse(out.changed)
            self.assertEqual(out.data["availability"], "not_initialized")
            self.assertEqual(out.data[key], [])
        self.assertEqual(self.files(), before)

    def test_pagination_uses_immutable_observation_sequence_and_source_filter(self):
        drafts = seed(self.store)
        before = self.files()
        first = self.run_cli("observations", "list", "--source", "one", "--limit", "1")
        self.assertTrue(first.ok, first.as_dict())
        self.assertTrue(first.data["has_more"])
        second = self.run_cli("observations", "list", "--source", "one", "--after", str(first.data["next_after"]))
        self.assertEqual(second.data["observations"][0]["observation_id"], drafts[2].observation_id)
        detail = self.run_cli("observations", "show", drafts[0].observation_id)
        self.assertEqual(detail.data["evidence"]["text"], TEXT)
        self.assertEqual(self.files(), before)

    def test_preview_uses_saved_bounds_without_writing(self):
        drafts = seed(self.store)
        with patch("newspipe.story_matching.propose_relation", side_effect=AssertionError("live comparison")):
            cli.run_subcommand(["stories", "set", "--from-json", '{"scan_limit":50}'], self.news)
            before = self.files()
            out = self.run_cli("stories", "preview", "--observation", drafts[1].observation_id)
            self.assertTrue(out.ok, out.as_dict())
            self.assertEqual(out.data["receipts"][0]["bounds"]["scan_limit"], 500)
            self.assertEqual(out.data["receipts"][0]["proposals"][0]["relation"], "exact_copy")
            self.assertEqual(self.files(), before)

    def test_unknown_detail_and_invalid_pagination_have_categorical_errors(self):
        self.assertEqual(self.run_cli("observations", "show", "ob_missing").error["code"], "E_NOT_FOUND")
        for args in (("observations", "list", "--after", "-1"), ("stories", "preview", "--limit", "101")):
            out = self.run_cli(*args)
            self.assertEqual(out.error["code"], "E_VALIDATION", out.as_dict())
            self.assertEqual(out.exit_code, 4)
            self.assertIsNone(out.data)

    def test_corrupt_and_locked_storage_are_not_empty_success(self):
        seed(self.store)
        db = self.store.root / "stories" / "history.sqlite3"
        with sqlite3.connect(db) as conn:
            conn.execute("BEGIN EXCLUSIVE")
            out = self.run_cli("stories", "preview")
            self.assertFalse(out.ok)
            self.assertEqual(out.error["code"], "E_RUNTIME")
            conn.rollback()
        db.write_bytes(b"PRIVATE damaged storage")
        before = self.files()
        for args in (("stories", "preview"), ("observations", "list")):
            out = self.run_cli(*args)
            self.assertEqual(out.error["code"], "E_RUNTIME")
            self.assertNotIn("PRIVATE", json.dumps(out.as_dict()))
            self.assertNotIn(str(db), json.dumps(out.as_dict()))
        self.assertEqual(self.files(), before)

    def test_settings_remain_available_to_disable_corrupt_history(self):
        seed(self.store)
        (self.store.root / "stories" / "history.sqlite3").write_bytes(b"corrupt")
        out = self.run_cli("stories", "settings")
        self.assertTrue(out.ok, out.as_dict())
        self.assertEqual(out.data["status"]["availability"], "unavailable")
        self.assertEqual(out.data["settings"]["mode"], "off")
