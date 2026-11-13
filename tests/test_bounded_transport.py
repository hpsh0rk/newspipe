"""Opt-in source limits also apply to HTTP/2 decoded streams and redirects."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest
from unittest.mock import MagicMock, patch

from newspipe import net


@contextmanager
def upstream():
    paths = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_GET(self):
            paths.append(self.path)
            if self.path.startswith("/redirect"):
                n = int(self.path.removeprefix("/redirect"))
                self.send_response(302)
                self.send_header("Location", f"/redirect{n + 1}" if n < 7 else "/small")
                self.end_headers()
            else:
                self.send_response(200)
                body = b'x' * (100 if self.path == "/large" else 10)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", paths
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class BoundedTransportTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(hasattr(net, "ResponseLimitError"), "opt-in transport limits are not implemented")

    def test_http1_limit_does_not_retry_large_body_through_proxy(self):
        with upstream() as (base, paths):
            self.assertEqual(net.request(base + "/small", max_bytes=10, max_redirects=5), b'x' * 10)
            with self.assertRaises(net.ResponseLimitError):
                net.request(base + "/large", max_bytes=10, max_redirects=5)
            self.assertEqual(paths, ["/small", "/large"])

    def test_redirect_budget_is_terminal_across_exit_retries(self):
        with upstream() as (base, paths), self.assertRaises(net.ResponseLimitError):
            net.request(base + "/redirect0", max_bytes=100, max_redirects=2)
        self.assertEqual(paths, ["/redirect0", "/redirect1", "/redirect2"])

    def test_http1_closed_response_and_lengthless_body_are_bounded(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.headers = {}
        response.read.return_value = b'x' * 11
        opener = MagicMock()
        opener.open.return_value = response
        with patch("urllib.request.build_opener", return_value=opener), self.assertRaises(net.ResponseLimitError):
            net.request("https://feed.example.invalid", max_bytes=10, max_redirects=5)
        response.read.assert_called_once_with(11)
        self.assertTrue(response.__exit__.called)
        self.assertEqual(opener.open.call_count, 1)

    def test_http2_stream_checks_decoded_chunks_and_closes_on_limit(self):
        client = MagicMock()
        client.__enter__.return_value = client
        response = MagicMock(status_code=200, headers={})
        response.iter_bytes.return_value = iter([b'x' * 8, b'y' * 8])
        client.stream.return_value.__enter__.return_value = response
        with patch("httpx.Client", return_value=client) as constructor, self.assertRaises(net.ResponseLimitError):
            net.request("https://feed.example.invalid", http2=True, max_bytes=10, max_redirects=3)
        self.assertEqual(constructor.call_count, 1)
        self.assertEqual(constructor.call_args.kwargs["max_redirects"], 3)
        self.assertTrue(client.stream.return_value.__exit__.called)
        client.get.assert_not_called()

    def test_bad_limits_fail_before_transport(self):
        for settings in ({"max_bytes": 0}, {"max_bytes": True}, {"max_redirects": -1}, {"max_redirects": 1.5}):
            with self.subTest(settings=settings), patch("urllib.request.build_opener") as opener, self.assertRaises(ValueError):
                net.request("https://feed.example.invalid", **settings)
            opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
