#!/usr/bin/env python3
"""Fail CI on publishable credentials; report locations, never matched values.

Scans tracked and non-ignored untracked working-tree files, not local ignored
credentials or Git history. This is a deterministic safety gate, not a complete
secret detector; rotate exposed credentials even after removing their files.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

PATTERNS = {
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"),
    "openai_token": re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}\b"),
    "github_token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    "aws_access_key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "slack_token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    "bearer_literal": re.compile(r"\bBearer\s+[A-Za-z0-9._-]{20,}\b", re.IGNORECASE),
}
ASSIGNMENT = re.compile(
    r"(?i)\b(?P<key>[A-Z0-9_]*(?:API_KEY|APP_SECRET|ACCESS_TOKEN|AUTH_TOKEN|ENCRYPT_KEY|CLIENT_SECRET|PASSWORD)"
    r"[A-Z0-9_]*)[\"']?\s*[:=]\s*(?P<quote>[\"']?)(?P<value>[A-Za-z0-9+/_.=-]{16,})")
PLACEHOLDERS = ("placeholder", "example", "dummy", "fake", "test", "your_")
ENV_EXAMPLES = {".env.example", ".env.sample", ".env.template"}


def scan_file(path: Path, relative: str) -> list[dict]:
    findings = []
    def add(line: int, rule: str):
        findings.append({"path": relative, "line": line, "rule": rule})

    if path.name == "handoff.md":
        add(1, "personal_handoff")
    if ((path.name == ".env" or path.name.startswith(".env.")) and path.name not in ENV_EXAMPLES
            or path.name == "credentials.json"):
        add(1, "credential_file")
    if path.is_symlink():
        return findings  # Never follow a repository link into private files outside the worktree.
    text = path.read_bytes().decode("utf-8", "replace")
    for number, line in enumerate(text.splitlines(), 1):
        for rule, pattern in PATTERNS.items():
            if pattern.search(line):
                add(number, rule)
        for match in ASSIGNMENT.finditer(line):
            value = match.group("value")
            if match.group("key").lower().endswith("_env"):
                continue
            if path.suffix == ".py" and not match.group("quote"):
                continue  # Python identifiers are references, not string credentials.
            if not value.lower().startswith(PLACEHOLDERS):
                add(number, "credential_literal")
                break
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--json", action="store_true", dest="json_out")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    findings = []
    error = ""
    scanned = 0
    try:
        files = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--cached", "--others",
                                "--exclude-standard"], check=True, capture_output=True).stdout
        for raw in sorted(set(files.split(b"\x00")) - {b""}):
            relative = raw.decode("utf-8", "replace")
            path = root / relative
            if not path.exists() and not path.is_symlink():
                continue  # A tracked file deleted in the working tree is not being published by CI.
            findings.extend(scan_file(path, relative))
            scanned += 1
    except (OSError, subprocess.CalledProcessError):
        error = "Cannot enumerate or read repository files; scan failed closed."
    ok = not findings and not error
    if args.json_out:
        print(json.dumps({"ok": ok, "scanned": scanned, "findings": findings, "error": error or None},
                         ensure_ascii=True))
    else:
        for finding in findings:
            # JSON quoting also prevents filenames from injecting terminal control sequences.
            print(f"{json.dumps(finding['path'], ensure_ascii=True)}:{finding['line']}: {finding['rule']}")
        print(error or f"Secret scan: {'clean' if ok else 'failed'} ({scanned} files, {len(findings)} findings)")
    return 2 if error else (0 if ok else 1)


if __name__ == "__main__":
    raise SystemExit(main())
