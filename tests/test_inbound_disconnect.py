"""Peer disconnection is a response transport event, not a business retry."""
import io
import unittest

from newspipe import inbound


class Writer(io.BytesIO):
    def __init__(self, error=None, at=1):
        super().__init__()
        self.error, self.at, self.calls = error, at, 0

    def write(self, data):
        self.calls += 1
        if self.error is not None and self.calls == self.at:
            raise self.error
        return super().write(data)


class InboundDisconnectTests(unittest.TestCase):
    def handler(self, writer):
        logs = []
        cls = inbound._make_handler(path="/events", creds=None, news_dir=None, hook_set=None,
                                    logger=logs.append, accept_events=False)
        handler = object.__new__(cls)
        handler.wfile = writer
        handler.request_version = "HTTP/1.1"
        handler.requestline = "GET / HTTP/1.1"
        handler.command = "GET"
        handler.path = "/assets/workbench.js"
        handler.close_connection = False
        return handler, logs

    def test_html_json_and_asset_disconnect_during_headers_or_body_is_quiet(self):
        for method in ("html", "json", "asset"):
            for at in (1, 2):
                for error in (BrokenPipeError("peer closed"), ConnectionResetError("peer reset")):
                    with self.subTest(method=method, at=at, error=type(error).__name__):
                        handler, logs = self.handler(Writer(error, at))
                        try:
                            if method == "html":
                                handler._send_html(200, "<p>hello</p>")
                            elif method == "json":
                                handler._send(200, {"ok": True})
                            else:
                                handler.do_GET()
                        except (BrokenPipeError, ConnectionResetError):
                            self.fail("closed response sockets must not escape the response boundary")
                        self.assertTrue(handler.close_connection)
                        self.assertFalse(any("peer" in text for text in logs))

    def test_unrelated_io_failures_are_not_suppressed(self):
        handler, _ = self.handler(Writer(OSError("synthetic I/O failure")))
        with self.assertRaisesRegex(OSError, "synthetic I/O failure"):
            handler._send_html(200, "<p>hello</p>")

    def test_success_keeps_html_security_headers_and_json_body(self):
        writer = Writer()
        handler, _ = self.handler(writer)
        handler._send_html(200, "<p>hello</p>")
        raw = writer.getvalue()
        self.assertIn(b"Content-Security-Policy: script-src 'self'", raw)
        self.assertIn(b"X-Content-Type-Options: nosniff", raw)
        self.assertIn(b"Content-Length: 12", raw)
        self.assertTrue(raw.endswith(b"<p>hello</p>"))
        writer = Writer()
        handler, _ = self.handler(writer)
        handler._send(200, {"ok": True})
        self.assertIn(b"Content-Type: application/json", writer.getvalue())
        self.assertTrue(writer.getvalue().endswith(b'{"ok": true}'))

    def test_redirect_disconnect_closes_connection_without_retry(self):
        writer = Writer(BrokenPipeError("peer closed"))
        handler, _ = self.handler(writer)
        redirect = getattr(handler, "_send_redirect", None)
        self.assertIsNotNone(redirect, "PRG redirects need the same safe response boundary")
        redirect("/config?draft=opaque")
        self.assertTrue(handler.close_connection)
        self.assertEqual(writer.calls, 1)


if __name__ == "__main__":
    unittest.main()
