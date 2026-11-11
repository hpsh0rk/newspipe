"""Real-engine acceptance against synthetic data, opt-in locally and mandatory in CI.

NEWSPIPE_BROWSER_TESTS=1 requires Playwright and both installed engines. Browser
requests are restricted to the ephemeral fixture origin; provider calls are denied
server-side too. Only failing scenarios retain synthetic screenshots and traces.
"""
from contextlib import contextmanager
import json
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
                self.expect(page.locator('[data-source="research_copy"]')).to_be_visible()
                self.expect(page.locator("#action-result")).to_be_in_viewport()
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
                self.expect(page.locator('[data-policy="live"] h3')).to_have_text("Custom live policy")
                page.goto(fixture.url + "/notifications?copy=live#policy-editor")
                form = page.locator('form[action="/api/actions/policy-save"]')
                form.locator('[name="name"]').fill("live_copy")
                form.locator('[name="dry"]').uncheck()
                self.assertTrue(self.submit(page, form)["ok"])
                self.assertIn("live_copy", config.load(fixture.news_dir).policies)
                self.expect(page.locator('[data-policy="live_copy"]')).to_be_visible()
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
                stale_hash = form.locator('[name="base_hash"]').input_value()
                failed = self.submit(page, form)
                self.assertEqual(failed["error"]["code"], "E_CONFLICT")
                self.expect(field).to_have_value("Keep this edit")
                self.assertEqual(form.locator('[name="base_hash"]').input_value(), stale_hash)
                self.expect(page.locator("#action-result")).to_be_in_viewport()
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
                self.expect(page.locator("#action-result")).to_be_in_viewport()
                self.assertEqual(len(attempts), 1)
                self.assertEqual(self.source_label(fixture), "研究与论文")

    def test_consecutive_toggles_refresh_cards_without_rebasing_dirty_editor(self):
        for engine in self.browsers:
            with self.subTest(engine=engine), self.scenario(engine) as (fixture, page):
                form = self.editor(fixture, page)
                field = form.locator('[name="f.feed_label"]')
                field.fill("Unsaved draft")
                old_hash = form.locator('[name="base_hash"]').input_value()
                row = page.locator('[data-source="reading"]')
                with page.expect_response(lambda r: "/api/actions/source-toggle" in r.url):
                    row.get_by_role("button", name="启用", exact=True).click()
                self.expect(row.get_by_role("button", name="停用", exact=True)).to_be_visible()
                self.expect(page.locator("#action-result")).to_be_in_viewport()
                with page.expect_response(lambda r: "/api/actions/source-toggle" in r.url) as response:
                    row.get_by_role("button", name="停用", exact=True).click()
                self.assertTrue(response.value.json()["ok"])
                self.expect(row.locator(".pill")).to_have_text("已停用")
                self.expect(field).to_have_value("Unsaved draft")
                self.assertEqual(form.locator('[name="base_hash"]').input_value(), old_hash)

    def test_confirmed_write_read_failure_is_visible_without_write_retry(self):
        for engine in self.browsers:
            with self.subTest(engine=engine), self.scenario(engine) as (fixture, page):
                form = self.editor(fixture, page)
                form.locator('[name="f.feed_label"]').fill("Saved despite read failure")
                form.locator('[name="dry"]').uncheck()
                attempts = []
                page.on("request", lambda r: attempts.append(r.url) if r.method == "POST" else None)
                page.route("**/config?edit=research", lambda route: route.abort())
                self.assertTrue(self.submit(page, form)["ok"])
                self.expect(page.locator("#action-result")).to_contain_text("写入已确认")
                self.expect(page.locator("#action-result")).to_be_in_viewport()
                self.assertEqual(self.source_label(fixture), "Saved despite read failure")
                self.assertEqual(len(attempts), 1)

    def test_read_reconciliation_preserves_dirty_controls_inside_operations(self):
        for engine in self.browsers:
            with self.subTest(engine=engine), self.scenario(engine) as (fixture, page):
                page.goto(fixture.url + "/ops")
                notify = page.locator('#source-research form[action="/api/actions/notify"]')
                notify.locator('[name="bypass_clock"]').check()
                old_hash = notify.locator('[name="base_hash"]').input_value()
                collect = page.locator('#source-reading form[action="/api/actions/collect"]')
                self.assertTrue(self.submit(page, collect, "只采集一次")["ok"])
                self.expect(notify.locator('[name="bypass_clock"]')).to_be_checked()
                self.assertEqual(notify.locator('[name="base_hash"]').input_value(), old_hash)
                self.expect(page.locator("#action-result")).to_contain_text("未保存")

    def test_delete_pending_blocks_escape_cancel_and_duplicate_post(self):
        for engine in self.browsers:
            with self.subTest(engine=engine), self.scenario(engine) as (fixture, page):
                page.goto(fixture.url + "/config")
                held = []
                def hold(route):
                    held.append((route, route.fetch()))
                    page.evaluate("document.body.dataset.readHeld = '1'")
                page.route("**/api/actions/source-remove", hold)
                page.locator('[data-source="reading"]').get_by_role("link", name="删除", exact=True).click()
                dialog = page.get_by_role("dialog")
                dialog.get_by_role("button", name="确认删除", exact=True).click()
                self.expect(page.locator("body")).to_have_attribute("data-read-held", "1")
                self.expect(dialog.get_by_role("button", name="确认删除", exact=True)).to_be_disabled()
                page.keyboard.press("Escape")
                dialog.get_by_role("button", name="取消", exact=True).click()
                self.expect(dialog).to_be_visible()
                dialog.locator("form").evaluate("form => form.dispatchEvent(new Event('submit', {bubbles:true, cancelable:true}))")
                self.assertEqual(len(held), 1)
                held[0][0].fulfill(response=held[0][1])
                self.expect(dialog).not_to_be_visible()
                self.expect(page.locator('[data-source="reading"]')).to_have_count(0)

    def test_dirty_during_read_is_preserved_and_active_content_is_rejected(self):
        for engine in self.browsers:
            with self.subTest(engine=engine), self.scenario(engine) as (fixture, page):
                page.goto(fixture.url + "/ops")
                held = []
                def hold(route):
                    held.append((route, route.fetch()))
                    page.evaluate("document.body.dataset.readHeld = '1'")
                page.route("**/ops", hold)
                collect = page.locator('#source-reading form[action="/api/actions/collect"]')
                collect.get_by_role("button", name="只采集一次", exact=True).click()
                notify = page.locator('#source-research form[action="/api/actions/notify"]')
                self.expect(collect).to_have_attribute("aria-busy", "true")
                self.expect(page.locator("body")).to_have_attribute("data-read-held", "1")
                notify.locator('[name="bypass_clock"]').check()
                self.assertEqual(len(held), 1)
                held[0][0].fulfill(response=held[0][1])
                self.expect(collect).not_to_have_attribute("aria-busy", "true")
                self.expect(notify.locator('[name="bypass_clock"]')).to_be_checked()
                page.unroute("**/ops")
                page.goto(fixture.url + "/config")
                def inject(route):
                    response = route.fetch()
                    text = response.text().replace('data-read-region="sources">',
                        'data-read-region="sources" onclick="window.injected=true">')
                    route.fulfill(response=response, body=text)
                page.route("**/config", inject)
                row = page.locator('[data-source="reading"]')
                row.get_by_role("button", name="启用", exact=True).click()
                self.expect(page.locator("#action-result")).to_contain_text("刷新失败")
                self.assertFalse(page.evaluate("Boolean(window.injected)"))
                self.assertTrue(config.load(fixture.news_dir).sources["reading"].enabled)

    def test_confirmed_legacy_flush_keeps_contextual_feedback_after_read_replacement(self):
        for engine in self.browsers:
            with self.subTest(engine=engine), self.scenario(engine) as (fixture, page):
                added = cli.run_subcommand(["source", "set", "legacy", "--from-json",
                    '{"adapter":"hn","fetch":{"trigger":"manual"}}', "--json"], fixture.news_dir)
                self.assertTrue(added.ok, added.as_dict())
                fixture.store.append_pending("legacy", [{"ext_id": "legacy-test", "title": "Offline pending item",
                    "url": "https://example.invalid/legacy", "source": "Preview"}])
                page.goto(fixture.url + "/ops")
                page.get_by_text("兼容操作 · 旧式跑一轮", exact=True).click()
                page.get_by_text("顺延队列明细 · 点「现在发」立刻补跑", exact=True).click()
                page.on("dialog", lambda dialog: dialog.accept())
                attempts = []
                def confirm_without_send(route):
                    attempts.append(route.request.url)
                    route.fulfill(content_type="application/json", body=json.dumps({"ok": True,
                        "changed": True, "data": {}, "warnings": []}))
                page.route("**/api/actions/flush", confirm_without_send)
                form = page.locator('[data-read-region="legacy-pending"] form[action="/api/actions/flush"]')
                self.assertTrue(self.submit(page, form, "现在发")["ok"])
                self.expect(page.locator("#action-result")).to_contain_text("已应用")
                self.expect(page.locator("#action-result")).to_be_in_viewport()
                self.assertEqual(len(attempts), 1)

    def test_first_policy_creation_removes_stale_empty_state(self):
        for engine in self.browsers:
            with self.subTest(engine=engine), self.scenario(engine) as (fixture, page):
                for name in ("research", "products", "reading"):
                    self.assertTrue(cli.run_subcommand(["source", "remove", name, "--json"], fixture.news_dir).ok)
                for name in ("digest", "live", "archive"):
                    self.assertTrue(cli.run_subcommand(["policy", "remove", name, "--json"], fixture.news_dir).ok)
                page.goto(fixture.url + "/notifications?new=1#policy-editor")
                empty = page.get_by_text("还没有模板。选择下面的起点，保存后才能绑定消息源。", exact=True)
                self.expect(empty).to_be_visible()
                form = page.locator('form[action="/api/actions/policy-save"]')
                form.locator('[name="name"]').fill("first_policy")
                form.locator('[name="p.mode"]').select_option("collect")
                form.locator('[name="dry"]').uncheck()
                self.assertTrue(self.submit(page, form)["ok"])
                self.expect(page.locator('[data-policy="first_policy"]')).to_be_visible()
                self.expect(empty).to_have_count(0)

    def test_custom_budget_blank_is_rejected_until_explicit_inheritance(self):
        for engine in self.browsers:
            for javascript in (True, False):
                with self.subTest(engine=engine, javascript=javascript), self.scenario(
                        engine, javascript=javascript) as (fixture, page):
                    form = self.editor(fixture, page)
                    form.get_by_text("自定义覆盖 · 每个字段可独立继承", exact=True).click()
                    choice = form.locator('[name="inherit.max_cards_per_day"]')
                    choice.select_option("custom")
                    field = form.locator('[name="n.max_cards_per_day"]')
                    field.fill("0")
                    form.locator('[name="dry"]').uncheck()
                    def save():
                        if javascript:
                            return self.submit(page, form)
                        with page.expect_navigation():
                            form.get_by_role("button", name="验证 / 保存", exact=True).click()
                        return None
                    saved = save()
                    if javascript:
                        self.assertTrue(saved["ok"], saved)
                    self.assertEqual(config.load(fixture.news_dir).sources["research"].notify.overrides["max_cards_per_day"], 0)
                    if not javascript:
                        form = self.editor(fixture, page)
                        form.get_by_text("自定义覆盖 · 每个字段可独立继承", exact=True).click()
                        form.locator('[name="dry"]').uncheck()
                    field = form.locator('[name="n.max_cards_per_day"]')
                    field.fill("")
                    before = (fixture.news_dir / "sources.yaml").read_bytes()
                    failed = save()
                    if javascript:
                        self.assertFalse(failed["ok"], failed)
                    else:
                        form = page.locator('form[action="/api/actions/source-patch"]')
                    self.expect(form.locator('[name="n.max_cards_per_day"]')).to_have_value("")
                    self.expect(form.locator('[name="inherit.max_cards_per_day"]')).to_have_value("custom")
                    self.assertEqual((fixture.news_dir / "sources.yaml").read_bytes(), before)
                    form.locator('[name="inherit.max_cards_per_day"]').select_option("inherit")
                    saved = save()
                    if javascript:
                        self.assertTrue(saved["ok"], saved)
                    self.assertNotIn("max_cards_per_day", config.load(fixture.news_dir).sources["research"].notify.overrides)
                    preview = cli.run_subcommand(["notification", "preview", "--source", "research", "--json"], fixture.news_dir)
                    self.assertEqual(preview.data["sources"][0]["after"]["values"]["max_cards_per_day"], 12)

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

    def test_source_numeric_clear_preview_save_and_reload_restore_defaults(self):
        for engine in self.browsers:
            with self.subTest(engine=engine), self.scenario(engine) as (fixture, page):
                seed = cli.run_subcommand(["source", "patch", "research", "--from-json",
                    '{"filter":{"max_items":5},"fetch":{"interval_min":30},"enrich":{"max_body_chars":900}}', "--json"], fixture.news_dir)
                self.assertTrue(seed.ok, seed.as_dict())
                form = self.editor(fixture, page)
                field = form.locator('[name="f.filter.max_items"]')
                field.locator("xpath=ancestor::details[1]").locator(":scope > summary").click()
                field.fill("")
                form.locator('[name="f.fetch.interval_min"]').fill("")
                before = (fixture.news_dir / "sources.yaml").read_bytes()
                self.assertTrue(self.submit(page, form, "预览变化")["ok"])
                self.assertEqual((fixture.news_dir / "sources.yaml").read_bytes(), before)
                self.assertTrue(self.submit(page, form)["ok"])
                self.assertEqual((fixture.news_dir / "sources.yaml").read_bytes(), before)
                form.locator('[name="dry"]').uncheck()
                saved = self.submit(page, form)
                self.assertTrue(saved["ok"], saved)
                values = config.source_values(config.load(fixture.news_dir).sources["research"])
                self.assertEqual(values["filter.max_items"], 13)
                self.assertEqual(values["fetch.interval_min"], 0.0)
                self.assertEqual(values["enrich.max_body_chars"], 900)
                page.reload()
                self.expect(form.locator('[name="f.filter.max_items"]')).to_have_value("13")
                self.expect(form.locator('[name="f.fetch.interval_min"]')).to_have_value("0.0")

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
                form.locator('[name="f.fetch.interval_min"]').fill("")
                maximum = form.locator('[name="f.filter.max_items"]')
                if not maximum.is_visible():
                    maximum.locator("xpath=ancestor::details[1]").locator(":scope > summary").click()
                maximum.fill("")
                form.locator('[name="dry"]').uncheck()
                with page.expect_navigation():
                    form.get_by_role("button", name="验证 / 保存").click()
                self.assertEqual(self.source_label(fixture), 'Source <draft> "value"')
                values = config.source_values(config.load(fixture.news_dir).sources["research"])
                self.assertEqual(values["fetch.interval_min"], 0.0)
                self.assertEqual(values["filter.max_items"], 13)
                page.goto(fixture.url + "/ops")
                before = fixture.store.pending_count("research")
                with page.expect_navigation():
                    page.locator('form[action="/api/actions/collect"]').filter(
                        has=page.locator('[name="name"][value="research"]')).get_by_role("button").click()
                self.assertIn("/ops?draft=", page.url)
                self.assertEqual(fixture.store.pending_count("research"), before + 1)
                page.reload()
                self.assertEqual(fixture.store.pending_count("research"), before + 1, "PRG refresh must not replay collection")

    def test_no_javascript_collapsed_invalid_field_reaches_server_validation(self):
        for engine in self.browsers:
            with self.subTest(engine=engine), self.scenario(engine, javascript=False) as (fixture, page):
                form = self.editor(fixture, page)
                field = form.locator('[name="f.filter.max_items"]')
                section = field.locator("xpath=ancestor::details[1]")
                section.locator(":scope > summary").click()
                field.fill("0")
                section.locator(":scope > summary").click()
                before = (fixture.news_dir / "sources.yaml").read_bytes()
                with page.expect_navigation():
                    form.get_by_role("button", name="验证 / 保存").click()
                self.assertIn("/config?draft=", page.url)
                field = page.locator('form[action="/api/actions/source-patch"] [name="f.filter.max_items"]')
                self.expect(field).to_be_visible()
                self.expect(field).to_have_value("0")
                self.expect(page.locator(".banner.err")).to_contain_text("filter")
                self.assertEqual((fixture.news_dir / "sources.yaml").read_bytes(), before)
                page.goto(fixture.url + "/config")
                attempts = []
                page.on("request", lambda request: attempts.append(request.url) if request.method == "POST" else None)
                page.locator('[data-source]').first.get_by_role("link", name="删除", exact=True).click()
                self.assertEqual(attempts, [], "Confirmation GET must not delete")
                self.assertIn("research", config.load(fixture.news_dir).sources)

    def test_action_geometry_is_consistent_on_desktop_and_mobile(self):
        for engine in self.browsers:
            for width in (360, 768, 1440):
                with self.subTest(engine=engine, width=width), self.scenario(engine, width=width) as (fixture, page):
                    for route in ("/config", "/notifications", "/ops"):
                        page.goto(fixture.url + route)
                        heights = page.locator('.btn:visible').evaluate_all('els => els.map(el => el.getBoundingClientRect().height)')
                        self.assertTrue(all(h >= (44 if width == 360 else 36) for h in heights), (route, heights))
                        self.assertLess(max(heights) - min(heights), 1, (route, heights))

    def test_delete_dialog_cancel_escape_blocked_and_stale_confirmation(self):
        for engine in self.browsers:
            with self.subTest(engine=engine), self.scenario(engine) as (fixture, page):
                page.goto(fixture.url + "/config")
                row = page.locator('[data-source="reading"]')
                self.assertEqual(row.locator('[name="confirm_delete"]').count(), 0)
                trigger = row.get_by_role("link", name="删除", exact=True)
                posts = []
                page.on("request", lambda request: posts.append(request.url) if request.method == "POST" else None)
                trigger.click()
                dialog = page.get_by_role("dialog")
                self.expect(dialog).to_be_visible()
                self.expect(dialog).to_contain_text("个人阅读清单")
                self.expect(dialog).to_contain_text("待处理内容和历史会保留")
                self.expect(dialog.get_by_role("button", name="取消", exact=True)).to_be_focused()
                page.keyboard.press("Escape")
                self.expect(dialog).not_to_be_visible()
                self.expect(trigger).to_be_focused()
                trigger.click()
                dialog.get_by_role("button", name="取消", exact=True).click()
                self.assertEqual(posts, [])
                trigger.click()
                out = cli.run_subcommand(["source", "enable", "reading", "--json"], fixture.news_dir)
                self.assertTrue(out.ok, out.as_dict())
                with page.expect_response(lambda r: "/api/actions/source-remove" in r.url) as response:
                    dialog.get_by_role("button", name="确认删除", exact=True).click()
                self.assertEqual(response.value.json()["error"]["code"], "E_CONFLICT")
                self.expect(dialog).to_be_visible()
                self.expect(dialog.locator('[data-delete-feedback]')).to_contain_text("E_CONFLICT")
                dialog.get_by_role("button", name="取消", exact=True).click()
                page.goto(fixture.url + "/notifications")
                page.locator('[data-policy="digest"]').get_by_role("link", name="删除", exact=True).click()
                self.expect(dialog).to_contain_text("research")
                self.expect(dialog.get_by_role("button", name="确认删除", exact=True)).to_be_disabled()

    def test_actual_source_edit_and_copy_links_land_at_the_editor(self):
        for engine in self.browsers:
            for javascript in (True, False):
                with self.subTest(engine=engine, javascript=javascript), self.scenario(engine, javascript=javascript) as (fixture, page):
                    for label, selector in (("编辑", "#new > summary"), ("复制", "#source-copy")):
                        page.goto(fixture.url + "/config")
                        page.locator('[data-source="research"]').get_by_role("link", name=label, exact=True).click()
                        self.expect(page.locator(selector)).to_be_in_viewport()

    def test_confirmed_source_and_policy_deletion_work_with_and_without_javascript(self):
        for engine in self.browsers:
            for javascript in (True, False):
                with self.subTest(engine=engine, javascript=javascript), self.scenario(
                        engine, javascript=javascript) as (fixture, page):
                    page.on("dialog", lambda dialog: dialog.accept())
                    attempts = []
                    page.on("request", lambda request: attempts.append(request.url) if request.method == "POST" else None)
                    for action, name, route in (("source-remove", "reading", "/config"),
                                                ("policy-remove", "archive", "/notifications")):
                        page.goto(fixture.url + route)
                        before = len(attempts)
                        card = page.locator(f'[data-{"source" if action == "source-remove" else "policy"}="{name}"]')
                        card.get_by_role("link", name="删除", exact=True).click()
                        self.assertEqual(len(attempts), before, "Opening confirmation must not submit")
                        form = page.locator(f'form[action="/api/actions/{action}"]')
                        if javascript:
                            self.assertTrue(self.submit(page, form, "确认删除")["ok"])
                            self.expect(card).to_have_count(0)
                            self.expect(page.locator("#action-result")).to_be_in_viewport()
                        else:
                            with page.expect_navigation():
                                form.get_by_role("button", name="确认删除", exact=True).click()
                            self.assertIn(route + "?draft=", page.url)
                        self.assertEqual(len(attempts), before + 1)
                        cfg = config.load(fixture.news_dir)
                        self.assertNotIn(name, cfg.sources if action == "source-remove" else cfg.policies)
                        if not javascript:
                            page.reload()
                            self.assertEqual(len(attempts), before + 1, "PRG refresh must not repeat deletion")


if __name__ == "__main__":
    unittest.main()
