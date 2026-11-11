"""Validated workbench writes and read-only previews use the CLI boundary."""
from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from newspipe import actions, cli, config, edit, inbound, pipeline, policy, state
from test_news_pipeline import _item

TEXT = """# preserved header
chat: oc_test
slots:
  am: '08:15'
policies:
  p:
    mode: digest
    slots: [am]
sources:
  src:
    adapter: rsshub
    fetch: {trigger: poll, interval_min: 10}
    notify: {policy: p, overrides: {quiet_hours: '', min_gap_min: 0}}
    card: {title: 'Original', custom_card_key: yes}
    custom_adapter_key: {nested: [1, 2]}
  # other source must remain byte-preserved
  other:
    adapter: hn
    enabled: false
    fetch: {trigger: manual}
    notify: {policy: p}
"""


class NotificationContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.news = Path(self.tmp.name)
        self.path = self.news / "sources.yaml"
        self.path.write_text(TEXT)

    def command(self, *argv):
        return cli.run_subcommand([*argv, "--json"], self.news).as_dict()

    def test_create_only_rejects_existing_source_and_policy_even_when_dry(self):
        before = self.path.read_bytes()
        for noun, name, body in (("source", "src", '{"adapter":"hn"}'), ("policy", "p", '{"mode":"collect"}')):
            for dry in ([], ["--dry-run"]):
                out = self.command(noun, "set", name, "--from-json", body, "--create-only", *dry)
                self.assertFalse(out["ok"], out)
                self.assertEqual(out["error"]["code"], "E_VALIDATION")
                self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(self.command("policy", "set", "new_policy", "--from-json", '{}', "--create-only")["ok"])

    def test_new_form_translation_requests_atomic_create_only(self):
        for action, form in (("source-save", {"name": ["src"], "f.adapter": ["hn"], "create_only": ["1"]}),
                             ("policy-save", {"name": ["p"], "p.mode": ["collect"], "create_only": ["1"]})):
            out = actions.dispatch(action, form, self.news)
            self.assertFalse(out["ok"], out)
            self.assertEqual(out["error"]["code"], "E_VALIDATION", out)

    def test_snapshot_hash_and_candidate_values_use_the_same_file_bytes(self):
        from newspipe import notification_view, view
        text = self.path.read_text().replace("\n", "\r\n")
        self.path.write_bytes(text.encode())
        self.assertEqual(view.build(self.news)["sources_hash"], edit.file_hash(self.path))
        original_load = config.load
        old_hash = edit.file_hash(self.path)
        def changing_load(*a, **kw):
            cfg = original_load(*a, **kw)
            self.path.write_text(TEXT.replace("Original", "Newer"))
            return cfg
        with patch.object(config, "load", side_effect=changing_load):
            snap = notification_view.snapshot(self.news)
        self.assertEqual(snap["hash"], old_hash)
        self.path.write_text(TEXT)
        old_hash = edit.file_hash(self.path)
        with patch.object(config, "load", side_effect=changing_load):
            out = self.command("source", "show", "src")
        self.assertEqual(out["data"]["hash"], old_hash)
        self.assertEqual(out["data"]["body"]["card"]["title"], "Original")

    def test_failed_phase_reports_durable_changes_truthfully(self):
        def failed(*args, **kwargs):
            state.Store(self.news).append_pending("src", [_item(1)])
            return pipeline.SourceRun(source="src", status="send_failed", note="remote failed")
        with patch.object(pipeline, "notify_source", side_effect=failed):
            out = self.command("notify", "src")
        self.assertFalse(out["ok"])
        self.assertTrue(out["changed"])

    def test_retry_audits_identity_and_uses_append_operation_timestamp(self):
        from datetime import datetime, timedelta
        now = datetime(2026, 11, 11, 10, 16)
        batch = {"source": "src", "digest": "2026-11-11", "slot": "am", "delivery_pending": True,
                 "delivery_id": "op", "notification_attempts": 3, "form": "append_card", "items": [],
                 "send_started_at": (now - timedelta(hours=2)).isoformat(),
                 "remote_attempt_started_at": (now - timedelta(minutes=1)).isoformat()}
        state.Store(self.news).write_batch(batch)
        with patch.object(cli, "_now", return_value=now):
            out = self.command("notification", "retry", "src", "--operation-id", "op")
        self.assertTrue(out["ok"], out)
        from newspipe import events, notification_view
        audit = events.list_events(self.news, days=None, types=["notification_retry"])
        self.assertEqual(audit[0]["payload"]["operation_id"], "op")
        batch["notification_attempts"] = 3
        state.Store(self.news).write_batch(batch)
        self.assertEqual(notification_view.snapshot(self.news, now=now)["sources"][0]["reason"], "retry_exhausted")

    def test_source_patch_preserves_unknown_keys_and_legacy_override_values(self):
        before = self.path.read_text().split("  other:", 1)[1]
        patch_body = {"card": {"title": "Renamed"}}
        out = self.command("source", "patch", "src", "--from-json", json.dumps(patch_body))
        self.assertTrue(out["ok"], out)
        raw = yaml.safe_load(self.path.read_text())["sources"]["src"]
        self.assertEqual(raw["custom_adapter_key"], {"nested": [1, 2]})
        self.assertIn("custom_card_key", raw["card"])
        self.assertEqual(raw["notify"]["overrides"]["quiet_hours"], "")
        self.assertEqual(raw["notify"]["overrides"]["min_gap_min"], 0)
        self.assertEqual(self.path.read_text().split("  other:", 1)[1], before)

    def test_patch_is_idempotent_and_dry_or_conflict_writes_nothing(self):
        body = json.dumps({"enabled": False})
        base = edit.file_hash(self.path)
        before = self.path.read_bytes()
        dry = self.command("source", "patch", "src", "--from-json", body, "--dry-run", "--base-hash", base)
        self.assertTrue(dry["ok"], dry)
        self.assertFalse(dry["changed"])
        self.assertEqual(self.path.read_bytes(), before)
        out = self.command("source", "patch", "src", "--from-json", body, "--base-hash", base)
        self.assertTrue(out["changed"])
        again = self.command("source", "patch", "src", "--from-json", body)
        self.assertTrue(again["ok"], again)
        self.assertFalse(again["changed"])
        conflict = self.command("source", "patch", "src", "--from-json", "{}", "--base-hash", base)
        self.assertEqual(conflict["error"]["code"], "E_CONFLICT")

    def test_policy_crud_reports_all_bindings_and_rejects_referenced_delete(self):
        out = self.command("policy", "set", "p", "--from-json", '{"mode":"immediate","label":"Continuous"}')
        self.assertTrue(out["ok"], out)
        self.assertEqual({s["source"] for s in out["data"]["affected"]}, {"src", "other"})
        before = self.path.read_bytes()
        out = self.command("policy", "remove", "p")
        self.assertEqual(out["error"]["code"], "E_VALIDATION")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(self.command("policy", "set", "copy", "--from-json", '{"mode":"collect"}')["ok"])
        self.assertTrue(self.command("policy", "remove", "copy")["ok"])
        self.assertTrue(self.command("policy", "show", "p")["ok"])
        self.assertTrue(self.command("policy", "list")["ok"])

    def test_global_edit_rejects_referenced_slot_and_preserves_sources_bytes(self):
        before = self.path.read_text().split("sources:", 1)[1]
        denied = self.command("global", "set", "--from-json", '{"slots":{}}')
        self.assertFalse(denied["ok"])
        out = self.command("global", "set", "--from-json", '{"notifications":{"paused":true,"max_cards_per_day":0}}')
        self.assertTrue(out["ok"], out)
        self.assertTrue(self.path.read_text().split("sources:", 1)[1].startswith(before))
        self.assertTrue(config.load(self.news).notifications.paused)

    def test_empty_and_inline_mapping_edits_remain_valid(self):
        self.path.write_text("chat: oc_test\nslots: {}\nsources: {}\npolicies: {}\n")
        self.assertTrue(self.command("policy", "set", "p", "--from-json", "{}")["ok"])
        self.assertTrue(self.command("source", "set", "src", "--from-json", '{"adapter":"hn","fetch":{"trigger":"manual"}}')["ok"])
        self.assertTrue(self.command("source", "remove", "src")["ok"])
        self.assertEqual(config.load(self.news).sources, {})

    def test_preview_validates_candidate_without_network_model_or_state_writes(self):
        store = state.Store(self.news)
        store.append_pending("src", [_item(1)])
        before = {str(p.relative_to(self.news)): p.read_bytes() for p in self.news.rglob("*") if p.is_file()}
        with patch.object(pipeline, "_adapter", side_effect=AssertionError("preview fetched")), \
             patch.object(pipeline, "_enrich_new", side_effect=AssertionError("preview enriched")), \
             patch.object(store, "note_card", side_effect=AssertionError("preview counted")):
            out = self.command("notification", "preview", "--source", "src", "--from-json",
                               '{"policies":{"p":{"mode":"collect"}}}')
        self.assertTrue(out["ok"], out)
        self.assertFalse(out["changed"])
        row = out["data"]["sources"][0]
        self.assertEqual(row["after"]["values"]["mode"], "collect")
        self.assertEqual(row["pending"], 1)
        self.assertIn("consum", row["consequence"])
        after = {str(p.relative_to(self.news)): p.read_bytes() for p in self.news.rglob("*") if p.is_file()}
        self.assertEqual(after, before)

    def test_source_patch_action_preserves_legacy_settings_until_explicit_migration(self):
        out = actions.dispatch("source-patch", {"name": ["src"], "f.feed_label": ["Changed"],
             "notify_policy": ["p"]}, self.news)
        self.assertTrue(out["ok"], out)
        src = config.load(self.news).sources["src"]
        self.assertEqual(src.notify.overrides["quiet_hours"], "")
        self.assertEqual(src.notify.overrides["min_gap_min"], 0)
        self.assertIn("custom_adapter_key", src.extra)

    def test_invalid_numeric_form_returns_envelope_not_internal_error(self):
        out = actions.dispatch("source-patch", {"name": ["src"], "f.fetch.interval_min": ["abc"]}, self.news)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "E_USAGE")
        self.assertIn("fetch.interval_min", out["error"]["message"])

    def test_empty_custom_notification_numbers_reject_without_writing(self):
        before = self.path.read_bytes()
        for key in ("max_cards_per_day", "min_gap_min"):
            for raw in (None, [], [""], ["  "]):
                for action in ("source-patch", "source-save", "notification-preview"):
                    with self.subTest(key=key, raw=raw, action=action):
                        form = {"name": ["src"], "f.adapter": ["hn"],
                                "f.fetch.trigger": ["manual"], "notify_policy": ["p"],
                                f"inherit.{key}": ["custom"]}
                        if raw is not None:
                            form[f"n.{key}"] = raw
                        out = actions.dispatch(action, form, self.news)
                        self.assertFalse(out["ok"], out)
                        self.assertEqual(out["error"]["code"], "E_USAGE")
                        self.assertIn("覆盖", out["error"]["message"])
                        self.assertEqual(self.path.read_bytes(), before)

    def test_zero_custom_numbers_and_empty_non_numbers_remain_explicit(self):
        form = {"name": ["src"], "notify_policy": ["p"],
                "inherit.mode": ["custom"], "n.mode": ["collect"],
                "inherit.slots": ["custom"], "n.slots": [""],
                "inherit.quiet_hours": ["custom"], "n.quiet_hours": [""],
                "inherit.max_cards_per_day": ["custom"], "n.max_cards_per_day": ["0"],
                "inherit.min_gap_min": ["custom"], "n.min_gap_min": ["0"]}
        out = actions.dispatch("source-patch", form, self.news)
        self.assertFalse(out["ok"], out)
        self.assertTrue(self.command("notification", "migrate")["ok"])
        binding = config.load(self.news).sources["src"].notify
        form = {"name": ["src"], "notify_policy": [binding.policy], "f.feed_label": ["New label"]}
        out = actions.dispatch("source-patch", form, self.news)
        self.assertTrue(out["ok"], out)
        self.assertEqual(config.load(self.news).sources["src"].notify.overrides, {})
        cfg = config.load(self.news)
        self.assertEqual(cfg.policies[binding.policy].quiet_hours, "")
        self.assertEqual(cfg.policies[binding.policy].min_gap_min, 0)

    def test_source_numeric_clearing_restores_defaults_but_omission_preserves(self):
        seed = {"fetch": {"interval_min": 30}, "filter": {"min_items": 2, "max_items": 5},
                "enrich": {"max_body_chars": 900, "fetch_body": True},
                "deliver": {"budget": {"max_cards_per_day": 4, "min_gap_min": 10}}}
        self.assertTrue(self.command("source", "patch", "src", "--from-json", json.dumps(seed))["ok"])
        other = self.path.read_text().split("  other:", 1)[1]
        form = {"name": ["src"], "f.feed_label": ["Only label"]}
        self.assertTrue(actions.dispatch("source-patch", form, self.news)["ok"])
        values = config.source_values(config.load(self.news).sources["src"])
        old = {"fetch.interval_min": 30, "filter.min_items": 2, "filter.max_items": 5,
               "enrich.max_body_chars": 900, "deliver.budget.max_cards_per_day": 4,
               "deliver.budget.min_gap_min": 10}
        for key, value in old.items():
            self.assertEqual(values[key], value, key)
        form = {"name": ["src"], **{f"f.{key}": [""] for key in old}}
        out = actions.dispatch("source-patch", form, self.news)
        self.assertTrue(out["ok"], out)
        self.assertTrue(out["changed"], out)
        values = config.source_values(config.load(self.news).sources["src"])
        defaults = {"fetch.interval_min": 0.0, "filter.min_items": 1, "filter.max_items": 13,
                    "enrich.max_body_chars": 6000, "deliver.budget.max_cards_per_day": 12,
                    "deliver.budget.min_gap_min": 0.0}
        raw = yaml.safe_load(self.path.read_text())["sources"]["src"]
        for key, value in defaults.items():
            self.assertEqual(values[key], value, key)
            cursor = raw
            parts = key.split(".")
            for part in parts[:-1]:
                cursor = cursor[part]
            self.assertNotIn(parts[-1], cursor, key)
        self.assertTrue(values["enrich.fetch_body"])
        self.assertEqual(raw["custom_adapter_key"], {"nested": [1, 2]})
        self.assertIn("custom_card_key", raw["card"])
        self.assertEqual(self.path.read_text().split("  other:", 1)[1], other)

    def test_numeric_clearing_dry_run_and_conflict_preserve_bytes(self):
        form = {"name": ["src"], "f.fetch.interval_min": [""], "dry": ["1"],
                "base_hash": [edit.file_hash(self.path)]}
        before = self.path.read_bytes()
        out = actions.dispatch("source-patch", form, self.news)
        self.assertTrue(out["ok"], out)
        self.assertFalse(out["changed"])
        self.assertEqual(self.path.read_bytes(), before)
        form.pop("dry")
        form["base_hash"] = ["stale"]
        out = actions.dispatch("source-patch", form, self.news)
        self.assertEqual(out["error"]["code"], "E_CONFLICT")
        self.assertEqual(self.path.read_bytes(), before)

    def test_numeric_clear_preview_and_save_predict_the_same_defaults(self):
        self.assertTrue(self.command("notification", "migrate")["ok"])
        self.assertTrue(self.command("source", "patch", "src", "--from-json",
            '{"notify":null,"filter":{"max_items":5},"deliver":{"budget":{"max_cards_per_day":4}}}',
            "--confirm-conversion")["ok"])
        state.Store(self.news).append_pending("src", [_item(i) for i in range(14)])
        form = {"name": ["src"], "f.filter.max_items": [""],
                "f.deliver.budget.max_cards_per_day": [""]}
        before = self.path.read_bytes()
        preview = actions.dispatch("notification-preview", form, self.news)
        self.assertTrue(preview["ok"], preview)
        self.assertEqual(preview["data"]["sources"][0]["plan"]["planned"], 13)
        self.assertEqual(preview["data"]["sources"][0]["after"]["values"]["max_cards_per_day"], 12)
        self.assertEqual(self.path.read_bytes(), before)
        saved = actions.dispatch("source-patch", form, self.news)
        self.assertTrue(saved["ok"], saved)
        actual = self.command("notification", "preview", "--source", "src")
        self.assertEqual(actual["data"]["sources"][0]["plan"], preview["data"]["sources"][0]["plan"])
        self.assertEqual(actual["data"]["sources"][0]["after"], preview["data"]["sources"][0]["after"])

    def test_blank_create_uses_defaults_and_patch_zero_remains_explicit(self):
        form = {"name": ["fresh"], "f.adapter": ["hn"], "f.fetch.trigger": ["manual"],
                "f.fetch.interval_min": [""], "f.filter.max_items": [""]}
        out = actions.dispatch("source-save", form, self.news)
        self.assertTrue(out["ok"], out)
        values = config.source_values(config.load(self.news).sources["fresh"])
        self.assertEqual(values["fetch.interval_min"], 0.0)
        self.assertEqual(values["filter.max_items"], 13)
        raw = yaml.safe_load(self.path.read_text())["sources"]["fresh"]
        self.assertNotIn("interval_min", raw["fetch"])
        self.assertNotIn("filter", raw)
        form = {"name": ["src"], "f.fetch.interval_min": ["0"], "f.filter.min_items": ["0"],
                "f.deliver.budget.max_cards_per_day": ["0"], "f.deliver.budget.min_gap_min": ["0"]}
        self.assertTrue(actions.dispatch("source-patch", form, self.news)["ok"])
        raw = yaml.safe_load(self.path.read_text())["sources"]["src"]
        self.assertEqual(raw["fetch"]["interval_min"], 0)
        self.assertEqual(raw["filter"]["min_items"], 0)
        self.assertEqual(raw["deliver"]["budget"], {"max_cards_per_day": 0, "min_gap_min": 0})

    def test_separate_collect_and_notify_commands_use_distinct_pipeline_entrypoints(self):
        run = pipeline.SourceRun(source="src", status="collected")
        with patch.object(pipeline, "collect_source", return_value=run) as collect, \
             patch.object(pipeline, "notify_source", return_value=run) as notify:
            self.assertTrue(self.command("collect", "src", "--dry")["ok"])
            collect.assert_called_once()
            notify.assert_not_called()
            self.assertTrue(self.command("notify", "src", "--bypass-clock", "--dry")["ok"])
            self.assertTrue(notify.call_args.kwargs["bypass_clock"])

    def test_new_contracts_are_discoverable(self):
        names = {c["name"] for c in self.command("api", "describe")["data"]["commands"]}
        self.assertTrue({"source patch", "policy list", "policy set", "policy remove", "global set",
                         "notification preview", "collect", "notify", "notification retry"} <= names)

    def test_new_contract_examples_include_required_arguments_and_parse(self):
        import shlex
        rows = self.command("api", "describe")["data"]["commands"]
        names = {"source patch", "source copy", "policy set", "policy show", "policy remove", "global set", "notification retry", "collect", "notify"}
        for row in rows:
            if row["name"] not in names:
                continue
            for example in row["examples"]:
                args = cli._build_sub_parser().parse_args(shlex.split(example)[1:])
                if row["name"] in {"policy set", "source patch", "global set"}:
                    self.assertTrue(args.from_json, example)
                if row["name"] == "notification retry":
                    self.assertTrue(args.operation_id, example)
                if row["name"] != "global set":
                    self.assertTrue(getattr(args, "name", None) or getattr(args, "source", None), example)

    def test_policy_preview_matches_replace_save_not_merge_of_old_values(self):
        self.command("policy", "set", "p", "--from-json", '{"quiet_hours":"23:00-07:30","min_gap_min":10}')
        preview = self.command("notification", "preview", "--source", "src", "--from-json",
                               '{"policies":{"p":{"mode":"immediate"}}}')
        self.assertTrue(preview["ok"], preview)
        self.assertEqual(preview["data"]["policies"]["p"]["values"]["quiet_hours"], "")

    def test_safe_retry_needs_matching_identity_and_blocks_expired_remote_outcome(self):
        store = state.Store(self.news)
        batch = {"source": "src", "digest": "2026-11-11", "slot": "am", "delivery_pending": True,
                 "delivery_id": "op", "notification_attempts": 3, "notification_retry_after": 100,
                 "form": "card", "items": [], "delivery_chat": "oc_test", "card_id": "card"}
        store.write_batch(batch)
        bad = self.command("notification", "retry", "src", "--operation-id", "other")
        self.assertEqual(bad["error"]["code"], "E_CONFLICT")
        dry = self.command("notification", "retry", "src", "--operation-id", "op", "--dry-run")
        self.assertTrue(dry["ok"], dry)
        self.assertFalse(dry["changed"])
        self.assertEqual(store.unfinished_batch("src")["notification_attempts"], 3)
        out = self.command("notification", "retry", "src", "--operation-id", "op")
        self.assertTrue(out["ok"], out)
        self.assertTrue(out["changed"])
        self.assertEqual(store.unfinished_batch("src")["delivery_id"], "op")
        batch.update(send_started_at="2026-01-01T00:00:00", send_uuid="keep", notification_attempts=3)
        store.write_batch(batch)
        expired = self.command("notification", "retry", "src", "--operation-id", "op")
        self.assertEqual(expired["error"]["code"], "E_VALIDATION")
        self.assertEqual(store.unfinished_batch("src")["send_uuid"], "keep")

    def test_path_like_source_ids_are_rejected_before_processing(self):
        out = self.command("source", "set", "../escape", "--from-json", '{"adapter":"hn","fetch":{"trigger":"manual"}}')
        self.assertFalse(out["ok"], out)
        self.assertEqual(out["error"]["code"], "E_VALIDATION")

    def test_dynamic_acquisition_slots_are_accepted_by_action_and_cli(self):
        self.assertEqual(actions.ACTIONS["run"].argv({"target": ["slot:evening"]}), ["run", "--slot", "evening"])

    def test_copied_sources_preserve_extensions_but_start_disabled_and_reject_collision(self):
        self.assertTrue(self.command("notification", "migrate")["ok"])
        out = self.command("source", "copy", "src", "--new-name", "copy")
        self.assertTrue(out["ok"], out)
        copied = config.load(self.news).sources["copy"]
        self.assertFalse(copied.enabled)
        self.assertIn("custom_adapter_key", copied.extra)
        denied = self.command("source", "copy", "src", "--new-name", "other")
        self.assertFalse(denied["ok"])

    def test_binding_conversion_requires_explicit_confirmation(self):
        body = '{"notify":{"policy":"p"}}'
        denied = self.command("source", "patch", "other", "--from-json", '{"notify":null}')
        self.assertEqual(denied["error"]["code"], "E_VALIDATION")
        self.assertTrue(self.command("source", "patch", "other", "--from-json", '{"notify":null}', "--confirm-conversion")["ok"])
        self.assertFalse(self.command("source", "patch", "other", "--from-json", body)["ok"])
        self.assertTrue(self.command("source", "patch", "other", "--from-json", body, "--confirm-conversion")["ok"])

    def test_empty_patch_preserves_original_comments_and_reports_no_change(self):
        before = self.path.read_bytes()
        out = self.command("source", "patch", "src", "--from-json", "{}")
        self.assertTrue(out["ok"])
        self.assertFalse(out["changed"])
        self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_preview_candidate_is_a_validation_envelope(self):
        out = self.command("notification", "preview", "--from-json", '{"policies":{"p":{"mode":"bad"}}}')
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "E_VALIDATION")

    def test_unconfirmed_or_ambiguous_web_deletion_never_writes(self):
        self.assertTrue(self.command("policy", "set", "spare", "--from-json", '{}')["ok"])
        before = self.path.read_bytes()
        for action, name in (("source-remove", "src"), ("policy-remove", "spare")):
            for confirmation in (None, [], [""], ["0"], ["false"], ["on"], ["true"],
                                 ["forged"], [" 1 "], ["1", "0"], ["1", "1"]):
                with self.subTest(action=action, confirmation=confirmation):
                    form = {"name": [name]}
                    if confirmation is not None:
                        form["confirm_delete"] = confirmation
                    out = actions.dispatch(action, form, self.news)
                    self.assertFalse(out["ok"], out)
                    self.assertEqual(out["error"]["code"], "E_USAGE")
                    self.assertIn("confirm_delete", out["error"]["message"])
                    self.assertEqual(self.path.read_bytes(), before)

    def test_confirmed_web_deletion_preserves_cli_dry_conflict_and_reference_guards(self):
        self.assertTrue(self.command("policy", "set", "spare", "--from-json", '{}')["ok"])
        before = self.path.read_bytes()
        denied = actions.dispatch("policy-remove", {"name": ["p"], "confirm_delete": ["1"]}, self.news)
        self.assertEqual(denied["error"]["code"], "E_VALIDATION")
        self.assertEqual(self.path.read_bytes(), before)
        for action, name in (("source-remove", "src"), ("policy-remove", "spare")):
            form = {"name": [name], "confirm_delete": ["1"], "dry": ["1"],
                    "base_hash": [edit.file_hash(self.path)]}
            before = self.path.read_bytes()
            dry = actions.dispatch(action, form, self.news)
            self.assertTrue(dry["ok"], dry)
            self.assertFalse(dry["changed"])
            self.assertEqual(self.path.read_bytes(), before)
            form.pop("dry")
            form["base_hash"] = ["stale"]
            conflict = actions.dispatch(action, form, self.news)
            self.assertEqual(conflict["error"]["code"], "E_CONFLICT")
            self.assertEqual(self.path.read_bytes(), before)
            form["base_hash"] = [edit.file_hash(self.path)]
            out = actions.dispatch(action, form, self.news)
            self.assertTrue(out["ok"], out)
            self.assertTrue(out["changed"])
            form.pop("base_hash")
            again = actions.dispatch(action, form, self.news)
            self.assertEqual(again["error"]["code"], "E_NOT_FOUND")
        cfg = config.load(self.news)
        self.assertNotIn("src", cfg.sources)
        self.assertNotIn("spare", cfg.policies)
        self.assertIn("other", cfg.sources)
        self.assertIn("p", cfg.policies)


