"""投递层 —— 打扰控制与投递裁决（轴④的语义实现）。

**顺序上的关键**：`plan()` 必须先于 `enrich()` 跑。先裁决"这一批要展示哪几条、被预算或元素上限
截掉哪些"，再只对留下的条目调模型——顺序反了就是先花钱再挑。

三种形态（form）：
- `card`：攒一批发一张新卡（超上限的部分进 pending，下一批顺延）。
- `append_card`：当日条目追加进同一张卡（滚动 ≤CARD_MAX_ITEMS）。
- `state_only`：只落 state，不发卡（如 Reddit 原料，交给下游 agent job）。

优先级（手动指定，后续接 priority_judge）：
- `high`：越过静默窗口与打扰预算 —— 这就是「重要信息免受频率限制」的落点；
- `normal`：受全部闸约束；
- `low`：只在槽位出现（poll 源若声明 low，等同 normal 但永不越过预算）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from newspipe.config import CARD_MAX_ITEMS

REASONS = ("ok", "quiet_hours", "budget_exceeded", "min_gap", "below_min_items", "nothing_new")


def in_quiet_hours(window: str, now: datetime) -> bool:
    """`quiet_hours: '23:00-07:30'`（支持跨午夜）；空/非法 = 不静默。"""
    m = re.match(r"\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$", window or "")
    if not m:
        return False
    a, b = int(m[1]) * 60 + int(m[2]), int(m[3]) * 60 + int(m[4])
    t = now.hour * 60 + now.minute
    return (t >= a or t < b) if a > b else a <= t < b


@dataclass
class Plan:
    source: str
    form: str
    reason: str
    digest: str
    slot: str
    items: list[dict] = field(default_factory=list)
    overflow: int = 0
    overflow_items: list[dict] = field(default_factory=list)
    deferred_items: list[dict] = field(default_factory=list)
    sends_card: bool = True
    priority: str = "normal"
    deferred: bool = False  # 条目未消费（进 pending 或留待下轮重取）

    @property
    def will_deliver(self) -> bool:
        return bool(self.items) and self.reason == "ok"

    @property
    def queued(self) -> list[dict]:
        """需要进显式顺延队列的条目（超出元素预算的 + 被静默/预算挡下的）。"""
        return [*self.overflow_items, *self.deferred_items]

    def as_dict(self) -> dict[str, Any]:
        return {"source": self.source, "form": self.form, "reason": self.reason,
                "planned": len(self.items), "overflow": self.overflow,
                "queued": len(self.queued),
                "sends_card": self.sends_card, "priority": self.priority,
                "deferred": self.deferred}


def card_cap(scfg: Any) -> int:
    """发卡源的元素预算硬闸；只落 state 的源不受此限（它不发卡）。"""
    cap = int(scfg.filter.max_items or CARD_MAX_ITEMS)
    return min(cap, CARD_MAX_ITEMS) if scfg.sends_card else cap


def _gap_minutes(state: dict, now: datetime) -> float | None:
    ts = state.get("last_card_ts")
    if not ts:
        return None
    try:
        return (now - datetime.fromisoformat(str(ts))).total_seconds() / 60
    except (TypeError, ValueError):
        return None


def plan(items: list[dict], scfg: Any, store: Any, *, digest: str, slot: str,
         now: datetime) -> Plan:
    """把候选条目裁决成一份投递计划。不写任何状态（除读取预算计数）。"""
    form = scfg.deliver.form
    priority = scfg.deliver.priority
    base = dict(source=scfg.name, form=form, digest=digest, slot=slot,
                sends_card=scfg.sends_card, priority=priority)

    if not items:
        return Plan(reason="nothing_new", **base)

    bypass = priority == "high"

    # 闸① 静默窗口
    if not bypass and in_quiet_hours(scfg.deliver.quiet_hours, now):
        return Plan(reason="quiet_hours", deferred=True, deferred_items=list(items), **base)

    # 闸② 打扰预算（跨源可比：按当日发卡计数 + 最小间隔）
    if not bypass and scfg.sends_card:
        budget = store.budget_state(digest)
        if int(budget.get("cards") or 0) >= int(scfg.deliver.max_cards_per_day or 0):
            return Plan(reason="budget_exceeded", deferred=True,
                        deferred_items=list(items), **base)
        gap = _gap_minutes(budget, now)
        if scfg.deliver.min_gap_min and gap is not None and gap < scfg.deliver.min_gap_min:
            return Plan(reason="min_gap", deferred=True, deferred_items=list(items), **base)

    # 闸③ 攒不够不发（条目留在原位，下轮重取即自然累积）
    if len(items) < int(scfg.filter.min_items or 0):
        return Plan(reason="below_min_items", deferred=True, **base)

    cap = card_cap(scfg)
    use, rest = items[:cap], items[cap:]
    # 截掉的部分必须显式带出去：pipeline 会把它放进顺延队列，否则这些条目就悄悄丢了
    return Plan(reason="ok", items=use, overflow=len(rest), overflow_items=rest, **base)
