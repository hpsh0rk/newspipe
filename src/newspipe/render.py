"""投递层 —— 渲染：纯函数 `state → card JSON`，两种视图。

列表页与详情页是**同一个卡片实体的两种渲染**（翻页 = 全量更新实体，见 channel.py）。
这是本次重排的交互核心：一张卡只发一次，之后靠 `view` 字段换页。

动作协议（三段式，网关把 card.action.trigger 合成 `/card <tag> <value_json>`）：
    {"domain": "news", "digest": "...", "batch": "state/batches/...", "id": "n03",
     "news_action": "open_detail" | "back_to_list" | "wiki" | "dismiss"}

元素预算：飞书 JSON 2.0 整卡 ≤200 元素（ErrCode 11310）。列表页每行 ~13 元素 ⇒ 上限约 15 行，
与 config.CARD_MAX_ITEMS 同源；详情页 ~23 元素。渲染前自检，超限变成一条带数字的显式错误，
而不是飞书那句无从下手的 11310。
"""
from __future__ import annotations

import json
from typing import Any

CARD_ELEMENT_LIMIT = 200
BADGE = {"unread": "", "read": "✅ ", "wiki": "⭐ ", "dismissed": "🚫 "}


def count_elements(node: Any) -> int:
    """递归数卡片里的组件/元素（凡带 tag 的 dict 记 1）——与飞书的计数口径实测一致。"""
    if isinstance(node, dict):
        return (1 if "tag" in node else 0) + sum(count_elements(v) for v in node.values())
    if isinstance(node, list):
        return sum(count_elements(v) for v in node)
    return 0


def display_title(item: dict) -> str:
    return str(item.get("title_zh") or item.get("title") or "").strip()


def display_summary(item: dict) -> str:
    return str(item.get("summary_zh") or item.get("summary") or "").strip()


def _value(batch: dict, item: dict, action: str) -> dict:
    return {"domain": "news", "digest": batch.get("digest", ""),
            "batch": batch.get("batch", ""), "id": item.get("id", ""),
            "news_action": action}


def _url_behaviors(item: dict) -> list[dict]:
    url = str(item.get("url") or "").strip()
    if not url:
        return []
    return [{"type": "open_url", "default_url": url, "pc_url": url,
             "ios_url": url, "android_url": url}]


def _tiny_button(content: str, action: str, tip: str, batch: dict, item: dict) -> dict:
    return {
        "tag": "button", "text": {"tag": "plain_text", "content": content},
        "type": "text", "size": "tiny",
        "hover_tips": {"tag": "plain_text", "content": tip},
        "behaviors": [{"type": "callback", "value": _value(batch, item, action)}],
    }


def _hook_button(hook: Any, batch: dict, item: dict) -> dict:
    """第三方 hook 的按钮。label 来自 `hooks.yaml`，action 带命名空间（不会撞核心动作）。

    样式刻意与核心按钮不同（`default` 而不是 `text`）——用户一眼能看出这是"外挂"按钮。
    """
    return {
        "tag": "button", "text": {"tag": "plain_text", "content": str(hook.label)[:20]},
        "type": "default", "size": "tiny",
        "hover_tips": {"tag": "plain_text", "content": f"第三方：{hook.id}"},
        "behaviors": [{"type": "callback", "value": _value(batch, item, hook.action)}],
    }


def _hook_buttons(hook_set: Any, scope: str, batch: dict, item: dict) -> list[dict]:
    if not hook_set:
        return []
    return [_hook_button(hook, batch, item) for hook in hook_set.buttons(scope)]


def _link_button(content: str, item: dict, *, kind: str = "primary", size: str = "small") -> dict:
    return {
        "tag": "button", "text": {"tag": "plain_text", "content": content},
        "type": kind, "size": size,
        "hover_tips": {"tag": "plain_text", "content": "打开原文"},
        "behaviors": _url_behaviors(item),
    }


