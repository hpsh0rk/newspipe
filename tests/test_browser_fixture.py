"""The browser fixture is temporary, local and cannot contact real providers."""
import json
import re
import sys
import threading
import unittest
import urllib.parse
import urllib.request
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from newspipe import actions, channel, cli, credentials, llm, net, pipeline, rss, rssconnect
from types import SimpleNamespace
from preview_workbench import offline_workbench


class BrowserFixtureTests(unittest.TestCase):
    def test_story_fixture_has_truthful_copy_change_commentary_and_migration_cases(self):
        with offline_workbench(story_history=True) as fixture:
            before = {p: p.read_bytes() for p in fixture.news_dir.rglob("*") if p.is_file()}
            preview = fixture.store.story_preview()
            relations = {p["relation"] for r in preview["receipts"] for p in r["proposals"]}
            self.assertIn("exact_copy", relations)
            self.assertIn("unclassified_change", relations)
            comment = fixture.story_ids["commentary"]
            self.assertFalse(any(p["relation"] == "exact_copy" for r in preview["receipts"] for p in r["proposals"] if p["current_id"] == comment))
            legacy = fixture.store.story_preview(observation_id=fixture.story_ids["legacy"])["receipts"][0]
            self.assertIn("migration_baseline_missing_material", legacy["limitations"])
            self.assertEqual(legacy["proposals"], [])
            late = fixture.store.observation(fixture.story_ids["late"])
            self.assertLess(late["pub_ts"], late["first_seen"])
            self.assertEqual(before, {p: p.read_bytes() for p in fixture.news_dir.rglob("*") if p.is_file()})
            self.assertEqual(fixture.blocked_effects, [])

    def test_synthetic_doctor_keeps_card_budget_and_never_probes_live_services(self):
        with offline_workbench(story_history=True) as fixture:
            with patch.object(credentials, "resolve_feishu", return_value=SimpleNamespace(describe=lambda: {
                    "app_id": "present", "app_secret": "present", "source": "synthetic"})):
                out = cli.run_subcommand(["doctor", "--json"], fixture.news_dir)
            self.assertTrue(out.ok, out.as_dict())
            self.assertGreaterEqual(out.data["card_budget"]["headroom"], 0)
            self.assertEqual(fixture.blocked_effects, [])

    def test_fixture_serves_contract_without_live_credentials_or_effects(self):
        with offline_workbench() as fixture:
            with urllib.request.urlopen(fixture.url + "/view", timeout=5) as response:
                payload = json.load(response)
            self.assertEqual(payload["chat"], "oc_offline_preview")
            self.assertEqual(len(payload["sources"]), 3)
            for effect in (lambda: credentials.resolve_feishu(), lambda: llm.chat_json("fake"),
                           lambda: channel.create_entity({}), lambda: pipeline._enrich_new(),
                           lambda: rssconnect._probe_request("https://example.invalid")):
                with self.assertRaisesRegex(RuntimeError, "offline fixture"):
                    effect()
            self.assertGreaterEqual(len(fixture.blocked_effects), 5)

    def test_collect_action_uses_fake_adapter_and_temporary_state(self):
        with offline_workbench() as fixture:
            before = fixture.store.pending_count("research")
            result = cli.run_subcommand(["collect", "research", "--json"], fixture.news_dir)
            self.assertTrue(result.ok, result.as_dict())
            self.assertEqual(fixture.store.pending_count("research"), before + 1)
            self.assertEqual(fixture.blocked_effects, [])

    def test_group_directory_is_synthetic_and_real_backends_are_guarded(self):
        from newspipe.backends.feishu_direct import FeishuDirectChannel
        from newspipe.backends.feishu_lark_cli import LarkCliChannel
        with offline_workbench() as fixture:
            out = cli.run_subcommand(["chats", "list", "--json"], fixture.news_dir)
            self.assertTrue(out.ok, out.as_dict())
            self.assertEqual(out.data["groups"][0]["chat_id"], "oc_offline_preview")
            for backend in (FeishuDirectChannel(), LarkCliChannel()):
                with self.assertRaisesRegex(RuntimeError, "offline fixture"):
                    backend.list_chats()

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

    def test_deployment_bind_environment_cannot_expose_the_fixture(self):
        with patch.dict("os.environ", {"NEWSPIPE_BIND_HOST": "0.0.0.0"}):
            with offline_workbench() as fixture:
                self.assertEqual(fixture.server.server_address[0], "127.0.0.1")

    def test_fixture_drains_in_flight_action_before_restoring_provider_guards(self):
        entered, release, closed = threading.Event(), threading.Event(), threading.Event()
        results, errors = [], []
        dispatch = actions.dispatch
        def delayed(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test did not release the request")
            return dispatch(*args, **kwargs)
        # The outer sentinel keeps the RED run safe even if fixture guards expire.
        with patch.object(net, "request", side_effect=RuntimeError("outer safe provider sentinel")), patch.object(
                actions, "dispatch", side_effect=delayed):
            manager = offline_workbench()
            fixture = manager.__enter__()
            self.addCleanup(manager.__exit__, None, None, None)
            self.addCleanup(release.set)
            with urllib.request.urlopen(fixture.url + "/ops", timeout=5) as response:
                page = response.read().decode()
            token = re.search(r'name="token" value="([^"]+)"', page).group(1)
            def request():
                try:
                    data = urllib.parse.urlencode({"token": token, "name": "research"}).encode()
                    req = urllib.request.Request(fixture.url + "/api/actions/collect", data=data,
                                                 headers={"Accept": "application/json"})
                    with urllib.request.urlopen(req, timeout=5) as response:
                        results.append(json.load(response))
                except Exception as exc:
                    errors.append(str(exc))
            def close():
                try:
                    manager.__exit__(None, None, None)
                except Exception as exc:
                    errors.append(str(exc))
                finally:
                    closed.set()
            client = threading.Thread(target=request)
            client.start()
            self.assertTrue(entered.wait(5))
            cleanup = threading.Thread(target=close)
            cleanup.start()
            try:
                fixture.thread.join(5)
                self.assertFalse(fixture.thread.is_alive())
                self.assertFalse(closed.wait(0.2), "Cleanup must wait for the active request while guards are installed")
                self.assertTrue(fixture.news_dir.exists())
            finally:
                release.set()
                client.join(5)
                cleanup.join(5)
            self.assertTrue(closed.is_set())
            self.assertFalse(client.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(len(results), 1)
            self.assertTrue(results[0]["ok"], results)
            self.assertEqual(fixture.blocked_effects, [])
            self.assertFalse(fixture.news_dir.exists())
