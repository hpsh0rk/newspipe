from __future__ import annotations
import sys
import json
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class WebDraftTests(unittest.TestCase):
    def test_inactive_adapter_snapshot_restores_raw_selected_controls(self):
        from newspipe.webdrafts import restore
        raw = '\n"ACME, Inc." & </textarea>\nNext'
        page = '<form action="/api/actions/source-save"><input name="adapter_rendered" value="google_news"><textarea name="f.settings.queries">normalized</textarea></form>'
        values = {"adapter_rendered": ["rsshub"], "f.adapter": ["google_news"],
                  "adapter_draft": [json.dumps({"google_news": {"settings.queries": [raw]}})]}
        out = restore(page, "source-save", values)
        self.assertIn('\n\n"ACME, Inc." &amp; &lt;/textarea&gt;\nNext</textarea>', out)
        self.assertIn('name="adapter_rendered" value="google_news"', out)
        self.assertNotIn('>normalized</textarea>', out)

    def test_multiline_raw_draft_restores_matching_textarea_without_active_markup(self):
        from newspipe.webdrafts import restore
        raw = '\n"ACME, Inc." & </textarea><script>bad()</script>\nNext'
        page = '<form action="/api/actions/source-save"><textarea name="f.settings.queries">old &amp; text</textarea></form>'
        page += '<form action="/api/actions/global-save"><textarea name="f.settings.queries">untouched &amp; text</textarea></form>'
        out = restore(page, "source-save", {"f.settings.queries": [raw]})
        self.assertIn('\n"ACME, Inc." &amp; &lt;/textarea&gt;&lt;script&gt;bad()&lt;/script&gt;\nNext', out)
        self.assertNotIn('<script>', out)
        self.assertIn('>untouched &amp; text</textarea>', out)
        self.assertNotIn('>old &amp; text</textarea>', out)

    def test_raw_controls_restore_only_matching_form_and_not_security_token(self):
        from newspipe.webdrafts import restore
        page = '<form action="/api/actions/policy-save"><input name="token" value="fresh"><input name="name" value="old"><input name="p.label" value="old"><input type="checkbox" name="dry" value="1" checked><select name="p.mode"><option value="digest" selected>D</option><option value="immediate">I</option></select></form><form action="/api/actions/global-save"><input name="p.label" value="untouched"></form>'
        out = restore(page, "policy-save", {"token": ["old"], "name": ["new"], "p.label": ['<x>"'], "p.mode": ["immediate"]})
        self.assertIn('name="token" value="fresh"', out)
        self.assertIn('value="&lt;x&gt;&quot;"', out)
        self.assertNotIn('name="dry" value="1" checked', out)
        self.assertIn('value="immediate" selected', out)
        self.assertIn('value="untouched"', out)

    def test_cache_expires_caps_and_consumes_once(self):
        from newspipe.webdrafts import Drafts
        cache = Drafts(cap=2, ttl=10)
        first = cache.put({"value": 1}, now=0)
        second = cache.put({"value": 2}, now=1)
        cache.put({"value": 3}, now=2)
        self.assertIsNone(cache.pop(first, now=3))
        self.assertEqual(cache.pop(second, now=3), {"value": 2})
        self.assertIsNone(cache.pop(second, now=3))
        expired = cache.put({}, now=4)
        self.assertIsNone(cache.pop(expired, now=15))

    def test_failed_editor_reveals_nested_controls_without_opening_sibling_forms(self):
        from newspipe.webdrafts import restore
        page = '<form action="/api/actions/source-patch"><details><summary>Filter</summary><details><summary>Advanced</summary><input name="f.filter.max_items" value="13"></details></details></form><form action="/api/actions/policy-save"><details><summary>Other</summary></details></form>'
        out = restore(page, "source-patch", {"f.filter.max_items": ["0"]}, reveal=True)
        self.assertEqual(out.count("<details open>"), 2)
        self.assertIn('<input name="f.filter.max_items" value="0">', out)
        self.assertIn('<details><summary>Other</summary>', out)
