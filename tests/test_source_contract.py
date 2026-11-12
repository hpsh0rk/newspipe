"""Pure acquisition contracts: no providers, credentials or source state."""
from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class SourceContractTests(unittest.TestCase):
    def setUp(self):
        try:
            self.contract = importlib.import_module("newspipe.source_contract")
        except ModuleNotFoundError:
            self.fail("the compatible acquisition contract is not implemented")

    def item(self, **fields):
        return {"ext_id": "native-1", "title": "A release", "url": "https://example.com/a",
                "summary": "Source text", **fields}

    def report(self, key="feed-1", status="success", fetched=1, **fields):
        return self.contract.SubscriptionReport(key, status, fetched, **fields)

    def test_status_distinguishes_empty_from_total_and_partial_failure(self):
        rows = [
            ([self.item()], (self.report(),), "success"),
            ([], (self.report(status="empty", fetched=0),), "empty"),
            ([], (self.report(status="failure", fetched=0, code="network"),), "failure"),
            ([self.item()], (self.report(), self.report("feed-2", "failure", 0, code="network")),
             "partial_failure"),
            ([], (self.report(status="empty", fetched=0),
                  self.report("feed-2", "failure", 0, code="network")), "partial_failure"),
        ]
        for items, reports, want in rows:
            with self.subTest(want=want, items=len(items)):
                result = self.contract.normalize_fetch(self.contract.FetchResult(items, reports),
                                                       source="s", adapter="rsshub")
                self.assertEqual(result.status, want)

    def test_legacy_items_keep_ids_unknown_fields_and_unknown_dates(self):
        original = self.item(ext_id="n" * 160, pub_ts=0, custom={"flag": False})
        result = self.contract.normalize_fetch([original], source="s", adapter="custom")
        self.assertFalse(result.reported)
        self.assertEqual(result.items, [original])
        self.assertEqual(result.items[0]["ext_id"], "n" * 160)
        self.assertEqual(result.items[0]["pub_ts"], 0)
        result.items[0]["custom"]["flag"] = True
        self.assertFalse(original["custom"]["flag"], "normalization cannot mutate adapter-owned input")

    def test_legacy_generator_is_consumed_once(self):
        yielded = []
        def generate():
            yielded.append("called")
            yield self.item()
        result = self.contract.normalize_fetch(generate(), source="s", adapter="custom")
        self.assertEqual(len(result.items), 1)
        self.assertEqual(yielded, ["called"])
        self.assertEqual(result.status, "success")

    def test_malformed_result_is_not_quiet_success_and_never_echoes_input(self):
        for value in ({"password": "sensitive-input"}, "sensitive-input", ["sensitive-input"],
                      [self.item(ext_id=None)], [self.item(pub_ts=float("nan"))]):
            with self.subTest(type=type(value).__name__):
                with self.assertRaises(ValueError) as raised:
                    self.contract.normalize_fetch(value, source="s", adapter="custom")
                self.assertNotIn("sensitive-input", str(raised.exception))

    def test_new_contract_dates_are_finite_or_explicitly_unknown(self):
        for value in (True, -1, float("nan"), float("inf"), "1700000000"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.contract.normalize_fetch(
                        self.contract.FetchResult([self.item(pub_ts=value)], (self.report(),)),
                        source="s", adapter="rsshub")
        for value in (None, 0, 1700000000.0):
            result = self.contract.normalize_fetch(
                self.contract.FetchResult([self.item(pub_ts=value)], (self.report(),)),
                source="s", adapter="rsshub")
            self.assertEqual(result.items[0]["pub_ts"], value)

    def test_invalid_reports_cannot_claim_complete_collection(self):
        for reports in ((), (self.report(status="bogus"),),
                        (self.report(fetched=-1),),
                        (self.report(), self.report()),
                        (self.report(watermark=float("inf")),),
                        (self.report(code="Authorization: sensitive-input"),)):
            with self.subTest(reports=reports):
                with self.assertRaises(ValueError) as raised:
                    self.contract.normalize_fetch(self.contract.FetchResult([self.item()], reports),
                                                   source="s", adapter="custom")
                self.assertNotIn("sensitive-input", str(raised.exception))

    def test_public_summary_redacts_subscription_locations_and_items(self):
        result = self.contract.normalize_fetch(self.contract.FetchResult(
            [self.item(summary="sensitive-material")],
            (self.report("https://user:password@example.com/feed?token=sensitive-input"),)),
            source="s", adapter="rsshub")
        summary = result.summary()
        encoded = json.dumps(summary)
        self.assertEqual(summary["status"], "success")
        self.assertEqual(summary["fetched"], 1)
        self.assertEqual(summary["successful"], 1)
        self.assertEqual(summary["failed"], 0)
        for secret in ("password", "sensitive-input", "sensitive-material", "user:"):
            self.assertNotIn(secret, encoded)

    def test_native_identity_handles_long_ids_partitions_and_unicode(self):
        identify = self.contract.native_identity
        first = identify("rsshub", "n" * 120 + "a", partition="feed-1")
        self.assertEqual(first, identify("rsshub", "n" * 120 + "a", partition="feed-1"))
        self.assertNotEqual(first, identify("rsshub", "n" * 120 + "b", partition="feed-1"))
        self.assertNotEqual(first, identify("rsshub", "n" * 120 + "a", partition="feed-2"))
        self.assertNotEqual(identify("rsshub", "\u4e2d\u6587"), identify("rsshub", "\u4e2d\u6587a"))
        self.assertRegex(first, r"^rsshub:[0-9a-f]{64}$")

    def test_persisted_summary_is_revalidated_and_bounded(self):
        result = self.contract.FetchResult([], tuple(
            self.report(str(n), "empty", 0) for n in range(150)))
        summary = result.summary()
        self.assertEqual(len(summary["reports"]), 100)
        self.assertEqual(summary["omitted_reports"], 50)
        safe = self.contract.safe_summary({**summary, "provider_body": "sensitive-input"})
        self.assertNotIn("sensitive-input", json.dumps(safe))
        for invalid in ({**summary, "failed": True},
                        {**summary, "status": "sensitive-input"},
                        {**summary, "reports": [{"key": "https://secret@host", "status": "failure",
                                                  "code": "network", "fetched": 0, "index": 1}]}):
            with self.assertRaises(ValueError):
                self.contract.safe_summary(invalid)


if __name__ == "__main__":
    unittest.main()
