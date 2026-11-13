"""RSSHub is optional; endpoint edits/resolution share the normal config boundary."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from newspipe import cli, config, edit, pipeline, state
from newspipe.adapters import rsshub
from newspipe.errors import ConfigError

TEXT = '''# global comment
chat: oc_test
slots: {}
# source comment
sources:
  feed:
    adapter: rsshub
    feeds: [/sspai/matrix]
    fetch: {trigger: manual}
    enrich: {summary: "off"}
'''
RSS = b'<rss version="2.0"><channel><item><guid>one</guid><title>Test</title><link>https://example.invalid/one</link></item></channel></rss>'


class RssHubSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.news = Path(self.tmp.name)
        (self.news / "sources.yaml").write_text(TEXT)
        self.env = patch.dict(os.environ, {"RSSHub_BASE": ""})
        self.env.start()
        self.addCleanup(self.env.stop)

    def command(self, *args):
        return cli.run_subcommand(["rsshub", *args, "--json"], self.news).as_dict()

    def test_show_exposes_effective_endpoint_and_origin_without_network(self):
        out = self.command("show")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["data"]["effective_url"], "http://127.0.0.1:1200")
        self.assertEqual(out["data"]["origin"], "default")
        self.assertEqual(out["data"]["dependent_sources"], ["feed"])
        self.assertFalse(out["changed"])

    def test_config_environment_and_default_precedence(self):
        from newspipe import rssconnect
        with patch.dict(os.environ, {"RSSHub_BASE": "https://env.example.invalid/prefix/"}):
            self.assertEqual(rssconnect.effective(""), ("https://env.example.invalid/prefix", "env:RSSHub_BASE"))
            self.assertEqual(rssconnect.effective("https://config.example.invalid/"), ("https://config.example.invalid", "config"))
        self.assertEqual(rssconnect.effective(""), ("http://127.0.0.1:1200", "default"))

    def test_feed_resolution_preserves_prefix_and_direct_feed_bypasses_invalid_env(self):
        from newspipe import rssconnect
        self.assertEqual(rssconnect.feed_url("/sspai/matrix?limit=5", "https://hub.example.invalid/rss/"),
                         "https://hub.example.invalid/rss/sspai/matrix?limit=5")
        with patch.dict(os.environ, {"RSSHub_BASE": "bad-address"}):
            self.assertEqual(rssconnect.feed_url("https://feed.example.invalid/news.xml", ""), "https://feed.example.invalid/news.xml")
        for value in ("relative", "//elsewhere.invalid/feed", "/../escape", "/%2e%2e/escape"):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                rssconnect.feed_url(value, "https://hub.example.invalid/rss")

    def test_endpoint_validation_does_not_echo_embedded_secrets(self):
        for value in ("ftp://example.invalid", "https://user:SECRET@example.invalid", "https://example.invalid/?key=SECRET",
                      "https://example.invalid/#SECRET", "https://example.invalid:99999", "missing-host"):
            with self.subTest(value=value):
                out = self.command("set", "--base-url", value)
                self.assertFalse(out["ok"], out)
                self.assertEqual(out["error"]["code"], "E_VALIDATION")
                self.assertNotIn("SECRET", json.dumps(out))
                self.assertEqual((self.news / "sources.yaml").read_text(), TEXT)
        with self.assertRaises(ConfigError):
            config.load(self.news, sources_text=TEXT + "rsshub: []\n")

    def test_set_dry_run_hash_conflict_and_clear_preserve_sibling_bytes(self):
        old_hash = edit.file_hash(self.news / "sources.yaml")
        out = self.command("set", "--base-url", "https://hub.example.invalid/", "--dry-run", "--base-hash", old_hash)
        self.assertTrue(out["ok"], out)
        self.assertEqual((self.news / "sources.yaml").read_text(), TEXT)
        out = self.command("set", "--base-url", "https://hub.example.invalid/", "--base-hash", old_hash)
        self.assertTrue(out["changed"], out)
        self.assertEqual(config.load(self.news).rsshub, {"base_url": "https://hub.example.invalid"})
        self.assertIn(TEXT, (self.news / "sources.yaml").read_text())
        conflict = self.command("set", "--base-url", "https://other.example.invalid", "--base-hash", old_hash)
        self.assertEqual(conflict["error"]["code"], "E_CONFLICT")
        cleared = self.command("set", "--base-url", "")
        self.assertTrue(cleared["ok"], cleared)
        self.assertEqual(self.command("show")["data"]["origin"], "default")

    def test_runtime_fetch_uses_current_config_and_env_not_import_snapshot(self):
        targets = []
        def request(url, **kwargs):
            targets.append(url)
            return RSS
        with patch("newspipe.net.request", side_effect=request):
            with patch.dict(os.environ, {"RSSHub_BASE": "https://first.example.invalid"}):
                self.assertEqual(len(rsshub.fetch({"feeds": ["/sspai/matrix"]}).items), 1)
            self.assertTrue(self.command("set", "--base-url", "https://second.example.invalid/prefix")["ok"])
            cfg = config.load(self.news)
            run = pipeline.collect_source(cfg, state.Store(self.news), "feed", dry=True)
            self.assertEqual(run.fetched, 1, run.note)
        self.assertEqual(targets, ["https://first.example.invalid/sspai/matrix", "https://second.example.invalid/prefix/sspai/matrix"])
