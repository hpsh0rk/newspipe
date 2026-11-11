"""Real-engine acceptance against synthetic data, opt-in locally and mandatory in CI.

NEWSPIPE_BROWSER_TESTS=1 requires Playwright and both installed engines. Browser
requests are restricted to the ephemeral fixture origin; provider calls are denied
server-side too. Only failing scenarios retain synthetic screenshots and traces.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import sys
import unittest
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from newspipe import cli, config
from preview_workbench import offline_workbench

ENABLED = os.environ.get("NEWSPIPE_BROWSER_TESTS") == "1"
ARTIFACTS = Path(__file__).resolve().parents[1] / "test-results/browser"


@unittest.skipUnless(ENABLED, "Enable NEWSPIPE_BROWSER_TESTS=1 for real-browser acceptance")
class WorkbenchBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright, expect
        except ImportError as exc:
            raise RuntimeError("Playwright is required; install .[browser-test]") from exc
        cls.expect = staticmethod(expect)
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        cls.browsers = {}
        for engine in ("chromium", "webkit"):
            # Missing binaries must error, never convert this mandatory gate to a skip.
            browser = getattr(cls.playwright, engine).launch(headless=True)
            cls.addClassCleanup(browser.close)
            cls.browsers[engine] = browser

    @contextmanager
    def scenario(self, engine, *, width=1440, javascript=True):
        with offline_workbench() as fixture:
            context = self.browsers[engine].new_context(
                viewport={"width": width, "height": 900}, java_script_enabled=javascript,
                reduced_motion="reduce", service_workers="block")
            context.set_default_timeout(5000)
            context.tracing.start(screenshots=True, snapshots=True, sources=True)
            escaped, errors = [], []
            def restrict(route):
                if urlsplit(route.request.url)[:2] == urlsplit(fixture.url)[:2]:
                    route.continue_()
                else:
                    escaped.append(route.request.url)
                    route.abort()
            context.route("**/*", restrict)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            try:
                yield fixture, page
                self.assertEqual(errors, [], "Unexpected JavaScript exception")
                self.assertEqual(escaped, [], "Browser tried to leave the fixture origin")
                self.assertEqual(fixture.blocked_effects, [], "Unexpected provider operation")
            except BaseException:
                ARTIFACTS.mkdir(parents=True, exist_ok=True)
                stem = f"{self._testMethodName}-{engine}-{width}-js{int(javascript)}"
                try:
                    page.screenshot(path=str(ARTIFACTS / f"{stem}.png"), full_page=True)
                finally:
                    context.tracing.stop(path=str(ARTIFACTS / f"{stem}.zip"))
                raise
            else:
                context.tracing.stop()
            finally:
                context.close()

    def editor(self, fixture, page, *, policy=False):
        page.goto(fixture.url + ("/notifications?edit=live#policy-editor" if policy else "/config?edit=research#new"))
        action = "policy-save" if policy else "source-patch"
        return page.locator(f'form[action="/api/actions/{action}"]')

    def submit(self, page, form, button="验证 / 保存"):
        with page.expect_response(lambda response: "/api/actions/" in response.url) as response:
            form.get_by_role("button", name=button, exact=True).click()
        envelope = response.value.json()
        self.expect(form).not_to_have_attribute("aria-busy", "true")
        return envelope

    def source_label(self, fixture):
        return config.source_values(config.load(fixture.news_dir).sources["research"])["feed_label"]

    def test_routes_editors_responsive_labels_keyboard_and_reduced_motion(self):
        routes = ("/", "/config", "/notifications", "/items", "/ops",
                  "/config?edit=research#new", "/notifications?edit=live#policy-editor")
        for engine in self.browsers:
            for width in (360, 768, 1440):
                with self.subTest(engine=engine, width=width), self.scenario(engine, width=width) as (fixture, page):
                    for route in routes:
                        response = page.goto(fixture.url + route)
                        self.assertEqual(response.status, 200, route)
                        self.expect(page.locator("h1")).to_be_visible()
                        self.assertEqual(page.locator("nav a.tab").count(), 5, route)
                        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width + 1, route)
                        unlabeled = page.locator("input:not([type=hidden]), select").evaluate_all("""els => els.filter(el =>
                            !el.labels?.length && !el.getAttribute('aria-label') && !el.getAttribute('aria-labelledby')
                        ).map(el => el.name)""")
                        self.assertEqual(unlabeled, [], route)
                        self.assertTrue(page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches"))
                        page.locator("body").click(position={"x": 1, "y": 1})
                        page.keyboard.press("Tab")
                        focus = page.evaluate("""() => {const el = document.activeElement, style = getComputedStyle(el);
                            return {tag: el.tagName, html: el.outerHTML, visible: !!el.getClientRects().length,
                                outline: parseFloat(style.outlineWidth), ring: style.boxShadow,
                                transition: parseFloat(style.transitionDuration)};}""")
                        self.assertNotEqual(focus["tag"], "BODY", route)
                        self.assertTrue(focus["visible"], route)
                        self.assertTrue(focus["outline"] >= 2 or focus["ring"] != "none", (route, focus))
                        self.assertLessEqual(focus["transition"], 0.001, route)

    def test_source_preview_validation_save_and_disabled_copy(self):
        for engine in self.browsers:
            with self.subTest(engine=engine), self.scenario(engine) as (fixture, page):
                form = self.editor(fixture, page)
                label = form.locator('[name="f.feed_label"]')
                label.fill("Candidate feed")
                before = (fixture.news_dir / "sources.yaml").read_bytes()
                self.assertTrue(self.submit(page, form, "预览变化")["ok"])
                self.expect(page.locator("#action-result")).to_contain_text("只读预览")
                self.assertEqual((fixture.news_dir / "sources.yaml").read_bytes(), before)
                self.assertTrue(self.submit(page, form)["ok"])
                self.expect(page.locator("#action-result")).to_contain_text("尚未应用")
                self.assertEqual(self.source_label(fixture), "研究与论文")
                form.locator('[name="dry"]').uncheck()
                saved = self.submit(page, form)
                self.assertTrue(saved["ok"], saved)
                self.assertEqual(self.source_label(fixture), "Candidate feed")
                self.expect(label).to_have_value("Candidate feed")
                page.goto(fixture.url + "/config?copy=research")
                form = page.locator('form[action="/api/actions/source-copy"]')
                form.locator('[name="new_name"]').fill("research_copy")
                form.locator('[name="dry"]').uncheck()
                copied = self.submit(page, form)
                self.assertTrue(copied["ok"], copied)
                sources = config.load(fixture.news_dir).sources
                self.assertFalse(sources["research_copy"].enabled)
                self.assertEqual(sources["research_copy"].notify.policy, "digest")
                self.expect(form.get_by_role("button", name="验证 / 保存")).to_be_disabled()

    def test_policy_save_copy_and_grouped_validation_feedback(self):
        for engine in self.browsers:
            with self.subTest(engine=engine), self.scenario(engine) as (fixture, page):
                form = self.editor(fixture, page, policy=True)
                form.locator('[name="p.label"]').fill("Custom live policy")
                form.locator('[name="dry"]').uncheck()
                saved = self.submit(page, form)
                self.assertTrue(saved["ok"], saved)
                self.assertEqual(config.load(fixture.news_dir).policies["live"].label, "Custom live policy")
                page.goto(fixture.url + "/notifications?copy=live#policy-editor")
                form = page.locator('form[action="/api/actions/policy-save"]')
                form.locator('[name="name"]').fill("live_copy")
                form.locator('[name="dry"]').uncheck()
                self.assertTrue(self.submit(page, form)["ok"])
                self.assertIn("live_copy", config.load(fixture.news_dir).policies)
                page.goto(fixture.url + "/notifications?edit=live#policy-editor")
                form = page.locator('form[action="/api/actions/policy-save"]')
                form.locator('[name="p.mode"]').select_option("digest")
                for slot in form.locator('[name="p.slots"][type=checkbox]').all():
                    slot.uncheck()
                failed = self.submit(page, form)
                self.assertFalse(failed["ok"])
                slots = form.locator('[name="p.slots"][type=checkbox]').first
                self.expect(slots).to_have_attribute("aria-invalid", "true")
                described = slots.get_attribute("aria-describedby") or ""
                self.assertIn("policy-p-slots-error", described)
                self.expect(page.locator("#policy-p-slots-error")).not_to_be_empty()
                self.expect(slots).to_be_focused()

    def test_native_invalid_collapsed_field_is_revealed_without_submission(self):
        for engine in self.browsers:
            with self.subTest(engine=engine), self.scenario(engine) as (fixture, page):
                form = self.editor(fixture, page)
                field = form.locator('[name="f.filter.max_items"]')
                section = field.locator("xpath=ancestor::details[1]")
                section.locator(":scope > summary").click()
                field.fill("0")
                section.locator(":scope > summary").click()
                requests = []
                page.on("request", lambda request: requests.append(request.url) if request.method == "POST" else None)
                form.get_by_role("button", name="验证 / 保存").click()
                self.expect(field).to_be_visible()
                self.expect(field).to_be_focused()
                self.assertEqual(requests, [])

    def test_inherited_override_does_not_validate_hidden_unused_candidate(self):
        for engine in self.browsers:
            with self.subTest(engine=engine), self.scenario(engine) as (fixture, page):
                form = self.editor(fixture, page)
                form.get_by_text("自定义覆盖 · 每个字段可独立继承", exact=True).click()
                choice = form.locator('[name="inherit.quiet_hours"]')
                choice.select_option("custom")
                form.locator('[name="n.quiet_hours"]').fill("not-a-clock")
                choice.select_option("inherit")
                form.locator('[name="f.feed_label"]').fill("Use inherited values")
                saved = self.submit(page, form)
                self.assertTrue(saved["ok"], saved)
                self.expect(page.locator("#action-result")).to_contain_text("尚未应用")

    def test_conflict_and_network_failure_keep_input_scroll_and_sections(self):
        for engine in self.browsers:
            with self.subTest(engine=engine), self.scenario(engine, width=768) as (fixture, page):
                form = self.editor(fixture, page)
                field = form.locator('[name="f.feed_label"]')
                field.fill("Keep this edit")
                form.get_by_text("自定义覆盖 · 每个字段可独立继承", exact=True).click()
                form.locator('[name="dry"]').uncheck()
                result = cli.run_subcommand(["source", "enable", "reading", "--json"], fixture.news_dir)
                self.assertTrue(result.ok, result.as_dict())
                button = form.get_by_role("button", name="验证 / 保存")
                button.scroll_into_view_if_needed()
                scroll = page.evaluate("scrollY")
                stale_hash = form.locator('[name="base_hash"]').input_value()
                failed = self.submit(page, form)
                self.assertEqual(failed["error"]["code"], "E_CONFLICT")
                self.expect(field).to_have_value("Keep this edit")
                self.assertEqual(form.locator('[name="base_hash"]').input_value(), stale_hash)
                self.assertAlmostEqual(page.evaluate("scrollY"), scroll, delta=2)
                self.expect(form.locator("details").filter(has=page.get_by_text("自定义覆盖 · 每个字段可独立继承", exact=True))).to_have_attribute("open", "")
                attempts = []
                def abort(route):
                    attempts.append(route.request.url)
                    route.abort()
                page.route("**/api/actions/source-patch", abort)
                button.click()
                self.expect(page.locator("#action-result")).to_contain_text("结果未确认")
                self.expect(form).not_to_have_attribute("aria-busy", "true")
                self.expect(field).to_have_value("Keep this edit")
                self.assertAlmostEqual(page.evaluate("scrollY"), scroll, delta=2)
                self.assertEqual(len(attempts), 1)
                self.assertEqual(self.source_label(fixture), "研究与论文")

    def test_edits_during_pending_save_remain_unsaved(self):
        for engine in self.browsers:
            with self.subTest(engine=engine), self.scenario(engine) as (fixture, page):
                form = self.editor(fixture, page)
                field = form.locator('[name="f.feed_label"]')
                field.fill("Submitted version")
                form.locator('[name="dry"]').uncheck()
                attempts = []
                def delay(route):
                    attempts.append(route.request.url)
                    response = route.fetch()
                    field.fill("Edited while waiting")
                    route.fulfill(response=response)
                page.route("**/api/actions/source-patch", delay)
                self.assertTrue(self.submit(page, form)["ok"])
                self.assertEqual(self.source_label(fixture), "Submitted version")
                self.expect(field).to_have_value("Edited while waiting")
                self.expect(page.locator("#action-result")).to_contain_text("新编辑尚未保存")
                dialogs = []
                def stay(dialog):
                    dialogs.append(dialog.message)
                    dialog.dismiss()
                page.on("dialog", stay)
                page.locator('nav a[href="/notifications"]').click()
                self.assertEqual(len(dialogs), 1)
                self.assertIn("未保存", dialogs[0])
                self.assertIn("/config?edit=research", page.url)
                self.assertEqual(len(attempts), 1)

    def test_no_javascript_failed_drafts_and_core_writes(self):
        for engine in self.browsers:
            with self.subTest(engine=engine), self.scenario(engine, width=360, javascript=False) as (fixture, page):
                form = self.editor(fixture, page, policy=True)
                form.locator('[name="p.label"]').fill('Raw <draft> "value"')
                form.locator('[name="p.mode"]').select_option("digest")
                with page.expect_navigation():
                    form.get_by_role("button", name="验证 / 保存").click()
                self.assertRegex(page.url, r"/notifications\?draft=[A-Za-z0-9_-]+$")
                form = page.locator('form[action="/api/actions/policy-save"]')
                self.expect(form).to_be_visible()
                self.expect(form.locator('[name="p.label"]')).to_have_value('Raw <draft> "value"')
                self.expect(form.locator('[name="p.mode"]')).to_have_value("digest")
                self.expect(form.locator('[name="p.slots"][type=checkbox]').first).not_to_be_checked()
                self.assertEqual(config.load(fixture.news_dir).policies["live"].mode, "immediate")
                form.locator('[name="p.slots"][value="am"]').check()
                form.locator('[name="dry"]').uncheck()
                with page.expect_navigation():
                    form.get_by_role("button", name="验证 / 保存").click()
                self.assertEqual(config.load(fixture.news_dir).policies["live"].mode, "digest")
                form = self.editor(fixture, page)
                form.locator('[name="f.feed_label"]').fill('Source <draft> "value"')
                form.locator('[name="f.feeds"]').fill("")
                with page.expect_navigation():
                    form.get_by_role("button", name="验证 / 保存").click()
                self.assertRegex(page.url, r"/config\?draft=[A-Za-z0-9_-]+$")
                form = page.locator('form[action="/api/actions/source-patch"]')
                self.expect(form).to_be_visible()
                self.expect(form.locator('[name="f.feed_label"]')).to_have_value('Source <draft> "value"')
                self.expect(form.locator('[name="f.feeds"]')).to_have_value("")
                self.expect(form.locator('[name="notify_policy"]')).to_have_value("digest")
                self.assertEqual(self.source_label(fixture), "研究与论文")
                form.locator('[name="f.feeds"]').fill("https://example.invalid/repaired.xml")
                form.locator('[name="dry"]').uncheck()
                with page.expect_navigation():
                    form.get_by_role("button", name="验证 / 保存").click()
                self.assertEqual(self.source_label(fixture), 'Source <draft> "value"')
                page.goto(fixture.url + "/ops")
                before = fixture.store.pending_count("research")
                with page.expect_navigation():
                    page.locator('form[action="/api/actions/collect"]').filter(
                        has=page.locator('[name="name"][value="research"]')).get_by_role("button").click()
                self.assertIn("/ops?draft=", page.url)
                self.assertEqual(fixture.store.pending_count("research"), before + 1)
                page.reload()
                self.assertEqual(fixture.store.pending_count("research"), before + 1, "PRG refresh must not replay collection")


if __name__ == "__main__":
    unittest.main()
