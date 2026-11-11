"""Progressive RSSHub forms keep the existing POST gates and one-use PRG."""
from html.parser import HTMLParser
import json
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest.mock import patch

from newspipe import actions, config, rssconnect, view
from preview_workbench import offline_workbench
from test_rsshub_settings import RSS


class RssForm(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values, self.active = {}, False
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.active = attrs.get("action") == "/api/actions/rsshub-save"
        if self.active and tag == "input" and attrs.get("name"):
            if attrs.get("type") != "checkbox" or "checked" in attrs:
                self.values[attrs["name"]] = attrs.get("value", "")
    def handle_endtag(self, tag):
        if tag == "form":
            self.active = False


def synthetic_probe(url):
    if url not in {"https://hub.example.invalid/prefix", "https://hub.example.invalid/prefix/rss",
                   "https://feed.example.invalid/feed.xml"}:
        raise AssertionError("Unexpected probe target")
    return (200, RSS)


class RssHubWebTests(unittest.TestCase):
    def form(self, fixture):
        with urllib.request.urlopen(fixture.url + "/config?rsshub=1") as response:
            text = response.read().decode()
        self.assertTrue('action="/api/actions/rsshub-save"' in text, "Missing RSSHub settings form")
        parsed = RssForm()
        parsed.feed(text)
        return parsed.values

    def post(self, fixture, action, values, headers=None):
        request = urllib.request.Request(fixture.url + "/api/actions/" + action,
            data=urllib.parse.urlencode(values).encode(),
            headers={"Origin": fixture.url, "Accept": "application/json", **(headers or {})})
        with urllib.request.urlopen(request) as response:
            return json.load(response)

    def test_rsshub_save_uses_existing_dry_run_and_hash_guards(self):
        with offline_workbench() as fixture:
            values = self.form(fixture)
            before = (fixture.news_dir / "sources.yaml").read_bytes()
            values["r.base_url"] = "https://hub.example.invalid/prefix"
            out = self.post(fixture, "rsshub-save", values)
            self.assertTrue(out["ok"], out)
            self.assertEqual((fixture.news_dir / "sources.yaml").read_bytes(), before)
            values.pop("dry")
            self.assertTrue(self.post(fixture, "rsshub-save", values)["ok"])
            self.assertEqual(config.load(fixture.news_dir).rsshub["base_url"], "https://hub.example.invalid/prefix")
            conflict = self.post(fixture, "rsshub-save", values)
            self.assertEqual(conflict["error"]["code"], "E_CONFLICT")
            self.assertEqual(fixture.blocked_effects, [])

    def test_probe_and_prg_refresh_keep_draft_hash_and_do_not_write(self):
        with offline_workbench() as fixture, patch.object(rssconnect, "_probe_request", side_effect=synthetic_probe) as transport:
            values = self.form(fixture)
            before = (fixture.news_dir / "sources.yaml").read_bytes()
            values.update({"r.base_url": "https://hub.example.invalid/prefix", "probe_feed": "/rss", "probe_kind": "feed"})
            request = urllib.request.Request(fixture.url + "/api/actions/rsshub-probe",
                data=urllib.parse.urlencode(values).encode(), headers={"Origin": fixture.url})
            with urllib.request.urlopen(request) as response:
                url, text = response.url, response.read().decode()
            self.assertIn("/config?draft=", url)
            self.assertNotIn("hub.example", url)
            parsed = RssForm()
            parsed.feed(text)
            self.assertEqual(parsed.values["r.base_url"], values["r.base_url"])
            self.assertEqual(parsed.values["probe_feed"], "/rss")
            self.assertEqual(parsed.values["base_hash"], values["base_hash"])
            urllib.request.urlopen(url).close()
            self.assertEqual(transport.call_count, 1)
            self.assertEqual((fixture.news_dir / "sources.yaml").read_bytes(), before)
            self.assertEqual(fixture.blocked_effects, [])

    def test_new_actions_reject_missing_token_and_cross_origin_before_probing(self):
        with offline_workbench() as fixture:
            values = self.form(fixture)
            for action in ("rsshub-save", "rsshub-probe"):
                for headers, fields in (({"Origin": "https://unrelated.example.invalid"}, values), ({}, {**values, "token": "bad"})):
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        self.post(fixture, action, fields, headers)
                    self.assertEqual(caught.exception.code, 403)
            self.assertEqual(fixture.blocked_effects, [])

    def test_disabled_actions_keep_metadata_but_no_forms_or_probe(self):
        with offline_workbench(view_actions=False) as fixture:
            with urllib.request.urlopen(fixture.url + "/config") as response:
                text = response.read().decode()
            self.assertTrue('data-read-region="rsshub"' in text, "Missing read-only RSSHub metadata")
            self.assertNotIn('action="/api/actions/rsshub-save"', text)
            for action in ("rsshub-save", "rsshub-probe"):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    self.post(fixture, action, {"token": "bad"})
                self.assertEqual(caught.exception.code, 404)
            self.assertEqual(fixture.blocked_effects, [])

    def test_explicit_probe_kind_separates_connection_and_required_feed(self):
        with offline_workbench() as fixture, patch.object(rssconnect, "_probe_request", side_effect=synthetic_probe) as transport:
            values = {"r.base_url": ["https://hub.example.invalid/prefix"], "probe_feed": ["/rss"]}
            out = actions.dispatch("rsshub-probe", {**values, "probe_kind": ["connection"]}, fixture.news_dir)
            self.assertTrue(out["ok"], out)
            self.assertEqual(out["data"]["status"], "reachable")
            self.assertEqual(transport.call_args.args[0], "https://hub.example.invalid/prefix")
            for fields in ({**values, "probe_kind": ["feed"], "probe_feed": [""]}, {**values, "probe_kind": ["unknown"]}):
                self.assertFalse(actions.dispatch("rsshub-probe", fields, fixture.news_dir)["ok"])
            self.assertEqual(transport.call_count, 1)
            out = actions.dispatch("rsshub-probe", {"r.base_url": ["not an address"], "probe_kind": ["feed"],
                "probe_feed": ["https://feed.example.invalid/feed.xml"]}, fixture.news_dir)
            self.assertTrue(out["ok"], out)

    def test_schema_metadata_labels_and_history_do_not_claim_a_live_test(self):
        with offline_workbench() as fixture:
            payload = view.build(fixture.news_dir)
            payload["rsshub"].update({"origin": "env:RSSHub_BASE", "effective_url": "https://hub.example.invalid/prefix",
                                      "dependent_sources": ["research"]})
            schema = config.describe_schema()
            schema["rsshub_fields"][0]["label"] = "Schema-owned RSSHub label"
            text = view.config_html(payload, schema=schema, actions={"token": "test"}, page={"rsshub": "1"})
            for expected in ("Schema-owned RSSHub label", "RSS / RSSHub", "历史验证", "尚未检测", "RSSHub_BASE", "research"):
                self.assertTrue(expected in text, f"Missing {expected}")
            self.assertNotIn("目录条目已在本机 RSSHub 实测", text)

    def test_no_js_save_dry_run_is_not_reported_as_applied(self):
        with offline_workbench() as fixture:
            values = self.form(fixture)
            values["r.base_url"] = "https://hub.example.invalid/prefix"
            request = urllib.request.Request(fixture.url + "/api/actions/rsshub-save",
                data=urllib.parse.urlencode(values).encode(), headers={"Origin": fixture.url})
            with urllib.request.urlopen(request) as response:
                text = response.read().decode()
            self.assertTrue("校验通过 · 尚未应用" in text, "No-JS dry-run must say it was not applied")
            parsed = RssForm()
            parsed.feed(text)
            self.assertEqual(parsed.values["r.base_url"], values["r.base_url"])
            self.assertEqual(parsed.values["base_hash"], values["base_hash"])
            self.assertEqual(config.load(fixture.news_dir).rsshub["base_url"], "")

    def test_failed_no_js_probe_is_safe_visible_and_does_not_mutate_state(self):
        with offline_workbench() as fixture, patch.object(rssconnect, "_probe_request", side_effect=urllib.error.URLError("SECRET")) as transport:
            values = self.form(fixture)
            values.update({"r.base_url": "https://hub.example.invalid/prefix", "probe_feed": "/rss?token=SECRET", "probe_kind": "feed"})
            before = {p.relative_to(fixture.news_dir): p.read_bytes() for p in fixture.news_dir.rglob("*") if p.is_file()}
            request = urllib.request.Request(fixture.url + "/api/actions/rsshub-probe",
                data=urllib.parse.urlencode(values).encode(), headers={"Origin": fixture.url})
            with urllib.request.urlopen(request) as response:
                text = response.read().decode()
                self.assertNotIn("SECRET", response.url)
            self.assertTrue("只读检测 · 失败" in text, "Missing explicit read-only failure")
            # The user's own input remains editable, but result/audit contain no secret.
            result = text.split('data-action-feedback', 1)[1].split('</div>', 1)[0]
            self.assertNotIn("SECRET", result)
            self.assertEqual(before, {p.relative_to(fixture.news_dir): p.read_bytes() for p in fixture.news_dir.rglob("*") if p.is_file()})
            self.assertEqual(transport.call_count, 1)
