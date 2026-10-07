"""事件流 —— 项目对外说话的唯一通道（**追加写，永不改写历史**）。

```
<news_dir>/state/events/<YYYY-MM-DD>.jsonl   事件：一行一个
<news_dir>/state/events/acks.jsonl           消费确认：一行一个
```

为什么两边都是追加写：事件文件可能正被常驻服务写入，ack 若去改写事件行就是竞态。
追加 + 读时做差集 ⇒ 无锁、崩溃安全、任意时刻可读。

为什么不是 MQ：状态本来就在磁盘上、投递需要回执与幂等、多一个 broker 就是多一个
静默失效点。真需要 MQ 时，这个文件就是现成的发布源（加一层薄适配即可）。

事件类型（`TYPES`，新增要同步 `docs` 与 skill）：

| type | 何时 | payload 关键字段 |
|---|---|---|
| `delivered` | 卡片发出 | `card_id` `message_id` `items` `chat` |
| `clicked` | 用户点了卡片 | `action` `item_id` `title` `url` |
| `favorite` | 用户点了收藏（待入库） | `item_id` `title` `url` `source` `digest` |
| `degraded` | AI 加工降级 | `count` `reasons` |
| `failed` | 采集/投递失败 | `status` `error` |
| `service` | 服务启停、槽位跳过 | `event` `slot` `reason` |
| `config` | 配置被改动（经 CLI） | `target` `op` `base_hash` |
"""

from __future__ import annotations

import json
import re
import secrets
from datetime import date as _date
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from newspipe import _atomic

TYPES = ("delivered", "clicked", "favorite", "degraded", "failed", "service", "config")

#: 待入库的事件类型（`newspipe queue list` 读的就是它）
QUEUE_TYPES = ("favorite",)

_DAY_FILE = re.compile(r"^\d{4}-\d{2}-\d{2}\.jsonl$")


def events_dir(news_dir: Path) -> Path:
    return Path(news_dir) / "state" / "events"


def _day_path(news_dir: Path, day: _date) -> Path:
    return events_dir(news_dir) / f"{day.isoformat()}.jsonl"


def acks_path(news_dir: Path) -> Path:
    return events_dir(news_dir) / "acks.jsonl"


def _new_id(ts: datetime) -> str:
    return f"ev_{ts.strftime('%Y%m%dT%H%M%S')}_{secrets.token_hex(3)}"


def _iso(ts: datetime) -> str:
    return ts.isoformat(timespec="seconds")


def append(news_dir: Path, type: str, *, payload: dict[str, Any] | None = None,
           source: str = "", slot: str = "", digest: str = "",
           ts: datetime | None = None) -> dict[str, Any]:
    """追加一个事件并返回它（含生成的 `id`）。类型未登记时抛 ValueError —— 宁可在写入侧炸，
    也不要在读侧让 agent 遇到一个它不认识的事件类型。"""
    if type not in TYPES:
        raise ValueError(f"未登记的事件类型：{type!r}（合法值：{'/'.join(TYPES)}）")
    now = ts or datetime.now()
    event: dict[str, Any] = {
        "id": _new_id(now),
        "ts": _iso(now),
        "type": type,
        "source": source,
        "slot": slot,
        "digest": digest,
        "payload": payload or {},
    }
    path = _day_path(Path(news_dir), now.date())
    path.parent.mkdir(parents=True, exist_ok=True)
    # 用原子追加（目录锁 + fsync + 回读校验）：事件日志是所有读取方的真相，
    # 写坏一行会污染后面每一个读者。
    _atomic.atomic_append_line(path, json.dumps(event, ensure_ascii=False))
    return event


def _read_lines(path: Path) -> Iterable[dict[str, Any]]:
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue                       # 半行（写到一半被杀）不阻塞读
        if isinstance(obj, dict) and obj.get("id"):
            out.append(obj)
    return out


def acked_ids(news_dir: Path) -> set[str]:
    return {str(a.get("id")) for a in _read_lines(acks_path(news_dir)) if a.get("id")}


