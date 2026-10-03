"""采集面 —— 调度：只决定「何时触发哪条分支」，不碰投递。

两条分支：
- slot：cron 在固定时点调用 `cli.py --slot am|noon|pm`，跑该槽位声明的源。
- poll：cron 每 5 分钟调用 `cli.py --alert`，由本模块决定每个源这一轮是否真去抓。

poll 的三道闸（顺序有意义）：
1. `quiet_hours` —— 窗口内整轮静默，且**不推进游标**（窗口结束后一次补发积压）；
2. `interval_min` —— 节流，闸挂在**游标文件的 updated 时间戳**上而不是心跳：
   心跳每轮都写（包括被节流的那轮），拿它计时会把自己永久锁死；
3. 首轮无游标 → 建基线不发卡（由 pipeline 处理，scheduler 只报告 run=True）。

`--source <n>` 手工调试时 force=True 跳过节流，否则改完配置看不见效果。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from newspipe.delivery import in_quiet_hours


@dataclass(frozen=True)
class Decision:
    source: str
    run: bool
    reason: str  # ok | quiet_hours | throttled


def poll_decisions(cfg: Any, store: Any, now: datetime, *, force: bool = False,
                   only: str | None = None) -> list[Decision]:
    out: list[Decision] = []
    for src in cfg.poll_sources():
        if only and src.name != only:
            continue
        if in_quiet_hours(src.deliver.quiet_hours, now):
            out.append(Decision(src.name, False, "quiet_hours"))
            continue
        interval = float(src.fetch.interval_min or 0)
        if interval and not force:
            age = store.cursor_age_min(src.name, now)
            if age is not None and age < interval:
                out.append(Decision(src.name, False, "throttled"))
                continue
        out.append(Decision(src.name, True, "ok"))
    return out


def slot_sources(cfg: Any, slot: str) -> list[Any]:
    return cfg.slot_sources(slot)
