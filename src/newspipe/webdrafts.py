"""Bounded, one-use server memory for progressive-enhancement PRG feedback.

URLs contain only random handles, never edits or tokens. Nothing is persisted.
"""
from __future__ import annotations

import html
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
    """Code-owned draft intents; these never dispatch a configuration writer."""
    intent = (form.get("editor_intent") or [""])[0]
    if not intent:
        return False
    from newspipe.actions import ActionInputError
    if action == "chat-list" and intent in {"manual-chat", "select-chat", "filter-chat"}:
        if intent != "filter-chat":
            form["chat_mode"] = ["manual" if intent == "manual-chat" else "select"]
        return True
    raise ActionInputError("编辑操作不可用，请重新打开编辑器")


def restore(page, action, values, *, reveal=False, feedback=""):
    """Restore only form controls in the code-rendered target; escape raw edits."""
    class Controls(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.out, self.active, self.select = [], False, None
            self.positions = {}

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
                if name and name != "token":
                    if kind in ("checkbox", "radio"):
                        attrs.pop("checked", None)
                        if attrs.get("value", "on") in values.get(name, []):
                            attrs["checked"] = None
                    elif name in values:
                        index = self.positions.get(name, 0)
                        self.positions[name] = index + 1
                        attrs["value"] = values[name][index] if index < len(values[name]) else ""
            self.out.append("<" + tag + "".join(" " + k + ("" if v is None else '="' + html.escape(v, quote=True) + '"') for k, v in attrs.items()) + ">")

        def handle_endtag(self, tag):
            if tag == "form":
                if self.active and feedback:
                    self.out.append(feedback)
                self.active = False
            if tag == "select":
                self.select = None
            self.out.append(f"</{tag}>")

        def handle_data(self, data):
            self.out.append(data)

        def handle_entityref(self, name):
            self.out.append(f"&{name};")

        def handle_charref(self, name):
            self.out.append(f"&#{name};")

        def handle_decl(self, decl):
            self.out.append(f"<!{decl}>")

    parser = Controls()
    parser.feed(page)
    return "".join(parser.out)
