"""闭环层 —— 卡片回调动作路由。

网关把 `card.action.trigger` 合成 `/card <tag> <value_json>` 斜杠命令，插件（薄壳）把
payload 转到这里。**本模块不直接发消息**：只写 state、重渲染、更新卡片实体。

动作集（v2 新增 open_detail / back_to_list，取代 v1 的"整行 = open_read"）：

| 动作 | 效果 | 副作用 |
|---|---|---|
| `open_detail` | `view={"item": id}`，未读则标记已阅 | actions.log |
| `back_to_list` | `view="list"` | actions.log |
| `wiki` | 状态 → wiki | actions.log + `_staging/news-wiki-queue.md` |
| `dismiss` | 状态 → dismissed | actions.log + `preferences.md`（降权信号） |

成功返回空字符串（静默，不在聊天里插消息）；失败返回一行可读的告警文本。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from newspipe import channel, render, state
from newspipe.config import default_news_dir
from newspipe.state import now_iso

ACTION_TO_STATUS = {"wiki": "wiki", "dismiss": "dismissed", "open_detail": "read"}
def default_staging_queue() -> Path:
    """⭐ 收集箱的落点。

    抽离前写死 Vault 绝对路径；现在「环境变量优先，其次 <data_root>/_staging/」——
    在 Vault 里跑（cwd = 仓库根）与抽离前一致，独立部署时用 `NEWSPIPE_STAGING_QUEUE` 指定。
    """
    env = os.environ.get("NEWSPIPE_STAGING_QUEUE")
    if env:
        return Path(env).expanduser()
    return default_news_dir().parent.parent / "_staging" / "news-wiki-queue.md"


# 兼容常量（调用点应优先用 default_staging_queue()）
STAGING_QUEUE = default_staging_queue()


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


def _append_staging(batch: dict, item: dict, staging: Path) -> None:
    staging.parent.mkdir(parents=True, exist_ok=True)
    with staging.open("a", encoding="utf-8") as fh:
        fh.write(f"- [ ] {batch.get('digest')} {item.get('id')} "
                 f"{item.get('title')} {item.get('url')}\n")


def _resolve(store: state.Store, payload: dict) -> tuple[Path, dict] | None:
    found = store.load_batch_by_rel(payload.get("batch", ""))
    if found is None:
        found = store.find_batch(str(payload.get("digest", "")))
    return found


def handle(payload: dict, *, news_dir: Path | None = None,
           staging_path: Path | None = None) -> str:
    """入口：定位批次 → 改 view/status → 副作用 → 重渲染 → 更新卡片实体。

    `staging_path` 只为测试注入：默认写 Vault 的 `_staging/news-wiki-queue.md`，
    单测必须传自己的临时路径，否则会把测试数据写进真实待办队列。
    """
    if not isinstance(payload, dict) or payload.get("domain") != "news":
        return ""
    action = str(payload.get("news_action") or "")
    if action not in ("open_detail", "back_to_list", "wiki", "dismiss"):
        return ""
    news_dir = Path(news_dir or default_news_dir())
    staging = Path(staging_path or default_staging_queue())
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
    else:
        item = next((i for i in items if i.get("id") == payload.get("id")), None)
        if item is None:
            return ""
        if action == "open_detail":
            batch["view"] = {"item": item["id"]}
            # 进详情页 = 已阅（这就是 v2 的"读"信号：不再需要单独的已读按钮）
            if item.get("status") in (None, "unread"):
                item["status"] = "read"
                item["updated_at"] = now_iso()
        else:
            new_status = ACTION_TO_STATUS[action]
            if item.get("status") != new_status:
                item["status"] = new_status
                item["updated_at"] = now_iso()
                if action == "dismiss":
                    _append_preference(batch, item, news_dir)
                elif action == "wiki":
                    _append_staging(batch, item, staging)
        _log_action(batch, item, action, news_dir)
        store.save_batch_at(path, batch)

    card_id = batch.get("card_id")
    if not card_id:
        return ""  # 只落 state 的批次没有卡片
    try:
        card = render.render(batch)
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
    return ""


def handle_json(payload_text: str, *, news_dir: Path | None = None,
                staging_path: Path | None = None) -> str:
    """插件薄壳用的字符串入口（网关给的是 JSON 文本）。"""
    try:
        payload: Any = json.loads(payload_text or "{}")
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, dict):
        return ""
    try:
        return handle(payload, news_dir=news_dir, staging_path=staging_path)
    except Exception as exc:  # 永不让网关那一轮崩掉
        return f"⚠️ 卡片处理失败：{exc}"
