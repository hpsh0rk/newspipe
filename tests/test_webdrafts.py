from __future__ import annotations
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class WebDraftTests(unittest.TestCase):
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
