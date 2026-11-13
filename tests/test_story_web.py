"""Compact preview and settings follow the existing read/write and PRG boundaries."""
from html.parser import HTMLParser
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse
import urllib.request

from newspipe import cli, config, edit, inbound, llm, state, view
from newspipe.observations import prepare_observation
from test_actions import NoRedirect
from test_observations import article
from test_story_contracts import seed, SOURCES, NOW


class StoryWebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.news = Path(self.tmp.name)
        (self.news / "sources.yaml").write_text(SOURCES, encoding="utf-8")
        self.store = state.Store(self.news)
        self.addCleanup(patch.stopall)
        for target in ("newspipe.pipeline._adapter", "newspipe.channel.send_card", "newspipe.credentials.resolve_feishu"):
            patch(target, side_effect=AssertionError("web attempted external work")).start()

    def files(self):
        return {str(p.relative_to(self.news)): p.read_bytes() for p in self.news.rglob("*") if p.is_file()}

    def serve(self, actions=True):
        server, _ = inbound.start_http(service_cfg={"http": {"host": "127.0.0.1", "port": 0}},
            creds=None, news_dir=self.news, accept_events=False, view_actions=actions,
            action_token="test-token", logger=lambda *_: None)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def get(self, base, path):
        with urllib.request.urlopen(base + path, timeout=5) as response:
            return response.read().decode()

    def post(self, base, *, values=None, token="test-token", origin=None, json_accept=True):
        fields = {"token": token, "base_hash": edit.file_hash(self.news / "sources.yaml"),
                  "return_to": "/items", "s.mode": "observe"}
        fields.update(values or {})
        request = urllib.request.Request(base + "/api/actions/story-settings",
            data=urllib.parse.urlencode(fields).encode(), headers={"Origin": origin or base,
            "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json" if json_accept else "text/html"})
        return urllib.request.build_opener(NoRedirect).open(request, timeout=5)

    def test_items_entry_and_preview_detail_reads_preserve_bytes(self):
        drafts = seed(self.store)
        base = self.serve()
        before = self.files()
        with patch("newspipe.story_matching.propose_relation", side_effect=AssertionError("read compared")):
            listing = self.get(base, "/items")
            self.assertIn('href="/items?story_preview=1"', listing)
            preview = self.get(base, "/items?story_preview=1")
            self.assertIn("提案", preview)
            self.assertIn("未分类变化", preview)
            self.assertNotIn("自动减少通知", preview)
            self.assertIn(drafts[0].observation_id, preview)
            detail = self.get(base, "/items?observation=" + drafts[0].observation_id)
            self.assertIn("原始版本", detail)
            payload = json.loads(self.get(base, "/view"))
            self.assertEqual(payload["stories"]["observation_count"], 3)
            self.assertNotIn("observations", payload["stories"])
        self.assertEqual(self.files(), before)

    def test_actions_disabled_hides_and_rejects_settings(self):
        base = self.serve(False)
        disabled = self.get(base, "/items?story_settings=1")
        self.assertNotIn('action="/api/actions/story-settings"', disabled)
        self.assertNotIn("test-token", disabled)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post(base)
        self.assertEqual(caught.exception.code, 404)

    def test_settings_modal_uses_schema_and_collapsed_advanced_bounds(self):
        base = self.serve()
        before = self.files()
        page = self.get(base, "/items?story_settings=1")
        self.assertIn('data-editor-panel="story-settings"', page)
        self.assertIn('id="editor-dialog"', page)
        self.assertIn('name="dry" value="1" checked', page)
        for f in config.describe_schema()["story_fields"]:
            self.assertIn('name="s.' + f["path"] + '"', page)
        self.assertIn('<details><summary>检索范围', page)
        self.assertEqual(self.files(), before)

    def test_origin_token_and_hash_fail_before_save(self):
        base = self.serve()
        before = (self.news / "sources.yaml").read_bytes()
        for token, origin in (("wrong", base), ("test-token", "https://evil.example")):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.post(base, token=token, origin=origin)
            self.assertEqual(caught.exception.code, 403)
        with self.post(base, values={"base_hash": "stale"}) as response:
            out = json.load(response)
        self.assertEqual(out["error"]["code"], "E_CONFLICT")
        with self.post(base, values={"base_hash": ""}) as response:
            self.assertEqual(json.load(response)["error"]["code"], "E_USAGE")
        self.assertEqual((self.news / "sources.yaml").read_bytes(), before)
        self.assertEqual(self.store.story_status()["availability"], "not_initialized")

    def test_story_form_participates_in_safe_editor_enhancement(self):
        from newspipe.webassets import JAVASCRIPT
        self.assertIn("'story-settings'", JAVASCRIPT)
        self.assertIn("/^[fpngrs]\\./", JAVASCRIPT)

    @patch("newspipe.webdrafts.secrets.token_urlsafe", return_value="opaque37token")
    def test_no_js_invalid_and_dry_run_retain_settings_draft_without_activation(self, _token):
        base = self.serve()
        before_config = (self.news / "sources.yaml").read_bytes()
        for number, dry in (("0", "1"), ("37", "1")):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.post(base, values={"s.window_days": number, "dry": dry}, json_accept=False)
            self.assertEqual(caught.exception.code, 303)
            location = caught.exception.headers["Location"]
            redirect = urllib.parse.urlsplit(location)
            self.assertEqual(redirect.path, "/items")
            self.assertEqual(urllib.parse.parse_qs(redirect.query), {"draft": ["opaque37token"]})
            page = self.get(base, location)
            self.assertIn('action="/api/actions/story-settings"', page)
            self.assertIn('name="s.window_days"', page)
            parser = InputValues()
            parser.feed(page)
            self.assertEqual(parser.values["s.window_days"], number)
            self.assertIn("仅校验" if number == "37" else "失败", page)
        self.assertEqual((self.news / "sources.yaml").read_bytes(), before_config)
        self.assertEqual(self.store.story_status()["availability"], "not_initialized")

    def test_settings_can_be_saved_when_history_is_corrupt_without_repair(self):
        seed(self.store)
        db = self.store.root / "stories" / "history.sqlite3"
        db.write_bytes(b"corrupt")
        base = self.serve()
        page = self.get(base, "/items?story_settings=1")
        self.assertIn('action="/api/actions/story-settings"', page)
        self.assertIn("历史暂不可用", page)
        with self.post(base, values={"s.mode": "off"}) as response:
            self.assertTrue(json.load(response)["ok"])
        self.assertEqual(db.read_bytes(), b"corrupt")

    def test_missing_and_invalid_detail_never_create_history(self):
        base = self.serve()
        before = self.files()
        for path, label in (("/items?story_preview=1", "尚未记录"),
                            ("/items?observation=ob_missing", "没有这个原始版本"),
                            ("/items?story_preview=1&after=-1", "分页参数")):
            self.assertIn(label, self.get(base, path))
        self.assertEqual(self.files(), before)

    def test_oversized_cursor_is_a_visible_validation_error_not_a_broken_request(self):
        seed(self.store)
        base = self.serve()
        before = self.files()
        page = self.get(base, "/items?story_preview=1&after=9223372036854775808")
        self.assertIn("分页参数", page)
        self.assertEqual(self.files(), before)

    def test_original_evidence_is_escaped_and_secret_links_are_not_clickable(self):
        draft = prepare_observation("one", "rsshub", article(title='<script>alert("x")</script>',
            source_text='Authorization: PRIVATE\nhttps://user:PRIVATE@example.com/a?token=PRIVATE',
            url="https://user:PRIVATE@example.com/a?token=PRIVATE", headers={"Authorization": "PRIVATE"}))
        snapshot = self.store.observation_capture_snapshot([draft], at=NOW, window_days=14, candidate_limit=5, scan_limit=500)
        self.store.record_observations([draft], at=NOW, mode="observe", config_revision="r", current_revision="r",
            expected_activation_id=None, snapshot=snapshot, proposals=[], baselines={})
        base = self.serve()
        page = self.get(base, "/items?observation=" + draft.observation_id)
        self.assertNotIn('PRIVATE', page)
        self.assertNotIn('<script>alert', page)
        self.assertIn('&lt;script&gt;', page)
        self.assertIn("已脱敏", page)
        self.assertNotIn(str(self.store.root), page)

    def test_comparison_detail_shows_original_difference_uncertainty_and_cost_without_model(self):
        cli.run_subcommand(["stories", "set", "--from-json", '{"mode":"observe"}'], self.news)
        drafts = seed(self.store)
        out = cli.run_subcommand(["stories", "evaluate", "--observation", drafts[2].observation_id, "--limit", "1"], self.news)
        self.assertTrue(out.ok, out.as_dict())
        base = self.serve()
        before = self.files()
        with patch.object(llm, "chat_json", side_effect=AssertionError("render model")):
            page = self.get(base, "/items?observation=" + drafts[2].observation_id)
        self.assertIn("关联与差异", page)
        self.assertIn("不确定", page)
        self.assertIn("原文变化（未分类）", page)
        self.assertIn("对照原文", page)
        self.assertIn("当前原文", page)
        self.assertIn("模型用量", page)
        self.assertIn("预留估计", page)
        self.assertIn("供应商实测", page)
        self.assertIn("未知消耗", page)
        self.assertIn("缓存命中", page)
        self.assertNotIn('action="/api/actions/story-evaluate"', page)
        self.assertEqual(before, self.files())

    def test_comparison_evidence_is_escaped_and_stale_status_is_explicit(self):
        from newspipe.story_compare import evaluate
        cli.run_subcommand(["stories", "set", "--from-json", '{"mode":"observe"}'], self.news)
        drafts = seed(self.store)
        receipt = evaluate(self.store, config.load(self.news), drafts[2].observation_id, limit=1)
        receipt["job_id"] = "cmp_escaped_fixture"
        receipt["pairs"][0]["result"]["differences"][0]["current"]["text"] = '<script>alert("fixture")</script>'
        self.store.record_comparison(receipt)
        cli.run_subcommand(["stories", "set", "--from-json", '{"mode":"off"}'], self.news)
        page = self.get(self.serve(), "/items?observation=" + drafts[2].observation_id)
        self.assertIn("仅历史记录", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn('<script>alert("fixture")', page)


class InputValues(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and attrs.get("name"):
            self.values[attrs["name"]] = attrs.get("value", "")
