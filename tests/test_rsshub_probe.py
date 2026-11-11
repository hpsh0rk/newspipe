"""Read-only probes against an isolated HTTP server, never production services."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
import urllib.error
from unittest.mock import patch

from newspipe import actions, cli, config, rssconnect, view
from newspipe.adapters import rsshub
from test_rsshub_settings import TEXT, RSS


@contextmanager
def upstream():
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_GET(self):
            requests.append(self.path)
            path = self.path.split("?")[0]
            status, body = {
                "/": (200, b'<html>Welcome SECRET</html>'),
                "/rss": (200, RSS),
                "/empty": (200, b'<rss version="2.0"><channel/></rss>'),
                "/atom": (200, b'<feed xmlns="http://www.w3.org/2005/Atom"/>'),
                "/broken": (200, b'SECRET <broken'),
                "/auth": (401, b'SECRET login cookie'),
                "/missing": (404, b'SECRET missing'),
                "/bad": (503, b'SECRET upstream failed'),
                "/huge": (200, b'x' * (1024 * 1024 + 1)),
            }.get(path, (404, b'missing'))
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_address[1]}', requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class RssHubProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.news = Path(self.tmp.name)
        (self.news / "sources.yaml").write_text(TEXT)

    def probe(self, base, feed=None):
        args = ["rsshub", "probe", "--base-url", base, "--json"]
        if feed is not None:
            args += ["--feed", feed]
        return cli.run_subcommand(args, self.news).as_dict()

    def files(self):
        return {p.relative_to(self.news): p.read_bytes() for p in self.news.rglob("*") if p.is_file()}

    def test_reachability_and_valid_empty_or_nonempty_feed_are_read_only(self):
        before = self.files()
        with upstream() as (base, requests):
            for feed, status, count in ((None, "reachable", None), ("/empty", "feed_valid", 0), ("/rss", "feed_valid", 1)):
                out = self.probe(base, feed)
                self.assertTrue(out["ok"], out)
                self.assertEqual(out["data"]["status"], status)
                self.assertEqual(out["data"].get("items"), count)
                self.assertFalse(out["changed"])
                self.assertNotIn("SECRET", json.dumps(out))
            self.assertEqual(requests, ["/", "/empty", "/rss"])
        self.assertEqual(self.files(), before)

    def test_probe_classifies_failures_without_echoing_bodies_or_retrying(self):
        with upstream() as (base, requests):
            for path, reason in (("/auth", "authentication"), ("/missing", "not_found"), ("/bad", "http_error"),
                                 ("/broken", "invalid_feed"), ("/atom", "unsupported_feed"), ("/huge", "too_large")):
                before = len(requests)
                out = self.probe(base, path)
                self.assertFalse(out["ok"], out)
                self.assertEqual(out["error"]["details"]["reason"], reason)
                self.assertEqual(len(requests), before + 1)
                self.assertFalse(out["changed"])
                self.assertNotIn("SECRET", json.dumps(out))

    def test_direct_probe_drops_query_values_from_output_and_network_errors(self):
        with upstream() as (base, _):
            out = self.probe("https://unused.example.invalid", base + "/rss?access_token=SECRET")
            self.assertTrue(out["ok"], out)
            self.assertEqual(out["data"]["checked_url"], base + "/rss")
            self.assertNotIn("SECRET", json.dumps(out))
        with patch.object(rssconnect, "_probe_request", side_effect=urllib.error.URLError("SECRET inaccessible")):
            out = self.probe("https://example.invalid")
        self.assertEqual(out["error"]["details"]["reason"], "network")
        self.assertNotIn("SECRET", json.dumps(out))

    def test_doctor_and_view_describe_dependencies_without_network(self):
        class Credentials:
            def describe(self):
                return {"available": True}
        with patch("newspipe.credentials.resolve_feishu", return_value=Credentials()), \
                patch.object(rssconnect, "_probe_request", side_effect=AssertionError("implicit probe")), \
                patch.dict(os.environ, {"RSSHub_BASE": ""}):
            out = cli.run_subcommand(["doctor", "--json"], self.news).as_dict()
            self.assertTrue(out["ok"], out)
            self.assertEqual(out["data"]["rsshub"]["dependent_sources"], ["feed"])
            self.assertTrue(any("RSSHub" in w for w in out["warnings"]))
            self.assertFalse(view.build(self.news)["rsshub"]["checked"])
            cli.run_subcommand(["source", "disable", "feed", "--json"], self.news)
            out = cli.run_subcommand(["doctor", "--json"], self.news).as_dict()
            self.assertFalse(any("RSSHub" in w for w in out["warnings"]))

    def test_guarded_action_delegates_to_same_probe_and_does_not_write(self):
        before = self.files()
        with upstream() as (base, _):
            out = actions.dispatch("rsshub-probe", {"r.base_url": [base], "probe_feed": ["/rss?access_token=SECRET"]}, self.news)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["command"], "rsshub.probe")
        self.assertFalse(out["changed"])
        self.assertNotIn("SECRET", json.dumps(out))
        self.assertEqual(self.files(), before)

    def test_runtime_unsupported_xml_is_not_a_successful_quiet_collection(self):
        with patch("newspipe.net.request", return_value=b'<feed xmlns="http://www.w3.org/2005/Atom"/>'), \
                patch("newspipe.adapters.rsshub.time.sleep"):
            with self.assertRaises(Exception):
                rsshub.fetch({"feeds": ["https://example.invalid/atom"]})
