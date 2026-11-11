"""Pure policy/config contracts: no adapters, credentials or remote effects."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from newspipe import config
from newspipe.errors import ConfigError


class PolicyConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.news = Path(self.tmp.name)
        self.raw = {"chat": "oc_test", "slots": {"am": "08:15", "pm": "18:35"},
                    "policies": {"daily": {"label": "Daily", "mode": "digest", "slots": ["am", "pm"]}},
                    "sources": {"s": {"adapter": "rsshub", "fetch": {"trigger": "poll", "interval_min": 10},
                                      "notify": {"policy": "daily"}, "custom_adapter_key": {"value": 7}}}}

    def load(self):
        return config.load(self.news, sources_text=yaml.safe_dump(self.raw))

    def test_resolves_defaults_and_preserves_acquisition(self):
        cfg = self.load()
        self.assertTrue(hasattr(cfg, "policies"), "Config must expose parsed policies")
        from newspipe import policy
        rule = policy.resolve(cfg, cfg.sources["s"])
        self.assertEqual(rule.policy_id, "daily")
        self.assertEqual(rule.values["mode"], "digest")
        self.assertEqual(rule.values["slots"], ["am", "pm"])
        self.assertEqual(rule.values["max_cards_per_day"], 12)
        self.assertEqual(rule.values["min_gap_min"], 0)
        self.assertFalse(rule.values["bypass_source_limits"])
        self.assertEqual(rule.origins["mode"], "policy:daily")
        src = policy.effective_source(cfg, cfg.sources["s"])
        self.assertEqual(src.fetch, cfg.sources["s"].fetch)
        self.assertEqual(src.extra, {"custom_adapter_key": {"value": 7}})

    def test_empty_zero_false_are_explicit_overrides(self):
        self.raw["policies"]["daily"].update(quiet_hours="23:00-07:30", min_gap_min=10,
                                            max_cards_per_day=6, bypass_source_limits=True)
        self.raw["sources"]["s"]["notify"]["overrides"] = {
            "quiet_hours": "", "min_gap_min": 0, "max_cards_per_day": 0, "bypass_source_limits": False}
        cfg = self.load()
        self.assertTrue(hasattr(cfg.sources["s"], "notify"), "binding must not be an adapter extra")
        from newspipe import policy
        rule = policy.resolve(cfg, cfg.sources["s"])
        for key, value in self.raw["sources"]["s"]["notify"]["overrides"].items():
            self.assertEqual(rule.values[key], value)
            self.assertEqual(rule.origins[key], "source:s")
        self.assertEqual(rule.origins["form"], "policy:daily")

    def test_legacy_source_is_not_migrated(self):
        self.raw.pop("policies")
        self.raw["sources"]["s"].pop("notify")
        self.raw["sources"]["s"]["deliver"] = {"form": "append_card", "priority": "high"}
        cfg = self.load()
        self.assertTrue(hasattr(cfg, "notifications"))
        from newspipe import policy
        self.assertIsNone(policy.resolve(cfg, cfg.sources["s"]))
        self.assertIs(policy.effective_source(cfg, cfg.sources["s"]), cfg.sources["s"])
        self.assertIsNone(cfg.notifications)
        self.assertEqual(cfg.policies, {})

    def test_collect_maps_to_state_only_without_mutating_source(self):
        self.raw["policies"]["daily"] = {"mode": "collect"}
        cfg = self.load()
        self.assertTrue(hasattr(cfg, "policies"))
        from newspipe import policy
        self.assertFalse(policy.effective_source(cfg, cfg.sources["s"]).sends_card)
        self.assertTrue(cfg.sources["s"].sends_card)

    def test_empty_registries_are_valid(self):
        self.raw.update(sources={}, slots={}, policies={})
        cfg = self.load()
        self.assertEqual(cfg.sources, {})
        self.assertEqual(cfg.slots, {})

    def test_invalid_policy_fields_fail_before_any_write(self):
        invalid = [
            {"mode": "other"}, {"mode": "digest", "slots": []},
            {"mode": "digest", "slots": ["missing"]}, {"form": "state_only"},
            {"quiet_hours": "bad"}, {"quiet_hours": "24:00-07:30"},
            {"quiet_hours": "23:60-07:30"}, {"quiet_hours": "9:00-10:00"},
            {"max_cards_per_day": -1}, {"max_cards_per_day": True},
            {"min_gap_min": -1}, {"min_gap_min": float("nan")}, {"min_gap_min": float("inf")},
            {"bypass_source_limits": "true"}, {"unexpected": 1}, {"slots": ["am", "am"]},
        ]
        for body in invalid:
            with self.subTest(body=body):
                self.raw["policies"]["daily"] = body
                with self.assertRaises(ConfigError):
                    self.load()

    def test_invalid_bindings_are_rejected_even_when_disabled(self):
        invalid = [{"policy": "missing"}, {"policy": "daily", "overrides": {"label": "No"}},
                   {"policy": "daily", "overrides": {"mode": "digest", "slots": []}},
                   {"overrides": {"form": "card"}}, {"policy": "daily", "unknown": True}]
        self.raw["sources"]["s"]["enabled"] = False
        for binding in invalid:
            with self.subTest(binding=binding):
                self.raw["sources"]["s"]["notify"] = binding
                with self.assertRaises(ConfigError):
                    self.load()

    def test_policy_ids_are_stable_ascii_identifiers(self):
        for name in ("bad id", "../x", "中文", "", 12):
            with self.subTest(name=name):
                self.raw["policies"] = {name: {}}
                with self.assertRaises(ConfigError):
                    self.load()

    def test_slot_times_and_all_references_are_validated(self):
        for value in ("25:00", "08:60", "8:15", "tomorrow"):
            with self.subTest(value=value):
                self.raw["slots"]["am"] = value
                with self.assertRaises(ConfigError):
                    self.load()
        self.raw["slots"] = {}
        with self.assertRaises(ConfigError):
            self.load()

    def test_duplicate_yaml_keys_are_not_silently_overwritten(self):
        text = "chat: oc_test\nslots: {}\nsources: {}\npolicies:\n  x: {}\n  x: {mode: collect}\n"
        with self.assertRaisesRegex(ConfigError, "duplicate|重复"):
            config.load(self.news, sources_text=text)

    def test_global_fields_are_explicit_hard_gates(self):
        self.raw["notifications"] = {"paused": True, "quiet_hours": "23:00-07:30",
                                     "max_cards_per_day": 0, "min_gap_min": 0}
        cfg = self.load()
        self.assertTrue(hasattr(cfg, "notifications"))
        self.assertTrue(cfg.notifications.paused)
        self.assertEqual(cfg.notifications.max_cards_per_day, 0)
        for body in ({"unknown": 1}, {"paused": "yes"}, {"min_gap_min": float("inf")},
                     {"max_cards_per_day": -1}, {"quiet_hours": "bad"}):
            with self.subTest(body=body):
                self.raw["notifications"] = body
                with self.assertRaises(ConfigError):
                    self.load()

    def test_schema_exposes_notification_fields_separately(self):
        schema = config.describe_schema(self.news)
        self.assertIn("policy_fields", schema)
        self.assertEqual({f["path"] for f in schema["policy_fields"]},
                         {"label", "mode", "times", "form", "quiet_hours", "max_cards_per_day",
                          "min_gap_min", "bypass_source_limits"})
        self.assertEqual({f["path"] for f in schema["notification_fields"]},
                         {"paused", "quiet_hours", "max_cards_per_day", "min_gap_min"})

    def test_legacy_limits_and_quiet_windows_are_also_validated(self):
        for deliver in ({"quiet_hours": "invalid"}, {"budget": {"max_cards_per_day": -1}},
                        {"budget": {"min_gap_min": float("nan")}}):
            with self.subTest(deliver=deliver):
                self.raw["sources"]["s"]["deliver"] = deliver
                with self.assertRaises(ConfigError):
                    self.load()
