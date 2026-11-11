"""Read-only deletion metadata and progressive confirmation rendering."""
import html
from urllib.parse import urlencode

E = lambda value: html.escape(str(value), quote=True)


def delete_confirmation(payload, kind, name):
    if kind == "source":
        row = next((r for r in payload["sources"] if r["name"] == name), None)
        if row is None:
            raise ValueError("消息源不存在")
        title, message, route, blocked = row["card_title"], "待处理内容和历史会保留；这里只删除消息源配置。", "/config", False
    elif kind == "policy":
        row = payload["notification"]["policies"].get(name)
        if row is None:
            raise ValueError("策略不存在")
        title, route = row["values"].get("label") or name, "/notifications"
        bound = row.get("bound_sources", [])
        blocked = bool(bound)
        message = ("仍被这些消息源引用：" + "、".join(bound) + "。请先重新绑定，再删除策略。" if blocked
                   else "只删除此通知策略；待处理内容和历史会保留。")
    else:
        raise ValueError("未知删除类型")
    return dict(action=f"{kind}-remove", name=name, title=title, message=message, route=route, blocked=blocked)


def delete_link(payload, token, kind, name):
    item = delete_confirmation(payload, kind, name)
    attrs = {**item, "token": token, "hash": payload["sources_hash"]}
    data = " ".join(f'data-delete-{key}="{E("1" if value is True else "0" if value is False else value)}"' for key, value in attrs.items())
    url = "/confirm-delete?" + urlencode({"kind": kind, "name": name})
    return f'<a class="btn danger" href="{E(url)}" data-delete {data}>删除</a>'


def _fields(item, token, base_hash):
    return "".join(f'<input type="hidden" name="{E(key)}" value="{E(value)}">' for key, value in
                   {"token": token, "base_hash": base_hash, "return_to": item["route"], "name": item["name"], "confirm_delete": "1"}.items())


def delete_dialog():
    item = dict(route="/config", name="")
    return ('<dialog id="delete-dialog" aria-labelledby="delete-title" aria-describedby="delete-message">'
            '<h2 id="delete-title"></h2><p id="delete-message"></p>'
            '<div data-delete-feedback role="status" aria-live="polite" tabindex="-1" hidden></div>'
            '<form method="post" action="/api/actions/source-remove" data-delete-form>'
            + _fields(item, "", "") + '<div class="row-actions">'
            '<button class="btn" type="button" data-delete-cancel autofocus>取消</button>'
            '<button class="btn danger" type="submit">确认删除</button></div></form></dialog>')


def confirmation_body(payload, actions, kind, name):
    item = delete_confirmation(payload, kind, name)
    body = f'<section class="editor"><h2>删除 {E(item["title"])}？</h2><p>稳定 ID <code>{E(name)}</code></p><p>{E(item["message"])}</p>'
    if actions and actions.get("token") and not item["blocked"]:
        body += f'<form method="post" action="/api/actions/{E(item["action"])}">' + _fields(item, actions["token"], payload["sources_hash"])
        body += f'<div class="row-actions"><a class="btn" href="{item["route"]}">取消</a><button class="btn danger">确认删除</button></div></form>'
    else:
        body += f'<a class="btn" href="{item["route"]}">返回清单</a>'
    return body + '</section>'