def _row(batch: dict, item: dict, hook_set: Any = None) -> dict:
    badge = BADGE.get(str(item.get("status") or "unread"), "")
    title = display_title(item) or "（无标题）"
    if item.get("status") not in (None, "unread"):
        title = f"<font color='grey'>{title}</font>"
    md = f"**{badge}{title}**\n<font color='grey' size='2'>{item.get('source') or ''}</font>"
    buttons = []
    if str(item.get("url") or "").strip():
        buttons.append({
            "tag": "button", "text": {"tag": "plain_text", "content": "🔗"},
            "type": "text", "size": "tiny",
            "hover_tips": {"tag": "plain_text", "content": "查看原文"},
            "behaviors": _url_behaviors(item),
        })
    buttons.append(_tiny_button("⭐", "wiki", "存进 wiki 知识库", batch, item))
    buttons.append(_tiny_button("🚫", "dismiss", "不感兴趣（降权该来源/话题）", batch, item))
    # 第三方 hook：list 作用域的按钮**每行**都会出现，所以它直接吃元素预算（×行数）。
    # 超限由 assert_within_limit 在发卡前拦下；`newspipe doctor` 会预先算出剩余预算。
    buttons.extend(_hook_buttons(hook_set, "list", batch, item))
    return {
        "tag": "interactive_container",
        "element_id": f"row_{item.get('id')}",
        "width": "fill", "padding": "6px 8px", "corner_radius": "6px", "margin": "4px",
        # 整行点击 = 看详情（容器内有交互组件时优先响应组件，所以 ⭐/🚫/🔗 各自独立）
        "behaviors": [{"type": "callback", "value": _value(batch, item, "open_detail")}],
        "elements": [{
            "tag": "column_set", "flex_mode": "none",
            "columns": [
                {"tag": "column", "width": "weighted", "weight": 5, "vertical_align": "center",
                 "elements": [{"tag": "markdown", "content": md}]},
                {"tag": "column", "width": "weighted", "weight": 1, "vertical_align": "center",
                 "elements": buttons},
            ],
        }],
    }


SLOT_LABEL = {"am": "早间", "noon": "午间", "pm": "晚间", "alert": "实时", "manual": "手动"}


def list_card(batch: dict, hook_set: Any = None) -> dict:
    items = batch.get("items") or []
    done = sum(1 for i in items if i.get("status") not in (None, "unread"))
    total = len(items)
    slot = SLOT_LABEL.get(str(batch.get("slot") or ""), str(batch.get("slot") or ""))
    label = f"{batch.get('digest', '')} {batch.get('title') or batch.get('source', '')}"
    if slot:
        label += f" · {slot}"
    subtitle = "点整行 = 看摘要详情 · 🔗原文 · ⭐入库 · 🚫不感兴趣"
    if batch.get("overflow"):
        subtitle += f" · 余 {batch['overflow']} 条顺延下一批"
    return {
        "schema": "2.0",
        "config": {"update_multi": True, "width_mode": "default",
                   "summary": {"content": f"📰 {label}（{done}/{total} 已处理）"}},
        "header": {
            "title": {"tag": "plain_text", "content": f"📰 {label}"},
            "subtitle": {"tag": "plain_text", "content": subtitle},
            "template": "blue",
            "icon": {"tag": "standard_icon", "token": "notice_colorful"},
            "text_tag_list": [{"tag": "text_tag",
                               "text": {"tag": "plain_text", "content": f"{done}/{total} 已处理"},
                               "color": "green" if done >= total else "wathet"}],
        },
        "body": {"direction": "vertical", "padding": "12px 12px 16px 12px",
                 "vertical_spacing": "2px",
                 "elements": [_row(batch, i, hook_set) for i in items]},
    }


