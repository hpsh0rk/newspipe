"""加工层 —— 幂等闸。

键 = (source, ext_id)。注册表跨天累积、只增不删：AIHOT 的 24 小时窗口会跨天重复给出同一条，
靠它挡住（v1 实测第二遍 0 发 7 skip）。批内也要去重——同一批里出现两次同一条目是源侧常见的抽风。
"""
from __future__ import annotations

from typing import Iterable


def fresh(items: Iterable[dict], store, source: str) -> list[dict]:
    seen = store.pushed_ids(source)
    out: list[dict] = []
    batch_seen: set[str] = set()
    for it in items:
        ext_id = str(it.get("ext_id") or "")
        if not ext_id or ext_id in seen or ext_id in batch_seen:
            continue
        batch_seen.add(ext_id)
        out.append(it)
    return out
