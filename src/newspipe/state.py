"""资讯管线 v2 —— 状态层（唯一读写入口）。

布局（v1 的 digests/ + pushed-ids/ + cursors/ + _status/ 四散布局在这里收敛成一个 state/）：

    <news_dir>/state/
      batches/<date>/<source>-<slot>.json   批次 state（含 items / view / card_id / seq）
      cursors/<source>.json                 poll 的时间闸（兼节流计时）
      pushed/<source>.jsonl                 幂等闸（跨天，只增不删）
      pending/<source>.jsonl                显式顺延队列（超元素预算的条目）
      status/<source>.json                  心跳
      budget/<date>.json                    当日发卡计数（打扰预算）
    <news_dir>/llm/
      receipts/<sha256>.json                回执：同 prompt 版本 + 同输入 → 复用结果
      usage/<date>.json                     当日用量（预算熔断）

所有写入都走 `_atomic.py` 的原子写（不另起一份）。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


from newspipe.errors import ConfigError  # noqa: E402
from newspipe._atomic import atomic_append_line, atomic_write_text, write_json_atomic  # noqa: E402

try:
    from zoneinfo import ZoneInfo

    TZ = ZoneInfo("Asia/Shanghai")
except Exception:  # pragma: no cover
    TZ = None

BATCH_ITEM_FIELDS = ("ext_id", "title", "url", "original_url", "source", "summary",
                     "category", "score", "pub_ts", "title_zh", "summary_zh", "body_zh",
                     "enrich_state", "enrich_note", "translate_state")
# 注：这是条目形状的**文档**，不是写盘时的白名单（批次按原样落盘，便于排障时看到降级原因）。


def now_iso() -> str:
    return (datetime.now(TZ) if TZ else datetime.now()).isoformat(timespec="seconds")


def today() -> str:
    return (datetime.now(TZ) if TZ else datetime.now()).strftime("%Y-%m-%d")


def read_json(path: Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


class Store:
    """状态读写。构造时传 news_dir 即可指向临时目录（测试用）。"""

    def __init__(self, news_dir: Path) -> None:
        self.news_dir = Path(news_dir)
        self.root = self.news_dir / "state"
        self.llm_root = self.news_dir / "llm"

    # ── 路径 ────────────────────────────────────────────────────────────────
    def _path(self, *parts: str) -> Path:
        p = self.root.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def batch_path(self, digest: str, source: str, slot: str) -> Path:
        return self._path("batches", digest, f"{source}-{slot}.json")

    def legacy_path(self, *parts: str) -> Path:
        return self.news_dir.joinpath(*parts)

    # ── 幂等闸 ──────────────────────────────────────────────────────────────
    def pushed_ids(self, source: str) -> set[str]:
        p = self.root / "pushed" / f"{source}.jsonl"
        if not p.is_file():
            return set()
        ids: set[str] = set()
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                ids.add(json.loads(line)["ext_id"])
            except Exception:
                continue
        return ids

    def record_pushed(self, source: str, items: Iterable[dict], digest: str) -> None:
        p = self._path("pushed", f"{source}.jsonl")
        for it in items:
            line = json.dumps({"ext_id": it["ext_id"], "date": digest,
                               "title": str(it.get("title", ""))[:60]}, ensure_ascii=False)
            atomic_append_line(p, line)

    # ── 时间闸 ──────────────────────────────────────────────────────────────
    def cursor(self, source: str) -> float | None:
        data = read_json(self.root / "cursors" / f"{source}.json")
        if not isinstance(data, dict):
            return None
        try:
            return float(data["last_ts"])
        except (KeyError, TypeError, ValueError):
            return None

    def set_cursor(self, source: str, ts: float, *, at: datetime | None = None) -> None:
        """`at` = 用调用方注入的时钟（测试与批量运行要一致，别混用墙钟）。"""
        when = at or (datetime.now(TZ) if TZ else datetime.now())
        write_json_atomic(self._path("cursors", f"{source}.json"),
                          {"last_ts": ts, "updated": when.isoformat(timespec="seconds")})

    def cursor_age_min(self, source: str, now: datetime) -> float | None:
        """游标文件的 updated 距今多少分钟（None = 从未真抓过）。

        节流闸挂在这里而不是心跳上：心跳每轮都写（包括被节流的那轮），拿它计时会把自己锁死。
        时区必须两边对齐：心跳写的是带 +08:00 的 aware 时间，而调用方常用 naive 的
        `datetime.now()`——直接相减会抛 TypeError 被吞成 None，节流闸就**静默失效**了
        （源每 5 分钟被真抓一次，反爬站点十几分钟就开始 403）。
        """
        data = read_json(self.root / "cursors" / f"{source}.json")
        if not isinstance(data, dict) or not data.get("updated"):
            return None
        try:
            when = datetime.fromisoformat(str(data["updated"]))
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=TZ or now.tzinfo)
        if now.tzinfo is None:
            now = now.replace(tzinfo=when.tzinfo)
        return (now - when).total_seconds() / 60

    # ── 批次 ────────────────────────────────────────────────────────────────
    def batch_rel(self, digest: str, source: str, slot: str) -> str:
        """批次的相对路径（卡片回调 payload 里带的就是它，渲染前就要知道）。"""
        return f"state/batches/{digest}/{source}-{slot}.json"

    def write_batch(self, batch: dict) -> Path:
        digest, source, slot = batch["digest"], batch["source"], batch["slot"]
        path = self.batch_path(digest, source, slot)
        batch["batch"] = self.batch_rel(digest, source, slot)  # 就地写回：卡片 payload 要用
        write_json_atomic(path, dict(batch), sort_keys=False)
        return path

    def read_batch(self, digest: str, source: str, slot: str) -> dict | None:
        data = read_json(self.batch_path(digest, source, slot))
        return data if isinstance(data, dict) else None

    def load_batch_by_rel(self, batch_rel: str) -> tuple[Path, dict] | None:
        """按批次相对路径载入（卡片回调里带的就是它）。路径必须落在 news 目录内。"""
        rel = str(batch_rel or "").strip()
        if not rel:
            return None
        path = (self.news_dir / rel).resolve()
        try:
            path.relative_to(self.news_dir.resolve())
        except ValueError:
            return None  # 越界路径：拒绝，不做任何 IO
        data = read_json(path)
        if not isinstance(data, dict):
            return None
        return path, data

    def find_batch(self, digest: str) -> tuple[Path, dict] | None:
        """当天唯一批次兜底（回调 payload 没带 batch 时）。多个批次按文件名字典序取第一个。"""
        day = self.root / "batches" / digest
        if not day.is_dir():
            return None
        for cand in sorted(day.glob("*.json")):
            data = read_json(cand)
            if isinstance(data, dict):
                return cand, data
        return None

    def save_batch_at(self, path: Path, batch: dict) -> None:
        write_json_atomic(Path(path), batch, sort_keys=False)

    # ── 心跳 ────────────────────────────────────────────────────────────────
    def heartbeat(self, source: str, **fields: Any) -> None:
        write_json_atomic(self._path("status", f"{source}.json"),
                          {"ts": now_iso(), **fields}, sort_keys=False)

    def status(self, source: str) -> dict:
        data = read_json(self.root / "status" / f"{source}.json")
        return data if isinstance(data, dict) else {}

    # ── 顺延队列 ────────────────────────────────────────────────────────────
    def append_pending(self, source: str, items: Iterable[dict]) -> int:
        p = self._path("pending", f"{source}.jsonl")
        n = 0
        for it in items:
            atomic_append_line(p, json.dumps(it, ensure_ascii=False))
            n += 1
        return n

    def pending_count(self, source: str) -> int:
        p = self.root / "pending" / f"{source}.jsonl"
        if not p.is_file():
            return 0
        return sum(1 for line in p.read_text(encoding="utf-8").splitlines() if line.strip())

    def take_pending(self, source: str, limit: int) -> list[dict]:
        """取出最多 limit 条并原地重写剩余部分（取出即消费）。"""
        p = self.root / "pending" / f"{source}.jsonl"
        if not p.is_file():
            return []
        rows: list[dict] = []
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        head, tail = rows[:limit], rows[limit:]
        if tail:
            atomic_write_text(p, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in tail))
        else:
            p.unlink(missing_ok=True)
        return head

    # ── 打扰预算 ────────────────────────────────────────────────────────────
    def budget_state(self, date: str) -> dict:
        data = read_json(self.root / "budget" / f"{date}.json")
        return data if isinstance(data, dict) else {"cards": 0, "last_card_ts": None}

    def note_card(self, date: str, *, at: datetime | None = None) -> dict:
        state = self.budget_state(date)
        state["cards"] = int(state.get("cards") or 0) + 1
        when = at or (datetime.now(TZ) if TZ else datetime.now())
        state["last_card_ts"] = when.isoformat(timespec="seconds")
        write_json_atomic(self._path("budget", f"{date}.json"), state, sort_keys=False)
        return state

    # ── 回执与用量 ──────────────────────────────────────────────────────────
    @staticmethod
    def receipt_key(prompt_version: str, model: str, payload: str) -> str:
        return hashlib.sha256(f"{prompt_version}\x00{model}\x00{payload}".encode()).hexdigest()

    def receipt(self, key: str) -> dict | None:
        data = read_json(self.llm_root / "receipts" / f"{key}.json")
        return data if isinstance(data, dict) else None

    def save_receipt(self, key: str, payload: dict) -> None:
        p = self.llm_root / "receipts" / f"{key}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(p, payload, sort_keys=False)

    def usage(self, date: str) -> dict:
        data = read_json(self.llm_root / "usage" / f"{date}.json")
        return data if isinstance(data, dict) else {"calls": 0, "chars": 0, "degraded": 0}

    def add_usage(self, date: str, *, calls: int = 0, chars: int = 0, degraded: int = 0,
                  reason: str = "") -> dict:
        """累计当日用量。`reason` 是失败原因的短码（length_empty / http_429 / not_json …）。

        必须记原因：加工层降级是"静默回退原文"，只有 degraded 计数时没人知道是模型、
        网络还是预算的问题（实测 12 次调用 10 次降级，原因全靠复现才查出来）。
        """
        state = self.usage(date)
        state["calls"] = int(state.get("calls") or 0) + calls
        state["chars"] = int(state.get("chars") or 0) + chars
        state["degraded"] = int(state.get("degraded") or 0) + degraded
        if reason:
            reasons = state.setdefault("reasons", {})
            key = reason[:60]
            reasons[key] = int(reasons.get(key) or 0) + 1
        state["updated"] = now_iso()
        p = self.llm_root / "usage" / f"{date}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(p, state, sort_keys=False)
        return state

    # ── 一次性迁移（v1 → v2 数据，不是代码兼容） ────────────────────────────
    def migrate_legacy(self) -> dict[str, int]:
        """把 v1 的幂等闸与游标导入 v2 布局。

        为什么必须做：pushed-ids 是跨天幂等闸，重排后如果空着，第一轮会把已经推过的条目
        再推一遍——用户看到的是重复刷屏。这是**数据搬迁**，不是保留旧格式。

        幂等闸取**并集**：v2 已经写过条目（切换当天跑过）时不能跳过旧账本，否则 v1 当天
        已推、v2 还没见过的条目会在下一个槽位重复推送。游标则相反——保留已有的（v2 建的基线
        比 v1 旧游标新，回退它会把积压条目一次推出来）。
        """
        moved = {"pushed": 0, "cursors": 0, "merged": 0}
        legacy_pushed = self.news_dir / "pushed-ids"
        if legacy_pushed.is_dir():
            for src_file in sorted(legacy_pushed.glob("*.jsonl")):
                target = self.root / "pushed" / src_file.name
                rows = [ln for ln in src_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
                if not rows:
                    continue
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    atomic_write_text(target, "".join(r + "\n" for r in rows))
                    moved["pushed"] += len(rows)
                    continue
                seen: set[str] = set()
                keep: list[str] = []
                for line in [*target.read_text(encoding="utf-8").splitlines(), *rows]:
                    if not line.strip():
                        continue
                    try:
                        key = str(json.loads(line).get("ext_id") or "")
                    except json.JSONDecodeError:
                        continue
                    if key and key not in seen:
                        seen.add(key)
                        keep.append(line)
                atomic_write_text(target, "".join(r + "\n" for r in keep))
                moved["merged"] += 1
        legacy_cursors = self.news_dir / "cursors"
        if legacy_cursors.is_dir():
            for src_file in sorted(legacy_cursors.glob("*.json")):
                target = self.root / "cursors" / src_file.name
                if target.exists():
                    continue
                data = read_json(src_file)
                if not isinstance(data, dict) or "last_ts" not in data:
                    continue
                write_json_atomic(target, data)
                moved["cursors"] += 1
        return moved


def require_news_dir(news_dir: Path | None) -> Path:
    if news_dir is None:
        raise ConfigError("news_dir 未指定")
    return Path(news_dir)
