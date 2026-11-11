"""Runtime feed errors must not disclose URL queries or sink healthy siblings."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import urllib.error
from unittest.mock import patch

from newspipe import config, events, pipeline, state, view
from newspipe.adapters import rsshub
from test_rsshub_settings import RSS, TEXT


class RssHubRuntimeSafetyTests(unittest.TestCase):
    def setUp(self):
        inherited = patch.dict("os.environ", {"RSSHub_BASE": ""})
        inherited.start()
        self.addCleanup(inherited.stop)

    def test_inferred_labels_never_include_url_query_values(self):
        for entry, expected in (("/rss?access_token=SECRET", "rss"),
                                ("https://feed.example.invalid?access_token=SECRET", "feed.example.invalid")):
            with self.subTest(entry=entry), patch("newspipe.net.request", return_value=RSS) as request:
                items = rsshub.fetch({"feeds": [entry]})
                self.assertEqual(items[0]["source"], expected)
                self.assertNotIn("SECRET", json.dumps(items))
                self.assertIn("access_token=SECRET", request.call_args.args[0], "Query still belongs on the request")

    def test_auth_errors_are_safe_in_pipeline_events_and_view(self):
        for entry in ("/rss?access_token=SECRET", "https://feed.example.invalid?access_token=SECRET"):
            with self.subTest(entry=entry), TemporaryDirectory() as tmp, \
                    patch("newspipe.net.request", side_effect=urllib.error.HTTPError("SECRET", 401, "SECRET", {}, None)), \
                    patch("newspipe.adapters.rsshub.time.sleep"):
                news = Path(tmp)
                (news / "sources.yaml").write_text(TEXT.replace("/sspai/matrix", json.dumps(entry)))
                run = pipeline.collect_source(config.load(news), state.Store(news), "feed")
                self.assertEqual(run.status, "config_missing")
                recorded = events.list_events(news, days=None)
                self.assertEqual(recorded[0]["type"], "failed")
                # Editable source_values intentionally retain the user's input;
                # runtime status and event diagnostics must never copy its query.
                self.assertNotIn("SECRET", json.dumps([run.as_dict(), recorded, view.build(news)["sources"]]))

    def test_bad_xml_does_not_discard_healthy_siblings_and_warns_safely(self):
        for bad in (b'<feed xmlns="http://www.w3.org/2005/Atom"/>', b'<html>SECRET error page</html>', b'SECRET <broken'):
            for feeds in (("/valid", "/bad?token=SECRET"), ("/bad?token=SECRET", "/valid")):
                with self.subTest(bad=bad, feeds=feeds), patch("newspipe.net.request", side_effect=lambda url, **_: RSS if url.endswith("/valid") else bad) as request, \
                        self.assertLogs("newspipe.adapters.rsshub", level="WARNING") as logs:
                    try:
                        items = rsshub.fetch({"feeds": feeds})
                    except Exception as exc:
                        self.fail(f"Healthy sibling was discarded: {type(exc).__name__}: {exc}")
                    self.assertEqual(len(items), 1)
                    self.assertEqual(items[0]["source"], "valid")
                    self.assertEqual(request.call_count, 2, "Do not retry parse errors or skip later feeds")
                self.assertNotIn("SECRET", " ".join(logs.output))

    def test_all_bad_xml_remains_a_visible_terminal_failure(self):
        for body in (b'<feed xmlns="http://www.w3.org/2005/Atom"/>', b'SECRET <broken'):
            with self.subTest(body=body), patch("newspipe.net.request", return_value=body), TemporaryDirectory() as tmp:
                news = Path(tmp)
                (news / "sources.yaml").write_text(TEXT)
                run = pipeline.collect_source(config.load(news), state.Store(news), "feed")
                self.assertIn(run.status, {"error", "config_missing"})
                self.assertEqual(run.fetched, 0)
                self.assertEqual(events.list_events(news, days=None)[0]["type"], "failed")
                self.assertNotIn("SECRET", json.dumps(run.as_dict()))