def detail_card(batch: dict, item: dict, hook_set: Any = None) -> dict:
    title = display_title(item) or "（无标题）"
    summary = display_summary(item)
    body_zh = str(item.get("body_zh") or "").strip()
    original = str(item.get("title") or "").strip()
    elements: list[dict] = [
        {"tag": "markdown", "element_id": "d_title", "content": f"**{title}**"},
        {"tag": "markdown", "element_id": "d_source",
         "content": f"<font color='grey' size='2'>{item.get('source') or ''}</font>"
                    + (f" · <font color='grey' size='2'>{item.get('category')}</font>"
                       if item.get("category") else "")},
    ]
    if summary:
        elements.append({"tag": "markdown", "element_id": "d_summary", "content": summary})
    else:
        elements.append({"tag": "markdown", "element_id": "d_summary",
                         "content": "<font color='grey'>（本条没有可展示的摘要）</font>"})
    if body_zh:
        elements.append({"tag": "hr"})
        elements.append({"tag": "markdown", "element_id": "d_body", "content": body_zh})
    if original and original != title:
        elements.append({"tag": "markdown", "element_id": "d_orig",
                         "content": f"<font color='grey' size='2'>原文标题：{original}</font>"})
    if item.get("judgment"):
        elements.append({"tag": "markdown", "element_id": "d_judgment",
                         "content": f"<font color='grey' size='2'>为什么值得看：{item['judgment']}</font>"})
    if item.get("enrich_state") == "degraded":
        elements.append({"tag": "markdown", "element_id": "d_note",
                         "content": "<font color='grey' size='2'>（AI 加工降级：以下为原文）</font>"})

    buttons: list[dict] = []
    if str(item.get("url") or "").strip():
        buttons.append(_link_button("🔗 查看原文", item))
    buttons.append(_tiny_button("⭐ 入库", "wiki", "存进 wiki 知识库", batch, item))
    buttons.append(_tiny_button("🚫 不感兴趣", "dismiss", "降权该来源/话题", batch, item))
    # 第三方 hook：详情页按钮（推荐放这里——只有一条条目，不吃 ×行数 的预算）
    buttons.extend(_hook_buttons(hook_set, "detail", batch, item))
    buttons.append(_tiny_button("← 返回列表", "back_to_list", "回到资讯列表页", batch, item))
    elements.append({"tag": "column_set", "flex_mode": "flow",
                     "columns": [{"tag": "column", "width": "auto", "elements": [b]}
                                 for b in buttons]})
    return {
        "schema": "2.0",
        "config": {"update_multi": True, "width_mode": "default",
                   "summary": {"content": f"📰 详情 · {title[:24]}"}},
        "header": {
            "title": {"tag": "plain_text", "content": "📰 条目详情"},
            "subtitle": {"tag": "plain_text", "content": str(item.get("source") or "")},
            "template": "wathet",
            "icon": {"tag": "standard_icon", "token": "notice_colorful"},
        },
        "body": {"direction": "vertical", "padding": "12px 12px 16px 12px",
                 "vertical_spacing": "4px", "elements": elements},
    }


def render(batch: dict, hook_set: Any = None) -> dict:
    """按批次 state 的 view 字段渲染当前页。

    `hook_set` 决定第三方按钮出现在哪些页（`scope: detail` / `list`）。默认 None = 与
    没有 hook 时逐字节一致——这是"不加 hook 的部署行为不变"的保证。
    """
    view = batch.get("view") or "list"
    if isinstance(view, dict) and view.get("item"):
        item = next((i for i in (batch.get("items") or []) if i.get("id") == view["item"]), None)
        if item is not None:
            return detail_card(batch, item, hook_set)
    return list_card(batch, hook_set)


def projected_elements(n_items: int, hook_set: Any = None) -> dict[str, int]:
    """给 `doctor` 用：按当前配置算 N 条时的列表页元素数（超限前就该知道）。"""
    batch = {"digest": "2026-01-01", "slot": "am", "source": "probe", "batch": "probe",
             "view": "list",
             "items": [{"id": f"p{i:02d}", "title": f"标题 {i}", "url": "https://example.com",
                        "source": "probe", "status": "unread"} for i in range(n_items)]}
    card = list_card(batch, hook_set)
    return {"items": n_items, "elements": count_elements(card), "limit": CARD_ELEMENT_LIMIT,
            "headroom": CARD_ELEMENT_LIMIT - count_elements(card)}


def assert_within_limit(card: dict, *, what: str) -> int:
    n = count_elements(card)
    if n > CARD_ELEMENT_LIMIT:
        raise ValueError(
            f"{what} 卡片超元素上限：{n} > {CARD_ELEMENT_LIMIT}（飞书 11310）；"
            f"条目数={len((card.get('body') or {}).get('elements') or [])} —— "
            f"减少条目数（config.CARD_MAX_ITEMS）或简化行结构")
    return n


def card_json(card: dict) -> str:
    return json.dumps(card, ensure_ascii=False)
