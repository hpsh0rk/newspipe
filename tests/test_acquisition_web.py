"""Acquisition clocks keep stable references and use the existing atomic writer."""
import json
import unittest
import urllib.parse
import urllib.request

import yaml
from newspipe import actions, config, policy
from preview_workbench import offline_workbench
from test_chat_web import values


class AcquisitionWebTests(unittest.TestCase):
    def test_unbound_scheduled_sends_warn_but_local_only_and_inactive_refs_do_not(self):
        with offline_workbench() as fixture:
            path = fixture.news_dir / "sources.yaml"
            for trigger, form, enabled, expected in (("slot", "card", True, True),
                                                    ("slot", "append_card", False, True),
                                                    ("slot", "state_only", True, False),
                                                    ("poll", "card", True, False),
                                                    ("manual", "card", True, False)):
                with self.subTest(trigger=trigger, form=form, enabled=enabled):
                    raw = yaml.safe_load(path.read_text())
                    source = raw["sources"]["products"]
                    source.pop("notify", None)
                    source["fetch"] = {"trigger": trigger, "slots": ["am"], "interval_min": 10}
                    source["deliver"] = {"form": form}
                    source["enabled"] = enabled
                    path.write_text(yaml.safe_dump(raw, sort_keys=False))
                    text = self.get(fixture)
                    self.assertEqual("修改也会改变旧发送时刻" in text, expected)
                    if expected:
                        self.assertIn('href="/notifications">查看迁移</a>', text)
                    self.assertIn("products", text)

    def get(self, fixture, route="/config?acquisition=1"):
        with urllib.request.urlopen(fixture.url + route) as response:
            return response.read().decode()

    def post(self, fixture, fields, *, as_json=True):
        request = urllib.request.Request(fixture.url + "/api/actions/global-save",
            data=urllib.parse.urlencode(fields, doseq=True).encode(),
            headers={"Origin": fixture.url, **({"Accept": "application/json"} if as_json else {})})
        with urllib.request.urlopen(request) as response:
            return response.url, json.load(response) if as_json else response.read().decode()

    def test_editor_is_on_source_page_with_hidden_keys_and_no_spare_rows(self):
        with offline_workbench() as fixture:
            text = self.get(fixture)
            self.assertTrue('data-editor-panel="acquisition"' in text, "Missing acquisition dialog")
            self.assertNotIn("采集计划标识", text)
            fields = values(text)
            self.assertEqual(fields["slot_id"], ["am", "pm"])
            self.assertEqual(fields["slot_time"], ["08:15", "18:35"])
            self.assertEqual(fields["global_scope"], ["acquisition"])
            self.assertNotIn("chat", fields)
            self.assertIn("24 小时制", text)
            self.assertIn("products", text)
            global_text = self.get(fixture, "/notifications?global=1")
            self.assertNotIn('name="slot_id"', global_text)
            self.assertIn('/config?acquisition=1', global_text)

    def test_changed_clock_preserves_identity_policy_times_and_collision_reservation(self):
        with offline_workbench() as fixture:
            fields = values(self.get(fixture))
            fields.update(slot_id=["am", "pm", "", "at_1000"], slot_time=["09:00", "18:35", "10:00", "11:00"])
            fields.pop("dry", None)
            _, out = self.post(fixture, fields)
            self.assertTrue(out["ok"], out)
            cfg = config.load(fixture.news_dir)
            self.assertEqual(cfg.slots["am"], "09:00")
            self.assertEqual(cfg.slots["at_1000_2"], "10:00")
            self.assertEqual(cfg.sources["products"].fetch.slots, ("am",))
            self.assertEqual(policy.sending_times(cfg, cfg.policies["digest"].values())["am"], "08:15")
            _, conflict = self.post(fixture, fields)
            self.assertEqual(conflict["error"]["code"], "E_CONFLICT")

    def test_scopes_and_referenced_removal_cannot_write_unrelated_fields(self):
        with offline_workbench() as fixture:
            fields = values(self.get(fixture))
            before = (fixture.news_dir / "sources.yaml").read_bytes()
            for extra in ({"chat": ["oc_other"]}, {"g.paused": ["1"]}, {"slot_id": [], "slot_time": []}):
                _, out = self.post(fixture, {**fields, **extra})
                self.assertFalse(out["ok"], out)
            globals_form = values(self.get(fixture, "/notifications?global=1"))
            _, out = self.post(fixture, {**globals_form, "slots_present": ["1"]})
            self.assertFalse(out["ok"], out)
            self.assertEqual((fixture.news_dir / "sources.yaml").read_bytes(), before)

    def test_no_js_add_remove_and_failure_keep_complete_rows_and_original_hash(self):
        with offline_workbench() as fixture:
            fields = values(self.get(fixture))
            old_hash = fields["base_hash"]
            _, text = self.post(fixture, {**fields, "editor_intent": ["add-slot"]}, as_json=False)
            fields = values(text)
            self.assertEqual(fields["slot_id"], ["am", "pm", ""])
            fields["slot_time"][2] = "14:00"
            _, text = self.post(fixture, {**fields, "editor_intent": ["add-slot"]}, as_json=False)
            fields = values(text)
            fields["slot_time"][3] = "bad"
            url, text = self.post(fixture, fields, as_json=False)
            self.assertIn("/config?draft=", url)
            restored = values(text)
            self.assertEqual(restored["slot_time"], fields["slot_time"])
            self.assertEqual(restored["base_hash"], old_hash)
            _, text = self.post(fixture, {**restored, "editor_intent": ["remove-slot:3"]}, as_json=False)
            self.assertEqual(values(text)["slot_time"], ["08:15", "18:35", "14:00"])
            self.assertEqual(config.load(fixture.news_dir).slots, {"am": "08:15", "pm": "18:35"})

    def test_empty_all_removed_dry_run_stays_empty_and_can_add_without_js(self):
        with offline_workbench() as fixture:
            path = fixture.news_dir / "sources.yaml"
            raw = yaml.safe_load(path.read_text())
            raw["sources"]["products"]["fetch"] = {"trigger": "manual", "slots": []}
            path.write_text(yaml.safe_dump(raw))
            fields = values(self.get(fixture))
            fields.pop("slot_id", None)
            fields.pop("slot_time", None)
            _, text = self.post(fixture, fields, as_json=False)
            restored = values(text)
            self.assertNotIn("slot_time", restored)
            self.assertIn("尚未设置采集时间", text)
            _, text = self.post(fixture, {**restored, "editor_intent": ["add-slot"]}, as_json=False)
            self.assertEqual(values(text)["slot_time"], [""])

    def test_legacy_equal_clocks_and_disabled_refs_are_visible_and_preserved(self):
        with offline_workbench() as fixture:
            path = fixture.news_dir / "sources.yaml"
            raw = yaml.safe_load(path.read_text())
            raw["slots"]["pm"] = "08:15"
            raw["policies"]["digest"].pop("times")
            raw["policies"]["digest"]["slots"] = ["am", "pm"]
            raw["sources"]["products"]["enabled"] = False
            raw["sources"]["products"]["future"] = {"keep": "yes"}
            path.write_text(yaml.safe_dump(raw))
            state_before = {p: p.read_bytes() for p in fixture.news_dir.rglob("*") if p.is_file() and p != path}
            text = self.get(fixture)
            self.assertIn("旧通知策略", text)
            self.assertIn("已停用", text)
            fields = values(text)
            fields["slot_time"] = ["09:00", "08:15"]
            fields.pop("dry", None)
            _, out = self.post(fixture, fields)
            self.assertTrue(out["ok"], out)
            cfg = config.load(fixture.news_dir)
            self.assertEqual(cfg.policies["digest"].slots, ("am", "pm"))
            self.assertEqual(cfg.policies["digest"].times, {})
            self.assertEqual(yaml.safe_load(path.read_text())["sources"]["products"]["future"], {"keep": "yes"})
            self.assertEqual({p: p.read_bytes() for p in state_before}, state_before)

    def test_draft_intents_reject_wrong_scope_and_out_of_bounds(self):
        with offline_workbench() as fixture:
            fields = values(self.get(fixture))
            for changes in ({"editor_intent": ["remove-slot:90"]}, {"editor_intent": ["unknown"]},
                            {"global_scope": ["notifications"], "editor_intent": ["add-slot"]}):
                _, out = self.post(fixture, {**fields, **changes})
                self.assertFalse(out["ok"], out)
            self.assertEqual(fixture.group_calls, [])
