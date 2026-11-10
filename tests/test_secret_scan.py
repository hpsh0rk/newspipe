"""The CI scanner inspects repository files without echoing credentials."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

SCANNER = Path(__file__).resolve().parents[1] / "scripts" / "scan_secrets.py"


class SecretScanTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCANNER.is_file(), "CI secret scanner is missing")
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        subprocess.run(["git", "-c", "init.templateDir=", "init", "-q", str(self.root)], check=True)

    def scan(self):
        return subprocess.run([sys.executable, str(SCANNER), "--root", str(self.root), "--json"],
                              text=True, capture_output=True, check=False)

    def test_token_and_literal_secret_are_redacted(self):
        key = "sk-" + "aB7xK9mQ2rT4vY6z" * 2
        literal = "zA9" * 10
        (self.root / "config.txt").write_text(f"OPENAI_API_KEY={key}\napp_secret: {literal}\n")
        result = self.scan()
        self.assertEqual(result.returncode, 1)
        self.assertNotIn(key, result.stdout + result.stderr)
        self.assertNotIn(literal, result.stdout + result.stderr)
        findings = json.loads(result.stdout)["findings"]
        self.assertTrue(any(f["line"] == 1 for f in findings))
        self.assertTrue(any(f["line"] == 2 for f in findings))
        self.assertEqual(set(findings[0]), {"path", "line", "rule"})

    def test_private_key_is_detected_without_printing_the_body(self):
        header = "-----BEGIN " + "PRIVATE KEY-----"
        (self.root / "key.txt").write_text(header + "\nprivate-body\n")
        result = self.scan()
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("private-body", result.stdout + result.stderr)
        self.assertIn("private_key", result.stdout)

    def test_placeholders_and_environment_references_are_allowed(self):
        (self.root / ".env.example").write_text("OPENAI_API_KEY=placeholder\nAPP_SECRET=${APP_SECRET}\n")
        (self.root / "service.yaml").write_text("encrypt_key_env: NEWSPIPE_ENCRYPT_KEY\n")
        (self.root / "client.py").write_text("app_secret=self.creds.app_secret\n")
        result = self.scan()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_ignored_local_credentials_are_not_read_but_tracked_env_is_rejected(self):
        (self.root / ".gitignore").write_text(".env\n")
        (self.root / ".env").write_text("sensitive local contents")
        self.assertEqual(self.scan().returncode, 0)
        subprocess.run(["git", "-C", str(self.root), "add", "-f", ".env"], check=True)
        result = self.scan()
        self.assertEqual(result.returncode, 1)
        self.assertIn("credential_file", result.stdout)
        self.assertNotIn("sensitive local contents", result.stdout + result.stderr)

    def test_personal_handoff_is_not_publishable(self):
        (self.root / "handoff.md").write_text("personal deployment notes")
        result = self.scan()
        self.assertEqual(result.returncode, 1)
        self.assertIn("personal_handoff", result.stdout)

    def test_non_repository_fails_closed(self):
        with TemporaryDirectory() as other:
            result = subprocess.run([sys.executable, str(SCANNER), "--root", other, "--json"],
                                    text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(json.loads(result.stdout)["ok"])

    def test_ci_scanner_and_its_tests_are_not_gitignored(self):
        result = subprocess.run(["git", "-C", str(SCANNER.parents[1]), "check-ignore", "--no-index",
                                 "scripts/scan_secrets.py", "tests/test_secret_scan.py"],
                                text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 1, "The CI gate itself must be publishable")
