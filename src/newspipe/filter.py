"""加工层 —— 过滤与排序（轴②的语义实现）。

关键词闸存在理由：综合羊毛流（如 linux.do 福利羊毛）里银行立减金、抽奖、游戏限免与 AI 额度贴
混在一起，不闸就会把资讯卡灌满无关内容。`include` 非空时任一命中即留，`exclude` 任一命中即丢
（exclude 优先），匹配 title + summary + category，大小写不敏感。

条数上下限**不在这里**：上限取决于投递形态（发卡受飞书 200 元素限制，只落 state 的不受），
所以由 delivery.plan 统一裁决。
"""
from __future__ import annotations

from typing import Any, Iterable


def apply_keywords(items: list[dict], scfg: Any) -> list[dict]:
    inc = [str(k).lower() for k in (scfg.filter.include_keywords or ())]
    exc = [str(k).lower() for k in (scfg.filter.exclude_keywords or ())]
    if not inc and not exc:
        return items
    out = []
    for it in items:
        hay = " ".join(str(it.get(f) or "") for f in ("title", "summary", "category")).lower()
        if exc and any(k in hay for k in exc):
            continue
        if inc and not any(k in hay for k in inc):
            continue
        out.append(it)
    return out


def rank(items: list[dict], mode: str) -> list[dict]:
    if mode == "score_desc":
        return sorted(items, key=lambda i: -(i.get("score") or 0))
    if mode == "pub_desc":
        return sorted(items, key=lambda i: -(i.get("pub_ts") or 0))
    return list(items)


def apply(items: Iterable[dict], scfg: Any) -> list[dict]:
    """关键词闸 + 排序。保持适配器给出的顺序（rank=none 时）。"""
    kept = apply_keywords(list(items), scfg)
    return rank(kept, scfg.filter.rank)
