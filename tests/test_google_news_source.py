"""Synthetic Google News feeds exercise the real adapter; no live acceptance."""
from html import escape
import importlib
import json
import unittest
import urllib.error
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from newspipe import net, observations, source_contract

STAMP = 1794564000.0  # 2026-11-13T10:00:00Z


def search_entry(guid="article-1", *, date="Fri, 13 Nov 2026 10:00:00 GMT", title="Original headline",
                 link="https://news.google.com/rss/articles/one", publisher="Publisher",
                 publisher_url="https://publisher.example.invalid/", body="Original excerpt"):
    return (f'<item><guid>{escape(guid)}</guid><title>{escape(title)}</title>'
            f'<link>{escape(link)}</link><pubDate>{escape(date)}</pubDate>'
            f'<source url="{escape(publisher_url, quote=True)}">{escape(publisher)}</source>'
            f'<description><![CDATA[{body}]]></description></item>')


def search_feed(*entries):
    return ('<rss version="2.0"><channel><title>Google News</title>' + ''.join(entries)
            + '</channel></rss>').encode()


class GoogleNewsSourceTests(unittest.TestCase):
    def setUp(self):
        try:
            self.adapter = importlib.import_module("newspipe.adapters.google_news")
        except ModuleNotFoundError:
            self.fail("native Google News adapter has not been implemented")

    def collect(self, body=None, cfg=None, **kwargs):
        body = search_feed(search_entry()) if body is None else body
        cfg = {"settings": {"queries": ["AI"]}} if cfg is None else cfg
        with patch.object(self.adapter, "request", return_value=body) as transport, patch.object(
                self.adapter.time, "time", return_value=STAMP + 7200):
            result = self.adapter.fetch(cfg, **kwargs)
        source_contract.normalize_fetch(result, source="s", adapter="google_news")
        for call in transport.call_args_list:
            parts = urlsplit(call.args[0])
            self.assertEqual((parts.scheme, parts.netloc, parts.path),
                             ("https", "news.google.com", "/rss/search"))
            self.assertEqual(call.kwargs["max_bytes"], 2 * 1024 * 1024)
            self.assertEqual(call.kwargs["max_redirects"], 0)
            self.assertEqual(call.kwargs["timeout"], 20)
            self.assertNotIn("headers", call.kwargs, "searches never load or forward credentials")
        return result, transport

    def test_unicode_quoted_queries_are_encoded_not_request_locations(self):
        query = '\u4eba\u5de5\u667a\u80fd "new model" & gl=US https://127.0.0.1/private#x'
        result, transport = self.collect(cfg={"settings": {"queries": [query]}})
        params = parse_qs(urlsplit(transport.call_args.args[0]).query)
        self.assertEqual(params, {"q": [query + " when:7d"], "hl": ["zh-CN"],
                                  "gl": ["CN"], "ceid": ["CN:zh-Hans"]})
        self.assertEqual(result.status, "success")
        self.assertEqual(transport.call_count, 1, "never follow article or publisher links")

    def test_locales_derive_editions_and_allow_explicit_same_region_override(self):
        for settings, edition in (
            ({"language": "en-US", "region": "US"}, "US:en"),
            ({"language": "zh-TW", "region": "TW"}, "TW:zh-Hant"),
            ({"language": "zh-SG", "region": "SG"}, "SG:zh-Hans"),
            ({"language": "pt", "region": "BR", "edition": "BR:pt-419"}, "BR:pt-419"),
        ):
            with self.subTest(settings=settings):
                _, call = self.collect(cfg={"settings": {"queries": ["AI"], **settings}})
                self.assertEqual(parse_qs(urlsplit(call.call_args.args[0]).query)["ceid"], [edition])

    def test_invalid_settings_and_since_fail_before_transport(self):
        bad = ({"queries": []}, {"queries": [" "]}, {"queries": "AI"},
               {"queries": ["q"] * 26}, {"queries": ["x" * 1025]}, {"queries": ["a\nb"]},
               {"queries": ["a\ud800"]}, {"queries": ["a\u0085b"]},
               {"language": "../private"}, {"language": "en-" + "a" * 40}, {"region": "cn"},
               {"edition": "US:en"}, {"edition": "CN:en&gl=US"}, {"max_results": 101},
               {"max_results": False}, {"lookback_days": 0}, {"lookback_days": 91})
        for settings in bad:
            with self.subTest(settings=settings):
                result, call = self.collect(cfg={"settings": {"queries": ["AI"], **settings}})
                self.assertEqual(result.status, "failure")
                self.assertEqual(result.reports[0].code, "configuration")
                call.assert_not_called()
        for since in (True, "date", -1, float("nan"), float("inf"), 10**1000):
            with self.subTest(since_type=type(since)):
                result, call = self.collect(since=since)
                self.assertEqual(result.reports[0].code, "configuration")
                call.assert_not_called()

    def test_window_operators_conflict_but_quoted_literal_does_not(self):
        for query in ("AI when:2d", "AFTER:2026-11-01 model", "AI before:2026-12-01"):
            result, call = self.collect(cfg={"settings": {"queries": [query]}})
            self.assertEqual(result.reports[0].code, "configuration")
            call.assert_not_called()
        result, _ = self.collect(cfg={"settings": {"queries": ['"when:2d" model']}})
        self.assertEqual(result.status, "success")

    def test_duplicate_queries_trim_without_lowercasing_search_semantics(self):
        result, call = self.collect(cfg={"settings": {"queries": [" AI ", "AI", "ai"]}})
        self.assertEqual(call.call_count, 2)
        self.assertEqual(len(result.reports), 2)
        self.assertNotEqual(result.reports[0].key, result.reports[1].key)
        self.assertEqual(result.items[0]["ext_id"], result.items[1]["ext_id"])

    def test_native_identity_does_not_truncate_or_use_headline_or_shared_url(self):
        result, _ = self.collect(search_feed(search_entry("n" * 200 + "a"), search_entry("n" * 200 + "b")))
        ids = [item["ext_id"] for item in result.items]
        self.assertEqual(len(set(ids)), 2)
        self.assertRegex(ids[0], r"^google_news:[0-9a-f]{64}$")
        self.assertEqual(result.items[0]["identity_version"], "google-news-native-v1")
        changed, _ = self.collect(search_feed(search_entry("n" * 200 + "a", title="Edited headline")))
        self.assertEqual(ids[0], changed.items[0]["ext_id"])
        self.assertNotIn("legacy_ext_id", changed.items[0])

    def test_permalink_fallback_and_publisher_attribution_are_separate(self):
        result, _ = self.collect(search_feed(search_entry(guid="", body='<p>Original &amp; excerpt</p>')))
        item, = result.items
        self.assertEqual(item["original_url"], "https://news.google.com/rss/articles/one")
        self.assertEqual(item["facts"]["publisher"], "Publisher")
        self.assertNotIn("author", item["facts"], "publisher is not a byline")
        self.assertIn({"role": "publisher", "url": "https://publisher.example.invalid/"}, item["referenced_urls"])
        self.assertEqual(item["content_kind"], "article")
        self.assertFalse(item["source_text_complete"])
        self.assertEqual(item["source_text"], "Original & excerpt")
        self.assertEqual(item["source_markup"], '<p>Original &amp; excerpt</p>')
        self.assertEqual(item["pub_ts"], STAMP)
        observation = observations.prepare_observation("s", "google_news", item)
        self.assertFalse(observation.evidence.text_complete)

    def test_unknown_and_equal_dates_are_retained_old_dates_filtered(self):
        body = search_feed(search_entry("old", date="Fri, 13 Nov 2026 09:59:59 GMT"),
                           search_entry("same"), search_entry("unknown", date="invalid"))
        result, _ = self.collect(body, since=STAMP)
        self.assertEqual([item["metadata"]["native_id"] for item in result.items], ["same", "unknown"])
        self.assertIsNone(result.items[1]["pub_ts"])
        future, _ = self.collect(body, since=STAMP + 7201)
        self.assertEqual([item["metadata"]["native_id"] for item in future.items], ["unknown"])

    def test_local_lookback_and_result_cap_do_not_depend_on_provider_order(self):
        body = search_feed(search_entry("older", date="Thu, 12 Nov 2026 10:00:00 GMT"), search_entry("newest"),
                           search_entry("old", date="Sun, 01 Nov 2026 10:00:00 GMT"),
                           search_entry("unknown", date=""))
        result, _ = self.collect(body, cfg={"settings": {"queries": ["AI"], "max_results": 1}})
        self.assertEqual([item["metadata"]["native_id"] for item in result.items], ["newest"])
        self.assertEqual(result.reports[0].diagnostics["window_kind"], "result_cap")
        self.assertIsNone(result.reports[0].watermark)

    def test_empty_and_short_searches_are_still_bounded_history_not_complete_watermarks(self):
        for body, status in ((search_feed(), "empty"), (search_feed(search_entry()), "success")):
            result, _ = self.collect(body)
            self.assertEqual(result.status, status)
            self.assertEqual(result.reports[0].diagnostics,
                             {"window_limited": True, "window_kind": "search_window"})
            self.assertIsNone(result.reports[0].watermark)
            self.assertNotIn("AI", json.dumps(result.summary()))

    def test_failed_query_keeps_completed_sibling_and_categorical_error(self):
        with patch.object(self.adapter, "request", side_effect=[search_feed(search_entry()),
                urllib.error.HTTPError("https://secret.invalid/?token=hidden", 429, "sensitive-input", {}, None)]), patch.object(
                self.adapter.time, "time", return_value=STAMP + 7200):
            result = self.adapter.fetch({"settings": {"queries": ["AI", "sensitive-query"]}})
        self.assertEqual(result.status, "partial_failure")
        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.reports[1].code, "rate_limit")
        for value in ("sensitive-input", "hidden", "sensitive-query"):
            self.assertNotIn(value, json.dumps(result.summary()))

    def test_invalid_and_oversized_provider_responses_are_not_empty_success(self):
        for body in (b"not xml", search_feed(search_entry(link="", title="")), b"x" * (2 * 1024 * 1024 + 1)):
            result, _ = self.collect(body)
            self.assertEqual(result.status, "failure")
            self.assertEqual(result.items, [])
        for error, code in ((TimeoutError("sensitive-input"), "timeout"),
                            (net.ResponseLimitError("sensitive-input"), "incomplete"),
                            (urllib.error.HTTPError("https://hidden", 403, "secret", {}, None), "permission")):
            with patch.object(self.adapter, "request", side_effect=error):
                result = self.adapter.fetch({"settings": {"queries": ["AI"]}})
            self.assertEqual(result.reports[0].code, code)
            self.assertNotIn("sensitive-input", json.dumps(result.summary()))
