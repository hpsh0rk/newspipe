"""Focused summaries retain controls and accessible contextual help."""
import unittest

from newspipe import config, view
from preview_workbench import offline_workbench
import test_browser_e2e as browser_tests


class WebHelpTests(unittest.TestCase):
    def test_help_is_escaped_and_normal_settings_are_compact_read_regions(self):
        from newspipe.webui import help_tip
        tip = help_tip('<script>unsafe</script>', 'help-one', '说明')
        self.assertNotIn('<script>', tip)
        self.assertIn('&lt;script&gt;', tip)
        self.assertIn('data-help', tip)
        self.assertNotIn('<details', tip, 'Help must be valid phrasing content inside summaries and labels')
        self.assertIn('aria-describedby="help-one"', tip)
        with offline_workbench() as fixture:
            payload = view.build(fixture.news_dir)
            for render, region in ((view.config_html, 'rsshub'), (view.notifications_html, 'global')):
                page = render(payload, schema=config.describe_schema(), actions={"token": "test"})
                self.assertIn(f'data-read-region="{region}"', page)
                self.assertIn('summary-strip', page)
                self.assertIn('data-help', page)

    def test_policy_advanced_values_and_explicit_validation_label_remain(self):
        with offline_workbench() as fixture:
            page = view.notifications_html(view.build(fixture.news_dir), schema=config.describe_schema(),
                                           actions={"token": "test"}, page={"edit": "live"})
            self.assertIn('data-policy-advanced', page)
            self.assertIn('data-save-button', page)
            self.assertIn('校验配置', page)
            self.assertIn('保存配置', page)
            for field in config.describe_schema()["policy_fields"]:
                self.assertIn('name="p.' + field["path"] + '"', page)
            self.assertIn('class="field-error"', page)


@unittest.skipUnless(browser_tests.ENABLED, "Enable NEWSPIPE_BROWSER_TESTS=1 for real-browser acceptance")
class WebHelpBrowserTests(unittest.TestCase):
    setUpClass = classmethod(browser_tests.WorkbenchBrowserTests.setUpClass.__func__)
    scenario = browser_tests.WorkbenchBrowserTests.scenario
    editor = browser_tests.WorkbenchBrowserTests.editor
    submit = browser_tests.WorkbenchBrowserTests.submit
    close_editor = browser_tests.WorkbenchBrowserTests.close_editor

    def test_global_summary_save_preserves_cached_policy_draft_and_hash(self):
        for engine in self.browsers:
            with self.subTest(engine=engine), self.scenario(engine) as (fixture, page):
                form = self.editor(fixture, page, policy=True)
                form.locator('[name="p.label"]').fill('Cached policy')
                base = form.locator('[name="base_hash"]').input_value()
                self.close_editor(page, dirty=True)
                page.get_by_role('link', name='全局设置', exact=True).click()
                global_form = page.locator('#editor-dialog form')
                global_form.locator('[name="g.max_cards_per_day"]').fill('33')
                global_form.locator('[name="dry"]').uncheck()
                self.expect(global_form.locator('[data-save-button]')).to_have_text('校验配置保存配置', use_inner_text=False)
                self.expect(global_form.get_by_role('button', name='保存配置', exact=True)).to_be_visible()
                self.assertTrue(self.submit(page, global_form)['ok'])
                self.expect(page.locator('[data-read-region="global"]')).to_contain_text('/ 33 次')
                page.locator('[data-policy="live"]').get_by_role('link', name='编辑', exact=True).click()
                self.expect(form.locator('[name="p.label"]')).to_have_value('Cached policy')
                self.assertEqual(form.locator('[name="base_hash"]').input_value(), base)

    def test_no_js_help_click_and_save_labels(self):
        for engine in self.browsers:
            with self.subTest(engine=engine), self.scenario(engine, width=360, javascript=False) as (fixture, page):
                page.goto(fixture.url + '/notifications?edit=live')
                form = page.locator('form[action="/api/actions/policy-save"]')
                self.expect(form.get_by_role('button', name='校验配置', exact=True)).to_be_visible()
                form.locator('[name="dry"]').uncheck()
                self.expect(form.get_by_role('button', name='保存配置', exact=True)).to_be_visible()
                help = form.locator('[data-help]').first
                help.locator('button').click()
                self.expect(help.locator('[role="tooltip"]')).to_be_visible()
                rect = help.locator('[role="tooltip"]').bounding_box()
                self.assertGreaterEqual(rect['x'], 7)
                self.assertLessEqual(rect['x'] + rect['width'], 353)

    def test_help_focus_hover_pin_escape_and_viewport_fit(self):
        for engine in self.browsers:
            with self.subTest(engine=engine), self.scenario(engine, width=360) as (fixture, page):
                self.editor(fixture, page, policy=True)
                modal = page.locator('#editor-dialog')
                help = modal.locator('[data-help]').first
                trigger = help.locator('button')
                trigger.hover()
                self.expect(help).to_have_attribute('open', '')
                tip = help.locator('[role="tooltip"]')
                rect = tip.bounding_box()
                self.assertGreaterEqual(rect['x'], 7)
                self.assertLessEqual(rect['x'] + rect['width'], 353)
                page.keyboard.press('Escape')
                self.expect(help).not_to_have_attribute('open', '')
                self.expect(modal).to_be_visible()
                trigger.click()
                page.mouse.move(1, 1)
                self.expect(help).to_have_attribute('open', '')
                page.keyboard.press('Escape')
                self.expect(help).not_to_have_attribute('open', '')
                self.expect(modal).to_be_visible()
                modal.locator('[data-editor-close]').first.focus()
                trigger.focus()
                self.expect(help).to_have_attribute('open', '')
