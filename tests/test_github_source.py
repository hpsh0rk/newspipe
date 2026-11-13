"""Public API fixtures exercise the native adapter; no live GitHub acceptance."""
from dataclasses import asdict
import importlib
import json
import os
import unittest
import urllib.error
from unittest.mock import patch

from newspipe import net, observations, source_contract, source_worker

STAMP = 1794564000.0  # 2026-11-13T10:00:00Z


def release(**kw):
    return {"id": 42, "name": "Version 2.0", "tag_name": "v2.0", "body": "Original release notes",
            "html_url": "https://github.com/Owner/Repo/releases/tag/v2.0", "draft": False,
            "prerelease": False, "published_at": "2026-11-13T10:00:00Z",
            "created_at": "2026-11-12T10:00:00Z", "updated_at": "2026-11-13T11:00:00Z",
            "author": {"login": "Alice"}, "target_commitish": "main", **kw}


def event(**kw):
    return {"id": "123", "type": "PushEvent", "public": True,
            "actor": {"login": "Alice"}, "repo": {"name": "Owner/Repo"},
            "created_at": "2026-11-13T10:00:00Z", "payload": {"ref": "refs/heads/main",
            "head": "a" * 40, "before": "b" * 40, "size": 1,
            "commits": [{"sha": "a" * 40, "message": "Original change"}]}, **kw}


