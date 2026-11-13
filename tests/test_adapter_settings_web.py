"""Adapter metadata reaches the real CLI writers and no-JS source dialog."""
import json
import http.client
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

    def test_inactive_drafts_are_sanitized_bounded_and_not_cli_parameters(self):
        from newspipe.webdrafts import safe_values, adapter_drafts
        form = self.form(**{"adapter_rendered": ["rsshub"], "f.adapter": ["rsshub"], "adapter_draft": [json.dumps({
            "github": {"settings.users": ["Alice"], "settings.token_env": ["Bearer sensitive-input"]}})]})
        retained = safe_values(form)
        self.assertNotIn("sensitive-input", json.dumps(retained))
        self.assertEqual(adapter_drafts(retained)["github"]["settings.users"], ["Alice"])
        self.assertNotIn("settings", actions.build_source_body(retained))
        with self.assertRaises(actions.ActionInputError):
            adapter_drafts({"adapter_draft": ["x" * 32769]})

    def test_no_js_save_requires_refresh_after_adapter_changes(self):
        with offline_workbench() as fixture:
            before = (fixture.news_dir / "sources.yaml").read_bytes()
            out = actions.dispatch("source-save", self.form(adapter_rendered=["rsshub"]), fixture.news_dir)
            self.assertFalse(out["ok"])
            self.assertEqual((fixture.news_dir / "sources.yaml").read_bytes(), before)


