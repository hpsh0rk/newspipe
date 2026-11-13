"""Adapter metadata reaches the real CLI writers and no-JS source dialog."""
import json
import unittest
import urllib.error
from urllib.parse import urlencode, urlsplit, parse_qs
import urllib.request

from newspipe import actions, config, edit, view
from preview_workbench import offline_workbench
from test_actions import NoRedirect


class AdapterSettingsWebTests(unittest.TestCase):
    def form(self, **kw):
        return {"name": ["github-news"], "create_only": ["1"], "f.adapter": ["github"],
                "f.enabled": ["0"], "f.fetch.trigger": ["manual"], "f.enrich.summary": ["off"],
                "f.settings.repositories": ["Owner/Repo"], "f.settings.users": ["Alice"],
                "f.settings.token_env": [""], "f.settings.max_pages": ["2"],
                "notify_policy": ["archive"], **kw}

    def test_selected_adapter_fields_are_translated_not_discarded(self):
        form = self.form(**{"f.feeds": ["https://foreign.invalid/feed"], "f.http2": ["1"]})
        body = actions.build_source_body(form)
        self.assertEqual(body.get("settings"), {"repositories": ["Owner/Repo"], "users": ["Alice"], "token_env": "", "max_pages": 2})
        self.assertNotIn("feeds", body)
        self.assertNotIn("http2", body)
        body = actions.build_source_body({**form, "f.adapter": ["rsshub"]})
        self.assertNotIn("settings", body)
        self.assertEqual(body["feeds"], ["https://foreign.invalid/feed"])

    def test_cli_create_dry_patch_copy_preserve_settings_and_unknown_extras(self):
        with offline_workbench() as fixture:
            base = edit.file_hash(fixture.news_dir / "sources.yaml")
            form = self.form(base_hash=[base], dry=["1"])
            checked = actions.dispatch("source-save", form, fixture.news_dir)
            self.assertTrue(checked["ok"], checked)
            self.assertNotIn("github-news", config.load(fixture.news_dir).sources)
            saved = actions.dispatch("source-save", {**form, "dry": ["0"]}, fixture.news_dir)
            self.assertTrue(saved["ok"], saved)
            source = config.load(fixture.news_dir).sources["github-news"]
            self.assertEqual(source.extra.get("settings", {}).get("max_pages"), 2)
            payload = view.build(fixture.news_dir)
            changes = {"name": ["github-news"], "base_hash": [payload["sources_hash"]],
                       "source_revision": [payload["source_revisions"]["github-news"]], "f.adapter": ["github"],
                       "f.settings.users": [""], "f.settings.max_pages": [""]}
            patched = actions.dispatch("source-patch", changes, fixture.news_dir)
            self.assertTrue(patched["ok"], patched)
            values = config.source_values(config.load(fixture.news_dir).sources["github-news"])
            self.assertEqual(values["settings.users"], [])
            self.assertEqual(values["settings.max_pages"], 1)
            self.assertEqual(values["settings.repositories"], ["Owner/Repo"])
            import yaml
            block = yaml.safe_load((fixture.news_dir / "sources.yaml").read_text())["sources"]["github-news"]
            block["unknown"] = {"keep": False}
            self.assertTrue(edit.set_source(fixture.news_dir, "github-news", block)["ok"])
            copied = actions.dispatch("source-copy", {"name": ["github-news"], "new_name": ["github-copy"],
                "base_hash": [edit.file_hash(fixture.news_dir / "sources.yaml")]}, fixture.news_dir)
            self.assertTrue(copied["ok"], copied)
            sources = config.load(fixture.news_dir).sources
            self.assertEqual(sources["github-copy"].extra, sources["github-news"].extra)
            self.assertFalse(sources["github-copy"].extra["unknown"]["keep"])

    def test_invalid_parameter_validation_never_writes_or_echoes_a_literal_token(self):
        with offline_workbench() as fixture:
            before = (fixture.news_dir / "sources.yaml").read_bytes()
            for key, value in (("max_pages", "4"), ("repositories", "Owner/.."), ("users", "../secret"),
                               ("token_env", "Bearer sensitive-input")):
                out = actions.dispatch("source-save", self.form(**{"f.settings." + key: [value],
                    "base_hash": [edit.file_hash(fixture.news_dir / "sources.yaml")]}), fixture.news_dir)
                self.assertFalse(out["ok"], (key, out))
                self.assertNotIn("sensitive-input", json.dumps(out))
                self.assertEqual((fixture.news_dir / "sources.yaml").read_bytes(), before)

    def test_selected_descriptor_controls_render_in_edit_and_copy(self):
        with offline_workbench() as fixture:
            self.assertTrue(actions.dispatch("source-save", self.form(), fixture.news_dir)["ok"])
            payload = view.build(fixture.news_dir)
            page = view.config_html(payload, schema=config.describe_schema(fixture.news_dir),
                actions={"token": "test"}, page={"edit": "github-news"})
            self.assertIn('name="f.settings.repositories"', page)
            self.assertIn('value="Owner/Repo"', page)
            self.assertIn('data-adapter-for="github"', page)
            self.assertIn("GitHub", page)
            fields = getattr(config, "source_fields", None)
            self.assertIsNotNone(fields, "source-specific fields need one schema composition function")
            self.assertIn("settings.repositories", {f["path"] for f in fields("github")})

    def test_no_js_adapter_refresh_retains_unsaved_controls_and_performs_no_write(self):
        with offline_workbench() as fixture:
            before = (fixture.news_dir / "sources.yaml").read_bytes()
            page = urllib.request.urlopen(fixture.url + "/config?new=1").read().decode()
            from html.parser import HTMLParser
            class Token(HTMLParser):
                value = ""
                def handle_starttag(self, tag, attrs):
                    attrs = dict(attrs)
                    if tag == "input" and attrs.get("name") == "token" and attrs.get("value"):
                        self.value = attrs["value"]
            token = Token(); token.feed(page)
            form = self.form(token=[token.value], editor_intent=["select-adapter"], return_to=["/config"],
                             **{"f.feed_label": ["My draft"]})
            request = urllib.request.Request(fixture.url + "/api/actions/source-save", data=urlencode(form, doseq=True).encode(),
                headers={"Origin": fixture.url})
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.build_opener(NoRedirect).open(request)
            self.assertEqual(caught.exception.code, 303)
            location = caught.exception.headers["Location"]
            self.assertEqual(set(parse_qs(urlsplit(location).query)), {"draft"})
            page = urllib.request.urlopen(fixture.url + location).read().decode()
            self.assertIn('name="f.settings.repositories"', page)
            self.assertIn('value="Owner/Repo"', page)
            self.assertIn('value="My draft"', page)
            self.assertIn("草稿已调整", page)
            self.assertEqual((fixture.news_dir / "sources.yaml").read_bytes(), before)

    def test_invalid_credential_reference_is_not_reflected_in_restored_draft(self):
        from newspipe.webdrafts import restore
        with offline_workbench() as fixture:
            payload = view.build(fixture.news_dir)
            page = view.config_html(payload, schema=config.describe_schema(), actions={"token": "test"},
                page={"new": "1", "prefill": {"adapter": "github"}})
            restored = restore(page, "source-save", self.form(**{"f.settings.token_env": ["Bearer sensitive-input"]}))
            self.assertNotIn("sensitive-input", restored)

    def test_acquisition_details_show_safe_quota_and_window_limits(self):
        from newspipe.source_contract import FetchResult, SubscriptionReport
        from datetime import datetime
        with offline_workbench() as fixture:
            report = FetchResult([], (SubscriptionReport("x", "empty", diagnostics={
                "credential_mode": "anonymous", "rate_remaining": 0, "rate_limit": 60,
                "window_limited": True}),))
            fixture.store.record_collection_report("research", report.summary(), at=datetime.now())
            page = view.cockpit_html(view.build(fixture.news_dir))
            self.assertIn("匿名 API", page)
            self.assertIn("额度剩余 0 / 60", page)
            self.assertIn("分页上限", page)


if __name__ == "__main__":
    unittest.main()
