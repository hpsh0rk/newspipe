import json
import os
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from newspipe import source_worker


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


if __name__ == "__main__":
    unittest.main()
