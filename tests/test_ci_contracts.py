"""CI declares the same mandatory verification coverage used before release."""
import importlib.util
import io
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]


class CIContractTests(unittest.TestCase):
    def workflow(self):
        # BaseLoader keeps GitHub's `on` key a string rather than a YAML 1.1 boolean.
        return yaml.load((ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader)

    def test_branch_pushes_and_pull_requests_both_have_gates(self):
        triggers = self.workflow()["on"]
        self.assertIn("pull_request", triggers)
        self.assertEqual(triggers["push"].get("branches"), ["**"])

    def test_python_matrix_matches_verified_versions(self):
        matrix = self.workflow()["jobs"]["test"]["strategy"]["matrix"]
        self.assertEqual(set(matrix["python-version"]), {"3.11", "3.12", "3.13"})

    def test_node_is_explicit_and_javascript_tests_are_required(self):
        job = self.workflow()["jobs"]["test"]
        node = next((step for step in job["steps"] if step.get("uses", "").startswith("actions/setup-node@")), None)
        self.assertIsNotNone(node, "Node must not depend on incidental runner contents")
        self.assertEqual(node["with"]["node-version"], "24")
        self.assertEqual(job.get("env", {}).get("NEWSPIPE_REQUIRE_JS_TESTS"), "1")

    def javascript_suite_without_node(self, required):
        path = ROOT / "tests/test_workbench_javascript.py"
        spec = importlib.util.spec_from_file_location("ci_missing_node_probe", path)
        module = importlib.util.module_from_spec(spec)
        with patch("shutil.which", return_value=None), patch.dict(os.environ, {"NEWSPIPE_REQUIRE_JS_TESTS": "1" if required else "0"}):
            spec.loader.exec_module(module)
            suite = unittest.defaultTestLoader.loadTestsFromTestCase(module.WorkbenchJavaScriptTests)
            result = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
        return result

    def test_required_javascript_tests_fail_instead_of_skip_without_node(self):
        result = self.javascript_suite_without_node(required=True)
        self.assertFalse(result.wasSuccessful(), "CI must not report success with JavaScript tests skipped")
        self.assertEqual(result.skipped, [])
        self.assertTrue(any("Node" in detail for _, detail in result.errors))

    def test_optional_javascript_tests_can_skip_without_node(self):
        result = self.javascript_suite_without_node(required=False)
        self.assertTrue(result.wasSuccessful())
        self.assertEqual(len(result.skipped), 4)
