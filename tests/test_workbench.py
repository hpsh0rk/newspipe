"""SSR/progressive enhancement is local, accessible and retains the CLI boundary."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from newspipe import config, view
from test_notification_contracts import TEXT

SCRIPT = '<script src="/assets/workbench.js" defer></script>'


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.news = Path(self.tmp.name)
        (self.news / "sources.yaml").write_text(TEXT)
        self.payload = view.build(self.news)
        self.actions = {"token": "fake-token", "sources_hash": self.payload["sources_hash"]}

    def test_builder_exposes_effective_rules_origins_and_current_source_values(self):
        self.assertIn("notification", self.payload)
        self.assertIn("source_values", self.payload)
        row = self.payload["notification"]["sources"][0]
        self.assertEqual(row["after"]["policy_id"], "p")
        self.assertEqual(row["after"]["origins"]["quiet_hours"], "source:src")

    def test_all_pages_share_five_routes_and_one_local_asset(self):
        page = view.cockpit_html(self.payload)
        for route in ("/", "/config", "/notifications", "/items", "/ops"):
            self.assertIn(f'href="{route}"', page)
        self.assertEqual(page.count(SCRIPT), 1)
        self.assertNotIn("<script", page.replace(SCRIPT, ""))
        self.assertIn('aria-live="polite"', page)

    def test_sources_editor_uses_patch_and_explicit_inherit_controls(self):
        page = view.config_html(self.payload, schema=config.describe_schema(self.news), actions=self.actions,
                                page={"edit": "src", "values": config.source_values(config.load(self.news).sources["src"])})
        self.assertIn('action="/api/actions/source-patch"', page)
        self.assertIn('name="notify_policy"', page)
        self.assertIn('name="inherit.quiet_hours"', page)
        self.assertIn('value="custom" selected', page)
        self.assertIn('name="return_to" value="/config"', page)
        self.assertIn("未提交字段保持原样", page)

    def test_sources_search_and_policy_filter_work_without_javascript(self):
        page = view.config_html(self.payload, schema=config.describe_schema(self.news), actions=self.actions,
                                page={"search": "src", "policy_filter": "p"})
        self.assertIn('data-source="src"', page)
        self.assertNotIn('data-source="other"', page)

    def test_notifications_page_separates_hard_globals_and_editable_templates(self):
        renderer = getattr(view, "notifications_html", None)
        self.assertIsNotNone(renderer, "new notification route must have a focused renderer")
        page = renderer(self.payload, schema=config.describe_schema(self.news), actions=self.actions, page={"edit": "p"})
        self.assertIn('action="/api/actions/global-save"', page)
        self.assertIn('action="/api/actions/policy-save"', page)
        self.assertIn("全局硬限制", page)
        self.assertIn("更新已有卡片也计一次", page)
        self.assertIn("Asia/Shanghai", page)
        self.assertIn("已停用", page)

    def test_read_only_notifications_hide_all_write_forms(self):
        renderer = getattr(view, "notifications_html", None)
        self.assertIsNotNone(renderer)
        page = renderer(self.payload, schema=config.describe_schema(self.news))
        self.assertNotIn('action="/api/actions/', page)
        self.assertIn("写操作未开启", page)

    def test_empty_sources_have_actionable_setup_and_do_not_require_placeholder(self):
        (self.news / "sources.yaml").write_text("chat: oc_test\nslots: {}\nsources: {}\npolicies: {}\n")
        page = view.config_html(view.build(self.news), schema=config.describe_schema(self.news), actions=self.actions)
        self.assertIn("添加消息源", page)
        self.assertIn("先创建通知策略", page)

    def test_local_asset_has_safe_feedback_and_no_persistent_drafts(self):
        from newspipe import webassets
        js = webassets.JAVASCRIPT
        self.assertIn("textContent", js)
        self.assertIn("application/json", js)
        self.assertIn("beforeunload", js)
        self.assertNotIn("innerHTML", js)
        self.assertNotIn("localStorage", js)
        self.assertNotIn("sessionStorage", js)
        self.assertNotIn("setInterval", js)

    def test_forms_protect_new_ids_and_no_script_deletes(self):
        page = view.config_html(self.payload, schema=config.describe_schema(), actions=self.actions, page={"new": "1"})
        self.assertIn("信源清单", page)
        self.assertIn('name="create_only" value="1"', page)
        self.assertIn('name="confirm_delete" value="1" required', page)
        policy_page = view.notifications_html(self.payload, schema=config.describe_schema(), actions=self.actions, page={"copy": "p"})
        self.assertIn('name="create_only" value="1"', policy_page)
        self.assertIn('name="preview_kind" value="global"', policy_page)

    def test_ops_has_separate_collection_notification_and_no_force_resend(self):
        from newspipe import state
        state.Store(self.news).write_batch({"source": "src", "digest": "2026-11-11", "slot": "am",
                                           "delivery_pending": True, "delivery_id": "op", "items": [], "notification_attempts": 3})
        self.payload = view.build(self.news)
        page = view.ops_html(self.payload, actions=self.actions)
        self.assertIn('/api/actions/collect', page)
        self.assertIn('/api/actions/notify', page)
        self.assertIn("不抓取", page)
        self.assertIn("原操作", page)
        self.assertNotIn("强制重发", page.replace("禁止强制重发", ""))

    def test_asset_uses_configured_read_path_and_refreshes_template_summaries(self):
        self.payload["service"]["view_path"] = "/read-contract"
        page = view.cockpit_html(self.payload)
        self.assertIn('data-view-path="/read-contract"', page)
        from newspipe.webassets import JAVASCRIPT
        self.assertNotIn("fetch('/view'", JAVASCRIPT)
        self.assertIn("data-policy-summary", JAVASCRIPT)
