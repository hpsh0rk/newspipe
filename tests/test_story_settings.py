"""Settings have one schema, optimistic writes, and no read-side activation."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import patch

from newspipe import cli, config, edit, state
from newspipe.errors import ConfigError


class StorySettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.news = Path(self.tmp.name)
        self.path = self.news / "sources.yaml"
        self.path.write_text("# keep\nchat: oc_test\nslots: {}\nsources: {}\n", encoding="utf-8")

    def test_defaults_schema_and_values_have_one_meaning(self):
        cfg = config.load(self.news)
        self.assertEqual(cfg.stories.values(), {"mode": "off", "window_days": 14, "candidate_limit": 5, "scan_limit": 500})
        for field in config.describe_schema()["story_fields"]:
            self.assertEqual(cfg.stories.values()[field["path"]], field["default"])

    def test_invalid_modes_bounds_types_and_unknown_fields_are_rejected(self):
        for body in ({"mode": "apply"}, {"mode": True}, {"window_days": 0}, {"window_days": 91},
                     {"candidate_limit": 21}, {"candidate_limit": 0}, {"scan_limit": 49},
                     {"scan_limit": 2001}, {"window_days": True}, {"candidate_limit": 5.0}, {"unexpected": 1}):
            with self.subTest(body=body), self.assertRaises(ConfigError):
                config.StorySettings.parse(body)

    def test_settings_snapshot_is_independent_of_other_config_or_credentials(self):
        text = "chat: false\nsources: [invalid]\nstories: {mode: observe, window_days: 30}\n"
        with patch.object(config, "load_service", side_effect=AssertionError("service read")), \
             patch.object(config, "available_adapters", side_effect=AssertionError("provider read")):
            settings, revision = config.story_settings_snapshot(self.news, sources_text=text)
        reordered, other_revision = config.story_settings_snapshot(self.news, sources_text="stories: {window_days: 30, mode: observe}\n# changed comment\n")
        self.assertEqual(revision, other_revision)
        self.assertEqual(settings, reordered)

    def test_cli_settings_writes_preserve_other_blocks_and_do_not_activate(self):
        before = self.path.read_bytes()
        out = cli.run_subcommand(["stories", "set", "--from-json", '{"mode":"observe","scan_limit":100}', "--dry-run"], self.news)
        self.assertTrue(out.ok, out.as_dict())
        self.assertFalse(out.changed)
        self.assertEqual(before, self.path.read_bytes())
        out = cli.run_subcommand(["stories", "set", "--from-json", '{"mode":"observe","scan_limit":100}', "--base-hash", edit.file_hash(self.path)], self.news)
        self.assertTrue(out.ok, out.as_dict())
        self.assertTrue(out.changed)
        self.assertTrue(self.path.read_text().startswith(before.decode()))
        self.assertEqual(state.Store(self.news).story_status()["availability"], "not_initialized")
        settings = cli.run_subcommand(["stories", "settings"], self.news)
        self.assertTrue(settings.ok, settings.as_dict())
        self.assertTrue(settings.data["awaiting_collection"])
        self.assertEqual(settings.data["settings"]["mode"], "observe")
        cli.run_subcommand(["stories", "set", "--from-json", '{"mode":"off"}'], self.news)
        self.assertEqual(config.load(self.news).stories.scan_limit, 100)

    def test_stale_hash_and_validation_fail_without_writing_or_initializing(self):
        before = self.path.read_bytes()
        for args, code in ((["--from-json", '{"mode":"observe"}', "--base-hash", "stale"], "E_CONFLICT"),
                           (["--from-json", '{"mode":"apply"}'], "E_VALIDATION")):
            out = cli.run_subcommand(["stories", "set", *args], self.news)
            self.assertFalse(out.ok)
            self.assertEqual(out.error["code"], code)
            self.assertEqual(out.exit_code, 4)
            self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(state.Store(self.news).story_status()["availability"], "not_initialized")

    def test_return_to_prior_mode_has_a_new_saved_revision(self):
        cli.run_subcommand(["stories", "set", "--from-json", '{"mode":"observe"}'], self.news)
        first = config.load(self.news).story_revision
        cli.run_subcommand(["stories", "set", "--from-json", '{"mode":"off"}'], self.news)
        cli.run_subcommand(["stories", "set", "--from-json", '{"mode":"observe"}'], self.news)
        second = config.load(self.news).story_revision
        self.assertNotEqual(first, second)
        cli.run_subcommand(["stories", "set", "--from-json", '{"mode":"observe"}'], self.news)
        self.assertEqual(config.load(self.news).story_revision, second)
