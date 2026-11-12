"""Similarity is a bounded proposal, never a semantic or delivery decision."""
import unittest

from newspipe.observations import prepare_observation
from newspipe.story_matching import candidate_terms, propose_relation, rank_candidates
from test_observations import article, TEXT


class MatchingTests(unittest.TestCase):
    def draft(self, source="one", **changes):
        return prepare_observation(source, "rsshub", article(**changes))

    def test_complete_evidence_required_for_exact_copy(self):
        full_a = self.draft()
        full_b = self.draft("two", url="https://other.example/reprint")
        self.assertEqual(propose_relation(full_a, full_b)["relation"], "exact_copy")
        for changes in ({"source_text_complete": False}, {"content_kind": "unknown"},
                        {"source_text": "x" * 159}, {"source_text": " " * 200 + "x" * 159}):
            a, b = self.draft(**changes), self.draft("two", **changes)
            self.assertNotEqual(propose_relation(a, b)["relation"], "exact_copy")
        self.assertEqual(propose_relation(self.draft(source_text="x" * 160),
                                         self.draft("two", source_text="x" * 160))["relation"], "exact_copy")

    def test_candidate_cap_is_not_an_exact_hash_cap(self):
        full_a = self.draft(source_text="word " * 5000 + "approval")
        full_b = self.draft("two", source_text="word " * 5000 + "rejection")
        relation = propose_relation(full_a, full_b)
        self.assertNotEqual(relation["relation"], "exact_copy")
        self.assertEqual(relation["relation"], "candidate")

    def test_reference_roles_editions_and_facts_prevent_copy_proposals(self):
        first = self.draft(facts={"version": 2}, referenced_urls=[{"role": "article", "url": "https://example.com/a?edition=2"}])
        for changes in ({"facts": {"version": 3}},
                        {"referenced_urls": [{"role": "comment", "url": "https://example.com/a?edition=2"}]},
                        {"referenced_urls": [{"role": "article", "url": "https://example.com/a?edition=3"}]}):
            item = {"facts": {"version": 2}, "referenced_urls": [{"role": "article", "url": "https://example.com/a?edition=2"}], **changes}
            self.assertNotEqual(propose_relation(first, self.draft("two", **item))["relation"], "exact_copy")

    def test_shared_urls_do_not_merge_social_activity_releases_or_snapshots(self):
        for kind in ("post", "comment", "activity", "release", "snapshot", "unknown"):
            with self.subTest(kind=kind):
                a, b = self.draft(content_kind=kind), self.draft("two", content_kind=kind)
                self.assertNotEqual(propose_relation(a, b)["relation"], "exact_copy")

    def test_same_native_versions_are_unclassified_changes(self):
        changed = self.draft(source_text=TEXT + " The vote is postponed.")
        relation = propose_relation(changed, self.draft())
        self.assertEqual(relation["relation"], "unclassified_change")
        self.assertIn("text", relation["evidence_fields"])
        self.assertNotIn("progress", str(relation))

    def test_late_publication_and_engagement_do_not_change_copy_evidence(self):
        a, b = self.draft(pub_ts=200, score=3), self.draft("two", pub_ts=0, score=900)
        self.assertEqual(propose_relation(a, b)["relation"], "exact_copy")
        self.assertEqual(propose_relation(a, self.draft(pub_ts=1, score=800))["reason"], "same_version")

    def test_unrelated_material_and_vague_titles_cannot_be_confident_copies(self):
        a = self.draft(title="Update", source_text="alpha beta gamma", source_text_complete=False)
        b = self.draft("two", title="Update", source_text="delta epsilon zeta", source_text_complete=False)
        self.assertEqual(propose_relation(a, b)["relation"], "unrelated")

    def test_terms_and_results_are_bounded_and_ties_are_stable(self):
        draft = self.draft(source_text=" ".join("term" + str(n) for n in range(10000)))
        terms = candidate_terms(draft)
        self.assertLessEqual(len(terms), 256)
        self.assertNotIn("term9999", terms)
        candidates = [self.draft(str(n)) for n in range(30)]
        rows = rank_candidates(self.draft(), candidates, limit=5)
        self.assertEqual(len(rows), 5)
        self.assertEqual([row["previous_id"] for row in rows], sorted(row["previous_id"] for row in rows))
        for limit in (0, 21, True):
            with self.assertRaises(ValueError):
                rank_candidates(self.draft(), candidates, limit=limit)

    def test_multilingual_retrieval_uses_local_cjk_terms(self):
        draft = self.draft(source_text="委员会批准工具版本二，保留历史回执。", source_text_complete=False)
        self.assertIn("委员", candidate_terms(draft))
        self.assertIn("回执", candidate_terms(draft))
