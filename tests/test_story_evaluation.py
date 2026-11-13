"""Measure the offline local-first boundary without any real-provider calls."""
import unittest

from story_comparison_fixtures import report


class EvaluationTests(unittest.TestCase):
    def test_fixture_results_report_missed_translation_and_safe_late_fallback(self):
        data = report()
        rows = {r["case"]: r for r in data["results"]}
        self.assertEqual(data["fixture_count"], 8)
        self.assertEqual(data["false_duplicate_proposals"], 0)
        self.assertEqual(data["missed_duplicate_associations"], 1)
        self.assertEqual(rows["translated_copy"]["result"], "no_candidate")
        self.assertEqual(rows["later_development"]["result"], "progress")
        self.assertEqual(rows["opposing_views"]["result"], "viewpoint")
        self.assertEqual(rows["late_old_publication"]["result"], "uncertain")
        self.assertEqual(rows["partial_duplicate"]["result"], "uncertain")
        self.assertEqual(data["real_model_calls"], 0)
        self.assertGreater(data["measured_tokens"], 0)
