"""Policy-owned sending clocks and lossless, explicit legacy migration."""
from datetime import datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import yaml

from newspipe import cli, config, edit, notification_view, pipeline, policy, service, state
from newspipe.errors import ConfigError
from test_news_pipeline import _item


class PolicyTimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.news = Path(self.tmp.name)
        self.path = self.news / "sources.yaml"
        self.raw = {"chat": "oc_test", "slots": {"am": "08:15"},
                    "policies": {"a": {"mode": "digest", "times": {"morning": "08:15"}},
                                 "b": {"mode": "digest", "times": {"morning": "08:15"}}},
                    "sources": {name: {"adapter": "hn", "fetch": {"trigger": "manual"},
                                       "notify": {"policy": ident}}
                                for name, ident in (("s1", "a"), ("s2", "a"), ("s3", "b"))}}

    def load(self):
        self.path.write_text(yaml.safe_dump(self.raw))
        return config.load(self.news)

    def command(self, *argv):
        return cli.run_subcommand([*argv, "--json"], self.news).as_dict()

    def legacy(self):
        self.raw["policies"] = {"a": {"mode": "digest", "slots": ["am"],
                                      "quiet_hours": "23:00-07:00"}}
        self.raw["sources"]["s3"]["notify"] = {"policy": "a", "overrides": {
            "quiet_hours": "", "min_gap_min": 0, "max_cards_per_day": 0,
            "bypass_source_limits": False}}
        self.raw["sources"]["s3"].update(enabled=False, custom={"keep": [1, 2]})
        return self.load()

    def test_policy_times_are_independent_and_shared_bindings_follow(self):
        self.load()
        out = self.command("policy", "set", "a", "--from-json",
                           json.dumps({"mode": "digest", "times": {"morning": "09:45", "evening": "20:00"}}))
        self.assertTrue(out["ok"], out)
        cfg = config.load(self.news)
        for name, clocks in (("s1", {"morning": "09:45", "evening": "20:00"}),
                             ("s2", {"morning": "09:45", "evening": "20:00"}),
                             ("s3", {"morning": "08:15"})):
            rule = policy.resolve(cfg, cfg.sources[name])
            self.assertEqual(policy.sending_times(cfg, rule.values), clocks)
        self.assertEqual({r["source"] for r in out["data"]["affected"]}, {"s1", "s2"})
        out = self.command("global", "set", "--from-json", '{"slots":{}}')
        self.assertTrue(out["ok"], out)

    def test_invalid_times_rejected(self):
        for times in ({}, {"x": "8:15"}, {"x": "24:00"},
                      {"bad key": "08:15"}, ["08:15"]):
            with self.subTest(times=times):
                self.raw["policies"]["a"] = {"mode": "digest", "times": times}
                with self.assertRaises(ConfigError):
                    self.load()

    def test_scheduler_and_preview_use_policy_clocks_not_global_acquisition(self):
        cfg = self.load()
        store = state.Store(self.news)
        now = datetime(2026, 11, 11, 8, 15)
        store.append_pending("s1", [_item(1)])
        with patch("newspipe.clock.now", return_value=now), patch.object(pipeline, "notify_source", return_value=pipeline.SourceRun(source="s1", status="dry")) as send:
            service._run_notifications(cfg, store, {}, now=now, news_dir=self.news, dry=True)
        self.assertEqual({call.kwargs["slot"] for call in send.call_args_list}, {"morning"})
        snap = notification_view.snapshot(self.news, cfg=cfg, now=now)
        self.assertNotEqual(snap["sources"][0]["reason"], "waiting_slot")
        from contextlib import nullcontext
        with patch("newspipe.runtime.activate", return_value=nullcontext()):
            run = pipeline.notify_source(cfg, store, "s1", now=now, dry=True)
        self.assertEqual(run.status, "dry")

    def test_migration_is_atomic_lossless_and_preserves_schedule_completion(self):
        before_cfg = self.legacy()
        before_rules = {n: policy.resolve(before_cfg, s).values for n, s in before_cfg.sources.items()}
        before = self.path.read_bytes()
        store = state.Store(self.news)
        store.complete_notification_slot("2026-11-11", "s1", "am", status="ok")
        state_before = {p: p.read_bytes() for p in self.news.rglob("*") if p.is_file() and p != self.path}
        dry = self.command("notification", "migrate", "--dry-run", "--base-hash", edit.file_hash(self.path))
        self.assertTrue(dry["ok"], dry)
        self.assertFalse(dry["changed"])
        self.assertEqual(self.path.read_bytes(), before)
        out = self.command("notification", "migrate", "--base-hash", edit.file_hash(self.path))
        self.assertTrue(out["ok"], out)
        self.assertTrue(out["changed"])
        cfg = config.load(self.news)
        self.assertNotEqual(cfg.sources["s3"].notify.policy, "a")
        for name, src in cfg.sources.items():
            self.assertEqual(src.notify.overrides, {})
            self.assertEqual(policy.sending_times(cfg, policy.resolve(cfg, src).values), {"am": "08:15"})
            for key in ("mode", "form", "quiet_hours", "max_cards_per_day", "min_gap_min", "bypass_source_limits"):
                self.assertEqual(policy.resolve(cfg, src).values[key], before_rules[name][key])
        self.assertEqual(yaml.safe_load(self.path.read_text())["sources"]["s3"]["custom"], {"keep": [1, 2]})
        self.assertEqual({p: p.read_bytes() for p in state_before}, state_before)
        self.assertTrue(store.notification_slot_complete("2026-11-11", "s1", "am"))
        self.assertFalse(self.command("notification", "migrate")["changed"])

    def test_migration_conflict_rejects_entire_operation(self):
        self.legacy()
        before = self.path.read_bytes()
        out = self.command("notification", "migrate", "--base-hash", "stale")
        self.assertFalse(out["ok"], out)
        self.assertEqual(out["error"]["code"], "E_CONFLICT")
        self.assertEqual(self.path.read_bytes(), before)

    def test_migration_preserves_legal_legacy_equal_clock_schedule_keys(self):
        self.legacy()
        self.raw["slots"]["pm"] = "08:15"
        self.raw["policies"]["a"]["slots"] = ["am", "pm"]
        self.load()
        store = state.Store(self.news)
        store.complete_notification_slot("2026-11-11", "s1", "am", status="ok")
        out = self.command("notification", "migrate")
        self.assertTrue(out["ok"], out)
        cfg = config.load(self.news)
        self.assertEqual(cfg.policies["a"].times, {"am": "08:15", "pm": "08:15"})
        self.assertTrue(store.notification_slot_complete("2026-11-11", "s1", "am"))
        self.assertFalse(store.notification_slot_complete("2026-11-11", "s1", "pm"))
        body = {**cfg.policies["a"].values(), "label": "Renamed"}
        self.assertTrue(self.command("policy", "set", "a", "--from-json", json.dumps(body))["ok"])

    def test_new_duplicate_clock_writes_reject_without_losing_legacy_read_compatibility(self):
        self.load()
        before = self.path.read_bytes()
        body = {"mode": "digest", "times": {"x": "08:15", "y": "08:15"}}
        out = self.command("policy", "set", "new", "--from-json", json.dumps(body))
        self.assertFalse(out["ok"], out)
        self.assertEqual(self.path.read_bytes(), before)

    def test_legacy_policy_cli_writes_freeze_clocks_instead_of_new_global_references(self):
        self.load()
        out = self.command("policy", "set", "legacy_input", "--from-json", '{"mode":"digest","slots":["am"]}')
        self.assertTrue(out["ok"], out)
        cfg = config.load(self.news)
        self.assertEqual(cfg.policies["legacy_input"].slots, ())
        self.assertEqual(cfg.policies["legacy_input"].times, {"am": "08:15"})

    def test_source_revision_supports_unknown_yaml_extension_types(self):
        self.raw["sources"]["s1"]["extension"] = {"data": {1: "one", "x": "text"}}
        self.load()
        from newspipe import view
        payload = view.build(self.news)
        self.assertTrue(payload["source_revisions"]["s1"])

    def test_bound_source_save_cannot_silently_drop_or_add_overrides(self):
        self.legacy()
        before = self.path.read_bytes()
        for body in ({"notify": {"policy": "a", "overrides": None}},
                     {"notify": {"overrides": {"min_gap_min": 1}}}):
            out = self.command("source", "patch", "s3", "--from-json", json.dumps(body))
            self.assertFalse(out["ok"], out)
            self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(self.command("source", "patch", "s3", "--from-json", '{"card":{"title":"New"}}')["ok"])
