"""Editors are explicit SSR panels, never permanent forms on list pages."""
import unittest
import urllib.parse
import urllib.request

from newspipe import config, view
from preview_workbench import offline_workbench


class WebEditorTests(unittest.TestCase):
    def test_no_js_policy_validation_preserves_all_added_clock_rows_and_keys(self):
        from test_rsshub_web import RssForm
        with offline_workbench() as fixture:
            with urllib.request.urlopen(fixture.url + '/notifications?edit=live') as response:
                page = response.read().decode()
            parser = RssForm()
            parser.feed(page.replace('/api/actions/policy-save', '/api/actions/rsshub-save'))
            values = {key: [value] for key, value in parser.values.items()}
            values.update({'p.mode': ['digest'], 'p.times_key': ['one', 'two', 'three', 'four'],
                           'p.times': ['09:45', '12:30', '20:00', 'bad-time']})
            before = (fixture.news_dir / 'sources.yaml').read_bytes()
            request = urllib.request.Request(fixture.url + '/api/actions/policy-save',
                data=urllib.parse.urlencode(values, doseq=True).encode(), headers={'Origin': fixture.url})
            with urllib.request.urlopen(request) as response:
                restored = response.read().decode()
            self.assertTrue('data-editor-panel="policy"' in restored)
            for value in ('09:45', '12:30', '20:00', 'bad-time', 'one', 'two', 'three', 'four'):
                self.assertTrue('value="' + value + '"' in restored, value)
            self.assertEqual((fixture.news_dir / 'sources.yaml').read_bytes(), before)

    def test_no_js_policy_copy_validation_retains_copy_context(self):
        from test_rsshub_web import RssForm
        with offline_workbench() as fixture:
            with urllib.request.urlopen(fixture.url + '/notifications?copy=live') as response:
                page = response.read().decode()
            parser = RssForm()
            parser.feed(page.replace('/api/actions/policy-save', '/api/actions/rsshub-save'))
            values = {**parser.values, 'name': 'copied_policy', 'p.mode': 'collect'}
            request = urllib.request.Request(fixture.url + '/api/actions/policy-save',
                data=urllib.parse.urlencode(values).encode(), headers={'Origin': fixture.url})
            with urllib.request.urlopen(request) as response:
                restored = response.read().decode()
            self.assertTrue('>复制策略</h2>' in restored, 'PRG lost copy editor identity')
            self.assertTrue('name="name"' in restored and 'value="copied_policy"' in restored)

    def test_normal_lists_have_edit_links_but_no_editing_forms(self):
        with offline_workbench() as fixture:
            payload = view.build(fixture.news_dir)
            actions = {"token": "test"}
            for render in (view.config_html, view.notifications_html):
                page = render(payload, schema=config.describe_schema(), actions=actions)
                self.assertTrue('data-editor-open' in page, "Missing progressive editor links")
                for action in ("source-save", "source-patch", "policy-save", "global-save", "rsshub-save"):
                    self.assertFalse(f'action="/api/actions/{action}"' in page, f"Permanent {action} editor")
                self.assertNotIn("编辑 / 创建策略", page)

    def test_requested_editors_have_distinct_titles_and_one_panel(self):
        with offline_workbench() as fixture:
            payload = view.build(fixture.news_dir)
            cases = ((view.config_html, {"new": "1"}, "添加消息源"),
                     (view.config_html, {"edit": "research"}, "编辑消息源"),
                     (view.config_html, {"copy": "research"}, "复制消息源"),
                     (view.config_html, {"rsshub": "1"}, "RSSHub 设置"),
                     (view.notifications_html, {"new": "1"}, "创建策略"),
                     (view.notifications_html, {"edit": "live"}, "编辑策略"),
                     (view.notifications_html, {"copy": "live"}, "复制策略"),
                     (view.notifications_html, {"global": "1"}, "全局通知设置"))
            for render, query, title in cases:
                with self.subTest(query=query):
                    page = render(payload, schema=config.describe_schema(), actions={"token": "test"}, page=query)
                    self.assertEqual(page.count('data-editor-panel='), 1)
                    self.assertTrue(title in page, title)
                    self.assertTrue('data-editor-close' in page, "Missing fallback close link")

    def test_no_js_global_preview_retains_requested_editor_and_controls(self):
        from test_rsshub_web import RssForm
        with offline_workbench() as fixture:
            with urllib.request.urlopen(fixture.url + "/notifications?global=1") as response:
                page = response.read().decode()
            parser = RssForm()
            # Reuse the small existing parser with a different target action.
            page = page.replace('/api/actions/global-save', '/api/actions/rsshub-save')
            parser.feed(page)
            values = parser.values
            values.pop("slot_id", None)
            values.pop("slot_time", None)
            self.assertTrue("token" in values, "Missing explicit global editor")
            before = (fixture.news_dir / "sources.yaml").read_bytes()
            values.update({"preview_kind": "global", "g.max_cards_per_day": "31"})
            request = urllib.request.Request(fixture.url + "/api/actions/notification-preview",
                data=urllib.parse.urlencode(values).encode(), headers={"Origin": fixture.url})
            with urllib.request.urlopen(request) as response:
                restored = response.read().decode()
            self.assertTrue('data-editor-panel="global"' in restored, "PRG lost global editor kind")
            self.assertTrue('name="g.max_cards_per_day"' in restored)
            self.assertTrue('value="31"' in restored)
            self.assertEqual((fixture.news_dir / "sources.yaml").read_bytes(), before)
