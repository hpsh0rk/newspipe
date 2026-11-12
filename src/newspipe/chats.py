"""Optional joined-group directory, bounded and independent of configuration writers."""
from __future__ import annotations

import re
from typing import Any

MAX_BYTES = 1024 * 1024
MAX_GROUPS = 1000
TIMEOUT = 5


class DirectoryError(Exception):
    def __init__(self, message: str, *, code: str = "E_NETWORK", hint: str = "稍后重试加载群列表；也可手动填写群 ID"):
        super().__init__(message)
        self.code, self.hint = code, hint


def validate_request(page_size: int, page_token: str) -> None:
    if type(page_size) is not int or not 1 <= page_size <= 100:
        raise DirectoryError("每页群数量必须在 1 到 100 之间", code="E_USAGE")
    if not isinstance(page_token, str) or len(page_token) > 2048 or any(ord(c) < 32 or ord(c) == 127 for c in page_token):
        raise DirectoryError("群列表分页信息不可用，请重新加载", code="E_USAGE")


def provider_error(code: Any = None, status: int = 200) -> DirectoryError:
    if code in (99991672, 99991679) or status == 403:
        return DirectoryError("当前机器人没有读取群列表的权限", code="E_CONFIG",
                              hint="请在该发送应用的飞书开发者后台开通 im:chat:read，并发布权限；不需要个人账号授权")
    if code == 232025:
        return DirectoryError("当前发送应用尚未启用机器人能力", code="E_CONFIG", hint="在当前应用的开发者后台启用机器人能力后重试")
    if code in (99991661, 99991663, 99991664, 99991668) or status == 401:
        return DirectoryError("发送应用身份验证失败", code="E_CONFIG", hint="核对当前发送应用的凭据后重新加载，不要切换到个人账号")
    return DirectoryError("群列表请求失败；没有更改通知目标")


def list_joined(channel: Any, *, page_size: int = 100, page_token: str = "") -> dict:
    validate_request(page_size, page_token)
    capability = getattr(channel, "list_chats", None)
    if not callable(capability):
        raise DirectoryError("当前发送通道不支持读取已加入的群", code="E_CONFIG", hint="使用手动群 ID，或为当前通道提供群列表能力；不会自动切换发送应用")
    try:
        page = capability(page_size=page_size, page_token=page_token)
    except DirectoryError:
        raise
    except Exception:
        raise DirectoryError("群列表读取失败；没有更改通知目标") from None
    malformed = DirectoryError("群列表响应不完整，请重新加载")
    if not isinstance(page, dict) or not isinstance(page.get("items"), list) or type(page.get("has_more")) is not bool:
        raise malformed
    items, more, cursor = page["items"], page["has_more"], page.get("page_token", "")
    if len(items) > page_size:
        raise malformed
    try:
        validate_request(page_size, cursor)
    except DirectoryError:
        raise malformed from None
    if more and (not cursor or cursor == page_token):
        raise malformed
    groups, seen = [], set()
    for item in items:
        if not isinstance(item, dict):
            raise malformed
        ident, name, external = item.get("chat_id"), item.get("name", ""), item.get("external", False)
        if (not isinstance(ident, str) or len(ident) > 256 or not re.fullmatch(r"oc_[A-Za-z0-9_-]+", ident)
                or not isinstance(name, str) or len(name) > 300 or type(external) is not bool):
            raise malformed
        if ident not in seen:
            groups.append({"chat_id": ident, "name": name, "external": external})
            seen.add(ident)
    return {"groups": groups, "has_more": more, "page_token": cursor if more else ""}
