"""Model credentials never leave the explicitly configured endpoint via redirects."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

from newspipe import llm, state


class ModelTransportTests(unittest.TestCase):
    def test_redirect_destination_receives_no_request_or_bearer_key(self):
        received = []
        class Destination(BaseHTTPRequestHandler):
            def do_GET(self):
                received.append(dict(self.headers))
                body = b'{"choices":[{"message":{"content":"{}"}}]}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *_):
                pass
        destination = ThreadingHTTPServer(("127.0.0.1", 0), Destination)
        class Provider(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{destination.server_port}/redirect")
                self.send_header("Content-Length", "0")
                self.end_headers()
            def log_message(self, *_):
                pass
        provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        for server in (destination, provider):
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
        with TemporaryDirectory() as tmp:
            store = state.Store(Path(tmp))
            ref = llm.ModelRef("fixture", "offline", f"http://127.0.0.1:{provider.server_port}", "offline-dummy-key", "fixture")
            with patch.object(llm, "resolve_model", return_value=ref):
                result = llm.chat_json("story_compare", system="s", user="u", prompt_version="redirect-test",
                                       store=store, models_cfg={}, now=datetime(2026, 11, 13),
                                       budget={"max_calls_per_day": 10, "max_calls_per_hour": 10}, response_limit=1024)
            self.assertEqual(received, [])
            self.assertFalse(result.ok)
            self.assertEqual(result.accounting["attempts"], 1)
            self.assertNotIn("offline-dummy-key", result.note)
