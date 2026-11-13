"""Real built-in adapters over synthetic transport, not live provider acceptance."""
from datetime import datetime, timezone
import json
import unittest
import urllib.error
from unittest.mock import patch

from newspipe import observations
from newspipe.adapters import hn, reddit, rsshub
from newspipe.source_contract import FetchResult, normalize_fetch
from test_feedparse import ATOM, rss

NOW = datetime(2026, 11, 13, 12, tzinfo=timezone.utc).timestamp()


def reddit_feed(*, content="Original post", published="2026-11-13T10:00:00Z", updated="2026-11-13T11:00:00Z"):
    return (f'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>t3_abc</id><title>Post</title>'
            f'<link rel="alternate" href="https://www.reddit.com/r/Test/comments/abc/post/"/>'
            f'<author><name>/u/Alice</name></author><published>{published}</published><updated>{updated}</updated>'
            f'<content type="html"><![CDATA[{content}]]></content></entry></feed>').encode()


def hit(**kw):
    return {"objectID": "123", "title": "A story", "url": "https://article.example.invalid/a",
            "points": 10, "num_comments": 5, "created_at_i": NOW - 60,
            "story_text": "Original discussion " * 40, **kw}


class SourceEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.sleep = patch("time.sleep")
        self.sleep.start()
        self.addCleanup(self.sleep.stop)
        self.clock = patch("time.time", return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def fetch(self, adapter, body, cfg=None, **kw):
        target = "newspipe.net.request" if adapter is rsshub else adapter.__name__ + ".request"
        with patch(target, return_value=body) as request:
            out = adapter.fetch(cfg or {"feeds": ["/rss"] if adapter is rsshub else ["Test"]}, **kw)
        self.assertIsInstance(out, FetchResult, "built-in adapters must report subscription outcomes")
        normalize_fetch(out, source="test", adapter=adapter.__name__.rsplit(".", 1)[-1])
        return out, request

    def test_each_adapter_reports_empty_success_and_bounded_transport(self):
        for adapter, body in ((rsshub, b'<rss><channel/></rss>'), (hn, b'{"hits": []}'),
                              (reddit, b'<feed xmlns="http://www.w3.org/2005/Atom"/>')):
            with self.subTest(adapter=adapter.__name__):
                out, request = self.fetch(adapter, body)
                self.assertEqual(out.status, "empty")
                self.assertTrue(out.reported)
                self.assertEqual(request.call_args.kwargs["max_bytes"], 2 * 1024 * 1024)
                self.assertEqual(request.call_args.kwargs["max_redirects"], 5)

    def test_rss_original_text_is_not_a_display_excerpt_or_full_article_claim(self):
        text = "Original facts & evidence " * 100
        out, request = self.fetch(rsshub, rss('<p>' + text + '</p>', description="Preview"))
        item, = out.items
        self.assertGreater(len(item["source_text"]), 1000)
        self.assertLessEqual(len(item["summary"]), 300)
        self.assertFalse(item["source_text_complete"])
        self.assertFalse(item["source_text_truncated"])
        self.assertEqual(item["content_kind"], "unknown")
        self.assertEqual(item["metadata"]["body_field"], "content:encoded")
        self.assertEqual(request.call_count, 1, "source links are not fetched")
        self.assertFalse(observations.prepare_observation("s", "rsshub", item).copy_hash)

    def test_supplied_authors_are_preserved_as_public_source_facts(self):
        for adapter, body in ((rsshub, rss().replace(b'</item>', b'<author>Alice</author></item>')),
                              (hn, json.dumps({"hits": [hit(author="Alice")]}).encode())):
            with self.subTest(adapter=adapter.__name__):
                out, _ = self.fetch(adapter, body)
                self.assertEqual(out.items[0]["facts"]["author"], "Alice")
                self.assertIn("Alice", observations.prepare_observation("s", "test", out.items[0]).evidence.facts_json)

    def test_rss_clipping_and_unknown_dates_are_explicit(self):
        out, _ = self.fetch(rsshub, rss("x" * 50000, date="not a date"))
        item, = out.items
        self.assertEqual(len(item["source_text"]), 20000)
        self.assertTrue(item["source_text_truncated"])
        self.assertIsNone(item["pub_ts"])
        self.assertIsNone(out.reports[0].watermark)

    def test_atom_recent_edit_keeps_old_publication_and_uses_updated_boundary(self):
        since = datetime(2026, 11, 12, tzinfo=timezone.utc).timestamp()
        out, _ = self.fetch(rsshub, ATOM, since=since)
        item, = out.items
        self.assertLess(item["pub_ts"], since)
        self.assertGreater(item["updated_ts"], since)
        self.assertEqual(out.reports[0].watermark, item["updated_ts"])
        empty, _ = self.fetch(rsshub, ATOM, since=item["updated_ts"])
        self.assertEqual(empty.status, "empty")

    def test_hn_respects_since_but_unknown_dates_stay_unknown(self):
        out, _ = self.fetch(hn, json.dumps({"hits": [hit(), hit(objectID="old", created_at_i=NOW - 120),
                                                   hit(objectID="unknown", created_at_i=None)]}).encode(), since=NOW - 90)
        self.assertEqual([i["ext_id"] for i in out.items], ["hn:123", "hn:unknown"])
        self.assertIsNone(out.items[1]["pub_ts"])

    def test_hn_retains_native_discussion_and_separates_linked_article_and_metrics(self):
        out, request = self.fetch(hn, json.dumps({"hits": [hit()]}).encode())
        item, = out.items
        self.assertEqual(item["original_url"], "https://news.ycombinator.com/item?id=123")
        self.assertEqual(item["url"], "https://article.example.invalid/a")
        self.assertEqual(item["content_kind"], "post")
        self.assertIn({"role": "article", "url": item["url"]}, item["referenced_urls"])
        self.assertGreater(len(item["source_text"]), 300)
        self.assertFalse(item["source_text_complete"])
        self.assertEqual(request.call_count, 1)
        updated, _ = self.fetch(hn, json.dumps({"hits": [hit(points=999, num_comments=500)]}).encode())
        self.assertEqual(observations.prepare_observation("s", "hn", item).version_hash,
                         observations.prepare_observation("s", "hn", updated.items[0]).version_hash)

    def test_hn_native_identity_cannot_inject_an_extra_permalink_parameter(self):
        out, _ = self.fetch(hn, json.dumps({"hits": [hit(objectID="a&b")]}).encode())
        self.assertEqual(out.items[0]["ext_id"], "hn:a&b")
        self.assertEqual(out.items[0]["original_url"], "https://news.ycombinator.com/item?id=a%26b")

    def test_reddit_recent_edit_preserves_post_identity_original_text_and_dates(self):
        out, request = self.fetch(reddit, reddit_feed(content="Original " * 200, published="2026-11-10T10:00:00Z"), since=NOW - 7200)
        item, = out.items
        self.assertEqual(item["ext_id"], "reddit:Test:abc")
        self.assertGreater(len(item["source_text"]), 600)
        self.assertEqual(len(item["summary"]), 600)
        self.assertEqual(item["content_kind"], "post")
        self.assertFalse(item["source_text_complete"])
        self.assertLess(item["pub_ts"], NOW - 7200)
        self.assertEqual(request.call_count, 1)

    def test_reddit_all_failed_is_not_quiet_and_partial_retains_healthy_posts(self):
        error = urllib.error.HTTPError("SECRET", 403, "SECRET", {}, None)
        def request(url, **_):
            if "/Bad/" in url:
                raise error
            return reddit_feed()
        with patch.object(reddit, "request", side_effect=request):
            partial = reddit.fetch({"feeds": ["Bad", "Test"]})
            failed = reddit.fetch({"feeds": ["Bad"]})
        self.assertIsInstance(partial, FetchResult)
        self.assertEqual(partial.status, "partial_failure")
        self.assertEqual(len(partial.items), 1)
        self.assertEqual(failed.status, "failure")
        self.assertEqual(failed.reports[0].code, "permission")
        self.assertNotIn("SECRET", json.dumps(partial.summary()) + json.dumps(failed.summary()))

    def test_rss_auth_failure_does_not_discard_a_healthy_subscription(self):
        def request(url, **_):
            if "/bad" in url:
                raise urllib.error.HTTPError("SECRET", 401, "SECRET", {}, None)
            return rss()
        with patch("newspipe.net.request", side_effect=request):
            out = rsshub.fetch({"feeds": ["/bad?token=SECRET", "/good"]})
        self.assertIsInstance(out, FetchResult)
        self.assertEqual(out.status, "partial_failure")
        self.assertEqual(out.reports[0].code, "credentials")
        self.assertEqual(len(out.items), 1)
        self.assertNotIn("SECRET", json.dumps(out.summary()) + json.dumps(out.items))

    def test_malformed_provider_data_and_rate_limits_are_categorical(self):
        for adapter, body in ((hn, b'{"hits": {"SECRET": 1}}'), (reddit, b'<html>SECRET</html>'), (rsshub, b'SECRET <broken')):
            with self.subTest(adapter=adapter.__name__):
                out, _ = self.fetch(adapter, body)
                self.assertEqual(out.status, "failure")
                self.assertEqual(out.reports[0].code, "invalid_response")
                self.assertNotIn("SECRET", json.dumps(out.summary()))
        for adapter in (hn, reddit, rsshub):
            target = "newspipe.net.request" if adapter is rsshub else adapter.__name__ + ".request"
            with patch(target, side_effect=urllib.error.HTTPError("SECRET", 429, "SECRET", {}, None)):
                out = adapter.fetch({"feeds": ["/rss"] if adapter is rsshub else ["Test"]})
            self.assertIsInstance(out, FetchResult)
            self.assertEqual(out.reports[0].code, "rate_limit")

    def test_invalid_reddit_subscription_is_rejected_without_url_injection(self):
        with patch.object(reddit, "request", return_value=reddit_feed()) as request:
            out = reddit.fetch({"feeds": ["../admin?token=SECRET", "Test"]})
        self.assertIsInstance(out, FetchResult)
        self.assertEqual(out.status, "partial_failure")
        self.assertEqual(request.call_count, 1)
        self.assertEqual(out.reports[0].code, "configuration")
        self.assertNotIn("SECRET", json.dumps(out.summary()))

    def test_empty_rss_configuration_and_excessive_subscriptions_fail_before_network(self):
        with patch("newspipe.net.request") as request:
            out = rsshub.fetch({"feeds": []})
        self.assertIsInstance(out, FetchResult)
        self.assertEqual(out.status, "failure")
        self.assertEqual(out.reports[0].code, "configuration")
        request.assert_not_called()
        for adapter in (rsshub, reddit):
            target = "newspipe.net.request" if adapter is rsshub else adapter.__name__ + ".request"
            with patch(target) as request:
                out = adapter.fetch({"feeds": ["/rss" if adapter is rsshub else "Test"] * 101})
            self.assertIsInstance(out, FetchResult)
            self.assertEqual(out.status, "failure")
            request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