def list_events(news_dir: Path, *, days: int = 7, since: datetime | None = None,
                types: Iterable[str] | None = None, unconsumed: bool = False,
                limit: int | None = None, newest_first: bool = False) -> list[dict[str, Any]]:
    """按时间升序返回事件。`days` 只限制扫描范围（不是过滤条件本身）。"""
    news_dir = Path(news_dir)
    folder = events_dir(news_dir)
    if not folder.is_dir():
        return []
    cutoff = since.date() if since else (_date.today() - timedelta(days=max(1, days) - 1))
    wanted = set(types) if types else None
    events: list[dict[str, Any]] = []
    for path in sorted(p for p in folder.iterdir() if _DAY_FILE.match(p.name)):
        try:
            day = _date.fromisoformat(path.name[:10])
        except ValueError:
            continue
        if day < cutoff:
            continue
        events.extend(_read_lines(path))
    if since is not None:
        stamp = _iso(since)
        events = [e for e in events if str(e.get("ts") or "") >= stamp]
    if wanted:
        events = [e for e in events if e.get("type") in wanted]
    if unconsumed:
        done = acked_ids(news_dir)
        events = [e for e in events if e.get("id") not in done]
    events.sort(key=lambda e: str(e.get("ts") or ""), reverse=newest_first)
    if limit:
        events = events[:limit]
    return events


def ack(news_dir: Path, ids: Iterable[str], *, by: str, ts: datetime | None = None,
        note: str = "") -> dict[str, Any]:
    """确认消费。结果**明确区分**三种情况（agent 需要据此判断是否重试）：

    - `acked`：本次真的写入了确认；
    - `already`：之前已确认过（幂等，不算错）；
    - `unknown`：事件 id 不存在（**必须让 agent 知道**，否则它会以为处理完了）。
    """
    news_dir = Path(news_dir)
    wanted = [str(i) for i in ids if str(i).strip()]
    known = {str(e.get("id")) for e in list_events(news_dir, days=90)}
    done = acked_ids(news_dir)
    now = ts or datetime.now()
    acked, already, unknown = [], [], []
    path = acks_path(news_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    for event_id in wanted:
        if event_id not in known:
            unknown.append(event_id)
            continue
        if event_id in done:
            already.append(event_id)
            continue
        record = {"id": event_id, "by": by, "at": _iso(now)}
        if note:
            record["note"] = note
        _atomic.atomic_append_line(path, json.dumps(record, ensure_ascii=False))
        acked.append(event_id)
    return {"acked": acked, "already": already, "unknown": unknown,
            "by": by, "at": _iso(now)}


def queue(news_dir: Path, *, days: int = 30) -> list[dict[str, Any]]:
    """待入库队列 = 未被确认的 `favorite` 事件（时间升序）。"""
    return list_events(news_dir, days=days, types=QUEUE_TYPES, unconsumed=True)


def prune(news_dir: Path, *, keep_days: int = 30, now: datetime | None = None) -> dict[str, Any]:
    """删掉过期的事件日文件。acks 不单独清理——它随事件文件一起过期，
    引用不存在的事件只会让 `unknown` 多一条，不影响正确性。"""
    news_dir = Path(news_dir)
    folder = events_dir(news_dir)
    if not folder.is_dir():
        return {"removed": [], "kept": 0}
    cutoff = (now or datetime.now()).date() - timedelta(days=max(1, keep_days))
    removed, kept = [], 0
    for path in sorted(folder.iterdir()):
        if not _DAY_FILE.match(path.name):
            continue
        try:
            day = _date.fromisoformat(path.name[:10])
        except ValueError:
            continue
        if day < cutoff:
            path.unlink()
            removed.append(path.name)
        else:
            kept += 1
    return {"removed": removed, "kept": kept, "keep_days": keep_days}


def stats(news_dir: Path, *, days: int = 7) -> dict[str, Any]:
    """给 `status` / `doctor` 用的一眼概览。"""
    events = list_events(news_dir, days=days)
    by_type: dict[str, int] = {}
    for event in events:
        key = str(event.get("type"))
        by_type[key] = by_type.get(key, 0) + 1
    unconsumed = list_events(news_dir, days=days, unconsumed=True)
    return {
        "days": days,
        "total": len(events),
        "by_type": by_type,
        "unconsumed": len(unconsumed),
        "queue": len(queue(news_dir)),
        "last_ts": events[-1]["ts"] if events else None,
    }


__all__ = ["TYPES", "QUEUE_TYPES", "append", "list_events", "ack", "acked_ids", "queue",
           "prune", "stats", "events_dir", "acks_path", "_atomic"]