class GitHubSourceTests(unittest.TestCase):
    def setUp(self):
        try:
            self.github = importlib.import_module("newspipe.adapters.github")
        except ModuleNotFoundError:
            self.fail("native GitHub adapter has not been implemented")

    def collect(self, rows, cfg=None, **kw):
        def transport(url, **options):
            self.assertTrue(url.startswith("https://api.github.com/"))
            self.assertEqual(options["max_bytes"], 2 * 1024 * 1024)
            self.assertEqual(options["max_redirects"], 0)
            self.assertEqual(options["timeout"], 20)
            options["response_headers"].update({"x-ratelimit-remaining": "59", "x-ratelimit-limit": "60",
                                                 "x-ratelimit-reset": "1794567600", "set-cookie": "SECRET"})
            return json.dumps(rows).encode()
        with patch.object(self.github, "request", side_effect=transport) as call:
            out = self.github.fetch(cfg or {"settings": {"repositories": ["Owner/Repo"]}}, **kw)
        source_contract.normalize_fetch(out, source="s", adapter="github")
        return out, call

    def test_release_keeps_native_identity_original_markdown_and_dates(self):
        out, _ = self.collect([release(body="# Release\n\nOriginal & facts " * 80)])
        item, = out.items
        self.assertRegex(item["ext_id"], r"^github:[0-9a-f]{64}$")
        self.assertEqual(item["content_kind"], "release")
        self.assertEqual(item["metadata"]["native_id"], 42)
        self.assertGreater(len(item["source_text"]), 600)
        self.assertTrue(item["source_text_complete"])
        self.assertEqual(item["pub_ts"], STAMP)
        self.assertEqual(item["updated_ts"], STAMP + 3600)
        self.assertEqual(item["original_url"], "https://github.com/Owner/Repo/releases/tag/v2.0")
        self.assertEqual(item["facts"]["tag"], "v2.0")
        self.assertEqual(item["facts"]["author"], "Alice")

    def test_same_repo_or_tag_url_does_not_collapse_distinct_native_records(self):
        releases, _ = self.collect([release(), release(id=43)])
        events, _ = self.collect([event(), event(id="124")], {"settings": {"users": ["Alice"]}})
        ids = [row["ext_id"] for row in [*releases.items, *events.items]]
        self.assertEqual(len(set(ids)), 4)
        self.assertEqual(events.items[0]["content_kind"], "activity")
        self.assertFalse(events.items[0]["metadata"]["permalink_available"])
        self.assertEqual(events.items[0]["original_url"], "")
        self.assertEqual(events.items[0]["url"], "https://github.com/Owner/Repo")
        self.assertEqual(events.items[0]["facts"]["ref"], "refs/heads/main")
        self.assertIn("Original change", events.items[0]["source_text"])

    def test_release_changes_not_engagement_create_new_observation_versions(self):
        first, _ = self.collect([release(reactions={"total_count": 1})])
        metrics, _ = self.collect([release(reactions={"total_count": 100})])
        changed, _ = self.collect([release(body="Later correction")])
        versions = [observations.prepare_observation("s", "github", result.items[0]).version_hash
                    for result in (first, metrics, changed)]
        self.assertEqual(versions[0], versions[1])
        self.assertNotEqual(versions[0], versions[2])

    def test_updated_release_keeps_old_publication_and_boundary_is_inclusive(self):
        out, _ = self.collect([release()], since=STAMP + 1800)
        self.assertEqual(len(out.items), 1)
        self.assertLess(out.items[0]["pub_ts"], STAMP + 1800)
        same, _ = self.collect([release()], since=STAMP + 3600)
        self.assertEqual(len(same.items), 1, "same-second records remain retry-safe")
        old, _ = self.collect([release()], since=STAMP + 3601)
        self.assertEqual(old.status, "empty")

    def test_unknown_dates_stay_unknown_and_withhold_watermark(self):
        out, _ = self.collect([release(published_at=None, updated_at=None)])
        self.assertIsNone(out.items[0]["pub_ts"])
        self.assertIsNone(out.reports[0].watermark)

    def test_private_events_and_draft_releases_are_not_collected(self):
        out, _ = self.collect([release(draft=True)])
        self.assertEqual(out.status, "empty")
        out, _ = self.collect([event(public=False)], {"settings": {"users": ["Alice"]}})
        self.assertEqual(out.status, "empty")

    def test_comment_event_preserves_message_permalink_separate_from_repository(self):
        body = {"action": "created", "issue": {"number": 7, "title": "A proposal"},
                "comment": {"id": 9, "body": "A different viewpoint",
                            "html_url": "https://github.com/Owner/Repo/issues/7#issuecomment-9"}}
        out, _ = self.collect([event(type="IssueCommentEvent", payload=body)], {"settings": {"users": ["Alice"]}})
        item, = out.items
        self.assertEqual(item["original_url"], body["comment"]["html_url"])
        self.assertEqual(item["source_text"], "A different viewpoint")
        self.assertFalse(item["source_text_complete"])
        self.assertIn({"role": "repository", "url": "https://github.com/Owner/Repo"}, item["referenced_urls"])

    def test_unknown_event_kind_is_retained_without_claiming_complete_evidence(self):
        out, _ = self.collect([event(type="FutureEvent", payload={"SECRET": "unknown"})], {"settings": {"users": ["Alice"]}})
        self.assertEqual(len(out.items), 1)
        self.assertFalse(out.items[0]["source_text_complete"])
        self.assertNotIn("SECRET", json.dumps(out.items))

    def test_unknown_event_does_not_interpret_a_known_payload_shape(self):
        out, _ = self.collect([event(type="FutureEvent", payload={"comment": {
            "body": "Unverified interpretation", "html_url": "https://github.com/Owner/Repo/issues/7#comment-9"}})],
            {"settings": {"users": ["Alice"]}})
        self.assertEqual(len(out.items), 1)
        self.assertEqual(out.items[0]["source_text"], "")
        self.assertEqual(out.items[0]["original_url"], "")
        self.assertNotIn("comment", out.items[0]["facts"])

    def test_release_event_drafts_are_excluded_and_unknown_visibility_fails(self):
        for draft, want in ((True, "empty"), (None, "failure"), ("false", "failure"), (False, "success")):
            with self.subTest(draft=draft):
                nested = release(draft=draft, body="Unpublished notes" if draft else "Published notes")
                out, _ = self.collect([event(type="ReleaseEvent", payload={"action": "published", "release": nested})],
                    {"settings": {"users": ["Alice"]}})
                self.assertEqual(out.status, want)
                if draft is not False:
                    self.assertEqual(out.items, [])

    def test_authenticated_release_collection_proves_repository_is_public_first(self):
        for private, visibility, want in ((True, "private", "failure"), (None, None, "failure"),
                                           (False, "private", "failure"), (False, "public", "success")):
            calls = []
            def transport(url, **options):
                calls.append(url)
                return json.dumps([release(body="Confidential notes")] if "/releases?" in url else
                                  {"private": private, "visibility": visibility}).encode()
            with self.subTest(private=private, visibility=visibility), patch.dict(os.environ, {"NEWSPIPE_TEST_TOKEN": "synthetic-token"}), patch.object(self.github, "request", side_effect=transport):
                out = self.github.fetch({"settings": {"repositories": ["Owner/Repo"], "token_env": "NEWSPIPE_TEST_TOKEN"}})
                self.assertEqual(out.status, want)
                self.assertEqual(calls[0], "https://api.github.com/repos/owner/repo")
                if want == "failure":
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(out.reports[0].code, "permission")
                    self.assertNotIn("Confidential", json.dumps(out.items))

    def test_invalid_nested_or_required_provider_fields_fail_the_subscription(self):
        for row in (release(id=True), release(body={"SECRET": 1}), release(author="bad"),
                    release(author=[]), release(published_at="bad"), release(html_url="http://127.0.0.1/admin"),
                    release(draft="false"), release(tag_name=None)):
            with self.subTest(row=row):
                out, _ = self.collect([row])
                self.assertEqual(out.status, "failure")
                self.assertEqual(out.reports[0].code, "invalid_response")
                self.assertNotIn("SECRET", json.dumps(out.summary()))
        out, _ = self.collect([event(payload={"commits": "bad"})], {"settings": {"users": ["Alice"]}})
        self.assertEqual(out.status, "failure")

    def test_empty_success_still_exposes_safe_quota_and_anonymous_status(self):
        out, _ = self.collect([])
        row = out.summary()["reports"][0]
        self.assertEqual(out.status, "empty")
        self.assertEqual(row["diagnostics"]["credential_mode"], "anonymous")
        self.assertEqual(row["diagnostics"]["rate_remaining"], 59)
        self.assertNotIn("SECRET", json.dumps(out.summary()))

    def test_explicit_missing_token_does_not_fall_back_to_anonymous(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(self.github, "request") as call:
            out = self.github.fetch({"settings": {"users": ["Alice"], "token_env": "NEWSPIPE_TEST_TOKEN"}})
        self.assertEqual(out.reports[0].code, "credentials")
        call.assert_not_called()

    def test_token_is_header_only_and_never_result_or_diagnostics(self):
        with patch.dict(os.environ, {"NEWSPIPE_TEST_TOKEN": "synthetic-secret"}):
            out, call = self.collect([], {"settings": {"users": ["Alice"], "token_env": "NEWSPIPE_TEST_TOKEN"}})
        self.assertEqual(call.call_args.kwargs["headers"]["Authorization"], "Bearer synthetic-secret")
        self.assertNotIn("synthetic-secret", call.call_args.args[0] + json.dumps(out.summary()))
        self.assertEqual(out.summary()["reports"][0]["diagnostics"]["credential_mode"], "token")

    def test_invalid_settings_fail_before_any_request(self):
        for settings in ({}, {"users": ["../private?SECRET"]}, {"repositories": ["https://github.com/a/b"]},
                         {"repositories": ["Owner/.."]},
                         {"users": ["Alice"], "max_pages": 0}, {"users": ["Alice"], "max_pages": 4},
                         {"users": ["Alice"], "max_pages": True}, {"users": ["Alice"], "token_env": "Bearer SECRET"}):
            with self.subTest(settings=settings), patch.object(self.github, "request") as call:
                out = self.github.fetch({"settings": settings})
                self.assertEqual(out.reports[0].code, "configuration")
                call.assert_not_called()

    def test_pagination_is_bounded_and_does_not_follow_provider_link_headers(self):
        pages = []
        def transport(url, **options):
            pages.append(url)
            options["response_headers"]["link"] = '<http://127.0.0.1/private>; rel="next"'
            return json.dumps([release(id=n) for n in range(100)]).encode()
        with patch.object(self.github, "request", side_effect=transport):
            out = self.github.fetch({"settings": {"repositories": ["Owner/Repo"], "max_pages": 2}})
        self.assertEqual(pages, ["https://api.github.com/repos/owner/repo/releases?per_page=100&page=1",
                                 "https://api.github.com/repos/owner/repo/releases?per_page=100&page=2"])
        self.assertIsNone(out.reports[0].watermark)
        self.assertTrue(out.summary()["reports"][0]["diagnostics"]["window_limited"])
        self.assertNotIn("private", json.dumps(out.summary()))

    def test_case_duplicates_are_one_subscription(self):
        out, call = self.collect([], {"settings": {"users": ["Alice", "alice"], "repositories": ["Owner/Repo", "owner/repo"]}})
        self.assertEqual(len(out.reports), 2)
        self.assertEqual(call.call_count, 2)

    def test_partial_failure_retains_completed_sibling_and_failure_quota(self):
        def transport(url, **options):
            if "/alice/" in url:
                return json.dumps([event()]).encode()
            raise urllib.error.HTTPError("SECRET", 403, "SECRET", {"X-RateLimit-Remaining": "0"}, None)
        with patch.object(self.github, "request", side_effect=transport):
            out = self.github.fetch({"settings": {"users": ["Alice", "Bob"]}})
        self.assertEqual(out.status, "partial_failure")
        self.assertEqual(len(out.items), 1)
        self.assertEqual(out.reports[1].code, "rate_limit")
        self.assertEqual(out.summary()["reports"][1]["diagnostics"]["rate_remaining"], 0)
        self.assertNotIn("SECRET", json.dumps(out.summary()))

    def test_page_two_failure_cannot_claim_page_one_as_completed_subscription(self):
        def transport(url, **options):
            if "page=2" in url:
                raise TimeoutError("SECRET")
            return json.dumps([release(id=n) for n in range(100)]).encode()
        with patch.object(self.github, "request", side_effect=transport):
            out = self.github.fetch({"settings": {"repositories": ["Owner/Repo"], "max_pages": 2}})
        self.assertEqual(out.items, [])
        self.assertEqual(out.reports[0].code, "timeout")
        self.assertIsNone(out.reports[0].watermark)

    def test_failures_without_quota_headers_remain_categorical(self):
        for status, want in ((401, "credentials"), (403, "permission"), (404, "permission"), (429, "rate_limit")):
            with self.subTest(status=status), patch.object(self.github, "request", side_effect=
                    urllib.error.HTTPError("SECRET", status, "SECRET", None, None)):
                out = self.github.fetch({"settings": {"users": ["Alice"]}})
                self.assertEqual(out.reports[0].code, want)
                self.assertNotIn("SECRET", json.dumps(out.summary()))


class AcquisitionDiagnosticTests(unittest.TestCase):
    def test_allowlisted_diagnostics_survive_summary_normalize_and_worker(self):
        try:
            report = source_contract.SubscriptionReport("repo:a/b", "empty", diagnostics={
                "credential_mode": "anonymous", "rate_remaining": 0, "window_limited": False})
        except TypeError:
            self.fail("safe optional acquisition diagnostics are not implemented")
        out = source_contract.normalize_fetch(source_contract.FetchResult([], (report,)), source="s", adapter="github")
        safe = source_contract.safe_summary(out.summary())
        self.assertEqual(safe["reports"][0]["diagnostics"]["rate_remaining"], 0)
        frame = json.dumps({"items": [], "reports": [asdict(report)], "reported": True}) + "\n" + '{"done":true}\n'
        decoded = source_worker._decode(frame.encode())
        self.assertEqual(decoded.summary(), out.summary())

    def test_unknown_fields_are_rejected_at_adapter_and_discarded_on_public_read(self):
        try:
            report = source_contract.SubscriptionReport("repo:a/b", "empty", diagnostics={"authorization": "SECRET"})
        except TypeError:
            self.fail("safe optional acquisition diagnostics are not implemented")
        with self.assertRaises(source_contract.SourceContractError):
            source_contract.normalize_fetch(source_contract.FetchResult([], (report,)), source="s", adapter="github")
        old = source_contract.FetchResult([], (source_contract.SubscriptionReport("a", "empty"),)).summary()
        old["reports"][0]["diagnostics"] = {"rate_remaining": 10, "authorization": "SECRET"}
        self.assertNotIn("SECRET", json.dumps(source_contract.safe_summary(old)))

    def test_bounded_http1_captures_headers_without_changing_body_contract(self):
        from test_bounded_transport import upstream
        with upstream() as (base, _):
            headers = {}
            try:
                body = net.request(base + "/small", max_bytes=10, max_redirects=0, response_headers=headers)
            except TypeError:
                self.fail("optional transport response headers are not implemented")
            self.assertEqual(body, b"x" * 10)
            self.assertEqual(headers["content-length"], "10")