class GoogleNewsSettingsWebTests(unittest.TestCase):
    def form(self, **kw):
        return {"name": ["google-news"], "create_only": ["1"], "f.adapter": ["google_news"],
                "f.enabled": ["0"], "f.fetch.trigger": ["manual"], "f.enrich.summary": ["off"],
                "f.settings.queries": ['"ACME, Inc." OR model\n\u4eba\u5de5\u667a\u80fd\u3001\u6280\u672f'],
                "f.settings.language": ["en-US"], "f.settings.region": ["US"],
                "f.settings.edition": [""], "f.settings.lookback_days": ["7"], "f.settings.max_results": ["50"],
                "notify_policy": ["archive"], **kw}

    def http_form(self, fixture, **kw):
        from test_rsshub_web import RssForm
        with urllib.request.urlopen(fixture.url + "/config?new=1") as response:
            page = response.read().decode()
        parsed = RssForm()
        parsed.feed(page.replace('/api/actions/source-save', '/api/actions/rsshub-save'))
        return self.form(token=[parsed.values["token"]], adapter_rendered=["google_news"],
                         adapter_draft=["{}"], return_to=["/config"], **kw)

    def test_maximum_ascii_queries_validate_and_save_through_real_http(self):
        queries = ["x" * 1019 + f"{i:05d}" for i in range(25)]
        with offline_workbench() as fixture:
            form = self.http_form(fixture, **{"f.settings.queries": ["\n".join(queries)], "dry": ["1"]})
            before = (fixture.news_dir / "sources.yaml").read_bytes()
            for dry in ("1", "0"):
                form["dry"] = [dry]
                body = urlencode(form, doseq=True).encode()
                self.assertLess(len(body), 65536)
                request = urllib.request.Request(fixture.url + "/api/actions/source-save", data=body,
                    headers={"Origin": fixture.url, "Accept": "application/json"})
                with urllib.request.urlopen(request) as response:
                    result = json.load(response)
                self.assertTrue(result["ok"], result)
                if dry == "1":
                    self.assertEqual((fixture.news_dir / "sources.yaml").read_bytes(), before)
            source = config.load(fixture.news_dir).sources["google-news"]
            self.assertEqual(source.extra["settings"]["queries"], queries)
            self.assertFalse(source.enabled)

    def test_no_js_long_and_oversized_query_errors_remain_editable(self):
        from html.parser import HTMLParser
        class QueryText(HTMLParser):
            def __init__(self):
                super().__init__()
                self.active, self.text = False, ""
            def handle_starttag(self, tag, attrs):
                if tag == "textarea":
                    self.active = dict(attrs).get("name") == "f.settings.queries"
            def handle_endtag(self, tag):
                if tag == "textarea":
                    self.active = False
            def handle_data(self, value):
                if self.active:
                    self.text += value
        for raw, message in (("\n".join("x" * 1019 + f"{i:05d}" for i in range(16)), "settings.max_results"),
                             ("x" * 32769, "草稿")):
            with self.subTest(size=len(raw)), offline_workbench() as fixture:
                form = self.http_form(fixture, **{"f.settings.queries": [raw], "f.settings.max_results": ["0"]})
                before = (fixture.news_dir / "sources.yaml").read_bytes()
                request = urllib.request.Request(fixture.url + "/api/actions/source-save",
                    data=urlencode(form, doseq=True).encode(), headers={"Origin": fixture.url})
                try:
                    with urllib.request.urlopen(request) as response:
                        self.assertEqual(response.status, 200)
                        page = response.read().decode()
                        self.assertEqual(set(parse_qs(urlsplit(response.url).query)), {"draft"})
                except (urllib.error.HTTPError, http.client.RemoteDisconnected) as error:
                    self.fail(f"Failed draft did not return an editable page: {error}")
                self.assertIn(message, page)
                parsed = QueryText(); parsed.feed(page)
                # The browser strips the reserved first textarea newline.
                self.assertEqual(parsed.text, "\n" + raw)
                self.assertEqual((fixture.news_dir / "sources.yaml").read_bytes(), before)


    def test_query_lines_keep_commas_and_phrases_as_one_query(self):
        body = actions.build_source_body(self.form(**{"f.feeds": ["https://foreign.invalid/feed"], "f.http2": ["1"]}))
        self.assertEqual(body["settings"]["queries"], ['"ACME, Inc." OR model', '\u4eba\u5de5\u667a\u80fd\u3001\u6280\u672f'])
        self.assertNotIn("feeds", body)
        self.assertNotIn("http2", body)

    def test_region_edition_mismatch_is_rejected_by_cli_writer_before_writing(self):
        with offline_workbench() as fixture:
            before = (fixture.news_dir / "sources.yaml").read_bytes()
            result = actions.dispatch("source-save", self.form(**{"f.settings.edition": ["CN:zh-Hans"]}), fixture.news_dir)
            self.assertFalse(result["ok"], result)
            self.assertEqual((fixture.news_dir / "sources.yaml").read_bytes(), before)
            self.assertIn("settings.edition", result["error"]["message"])

    def test_queries_render_as_multiline_and_existing_values_round_trip(self):
        with offline_workbench() as fixture:
            result = actions.dispatch("source-save", self.form(), fixture.news_dir)
            self.assertTrue(result["ok"], result)
            payload = view.build(fixture.news_dir)
            page = view.config_html(payload, schema=config.describe_schema(), actions={"token": "test"},
                                    page={"edit": "google-news"})
            from html.parser import HTMLParser
            class Controls(HTMLParser):
                query_tag = ""
                def handle_starttag(self, tag, attrs):
                    if dict(attrs).get("name") == "f.settings.queries":
                        self.query_tag = tag
            controls = Controls(); controls.feed(page)
            self.assertEqual(controls.query_tag, "textarea")
            self.assertIn("每行", page)

    def test_dry_preview_patch_copy_and_conflict_preserve_queries_and_unknown_extras(self):
        from newspipe import cli
        with offline_workbench() as fixture:
            before = (fixture.news_dir / "sources.yaml").read_bytes()
            form = self.form(base_hash=[edit.file_hash(fixture.news_dir / "sources.yaml")])
            dry = actions.dispatch("source-save", {**form, "dry": ["1"]}, fixture.news_dir)
            self.assertTrue(dry["ok"], dry)
            self.assertEqual((fixture.news_dir / "sources.yaml").read_bytes(), before)
            result = actions.dispatch("source-save", form, fixture.news_dir)
            self.assertTrue(result["ok"], result)
            source = config.load(fixture.news_dir).sources["google-news"]
            self.assertEqual(source.extra["settings"]["queries"], ['"ACME, Inc." OR model', '\u4eba\u5de5\u667a\u80fd\u3001\u6280\u672f'])
            added = cli.run_subcommand(["source", "patch", "google-news", "--from-json",
                                       '{"unknown":{"keep":false}}', "--json"], fixture.news_dir)
            self.assertTrue(added.ok, added.as_dict())
            payload = view.build(fixture.news_dir)
            patch_form = {"name": ["google-news"], "f.adapter": ["google_news"],
                          "base_hash": [payload["sources_hash"]], "source_revision": [payload["source_revisions"]["google-news"]],
                          "f.settings.queries": ['"B, Co"'], "f.settings.max_results": [""]}
            original = (fixture.news_dir / "sources.yaml").read_bytes()
            preview = actions.dispatch("notification-preview", {**patch_form, "preview_kind": ["source"]}, fixture.news_dir)
            self.assertTrue(preview["ok"], preview)
            self.assertEqual((fixture.news_dir / "sources.yaml").read_bytes(), original)
            self.assertTrue(actions.dispatch("source-patch", patch_form, fixture.news_dir)["ok"])
            conflict = actions.dispatch("source-patch", patch_form, fixture.news_dir)
            self.assertFalse(conflict["ok"])
            self.assertEqual(conflict["error"]["code"], "E_CONFLICT")
            source = config.load(fixture.news_dir).sources["google-news"]
            self.assertEqual(source.extra["settings"]["queries"], ['"B, Co"'])
            self.assertEqual(source.extra["settings"]["max_results"], 50)
            self.assertFalse(source.extra["unknown"]["keep"])
            copy = actions.dispatch("source-copy", {"name": ["google-news"], "new_name": ["google-copy"],
                "base_hash": [edit.file_hash(fixture.news_dir / "sources.yaml")]}, fixture.news_dir)
            self.assertTrue(copy["ok"], copy)
            copied = config.load(fixture.news_dir).sources["google-copy"]
            self.assertEqual(copied.extra, source.extra)
            self.assertFalse(copied.enabled)
            self.assertEqual(source.notify.overrides, {})

    def test_invalid_limits_and_queries_never_write(self):
        with offline_workbench() as fixture:
            before = (fixture.news_dir / "sources.yaml").read_bytes()
            for key, value in (("max_results", "0"), ("lookback_days", "91"), ("language", "../locale"),
                               ("queries", "AI when:2d")):
                result = actions.dispatch("source-save", self.form(**{"f.settings." + key: [value]}), fixture.news_dir)
                self.assertFalse(result["ok"], result)
                self.assertEqual((fixture.news_dir / "sources.yaml").read_bytes(), before)

    def test_search_and_result_windows_are_not_presented_as_pagination(self):
        from newspipe.source_contract import FetchResult, SubscriptionReport
        from datetime import datetime
        with offline_workbench() as fixture:
            for kind, text in (("search_window", "搜索窗口"), ("result_cap", "保留条数上限"), ("page_cap", "分页上限")):
                for status in ("empty", "failure"):
                    report = SubscriptionReport("private-query", status, code="network" if status == "failure" else "",
                                                diagnostics={"window_limited": True, "window_kind": kind})
                    fixture.store.record_collection_report("research", FetchResult([], (report,)).summary(), at=datetime.now())
                    page = view.cockpit_html(view.build(fixture.news_dir))
                    self.assertIn(text, page)
                    self.assertNotIn("private-query", page)
                    if kind != "page_cap":
                        self.assertNotIn("已达分页上限", page)


if __name__ == "__main__":
    unittest.main()
