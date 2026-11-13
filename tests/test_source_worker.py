import json
import io
import os
import subprocess
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from unittest.mock import patch

from newspipe import actions, config, edit, events, pipeline, runtime, source_worker, state
from newspipe.errors import ConfigError
from test_news_pipeline import _src


class SourceWorkerTests(unittest.TestCase):
    def acquire(self, mode="legacy", seconds=3, **cfg):
        with source_worker.limit(seconds):
            return source_worker.Adapter("source_worker_fixture").fetch(dict(mode=mode, **cfg), since=50)

    def test_legacy_native_identity_type_and_generator_are_preserved(self):
        for generator in (False, True):
            result = self.acquire(generator=generator)
            self.assertFalse(result.reported)
            self.assertIs(type(result.items[0]["ext_id"]), int)
            self.assertEqual(result.items[0]["ext_id"], 0)
            self.assertEqual(result.items[0]["pub_ts"], 50)
            self.assertNotEqual(result.items[0]["metadata"]["pid"], os.getpid())

    def test_empty_success_keeps_its_watermark(self):
        result = self.acquire("empty")
        self.assertEqual(result.status, "empty")
        self.assertTrue(result.complete)
        self.assertEqual(result.reports[0].watermark, 100)

    def test_partial_timeout_keeps_completed_partition_and_reaps_child(self):
        started = time.monotonic()
        result = self.acquire("slow", seconds=.4)
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(result.status, "partial_failure")
        self.assertEqual([item["ext_id"] for item in result.items], ["first"])
        self.assertEqual(result.reports[-1].code, "timeout")
        self.assertFalse(result.complete)

    def test_cpu_stall_is_terminated_not_left_in_background(self):
        started = time.monotonic()
        result = self.acquire("cpu", seconds=.3)
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(result.status, "failure")
        self.assertEqual(result.reports[0].code, "timeout")

    def test_crash_without_terminal_receipt_is_not_success(self):
        result = self.acquire("crash")
        self.assertEqual(result.status, "partial_failure")
        self.assertEqual([item["ext_id"] for item in result.items], ["first"])
        self.assertEqual(result.reports[-1].code, "network")

    def test_configuration_failure_and_noisy_exceptions_are_safe(self):
        for mode, code in (("configuration", "configuration"), ("noise", "network")):
            result = self.acquire(mode)
            self.assertEqual(result.status, "failure")
            self.assertEqual(result.reports[-1].code, code)
            self.assertNotIn("PRIVATE_FIXTURE_VALUE", json.dumps(result.summary()))

    def test_result_overflow_keeps_only_previous_complete_frames(self):
        result = self.acquire("overflow")
        self.assertEqual(result.status, "partial_failure")
        self.assertEqual([item["ext_id"] for item in result.items], ["first"])
        self.assertEqual(result.reports[-1].code, "incomplete")

    def test_oversized_input_is_rejected_without_exporting_settings(self):
        result = self.acquire(secret="PRIVATE_FIXTURE_VALUE" + "x" * (1024 * 1024))
        self.assertEqual(result.status, "failure")
        self.assertEqual(result.reports[-1].code, "configuration")
        self.assertNotIn("PRIVATE_FIXTURE_VALUE", json.dumps(result.summary()))

    @unittest.skipUnless(hasattr(os, "fork"), "process-group fixture requires POSIX")
    def test_descendant_holding_protocol_pipe_is_terminated_after_leader_exit(self):
        result = self.acquire("descendant", seconds=.4)
        self.assertEqual(result.status, "partial_failure")
        pid = result.items[0]["metadata"]["pid"]
        until = time.monotonic() + 2
        while time.monotonic() < until:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(.02)
        else:
            self.fail("descendant still exists after acquisition timeout")

    def test_budget_scope_restores_and_rejects_invalid_values(self):
        for bad in (0, -1, True, float("inf"), float("nan")):
            with self.assertRaises(ValueError):
                with source_worker.limit(bad):
                    self.fail("invalid budget accepted")
        with source_worker.limit(.05):
            with source_worker.limit(3):
                self.assertEqual(source_worker.Adapter("source_worker_fixture").fetch({"mode": "empty"}).status, "empty")
        self.assertEqual(source_worker.Adapter("source_worker_fixture").fetch({"mode": "empty"}).status, "empty")

    def test_concurrent_budgets_do_not_leak_between_callers(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            short = pool.submit(self.acquire, "slow", .3)
            long = pool.submit(self.acquire, "complete", 3)
            self.assertEqual(short.result().status, "partial_failure")
            self.assertEqual(long.result().status, "success")

    def test_truncated_frame_never_becomes_a_completed_partition(self):
        first = b'{"items":[{"ext_id":"first"}],"reports":[{"key":"a","status":"success","fetched":1,"code":"","watermark":null}],"reported":true}\n'
        result = source_worker._decode(first + b'{"items":[', "network")
        self.assertEqual(result.status, "partial_failure")
        self.assertEqual([item["ext_id"] for item in result.items], ["first"])
        self.assertEqual(result.reports[-1].code, "invalid_response")

    def test_cleanup_uses_one_shared_allowance_not_two_sequential_waits(self):
        elapsed = [0]

        class StalledProcess:
            stdin = io.BytesIO()
            stdout = io.BytesIO()

            def communicate(self, input=None, timeout=None):
                elapsed[0] += timeout
                raise subprocess.TimeoutExpired("fixture", timeout, output=b"")

            def wait(self, timeout=None):
                elapsed[0] += timeout
                raise subprocess.TimeoutExpired("fixture", timeout)

        with patch.object(source_worker.subprocess, "Popen", return_value=StalledProcess()), \
                patch.object(source_worker, "_kill_group"), patch.object(source_worker.time, "monotonic", side_effect=lambda: elapsed[0]), \
                source_worker.limit(.25):
            result = source_worker.Adapter("source_worker_fixture").fetch()
        self.assertEqual(result.status, "failure")
        self.assertEqual(result.reports[-1].code, "timeout")
        self.assertLessEqual(elapsed[0], 2.25)


class WorkerSettingsTests(unittest.TestCase):
    def test_budget_is_validated_and_schema_values_roundtrip(self):
        self.assertEqual(config.FetchCfg.parse({"trigger": "manual"}, where="fetch").timeout_seconds, 90)
        for bad in (0, -1, 601, True, 1.5, float("inf"), "90"):
            with self.subTest(bad=bad), self.assertRaises(ConfigError):
                config.FetchCfg.parse({"trigger": "manual", "timeout_seconds": bad}, where="fetch")
        for seconds in (1, 135, 600):
            source = replace(_src(trigger="manual"), fetch=config.FetchCfg(trigger="manual", timeout_seconds=seconds))
            values = config.source_values(source)
            self.assertEqual(values["fetch.timeout_seconds"], seconds)
            fields = config.describe_schema()["fields"]
            form = {"f." + field["path"]: [json.dumps(values[field["path"]]) if field["type"] == "bool"
                                    else "\n".join(values[field["path"]]) if field["type"] in {"list", "slots"}
                                    else str(values[field["path"]])] for field in fields}
            body = actions.build_source_body(form, {"fields": fields})
            self.assertEqual(body["fetch"]["timeout_seconds"], seconds)

    def test_cli_create_edit_dry_and_copy_preserve_budget(self):
        with TemporaryDirectory() as tmp:
            news = Path(tmp)
            (news / "sources.yaml").write_text("chat: oc_test\nslots: {}\nsources: {}\n")
            body = {"adapter": "aihot", "fetch": {"trigger": "manual", "timeout_seconds": 135}}
            self.assertTrue(edit.set_source(news, "first", body, create_only=True)["ok"])
            before = (news / "sources.yaml").read_bytes()
            changed = {**body, "fetch": {"trigger": "manual", "timeout_seconds": 200}}
            self.assertTrue(edit.set_source(news, "first", changed, dry_run=True)["ok"])
            self.assertEqual((news / "sources.yaml").read_bytes(), before)
            self.assertTrue(edit.set_source(news, "first", changed)["ok"])
            self.assertTrue(edit.set_source(news, "copy", changed, create_only=True)["ok"])
            self.assertEqual([s.fetch.timeout_seconds for s in config.load(news).sources.values()], [200, 200])


class WorkerPipelineTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.news = Path(tmp.name)
        self.store = state.Store(self.news)
        self.now = datetime(2026, 11, 13, 10)
        self.store.set_cursor("src", 50, at=self.now)
        self.source = replace(_src(trigger="manual", form="state_only"),
                              fetch=config.FetchCfg(trigger="manual", timeout_seconds=1), extra={"mode": "slow"})
        self.cfg = config.Config(chat="oc_test", slots={}, sources={"src": self.source})
        self.addCleanup(patch.stopall)
        patch.object(runtime, "activate", side_effect=lambda *_: nullcontext()).start()
        patch.object(pipeline, "_adapter", side_effect=lambda _: source_worker.Adapter("source_worker_fixture")).start()
        patch("newspipe.channel.send_card", side_effect=AssertionError("live send")).start()
        patch.object(pipeline, "_enrich_new", side_effect=AssertionError("model access")).start()

    def collect(self, **kw):
        return pipeline.collect_source(self.cfg, self.store, "src", now=self.now, **kw)

    def test_timeout_is_partial_preserves_cursor_and_retry_is_idempotent(self):
        first = self.collect()
        self.assertEqual(first.status, "partial_failure")
        self.assertEqual(first.collection["reports"][-1]["code"], "timeout")
        self.assertEqual(self.store.cursor("src"), 50)
        self.assertEqual([i["ext_id"] for i in self.store.peek_pending("src")], ["first"])
        self.store = state.Store(self.news)
        self.collect()
        self.assertEqual(self.store.pending_count("src"), 1)
        self.source = replace(self.source, extra={"mode": "complete"})
        self.cfg = replace(self.cfg, sources={"src": self.source})
        self.collect()
        self.assertEqual(self.store.pending_count("src"), 2)
        self.assertEqual(self.store.cursor("src"), 100)

    def test_cpu_timeout_becomes_error_with_no_cursor_or_content_loss(self):
        self.cfg = replace(self.cfg, sources={"src": replace(self.source, extra={"mode": "cpu"})})
        run = self.collect()
        self.assertEqual(run.status, "error")
        self.assertEqual(run.collection["reports"][0]["code"], "timeout")
        self.assertEqual(self.store.cursor("src"), 50)
        self.assertEqual(self.store.pending_count("src"), 0)

    def test_dry_timeout_does_not_write_state_or_consume_favorite(self):
        favorite = events.append(self.news, "favorite", payload={"ext_id": "old"}, source="src")
        before = {p: p.read_bytes() for p in self.news.rglob("*") if p.is_file()}
        self.assertEqual(self.collect(dry=True).status, "dry")
        self.assertEqual(before, {p: p.read_bytes() for p in self.news.rglob("*") if p.is_file()})
        self.assertIn(favorite, events.list_events(self.news, days=None))

    def test_legacy_timeout_does_not_advance_cursor(self):
        self.cfg = replace(self.cfg, sources={"src": replace(self.source, extra={"mode": "cpu"},
                            fetch=config.FetchCfg(trigger="poll", interval_min=1, timeout_seconds=1))})
        run = pipeline.run_source(self.cfg, self.store, "src", now=self.now, dry=True)[0]
        self.assertEqual(run.collection["reports"][0]["code"], "timeout")
        self.assertEqual(self.store.cursor("src"), 50)


class RealFeedWorkerTests(unittest.TestCase):
    def test_rss_results_keep_global_publication_order_across_partitions(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                native, date = ("old", "00:01:40") if self.path == "/old" else ("new", "00:03:20")
                body = f'<rss version="2.0"><channel><item><guid>{native}</guid><title>{native}</title><pubDate>Thu, 01 Jan 1970 {date} +0000</pubDate></item></channel></rss>'.encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            with source_worker.limit(3):
                result = pipeline._adapter("rsshub").fetch({"feeds": [base + "/old", base + "/new"]})
            self.assertEqual(result.status, "success")
            self.assertEqual([item["ext_id"] for item in result.items], ["new", "old"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_rss_worker_streams_before_second_feed_stalls(self):
        release = threading.Event()
        body = b'<rss version="2.0"><channel><item><guid>first</guid><title>Original</title><description>Evidence</description><pubDate>Thu, 01 Jan 1970 00:01:40 +0000</pubDate></item></channel></rss>'

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/slow":
                    release.wait(3)
                self.send_response(200)
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            with source_worker.limit(1):
                result = pipeline._adapter("rsshub").fetch({"feeds": [base + "/fast", base + "/slow"]}, since=50)
            self.assertEqual(result.status, "partial_failure")
            self.assertEqual([item["ext_id"] for item in result.items], ["first"])
            self.assertEqual(result.items[0]["source_text"], "Evidence")
            self.assertEqual(result.reports[-1].code, "timeout")
        finally:
            release.set()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
