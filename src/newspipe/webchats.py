"""Bounded one-use directory pages for server-rendered group selection."""
from newspipe.chats import DirectoryError, MAX_GROUPS
from newspipe.webdrafts import Drafts


class DirectoryPages:
    def __init__(self):
        self.pages = Drafts()

    def take(self, form, *, loading=False):
        previous = self.pages.pop((form.get("chat_handle") or [""])[0]) or {
            "groups": [], "has_more": False, "page_token": "", "loaded": False}
        more = loading and (form.get("chat_load") or [""])[0] == "more"
        if more and (not previous["has_more"] or len(previous["groups"]) >= MAX_GROUPS
                     or (form.get("page_token") or [""])[0] != previous["page_token"]):
            raise DirectoryError("群列表分页已过期，请重新加载；已选群不会改变", code="E_USAGE")
        if loading and not more:
            form["page_token"] = [""]
        return previous

    def finish(self, form, envelope, previous=None):
        previous = previous or {"groups": [], "has_more": False, "page_token": "", "loaded": False}
        state = dict(previous)
        data = envelope.get("data") or {}
        if envelope.get("ok") and "groups" in data:
            groups = previous["groups"] if (form.get("chat_load") or [""])[0] == "more" else []
            merged = {item["chat_id"]: item for item in groups}
            merged.update({item["chat_id"]: item for item in data["groups"]})
            state = {"groups": list(merged.values())[:MAX_GROUPS], "has_more": data["has_more"],
                     "page_token": data["page_token"], "loaded": True}
            state["limited"] = len(merged) > MAX_GROUPS
        state["limited"] = state.get("limited", False) or (len(state["groups"]) >= MAX_GROUPS and state["has_more"])
        state["handle"] = self.pages.put({k: v for k, v in state.items() if k != "handle"})
        form["chat_handle"] = [state["handle"]]
        form["page_token"] = [state["page_token"]]
        return state
