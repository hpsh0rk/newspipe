"""Group selection is a read-only, gated enhancement with complete no-JS drafts."""
from html.parser import HTMLParser
import json
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest.mock import patch
from types import SimpleNamespace

from newspipe import backends, config, runtime, view
from newspipe.chats import DirectoryError
from preview_workbench import offline_workbench


class GlobalForm(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values, self.active, self.select = {}, False, None
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.active = attrs.get("action") == "/api/actions/global-save"
        if not self.active:
            return
        if tag == "input" and attrs.get("name") and "disabled" not in attrs:
            if attrs.get("type") not in ("checkbox", "radio") or "checked" in attrs:
                self.values.setdefault(attrs["name"], []).append(attrs.get("value", ""))
        if tag == "select" and "disabled" not in attrs:
            self.select = attrs.get("name")
        if tag == "option" and self.select and "selected" in attrs:
            self.values[self.select] = [attrs.get("value", "")]
    def handle_endtag(self, tag):
        if tag == "select":
            self.select = None
        if tag == "form":
            self.active = False


def values(text):
    parsed = GlobalForm()
    parsed.feed(text)
    return parsed.values


class ChatWebTests(unittest.TestCase):
    def test_directory_capacity_is_explicit_even_when_provider_has_no_more_pages(self):
        from newspipe.webchats import DirectoryPages
        previous = {"groups": [{"chat_id": "oc_" + str(i), "name": "Group", "external": False} for i in range(995)],
                    "has_more": True, "page_token": "next", "loaded": True}
        data = {"groups": [{"chat_id": "oc_" + str(i), "name": "Group", "external": False} for i in range(995, 1005)],
                "has_more": False, "page_token": ""}
        state = DirectoryPages().finish({"chat_load": ["more"]}, {"ok": True, "data": data}, previous)
        self.assertEqual(len(state["groups"]), 1000)
        self.assertTrue(state["limited"])

    def get(self, fixture, route="/notifications?global=1"):
        with urllib.request.urlopen(fixture.url + route) as response:
            return response.read().decode()

    def post(self, fixture, fields, *, json_response=False, origin=None):
        request = urllib.request.Request(fixture.url + "/api/actions/chat-list", data=urllib.parse.urlencode(fields, doseq=True).encode(),
            headers={"Origin": origin or fixture.url, **({"Accept": "application/json"} if json_response else {})})
        with urllib.request.urlopen(request) as response:
            return (response.url, json.load(response) if json_response else response.read().decode())

    def test_initial_select_preserves_target_and_never_loads_groups(self):
        with offline_workbench() as fixture:
            text = self.get(fixture)
            self.assertIn("data-chat-picker", text)
            self.assertIn('<select name="chat"', text)
            self.assertEqual(values(text)["chat"], ["oc_offline_preview"])
            self.assertIn("名称未加载", text)
            self.assertEqual(fixture.group_calls, [])
            self.assertEqual(fixture.blocked_effects, [])

    def test_no_js_read_retains_unsaved_values_hash_and_does_not_repeat_on_refresh(self):
        with offline_workbench() as fixture:
            fields = values(self.get(fixture))
            fields.update({"chat": ["oc_unsaved"], "g.min_gap_min": ["broken-draft"], "g.paused": ["0", "1"]})
            fields.pop("dry", None)
            before = (fixture.news_dir / "sources.yaml").read_bytes()
            url, text = self.post(fixture, fields)
            self.assertIn("?draft=", url)
            self.assertNotIn("oc_unsaved", url)
            actual = values(text)
            for name in ("chat", "base_hash", "g.min_gap_min", "g.paused"):
                self.assertEqual(actual[name], fields[name])
            self.assertIn("我的阅读群", text)
            self.assertIn("只读群列表", text)
            self.assertNotIn("已应用", text)
            urllib.request.urlopen(url).close()
            self.assertEqual(len(fixture.group_calls), 1)
            self.assertEqual((fixture.news_dir / "sources.yaml").read_bytes(), before)

    def test_more_pages_merge_and_manual_mode_does_not_query(self):
        with offline_workbench() as fixture:
            _, text = self.post(fixture, values(self.get(fixture)))
            fields = values(text)
            fields["chat_load"] = ["more"]
            fields["page_token"] = ["offline-next"]
            _, text = self.post(fixture, fields)
            self.assertIn("我的阅读群", text)
            self.assertIn("周末读书", text)
            self.assertEqual(fixture.group_calls, ["", "offline-next"])
            fields = values(text)
            fields["editor_intent"] = ["manual-chat"]
            _, text = self.post(fixture, fields)
            self.assertIn('data-chat-manual name="chat"', text)
            self.assertEqual(len(fixture.group_calls), 2)
            self.assertEqual(values(text)["chat"], ["oc_offline_preview"])

    def test_lookup_failure_preserves_target_and_shows_permission_not_empty_success(self):
        with offline_workbench() as fixture:
            fields = values(self.get(fixture))
            with patch.object(runtime, "activate", side_effect=lambda *_: backends.use_channel(SimpleNamespace(
                    list_chats=lambda **_: (_ for _ in ()).throw(DirectoryError("缺少群权限", code="E_CONFIG", hint="开通 im:chat:read"))))):
                _, text = self.post(fixture, fields)
            self.assertIn("缺少群权限", text)
            self.assertIn("im:chat:read", text)
            self.assertNotIn("机器人还没有加入任何群", text)
            self.assertEqual(values(text)["chat"], fields["chat"])

    def test_forged_more_cursor_and_gates_reject_before_directory(self):
        with offline_workbench() as fixture:
            fields = values(self.get(fixture))
            _, envelope = self.post(fixture, {**fields, "chat_load": ["more"], "page_token": ["forged"]}, json_response=True)
            self.assertFalse(envelope["ok"], envelope)
            self.assertEqual(fixture.group_calls, [])
            for args in ({"fields": {**fields, "token": ["wrong"]}}, {"fields": fields, "origin": "https://other.invalid"}):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    self.post(fixture, json_response=True, **args)
                self.assertEqual(error.exception.code, 403)
            self.assertEqual(fixture.group_calls, [])
        with offline_workbench(view_actions=False) as fixture:
            self.assertNotIn("data-chat-picker", self.get(fixture))
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.post(fixture, {"token": ["wrong"]}, json_response=True)
            self.assertEqual(error.exception.code, 404)

    def test_duplicate_group_names_and_hostile_text_are_display_data(self):
        with offline_workbench() as fixture:
            payload = view.build(fixture.news_dir)
            text = view.notifications_html(payload, schema=config.describe_schema(), actions={"token": "test"}, page={"global": "1",
                "chat_directory": {"groups": [{"chat_id": "oc_one", "name": "Same", "external": False},
                    {"chat_id": "oc_two", "name": "Same", "external": False},
                    {"chat_id": "oc_three", "name": '<img src=x onerror="alert(1)">', "external": True}], "has_more": False, "loaded": True}})
            self.assertIn("Same", text)
            self.assertIn("oc_one", text)
            self.assertIn("oc_two", text)
            self.assertNotIn('<img src=x', text)
            self.assertIn("&lt;img", text)