class NotificationHttpTests(unittest.TestCase):
    setUp = NotificationContractTests.setUp

    def serve(self):
        server, _ = inbound.start_http(service_cfg={"http": {"host": "127.0.0.1", "port": 0}},
                                       creds=None, news_dir=self.news, accept_events=False,
                                       view_actions=True, action_token="test-token")
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def post(self, url, *, token="test-token", origin=None, json_accept=True, extra=None,
             action="source-patch"):
        values = {"name": "src", "f.feed_label": "Saved", "token": token,
                  "return_to": "/config"}
        values.update(extra or {})
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if json_accept:
            headers["Accept"] = "application/json"
        if origin:
            headers["Origin"] = origin
        request = urllib.request.Request(url + "/api/actions/" + action,
            data=urllib.parse.urlencode(values, doseq=True).encode(), headers=headers)
        return urllib.request.urlopen(request)

    def test_http_delete_requires_exact_confirmation_after_security_gates(self):
        self.assertTrue(cli.run_subcommand(["policy", "set", "spare", "--from-json", '{}', "--json"], self.news).ok)
        url = self.serve()
        before = self.path.read_bytes()
        for action, name in (("source-remove", "src"), ("policy-remove", "spare")):
            for confirmation in (None, "", "0", "false", "on", "forged", ["1", "0"], ["1", "1"]):
                extra = {"name": name}
                if confirmation is not None:
                    extra["confirm_delete"] = confirmation
                with self.subTest(action=action, confirmation=confirmation), self.post(
                        url, action=action, origin=url, extra=extra) as response:
                    out = json.load(response)
                    self.assertFalse(out["ok"], out)
                    self.assertEqual(out["error"]["code"], "E_USAGE")
                    self.assertEqual(self.path.read_bytes(), before)
            for token, origin in (("wrong", url), ("test-token", "https://evil.example")):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    self.post(url, action=action, token=token, origin=origin,
                              extra={"name": name, "confirm_delete": "1"})
                self.assertEqual(caught.exception.code, 403)
                self.assertEqual(self.path.read_bytes(), before)
            with self.post(url, action=action, origin=url,
                           extra={"name": name, "confirm_delete": "1", "dry": "1"}) as response:
                self.assertTrue(json.load(response)["ok"])
                self.assertEqual(self.path.read_bytes(), before)

    def test_json_action_uses_same_envelope_and_security_gates(self):
        url = self.serve()
        with self.post(url, origin=url) as response:
            self.assertEqual(response.status, 200)
            out = json.load(response)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["command"], "source.patch")
        self.assertIn("hash", out["data"])
        for token, origin in (("wrong", url), ("test-token", "https://evil.example")):
            with self.subTest(token=token, origin=origin), self.assertRaises(urllib.error.HTTPError) as caught:
                self.post(url, token=token, origin=origin)
            self.assertEqual(caught.exception.code, 403)

    def test_no_script_failure_retains_inputs_and_old_hash_without_url_draft(self):
        url = self.serve()
        before = (self.news / "sources.yaml").read_bytes()
        with self.post(url, json_accept=False, extra={"f.feed_label": '<edited & retained>', "base_hash": "stale"}) as response:
            address = response.url
            page = response.read().decode()
        self.assertIn('value="&lt;edited &amp; retained&gt;"', page)
        self.assertIn('name="base_hash" value="stale"', page)
        self.assertNotIn("edited", address)
        self.assertNotIn("stale", address)
        self.assertEqual((self.news / "sources.yaml").read_bytes(), before)
        again = urllib.request.urlopen(address).read().decode()
        self.assertNotIn('value="&lt;edited &amp; retained&gt;"', again)

    def test_html_action_redirects_to_own_allowlisted_page(self):
        url = self.serve()
        with self.post(url, json_accept=False) as response:
            self.assertTrue(response.url.startswith(url + "/config?draft="), response.url)
        with self.post(url, json_accept=False, extra={"return_to": "https://evil.example"}) as response:
            self.assertTrue(response.url.startswith(url + "/?draft="), response.url)
