"""Semantic labels must cite immutable, bounded source evidence."""
from copy import deepcopy
import json
import unittest

from newspipe import story_evidence as evidence


def observation(ident="one", text="The council approved version two. " * 5, **changes):
    row = {"observation_id": ident, "version_hash": ident + "-v1", "extractor_version": "v1",
           "kind": "article", "source": ident, "pub_ts": 20 if ident == "one" else 10,
           "evidence": {"title": "Council release", "text": text, "facts_text": "{}",
                        "text_complete": True, "text_truncated": False, "facts_truncated": False,
                        "permalink": {"url": "https://example.invalid/" + ident, "clickable": True}}}
    row.update(changes)
    return row


def answer(relation="progress", kind="new_fact"):
    return {"relation": relation, "differences": [{"kind": kind, "current": "c1", "previous": "p1"}]}


class EvidenceTests(unittest.TestCase):
    def pair(self, current=None, previous=None):
        return evidence.comparison_input(current or observation(), previous or observation("two"))

    def test_bounds_and_immutable_versions_are_visible(self):
        pair = self.pair(observation(text="汉字。" * 15000))
        self.assertLessEqual(len(pair["current"]["excerpts"]), 12)
        self.assertTrue(all(len(row["text"]) <= 240 for row in pair["current"]["excerpts"]))
        self.assertFalse(pair["current"]["fully_represented"])
        self.assertEqual(pair["current"]["version_hash"], "one-v1")
        self.assertEqual(pair["current"]["extractor_version"], "v1")
        self.assertLess(len(json.dumps(pair, ensure_ascii=False)), 8000)

    def test_valid_progress_has_exact_original_quotes_not_model_paraphrases(self):
        result = evidence.validate_comparison(answer(), self.pair())
        self.assertEqual(result["relation"], "progress")
        self.assertEqual(result["differences"][0]["current"]["text"], observation()["evidence"]["text"])
        self.assertEqual(result["differences"][0]["previous"]["observation_id"], "two")

    def test_invalid_ids_schema_and_relation_fail_closed(self):
        for bad in (None, [], {}, {"relation": "merge"}, answer(kind="confirmed_truth"),
                    {"relation": "progress", "differences": []},
                    {"relation": "viewpoint", "differences": [{"kind": "viewpoint", "current": "p1", "previous": "c1"}]},
                    {**answer(), "summary": "invented truth"},
                    {"relation": "progress", "differences": [answer()["differences"][0]] * 6}):
            with self.subTest(bad=bad):
                result = evidence.validate_comparison(bad, self.pair())
                self.assertEqual(result["relation"], "uncertain")
                self.assertEqual(result["differences"], [])

    def test_duplicate_cannot_claim_full_agreement_from_partial_material(self):
        for change in ({"text_complete": False}, {"text_truncated": True}, {"facts_truncated": True}):
            current = observation()
            current["evidence"].update(change)
            self.assertEqual(evidence.validate_comparison(answer("duplicate", "changed_claim"), self.pair(current))["relation"], "uncertain")
        self.assertEqual(evidence.validate_comparison(answer("duplicate", "changed_claim"), self.pair(observation(text="x" * 4000)))["relation"], "uncertain")
        self.assertEqual(evidence.validate_comparison(answer("duplicate", "changed_claim"), self.pair(observation(kind="comment")))["relation"], "uncertain")
        self.assertEqual(evidence.validate_comparison(answer("duplicate", "changed_claim"), self.pair())["relation"], "duplicate")

    def test_unknown_or_reverse_chronology_cannot_be_progress(self):
        for stamp in (None, 5):
            self.assertEqual(evidence.validate_comparison(answer(), self.pair(observation(pub_ts=stamp)))["relation"], "uncertain")
        self.assertEqual(evidence.validate_comparison(answer("viewpoint", "viewpoint"), self.pair(observation(pub_ts=None)))["relation"], "viewpoint")

    def test_unrelated_and_uncertain_do_not_carry_asserted_differences(self):
        for relation in ("unrelated", "uncertain"):
            result = evidence.validate_comparison({"relation": relation, "differences": []}, self.pair())
            self.assertEqual(result["relation"], relation)
            self.assertEqual(result["differences"], [])

    def test_local_changes_do_not_invent_semantic_labels(self):
        pair = self.pair(observation(text="New release two."), observation("two", text="Old release one."))
        result = evidence.local_comparison({"relation": "unclassified_change", "evidence_fields": ["text"]}, pair)
        self.assertEqual(result["relation"], "uncertain")
        self.assertEqual(result["differences"][0]["kind"], "unclassified")
        self.assertEqual(result["differences"][0]["current"]["text"], "New release two.")
        self.assertEqual(evidence.local_comparison({"relation": "exact_copy"}, pair)["relation"], "duplicate")

    def test_validation_never_mutates_saved_evidence(self):
        pair = self.pair()
        before = deepcopy(pair)
        evidence.validate_comparison(answer(), pair)
        self.assertEqual(pair, before)

    def test_material_only_proposal_can_show_actual_excerpt_difference_without_classifying_it(self):
        pair = self.pair(observation(text="New decision."), observation("two", text="Earlier proposal."))
        result = evidence.local_comparison({"relation": "unclassified_change", "evidence_fields": ["source_material"]}, pair)
        self.assertEqual(result["relation"], "uncertain")
        self.assertEqual(result["differences"][0]["current"]["text"], "New decision.")

    def test_whitespace_title_only_and_incompatible_categories_cannot_assert_relationships(self):
        whitespace = self.pair(observation(text=" " * 200), observation("two", text=" " * 200))
        self.assertEqual(evidence.validate_comparison(answer("duplicate", "changed_claim"), whitespace)["relation"], "uncertain")
        empty = self.pair(observation(text=""), observation("two", text=""))
        titles = {"relation": "progress", "differences": [{"kind": "new_fact", "current": "c0", "previous": "p0"}]}
        self.assertEqual(evidence.validate_comparison(titles, empty)["relation"], "uncertain")
        for relation, kind in (("duplicate", "viewpoint"), ("duplicate", "new_fact"), ("progress", "viewpoint")):
            with self.subTest(relation=relation, kind=kind):
                self.assertEqual(evidence.validate_comparison(answer(relation, kind), self.pair())["relation"], "uncertain")

    def test_omitted_facts_public_projection_cannot_support_full_duplicate(self):
        from newspipe.state import Store
        from newspipe.observations import prepare_observation
        from test_observations import article
        from test_story_state import NOW
        from pathlib import Path
        from tempfile import TemporaryDirectory
        facts = {"decision": "cancelled"}
        for _ in range(18):
            facts = {"nested": facts}
        with TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            draft = prepare_observation("source", "rsshub", article(facts=facts))
            snapshot = store.observation_capture_snapshot([draft], at=NOW, window_days=14, candidate_limit=5, scan_limit=500)
            store.record_observations([draft], at=NOW, mode="observe", config_revision="r", current_revision="r",
                                      expected_activation_id=None, snapshot=snapshot, proposals=[], baselines={})
            row = store.observation(draft.observation_id)
            self.assertIn("[nested facts omitted]", row["evidence"]["facts_text"])
            self.assertFalse(row["evidence"]["facts_projection_complete"])
            pair = self.pair(row)
            self.assertFalse(pair["current"]["fully_represented"])
            self.assertEqual(evidence.validate_comparison(answer("duplicate", "changed_claim"), pair)["relation"], "uncertain")
