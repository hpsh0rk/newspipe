"""Deletion confirmation is a read-only page until the explicit guarded POST."""
import sys
import unittest
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from newspipe import config
from preview_workbench import offline_workbench


class FormValues(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values, self.action, self.active = {}, "", False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.active = attrs.get("method") == "post"
            if self.active:
                self.action = attrs["action"]
        if tag == "input" and self.active and attrs.get("name"):
            self.values[attrs["name"]] = attrs.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form":
            self.active = False


class DeleteConfirmationTests(unittest.TestCase):
    def test_confirmation_get_and_post_refresh_never_replay_deletion(self):
        with offline_workbench() as fixture:
            url = fixture.url + "/confirm-delete?kind=source&name=reading"
            before = (fixture.news_dir / "sources.yaml").read_bytes()
            with urllib.request.urlopen(url) as response:
                text = response.read().decode()
            self.assertIn("个人阅读清单", text)
            self.assertIn("待处理内容和历史会保留", text)
            self.assertEqual((fixture.news_dir / "sources.yaml").read_bytes(), before)
            parsed = FormValues()
            parsed.feed(text)
            self.assertEqual(parsed.action, "/api/actions/source-remove")
            self.assertEqual(parsed.values["confirm_delete"], "1")
            request = urllib.request.Request(fixture.url + parsed.action,
                data=urllib.parse.urlencode(parsed.values).encode(), headers={"Origin": fixture.url})
            with urllib.request.urlopen(request) as response:
                final_url = response.url
            self.assertNotIn("reading", config.load(fixture.news_dir).sources)
            after = (fixture.news_dir / "sources.yaml").read_bytes()
            urllib.request.urlopen(final_url).close()
            self.assertEqual((fixture.news_dir / "sources.yaml").read_bytes(), after)
            self.assertEqual(fixture.blocked_effects, [])

    def test_referenced_policy_and_invalid_targets_cannot_render_confirmation_post(self):
        with offline_workbench() as fixture:
            with urllib.request.urlopen(fixture.url + "/confirm-delete?kind=policy&name=digest") as response:
                text = response.read().decode()
            self.assertIn("research", text)
            self.assertIn("先重新绑定", text)
            self.assertNotIn('method="post"', text)
            for query in ("kind=other&name=reading", "kind=source&name=missing", "kind=source&name=../escape"):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(fixture.url + "/confirm-delete?" + query)
                self.assertEqual(caught.exception.code, 404)
            self.assertEqual(fixture.blocked_effects, [])
