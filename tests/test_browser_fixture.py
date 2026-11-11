"""The browser fixture is temporary, local and cannot contact real providers."""
import json
import sys
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from newspipe import channel, cli, credentials, llm, pipeline, rss
from preview_workbench import offline_workbench


class BrowserFixtureTests(unittest.TestCase):
    def test_fixture_serves_contract_without_live_credentials_or_effects(self):
        with offline_workbench() as fixture:
            with urllib.request.urlopen(fixture.url + "/view", timeout=5) as response:
                payload = json.load(response)
            self.assertEqual(payload["chat"], "oc_offline_preview")
            self.assertEqual(len(payload["sources"]), 3)
            for effect in (lambda: credentials.resolve_feishu(), lambda: llm.chat_json("fake"),
                           lambda: channel.create_entity({}), lambda: pipeline._enrich_new()):
                with self.assertRaisesRegex(RuntimeError, "offline fixture"):
                    effect()
            self.assertGreaterEqual(len(fixture.blocked_effects), 4)

    def test_collect_action_uses_fake_adapter_and_temporary_state(self):
        with offline_workbench() as fixture:
            before = fixture.store.pending_count("research")
            result = cli.run_subcommand(["collect", "research", "--json"], fixture.news_dir)
            self.assertTrue(result.ok, result.as_dict())
            self.assertEqual(fixture.store.pending_count("research"), before + 1)
            self.assertEqual(fixture.blocked_effects, [])

    def test_rss_discovery_cannot_escape_through_server_network(self):
        with offline_workbench() as fixture:
            with self.assertRaisesRegex(rss.RssError, "offline fixture blocks network"):
                rss.discover("https://example.invalid", probe_common=False)
            self.assertEqual(fixture.blocked_effects, ["network"])

    def test_fixture_shuts_down_server_and_removes_files(self):
        with offline_workbench() as fixture:
            news = fixture.news_dir
            self.assertTrue(fixture.thread.is_alive())
            self.assertTrue(news.is_dir())
        self.assertFalse(fixture.thread.is_alive())
        self.assertEqual(fixture.server.socket.fileno(), -1)
        self.assertFalse(news.exists())
