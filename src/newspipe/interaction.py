"""闭环层 —— 卡片回调动作路由。

网关把 `card.action.trigger` 合成 `/card <tag> <value_json>` 斜杠命令，插件（薄壳）把
payload 转到这里（独立部署时由 `inbound.py` 直接调）。**本模块不直接发消息**：
只写 state、记事件、重渲染、更新卡片实体。

动作集：

| 动作 | 效果 | 副作用 |
|---|---|---|
| `open_detail` | `view={"item": id}`，未读则标记已阅 | actions.log + `clicked` 事件 |
| `back_to_list` | `view="list"` | actions.log + `clicked` 事件 |
| `wiki`（⭐ 收藏） | 状态 → wiki | actions.log + **`favorite` 事件（待入库队列）** |
| `dismiss`（🚫） | 状态 → dismissed | actions.log + `preferences.md` + `clicked` 事件 |
| `<hook action>` | 由第三方 handler 决定 | `clicked` 事件（带 hook id 与 handler 结果） |

**边界（防腐层）**：本模块只写**项目自己的**数据（`<news_dir>/` 下的 state/actions.log/
preferences.md）。它**不再往宿主写文件**——收藏不再直接写 Vault 的待办队列，而是记一条
`favorite` 事件，由宿主用 `newspipe queue list --json` 读、人确认后再入库。

成功返回空字符串（静默，不在聊天里插消息）；失败/需要人知道时返回一行可读文本
（`inbound` 会把它变成卡片 toast）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from newspipe import channel, events, hooks as hooks_mod, render, state
from newspipe.config import default_news_dir
from newspipe.state import now_iso

ACTION_TO_STATUS = {"wiki": "wiki", "dismiss": "dismissed", "open_detail": "read"}

#: 核心动作（hook 不得覆盖；见 hooks.CORE_ACTIONS）
CORE_ACTIONS = ("open_detail", "back_to_list", "wiki", "dismiss")


def _log_action(batch: dict, item: dict, action: str, news_dir: Path) -> None:
    line = "\t".join([now_iso(), str(batch.get("digest", "")), str(item.get("id", "")),
                      action, str(item.get("source", "")), str(item.get("title", ""))])
    path = news_dir / "actions.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _append_preference(batch: dict, item: dict, news_dir: Path) -> None:
    path = news_dir / "preferences.md"
    if not path.exists():
        path.write_text(
            "---\ntype: news-preferences\ntitle: 资讯偏好（降权/加权信号）\n"
            "visibility: private\n---\n\n# 降权信号（🚫 累计）\n\n", encoding="utf-8")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"- {batch.get('digest')} 来源={item.get('source')} "
                 f"标题={item.get('title')}\n")


def _item_ref(batch: dict, item: dict) -> dict[str, Any]:
    """事件里的条目引用：给宿主足够信息去干它自己的事（不必回读批次）。"""
    return {"item_id": item.get("id"), "title": item.get("title"), "url": item.get("url"),
            "original_url": item.get("original_url") or "", "source": item.get("source") or "",
            "digest": batch.get("digest"), "batch": batch.get("batch") or ""}


def _record(news_dir: Path, event_type: str, *, batch: dict, item: dict,
            action: str, extra: dict | None = None) -> None:
    payload = _item_ref(batch, item)
    payload["action"] = action
    if extra:
        payload.update(extra)
    events.append(news_dir, event_type, payload=payload,
                  source=str(item.get("source") or ""),
                  slot=str(batch.get("slot") or ""), digest=str(batch.get("digest") or ""))


def _resolve(store: state.Store, payload: dict) -> tuple[Path, dict] | None:
    found = store.load_batch_by_rel(payload.get("batch", ""))
    if found is None:
        found = store.find_batch(str(payload.get("digest", "")))
    return found


def _run_hook(hook: hooks_mod.Hook, payload: dict, *, news_dir: Path,
              batch: dict, item: dict) -> str:
    """调第三方 handler 并把它变成一条 toast。失败**不静默**，但也不影响核心按钮。"""
    ctx = {"domain": "news", "hook": hook.id, "hook_action": hook.action,
           "operator": str(payload.get("operator") or ""),
           "chat": str(payload.get("chat") or ""),
           **_item_ref(batch, item)}
    result = hooks_mod.run_handler(hook, ctx)
    _record(news_dir, "clicked", batch=batch, item=item, action=hook.action,
            extra={"hook": hook.id, "hook_ok": result.get("ok"),
                   "hook_exit_code": result.get("exit_code"), "hook_ms": result.get("ms")})
    if result.get("ok"):
        return str(result.get("stdout") or f"{hook.label}：已处理")[:400]
    detail = result.get("error") or result.get("stderr") or f"退出码 {result.get('exit_code')}"
    return f"⚠️ {hook.label} 未完成：{str(detail)[:200]}"


def handle(payload: dict, *, news_dir: Path | None = None,
           hook_set: hooks_mod.HookSet | None = None) -> str:
    """入口：定位批次 → 改 view/status → 记事件/跑 hook → 重渲染 → 更新卡片实体。"""
    if not isinstance(payload, dict) or payload.get("domain") != "news":
        return ""
    action = str(payload.get("news_action") or "")
    hook = hook_set.by_action(action) if hook_set else None
    if action not in CORE_ACTIONS and hook is None:
        return ""
    news_dir = Path(news_dir or default_news_dir())
    store = state.Store(news_dir)
    found = _resolve(store, payload)
    if found is None:
        return ""  # 批次已不在（历史卡/被清理）：静默，不回告警刷屏
    path, batch = found
    items = batch.get("items") or []

    if action == "back_to_list":
        batch["view"] = "list"
        store.save_batch_at(path, batch)
        _log_action(batch, {"id": "", "source": "", "title": ""}, action, news_dir)
        _record(news_dir, "clicked", batch=batch, item={}, action=action)
    else:
        item = next((i for i in items if i.get("id") == payload.get("id")), None)
        if item is None:
            return ""
        if hook is not None:
            _log_action(batch, item, action, news_dir)
            store.save_batch_at(path, batch)
            return _run_hook(hook, payload, news_dir=news_dir, batch=batch, item=item)
        if action == "open_detail":
            batch["view"] = {"item": item["id"]}
            # 进详情页 = 已阅（这就是 v2 的"读"信号：不再需要单独的已读按钮）
            if item.get("status") in (None, "unread"):
                item["status"] = "read"
                item["updated_at"] = now_iso()
            _record(news_dir, "clicked", batch=batch, item=item, action=action)
        else:
            new_status = ACTION_TO_STATUS[action]
            if item.get("status") != new_status:
                item["status"] = new_status
                item["updated_at"] = now_iso()
                if action == "dismiss":
                    _append_preference(batch, item, news_dir)
                elif action == "wiki":
                    # ⭐ 收藏 → 待入库队列（事件流）；宿主读 queue list 后由人确认再入库
                    _record(news_dir, "favorite", batch=batch, item=item, action=action)
                else:
                    _record(news_dir, "clicked", batch=batch, item=item, action=action)
            elif action == "wiki":
                # 重复点收藏：条目已是 wiki 状态，但仍然把"想入库"的意图补记一次
                _record(news_dir, "favorite", batch=batch, item=item, action=action)
        _log_action(batch, item, action, news_dir)
        store.save_batch_at(path, batch)

    card_id = batch.get("card_id")
    if not card_id:
        return ""  # 只落 state 的批次没有卡片
    try:
        card = render.render(batch, hook_set=hook_set)
        render.assert_within_limit(card, what=f"{batch.get('source')}-{batch.get('slot')}")
    except ValueError as exc:
        return f"⚠️ 卡片渲染失败：{exc}"
    seq = int(batch.get("seq") or 1) + 1
    try:
        if not channel.update_entity(str(card_id), seq, card):
            return "⚠️ 状态已记录，但卡片更新失败（下次交互会自愈）"
    except Exception as exc:  # 通道故障不该让回调链路崩掉
        return f"⚠️ 状态已记录，但卡片更新失败：{exc}"
    batch["seq"] = seq
    store.save_batch_at(path, batch)
    if action == "wiki":
        return "⭐ 已加入待入库队列（确认后进 Wiki）"
    return ""


def handle_json(payload_text: str, *, news_dir: Path | None = None,
                hook_set: hooks_mod.HookSet | None = None) -> str:
    """插件薄壳用的字符串入口（网关给的是 JSON 文本）。"""
    try:
        payload: Any = json.loads(payload_text or "{}")
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, dict):
        return ""
    try:
        return handle(payload, news_dir=news_dir, hook_set=hook_set)
    except Exception as exc:  # 永不让网关那一轮崩掉
        return f"⚠️ 卡片处理失败：{exc}"
