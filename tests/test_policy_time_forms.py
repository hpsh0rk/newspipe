from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from newspipe import actions, config


class PolicyTimeFormTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.news = Path(self.tmp.name)
        (self.news / "sources.yaml").write_text("chat: oc_test\nslots: {}\nsources: {}\npolicies: {}\n")

    def test_multiple_clocks_and_stable_hidden_keys_roundtrip(self):
        out = actions.dispatch("policy-save", {"name": ["daily"], "p.mode": ["digest"],
            "p.times": ["08:15", "12:30", "18:35", ""],
            "p.times_key": ["am", "", "", ""]}, self.news)
        self.assertTrue(out["ok"], out)
        times = config.load(self.news).policies["daily"].times
        self.assertEqual(times["am"], "08:15")
        self.assertEqual(set(times.values()), {"08:15", "12:30", "18:35"})

    def test_inactive_times_are_cleared_when_saved_as_immediate(self):
        out = actions.dispatch("policy-save", {"name": ["live"], "p.mode": ["immediate"],
            "p.times": ["invalid"], "p.times_key": [""]}, self.news)
        self.assertTrue(out["ok"], out)
        self.assertEqual(config.load(self.news).policies["live"].times, {})

    def test_override_fields_are_rejected_not_silently_ignored(self):
        for key in ("inherit.min_gap_min", "n.min_gap_min"):
            out = actions.dispatch("source-save", {"name": ["s"], "f.adapter": ["hn"],
                "f.fetch.trigger": ["manual"], "notify_policy": ["daily"], key: ["0"]}, self.news)
            self.assertFalse(out["ok"], out)
            self.assertEqual(out["error"]["code"], "E_USAGE")

    def test_override_fields_without_policy_selection_are_also_rejected(self):
        out = actions.dispatch("source-save", {"name": ["s"], "f.adapter": ["hn"],
            "f.fetch.trigger": ["manual"], "n.min_gap_min": ["0"]}, self.news)
        self.assertFalse(out["ok"], out)
        self.assertEqual(out["error"]["code"], "E_USAGE")

    def test_malformed_rows_never_write(self):
        before = (self.news / "sources.yaml").read_bytes()
        for clocks, keys in ((["08:15"], []), (["08:15", "08:15"], ["x", "y"]),
                             (["08:15", "18:35"], ["x", "x"]), (["24:00"], [""])):
            out = actions.dispatch("policy-save", {"name": ["daily"], "p.mode": ["digest"],
                "p.times": clocks, "p.times_key": keys}, self.news)
            self.assertFalse(out["ok"], out)
            self.assertEqual((self.news / "sources.yaml").read_bytes(), before)
