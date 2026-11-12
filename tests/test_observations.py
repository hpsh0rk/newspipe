"""Pure source versions must retain evidence without inventing article identity."""
from dataclasses import FrozenInstanceError
import json
import unittest

from newspipe.observations import canonical_reference, prepare_observation
from newspipe.source_contract import SourceContractError

TEXT = ("The committee approved toolkit version 2 after public review. "
        "The release adds a documented repair workflow and keeps existing receipts. ") * 3


def article(native_id=1, **changes):
    return {"ext_id": native_id, "title": "Toolkit version 2", "source_text": TEXT,
            "source_text_complete": True, "content_kind": "article",
            "url": "https://publisher.example/news/2", "pub_ts": 0, **changes}


class ObservationTests(unittest.TestCase):
    def draft(self, item=None, source="one", adapter="rsshub"):
        return prepare_observation(source, adapter, article() if item is None else item)

    def test_native_ids_remain_full_and_keep_original_types(self):
        for native_id in (0, "0", "x" * 10000, "消息/版本二"):
            with self.subTest(native_id=str(native_id)[:20]):
                draft = self.draft(article(native_id))
                self.assertEqual(json.loads(draft.original_json)["ext_id"], native_id)
                self.assertIs(type(draft.native_id), type(native_id))
        self.assertEqual(self.draft(article(0)).identity_hash, self.draft(article("0")).identity_hash)
        self.assertNotEqual(self.draft(article("x" * 10000)).identity_hash,
                            self.draft(article("x" * 9999 + "y")).identity_hash)

    def test_source_adapter_and_partition_are_identity_boundaries(self):
        first = self.draft(article(subscription="a"))
        for other in (self.draft(article(subscription="b")),
                      self.draft(article(subscription="a"), source="two"),
                      self.draft(article(subscription="a"), adapter="custom")):
            self.assertNotEqual(first.identity_hash, other.identity_hash)

    def test_original_and_nested_evidence_are_immutable(self):
        item = article(facts={"version": 2}, extra={"values": [1]})
        draft = self.draft(item)
        original = draft.original_json
        item["facts"]["version"] = 3
        item["extra"]["values"].append(2)
        self.assertEqual(draft.original_json, original)
        self.assertEqual(json.loads(draft.evidence.facts_json), {"version": 2})
        with self.assertRaises(FrozenInstanceError):
            draft.evidence.text = "overwritten"

    def test_metrics_ai_and_timestamps_do_not_create_versions(self):
        before = self.draft(article(score=1, pub_ts=None))
        after = self.draft(article(score=900, pub_ts=200, observed_ts=300,
                                   comments=40, title_zh="生成标题", summary_zh="生成摘要"))
        self.assertEqual(before.observation_id, after.observation_id)
        self.assertIsNone(before.pub_ts)
        self.assertEqual(self.draft().pub_ts, 0)
        self.assertEqual(after.observed_ts, 300)

    def test_source_changes_and_structured_facts_create_versions(self):
        first = self.draft(article(facts={"version": 2}))
        for item in (article(title="Toolkit version 3", facts={"version": 2}),
                     article(source_text=TEXT + " Correction: no vote yet.", facts={"version": 2}),
                     article(facts={"version": 3}), article(facts={"version": 2, "date": "2026-11-13"})):
            self.assertNotEqual(first.version_hash, self.draft(item).version_hash)

    def test_nfc_and_line_endings_normalize_but_punctuation_and_indentation_stay(self):
        first = self.draft(article(source_text="café\r\n  code: x != y"))
        equal = self.draft(article(source_text="cafe\u0301\n  code: x != y"))
        self.assertEqual(first.version_hash, equal.version_hash)
        for text in ("café\ncode: x != y", "café\n  code: x = y"):
            self.assertNotEqual(first.version_hash, self.draft(article(source_text=text)).version_hash)

    def test_kind_and_completeness_are_explicit_not_inferred(self):
        legacy = self.draft({"ext_id": 1, "title": "News", "summary": TEXT, "url": "https://example.com/article"})
        self.assertEqual(legacy.kind, "unknown")
        self.assertFalse(legacy.evidence.text_complete)
        self.assertEqual(legacy.evidence.text, TEXT)
        self.assertEqual(self.draft(article(content_kind="future-kind")).kind, "unknown")
        self.assertNotEqual(self.draft().version_hash,
                            self.draft(article(source_text_complete=False)).version_hash)

    def test_explicit_truncation_invalidates_completeness_with_new_evidence_version(self):
        full = self.draft()
        truncated = self.draft(article(source_text_truncated=True))
        self.assertFalse(truncated.evidence.text_complete)
        self.assertEqual(truncated.copy_hash, "")
        self.assertNotEqual(truncated.version_hash, full.version_hash)
        self.assertTrue(json.loads(truncated.original_json)["source_text_truncated"])

    def test_invalid_explicit_material_is_not_stringified(self):
        for changes in ({"ext_id": True}, {"source_text": {}}, {"facts": []},
                        {"subscription": []}, {"content_kind": 3}, {"source_text_complete": "true"}, {"source_text_truncated": "true"},
                        {"referenced_urls": [{"role": "article", "url": []}]},
                        {"observed_ts": float("nan")}, {"pub_ts": -1}):
            with self.subTest(changes=changes), self.assertRaises(SourceContractError):
                self.draft(article(**changes))

    def test_tracking_removal_preserves_meaningful_url_bytes_and_roles(self):
        self.assertEqual(canonical_reference("HTTPS://EXAMPLE.COM:443/a?x=1&utm_source=z&x=2#v2", "article"),
                         canonical_reference("https://example.com/a?x=1&x=2#v2", "article"))
        for url, role in (("https://example.com/a?x=2&x=1#v2", "article"),
                          ("https://example.com/a?x=1&x=2#v3", "article"),
                          ("https://example.com/a?x=1&x=2#v2", "comment"),
                          ("https://example.com/a?x=1&x=2&edition=2#v2", "article")):
            self.assertNotEqual(canonical_reference(url, role),
                                canonical_reference("https://example.com/a?x=1&x=2#v2", "article"))
