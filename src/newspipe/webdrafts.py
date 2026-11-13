"""Bounded, one-use server memory for progressive-enhancement PRG feedback.

URLs contain only random handles, never edits or tokens. Nothing is persisted.
"""
from __future__ import annotations

import html
import json
import re
import secrets
import threading
import time
from collections import OrderedDict
from html.parser import HTMLParser


class Drafts:
    def __init__(self, *, cap=32, ttl=600):
        self.cap, self.ttl = cap, ttl
        self.items = OrderedDict()
        self.lock = threading.Lock()

    def put(self, value, *, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            self._expire(now)
            key = secrets.token_urlsafe(24)
            self.items[key] = (now, value)
            while len(self.items) > self.cap:
                self.items.popitem(last=False)
            return key

    def _expire(self, now):
        for key, (stamp, _) in list(self.items.items()):
            if now - stamp >= self.ttl:
                del self.items[key]

    def pop(self, key, *, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            self._expire(now)
            value = self.items.pop(key, None)
            return value[1] if value else None


def edit_controls(action, form):
    """Translate explicit validation and draft-only intents after POST gates."""
    if action in {"source-save", "source-patch"} and "adapter_draft" in form:
        adapter_drafts(form)
    if (form.get("validate_only") or [""])[0] == "1" and action in {
            "source-save", "source-patch", "source-copy", "policy-save", "global-save", "rsshub-save", "story-settings"}:
        form["dry"] = ["1"]
    intent = (form.get("editor_intent") or [""])[0]
    if not intent:
        return False
    from newspipe.actions import ActionInputError
    if action in {"source-save", "source-patch"} and intent == "select-adapter":
        from newspipe import config
        if (form.get("f.adapter") or [""])[0] not in config.adapter_names():
            raise ActionInputError("请选择可用的适配器")
        return True
    if action == "chat-list" and intent in {"manual-chat", "select-chat", "filter-chat"}:
        if intent != "filter-chat":
            form["chat_mode"] = ["manual" if intent == "manual-chat" else "select"]
        return True
    slot = action == "global-save" and (form.get("global_scope") or [""])[0] == "acquisition"
    timing = action == "policy-save"
    suffix = "slot" if slot else "time" if timing else ""
    if suffix and (intent == "add-" + suffix or intent.startswith("remove-" + suffix + ":")):
        key_name, clock_name = ("slot_id", "slot_time") if slot else ("p.times_key", "p.times")
        keys, clocks = list(form.get(key_name, [])), list(form.get(clock_name, []))
        if len(keys) != len(clocks) or len(keys) > 100:
            raise ActionInputError("时间行不完整或超过 100 行，请重新打开编辑器")
        if intent.startswith("add-"):
            if len(keys) >= 100:
                raise ActionInputError("最多添加 100 个时间")
            keys.append("")
            clocks.append("")
        else:
            index_text = intent.split(":", 1)[1]
            if not index_text.isascii() or not index_text.isdigit() or not 0 <= int(index_text) < len(keys):
                raise ActionInputError("要移除的时间行已不存在")
            index = int(index_text)
            keys.pop(index)
            clocks.pop(index)
        form[key_name], form[clock_name] = keys, clocks
        if slot:
            form["slots_present"] = ["1"]
        return True
    raise ActionInputError("编辑操作不可用，请重新打开编辑器")


def adapter_drafts(form):
    """Bounded per-adapter raw edits; never a configuration or write authority."""
    from newspipe import config
    from newspipe.actions import ActionInputError
    encoded = (form.get("adapter_draft") or ["{}"])[0]
    if len(encoded.encode("utf-8")) > 32768:
        raise ActionInputError("适配器草稿最多 32 KiB，请减少订阅参数")
    try:
        stored = json.loads(encoded)
    except (ValueError, RecursionError):
        raise ActionInputError("适配器草稿无效，请重新打开编辑器") from None
    if not isinstance(stored, dict):
        raise ActionInputError("适配器草稿需要映射")
    rendered = (form.get("adapter_rendered") or form.get("f.adapter") or [""])[0]
    out = {}
    for name, descriptor in config.describe_schema()["adapter_descriptors"].items():
        previous = stored.get(name, {})
        if not isinstance(previous, dict):
            raise ActionInputError("适配器草稿字段无效")
        values = {}
        for field in descriptor.get("fields", []):
            path = field["path"]
            raw = form.get("f." + path) if name == rendered else None
            raw = previous.get(path) if raw is None else raw
            if raw is None:
                continue
            if (not isinstance(raw, list) or len(raw) > 100
                    or any(not isinstance(v, str) or len(v.encode("utf-8")) > 16384 for v in raw)):
                raise ActionInputError("适配器草稿字段超出范围")
            if field.get("credential") and any(v and not re.fullmatch(r"[A-Z_][A-Z0-9_]*", v.strip()) for v in raw):
                raw = [""]
            values[path] = list(raw)
        if values:
            out[name] = values
    if len(json.dumps(out).encode()) > 32768:
        raise ActionInputError("适配器草稿最多 32 KiB，请减少订阅参数")
    return out


def safe_values(values):
    """Mistakenly pasted secrets must not return in feedback or retained drafts."""
    from newspipe import config
    out = dict(values)
    for descriptor in config.describe_schema()["adapter_descriptors"].values():
        for field in descriptor.get("fields", []):
            if not field.get("credential"):
                continue
            name = "f." + field["path"]
            if name in out and any(v and not re.fullmatch(r"[A-Z_][A-Z0-9_]*", v.strip()) for v in out[name]):
                out[name] = [""]
    if "adapter_draft" in out:
        from newspipe.actions import ActionInputError
        try:
            out["adapter_draft"] = [json.dumps(adapter_drafts(out))]
        except ActionInputError:
            out["adapter_draft"] = ["{}"]
    return out


def restore(page, action, values, *, reveal=False, feedback=""):
    """Restore only form controls in the code-rendered target; escape raw edits."""
    values = safe_values(values)
    if action in {"source-save", "source-patch"}:
        from newspipe.actions import ActionInputError
        try:
            snapshot = adapter_drafts(values)
        except ActionInputError:
            snapshot = {}
        selected = (values.get("f.adapter") or [""])[0]
        # The posted controls may belong to the previous adapter, not this one.
        values = {**values, **{"f." + path: raw for path, raw in snapshot.get(selected, {}).items()}}
    class Controls(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.out, self.active, self.select = [], False, None
            self.positions = {}
            self.textarea = False

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "form":
                self.active = attrs.get("action") == f"/api/actions/{action}"
            if self.active and tag == "details" and reveal and "data-help" not in attrs:
                attrs["open"] = None
            if self.active and tag == "select":
                self.select = attrs.get("name")
            if self.active and tag == "option" and self.select in values:
                attrs.pop("selected", None)
                if attrs.get("value", "") in values[self.select]:
                    attrs["selected"] = None
            if self.active and tag == "input":
                name, kind = attrs.get("name"), attrs.get("type", "text")
                if name and name not in {"token", "adapter_rendered", "adapter_draft"}:
                    if kind in ("checkbox", "radio"):
                        attrs.pop("checked", None)
                        if attrs.get("value", "on") in values.get(name, []):
                            attrs["checked"] = None
                    elif name in values:
                        index = self.positions.get(name, 0)
                        self.positions[name] = index + 1
                        attrs["value"] = values[name][index] if index < len(values[name]) else ""
            self.out.append("<" + tag + "".join(" " + k + ("" if v is None else '="' + html.escape(v, quote=True) + '"') for k, v in attrs.items()) + ">")
            if self.active and tag == "textarea":
                name = attrs.get("name")
                if name in values and name not in {"token", "adapter_rendered", "adapter_draft"}:
                    index = self.positions.get(name, 0)
                    self.positions[name] = index + 1
                    raw = values[name][index] if index < len(values[name]) else ""
                    self.out.append("\n" + html.escape(raw, quote=False))
                    self.textarea = True

        def handle_endtag(self, tag):
            if tag == "form":
                if self.active and feedback:
                    self.out.append(feedback)
                self.active = False
            if tag == "select":
                self.select = None
            if tag == "textarea":
                self.textarea = False
            self.out.append(f"</{tag}>")

        def handle_data(self, data):
            if not self.textarea:
                self.out.append(data)

        def handle_entityref(self, name):
            if not self.textarea:
                self.out.append(f"&{name};")

        def handle_charref(self, name):
            if not self.textarea:
                self.out.append(f"&#{name};")

        def handle_decl(self, decl):
            self.out.append(f"<!{decl}>")

    parser = Controls()
    parser.feed(page)
    return "".join(parser.out)
