"""只读视图契约 —— 面板 / 门户 / Agent 唯一的数据面（`newspipe view` 与 `GET /view`）。

**为什么必须有这一层。** `state/**` 的目录布局是本包的**私有实现**。宿主（Vault 面板）曾经
直接解析 `state/status/<source>.json`、`state/batches/<date>/*.json`、`state/pushed/*.jsonl`……
本包把批次目录从 `digests/` 改成 `batches/` 之后，那份代码读不到文件却**不报错**，只是把所有
信源显示成「从未运行」：改布局的人根本不知道有谁在读，读的人也看不出自己读的是空的。

**契约里连语义一起给**（状态码 + 人可读标签 + 语气 + 是否超期），因为
「`below_min_items` 是攒批还是故障」是本包的知识，不是宿主的。宿主只负责渲染。
字段名与面板的 `NewsResponse` 对齐：改这里的字段名 = 改面板的接口，两边一起改。

输出形态：

    {
      "contract_version": 1,
      "generated_at": "2026-10-04T09:10:00+08:00",
      "chat": "oc_…", "slots": {"am": "08:15", …},
      "service": {"channel": …, "inbound_mode": …, "tick_seconds": …, "view_path": "/view"},
      "sources": [ {name, enabled, adapter, trigger, slots, interval_min, form, priority,
                    quiet_hours, max_cards_per_day, min_gap_min, summary_mode, fetch_body,
                    translate_body, include_keywords, exclude_keywords, min_items, max_items,
                    rank, card_title, sends_card, status, status_label, status_tone, status_ts,
                    stale, status_detail, status_note, pushed_total, pending_total}, … ],
      "today":  [ {file, source, slot, title, count, marked, has_card, view, overflow}, … ],
      "summary": {batches, items, marked, read_rate, actions_today, cards_today, last_card_ts},
      "llm":    {calls, degraded, chars, reasons},
      "preferences_preview": "…"
    }
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote as urlquote

from newspipe import config, edit, state
from newspipe.style import CSS

CONTRACT_VERSION = 1

#: 心跳多久算「超期未运行」。槽位源一天跑 3 次（最坏间隔 14h）⇒ 30h；轮询源 2h。
STALE_AFTER: dict[str, timedelta] = {"slot": timedelta(hours=30), "poll": timedelta(hours=2)}
DEFAULT_STALE_AFTER = timedelta(hours=30)

#: 心跳词表：状态码 → (人可读标签, 语气)。语气取值 = 面板 Pill 的 tone 集合。
STATUS_LABELS: dict[str, tuple[str, str]] = {
    "ok": ("已运行", "ok"),
    "nothing_new": ("无新内容", "ok"),
    "state_only": ("只落 state", "ok"),
    "baseline": ("已建基线", "ok"),
    "below_min_items": ("攒批中", "muted"),
    "quiet_hours": ("静默时段", "muted"),
    "throttled": ("节流跳过", "muted"),
    "min_gap": ("间隔未到", "muted"),
    "dry": ("试运行", "muted"),
    "budget_exceeded": ("预算熔断", "warn"),
    "overflow": ("超元素上限", "warn"),
    "config_missing": ("缺凭据", "warn"),
    "send_failed": ("发送失败", "danger"),
    "error": ("故障", "danger"),
}
#: 这些状态是「健康但安静」，超期检查只对它们生效（故障状态本来就该红，不必再标超期）。
HEALTHY = {"ok", "nothing_new", "state_only", "baseline"}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _line_count(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        with path.open(encoding="utf-8") as fh:
            return sum(1 for line in fh if line.strip())
    except OSError:
        return 0


def _is_stale(status: str, ts: Any, trigger: str, now: datetime) -> bool:
    """超期判定。时区必须对齐：心跳写的是带 +08:00 的 aware 时间，调用方常给 naive 的 now
    ——直接相减会抛 TypeError，被吞掉就等于「永远不超期」，闸静默失效。"""
    if status not in HEALTHY or not ts:
        return False
    try:
        when = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return True
    if when.tzinfo is None and now.tzinfo is not None:
        when = when.replace(tzinfo=now.tzinfo)
    elif when.tzinfo is not None and now.tzinfo is None:
        now = now.replace(tzinfo=when.tzinfo)
    return now - when > STALE_AFTER.get(trigger, DEFAULT_STALE_AFTER)


def _source_row(src: config.SourceConfig, store: state.Store, *, now: datetime) -> dict[str, Any]:
    hb = store.status(src.name)
    status = str(hb.get("status") or "never")
    label, tone = STATUS_LABELS.get(status, (status or "未知", ""))
    ts = hb.get("ts")
    trigger = src.fetch.trigger
    stale = _is_stale(status, ts, trigger, now)
    if stale:
        label, tone = "超期未运行", "warn"
    return {
        "name": src.name,
        "enabled": src.enabled,
        "adapter": src.adapter,
        # 四轴（面板按轴渲染，不解析 state）
        "trigger": trigger,
        "slots": list(src.fetch.slots),
        "interval_min": src.fetch.interval_min,
        "form": src.deliver.form,
        "priority": src.deliver.priority,
        "quiet_hours": src.deliver.quiet_hours,
        "max_cards_per_day": src.deliver.max_cards_per_day,
        "min_gap_min": src.deliver.min_gap_min,
        "summary_mode": src.enrich.summary,
        "fetch_body": src.enrich.fetch_body,
        "translate_body": src.enrich.translate_body,
        "include_keywords": len(src.filter.include_keywords),
        "exclude_keywords": len(src.filter.exclude_keywords),
        "min_items": src.filter.min_items,
        "max_items": src.filter.max_items,
        "rank": src.filter.rank,
        "card_title": src.card_title,
        "sends_card": src.sends_card,
        # 运行态（语义已裁决：标签/语气/超期都是本包给的）
        "status": status,
        "status_label": label,
        "status_tone": tone,
        "status_ts": ts,
        "stale": stale,
        "status_detail": {k: hb[k] for k in ("fetched", "kept", "new", "planned", "queued",
                                             "card", "enrich", "note") if k in hb},
        "status_note": str(hb.get("note") or ""),
        "pushed_total": len(store.pushed_ids(src.name)),
        "pending_total": store.pending_count(src.name),
    }


def _today_batches(store: state.Store, digest: str) -> list[dict[str, Any]]:
    batch_dir = store.root / "batches" / digest
    rows: list[dict[str, Any]] = []
    if not batch_dir.is_dir():
        return rows
    for path in sorted(batch_dir.glob("*.json")):
        batch = _read_json(path)
        items = batch.get("items") or []
        if not isinstance(items, list):
            items = []
        rows.append({
            "file": path.name,
            "source": batch.get("source"),
            "slot": batch.get("slot"),
            "title": batch.get("title"),
            "count": len(items),
            "marked": sum(1 for i in items if isinstance(i, dict)
                          and i.get("status") not in (None, "unread")),
            "has_card": bool(batch.get("card_id")),
            "view": "详情" if isinstance(batch.get("view"), dict) else "列表",
            "overflow": batch.get("overflow") or 0,
        })
    return rows


#: 趋势看多少天。**14 天**：够看出「最近是不是变差了」，又不至于把页面拉成一张长表。
HISTORY_DAYS = 14


def history(store: state.Store, *, days: int = HISTORY_DAYS,
            now: datetime | None = None) -> list[dict[str, Any]]:
    """近 N 天的**每日汇总**（趋势）。

    **只算落盘过的东西**：`state/batches/<date>/`（条目与已标记）、`state/budget/<date>.json`
    （发卡）、`llm/usage/<date>.json`（AI 调用与降级）、`state/pushed/<源>.jsonl`（推送台账）。

    台账那一路不能省：**有些源不写批次文件**（poll + `append_card` 直接追加进当日实时卡），
    只看批次会把它们的产出算成「没有」—— 实测 `linuxdo_deals` 台账 13 条、批次文件 0 个。
    没落盘的一律不给：「各源历史成功率」根本没落盘（`state/status/` 只留最后一次心跳），
    所以这里只给「当天有没有产出」，绝不拿当前状态去反推历史。
    """
    moment = now or datetime.now()
    pushed_by_day = store.pushed_by_day()
    out: list[dict[str, Any]] = []
    for back in range(days - 1, -1, -1):
        day = (moment - timedelta(days=back)).strftime("%Y-%m-%d")
        batch_dir = store.root / "batches" / day
        produced: list[str] = []
        items = marked = 0
        if batch_dir.is_dir():
            for path in sorted(batch_dir.glob("*.json")):
                batch = _read_json(path)
                raw = batch.get("items") or []
                rows = raw if isinstance(raw, list) else []
                items += len(rows)
                marked += sum(1 for i in rows if isinstance(i, dict)
                              and i.get("status") not in (None, "unread"))
                if batch.get("source"):
                    produced.append(str(batch["source"]))
        day_pushed = pushed_by_day.get(day) or {}
        budget = store.budget_state(day)
        usage = store.usage(day)
        out.append({
            "date": day,
            "items": items,
            "marked": marked,
            "read_rate": round(marked / items, 2) if items else None,
            "cards": int(budget.get("cards") or 0),
            "pushed": sum(day_pushed.values()),
            "calls": int(usage.get("calls") or 0),
            "degraded": int(usage.get("degraded") or 0),
            # 产出 = 批次落盘 ∪ 推送台账。两个都算，缺一个就会漏源。
            "sources": sorted(set(produced) | set(day_pushed)),
        })
    return out


#: 条目浏览的回看天数与硬上限。14 天全量能把页面拉成几 MB，所以先给 7 天、再截断渲染。
ITEMS_DAYS = 7
ITEMS_CAP = 2000
ITEMS_RENDER_CAP = 200

#: 批次详情路由只认这种文件名 —— 防路径穿越（`/` 与「以点开头」都不允许）。
#: 真正的越界拦截在 `state.Store.load_batch_by_rel()`，这里只是第一道形状校验。
BATCH_STEM_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,80}$")
DIGEST_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: 条目行里要展示的字段（批次里每条有 13 个键，页面只取这些）。
ITEM_FIELDS = ("ext_id", "title", "url", "original_url", "source", "summary",
               "category", "score", "status", "enrich_state")


def recent_items(store: state.Store, *, days: int = ITEMS_DAYS, limit: int = ITEMS_CAP,
                 now: datetime | None = None) -> list[dict[str, Any]]:
    """近 N 天批次里的**条目**（带批次身份），最新在前。

    为什么不在 `/view` 契约里：那是给宿主/面板的只读契约，形状要稳、也不该背几百条条目。
    条目面是「我自己的页面要渲染」的数据，跟 `action_snapshot()` 同一类。

    `batch_source` 是配置里的源名（`aihot`），`source` 是条目自己的发布者
    （`LMSYS：Blog`）—— 两个都叫 source 会串味，所以分开命名。
    """
    moment = now or datetime.now()
    out: list[dict[str, Any]] = []
    for back in range(days - 1, -1, -1):
        day = (moment - timedelta(days=back)).strftime("%Y-%m-%d")
        batch_dir = store.root / "batches" / day
        if not batch_dir.is_dir():
            continue
        for path in sorted(batch_dir.glob("*.json")):
            batch = _read_json(path)
            raw = batch.get("items") or []
            if not isinstance(raw, list):
                continue
            for item in raw:
                if not isinstance(item, dict):
                    continue
                row: dict[str, Any] = {
                    "date": day,
                    "slot": str(batch.get("slot") or ""),
                    "batch_source": str(batch.get("source") or path.stem),
                    "batch_title": str(batch.get("title") or ""),
                    "batch_file": path.stem,
                    "has_card": bool(batch.get("card_id")),
                }
                for key in ITEM_FIELDS:
                    row[key] = item.get(key)
                out.append(row)
    out.sort(key=lambda r: (str(r.get("date") or ""), str(r.get("slot") or "")), reverse=True)
    return out[:limit]


def filter_items(items: list[dict[str, Any]], *, q: str = "", source: str = "",
                 status: str = "") -> list[dict[str, Any]]:
    """页面筛选：关键词（标题/摘要/发布者/分类/批次标题）+ 配置源 + 状态。

    全部在服务端做 —— 这样页面不用 JS，`curl` 也能筛（和「自包含」这条底线一致）。
    """
    needle = q.strip().lower()
    out: list[dict[str, Any]] = []
    for row in items:
        if source and str(row.get("batch_source") or "") != source:
            continue
        if status and str(row.get("status") or "unread") != status:
            continue
        if needle:
            hay = " ".join(str(row.get(k) or "") for k in
                           ("title", "summary", "source", "category", "batch_title")).lower()
            if needle not in hay:
                continue
        out.append(row)
    return out


def items_snapshot(news_dir: Path, *, days: int = ITEMS_DAYS,
                   now: datetime | None = None) -> dict[str, Any]:
    """「条目」Tab 与驾驶舱「最近入库」的数据面（页面专用）。"""
    cfg = config.load(news_dir)
    items = recent_items(state.Store(news_dir), days=days, now=now)
    return {
        "days": days,
        "items": items,
        "total": len(items),
        "sources": sorted(cfg.sources),
        # 状态取值从数据里现取，不硬编码一份清单（有新的就自动出现）
        "statuses": sorted({str(i.get("status") or "unread") for i in items}),
    }


def pending_items(store: state.Store, source: str, *, limit: int = 12) -> list[dict[str, Any]]:
    """顺延队列里到底有哪些条目（页面展开用；`pending_total` 只是个数字）。

    `pending/<source>.jsonl` 是本包的私有布局，所以**只有本包能读** —— 宿主拿不到这个，
    也不该拿：要展示就读契约。
    """
    path = store.root / "pending" / f"{source}.jsonl"
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    out.append(item)
                if len(out) >= limit:
                    break
    except OSError:
        return []
    return out


def action_snapshot(news_dir: Path, *, now: datetime | None = None,
                    event_days: int = 7, queue_days: int = 30) -> dict[str, Any]:
    """写操作页需要的**动作面数据**（不放进 `/view` 契约）。

    为什么分开：`/view` 是给宿主/面板的**只读**契约，形状要稳；这里是「我自己的页面要
    渲染按钮」的输入，随交互一起演化。分开之后面板不会因为加了按钮而看到半成品字段。
    """
    from newspipe import events

    moment = now or datetime.now()
    cfg = config.load(news_dir)
    store = state.Store(news_dir)
    pending = {name: pending_items(store, name)
               for name, src in cfg.sources.items() if store.pending_count(name)}
    return {
        "generated_at": moment.isoformat(timespec="seconds"),
        "queue": events.queue(news_dir, days=queue_days),
        "events": [e for e in events.list_events(news_dir, days=event_days, unconsumed=True,
                                                 limit=30)
                   if e.get("type") != "favorite"],
        "pending": pending,
        "sources_hash": edit.file_hash(news_dir / "sources.yaml"),
        "run_targets": ["am", "noon", "pm", "poll"],
    }


# ------------------------------------------------------------------ 结果横幅编解码
#: POST 之后用 303 把结果编码进 URL（PRG）：这样刷新页面不会重放写操作。
#: 不存服务端会话 —— 单用户本地工具，为一条横幅引入会话存储不值得。
FLASH_MAX = 2000


def flash_encode(payload: dict[str, Any]) -> str:
    import base64

    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")[:FLASH_MAX]
    return base64.urlsafe_b64encode(raw).decode("ascii")


def flash_decode(token: str) -> dict[str, Any] | None:
    import base64

    if not token:
        return None
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception:                                  # noqa: BLE001 —— 坏的横幅不该 500
        return None
    return data if isinstance(data, dict) else None


def build(news_dir: Path | None = None, *, now: datetime | None = None) -> dict[str, Any]:
    """构建视图。`now` 可注入（测试别用墙钟）；配置坏了就让 ConfigError 冒出去。"""
    news_dir = Path(news_dir or config.default_news_dir())
    cfg = config.load(news_dir)
    store = state.Store(news_dir)
    moment = now or datetime.now()
    digest = moment.strftime("%Y-%m-%d")

    rows = [_source_row(src, store, now=moment) for src in cfg.sources.values()]
    today = _today_batches(store, digest)
    items = sum(b["count"] for b in today)
    marked = sum(b["marked"] for b in today)
    budget = store.budget_state(digest)
    usage = store.usage(digest)
    log = news_dir / "actions.log"
    preferences = news_dir / "preferences.md"

    return {
        "contract_version": CONTRACT_VERSION,
        "generated_at": moment.isoformat(timespec="seconds"),
        "chat": cfg.chat,
        "slots": dict(cfg.slots),
        "service": {
            "channel": cfg.service.channel,
            "inbound_mode": cfg.service.inbound_mode,
            "tick_seconds": cfg.service.tick_seconds,
            "view_path": cfg.service.view_path,
            "view_enabled": cfg.service.view_enabled,
        },
        "sources": rows,
        "sources_hash": edit.file_hash(news_dir / "sources.yaml"),
        "today": today,
        "summary": {
            "batches": len(today),
            "items": items,
            "marked": marked,
            "read_rate": round(marked / items, 2) if items else None,
            "actions_today": _line_count(log) and sum(
                1 for line in log.open(encoding="utf-8") if line.startswith(digest)),
            "cards_today": int(budget.get("cards") or 0),
            "last_card_ts": budget.get("last_card_ts"),
        },
        "llm": {
            "calls": int(usage.get("calls") or 0),
            "degraded": int(usage.get("degraded") or 0),
            "chars": int(usage.get("chars") or 0),
            "reasons": usage.get("reasons") or {},
        },
        "history": history(store, days=HISTORY_DAYS, now=moment),
        "preferences_preview": (preferences.read_text(encoding="utf-8")[-600:]
                                if preferences.is_file() else ""),
    }


# ------------------------------------------------------------------ 服务端渲染页
#: 页面样式在 `style.py`（纯 CSS、零 JS、零外部请求；动效全走平台原生能力）。
_CSS = CSS


def _pill(tone: str, label: str) -> str:
    return f'<span class="pill {html.escape(tone)}">{html.escape(label)}</span>'


def _form(action: str, token: str, fields: dict[str, Any], label: str, *,
          danger: bool = False, cls: str = "", extra: str = "") -> str:
    """一个写操作按钮 = 一个 POST 表单（无 JS、无前端构建链）。

    `token` 是服务启动时生成的随机值，页面渲染时嵌进表单。**没有它不行**：本地端口对
    浏览器是可达的，你浏览器里任何一个网页都能 POST 到这个地址 —— 这不是理论风险。
    """
    hidden = "".join(
        f'<input type="hidden" name="{html.escape(str(k))}" value="{html.escape(str(v))}">'
        for k, v in fields.items())
    css = "btn" + (" danger" if danger else "") + (f" {cls}" if cls else "")
    return (f'<form method="post" action="/api/actions/{html.escape(action)}" class="inline">'
            f'<input type="hidden" name="token" value="{html.escape(token)}">{hidden}{extra}'
            f'<button class="{css}" type="submit">{html.escape(label)}</button></form>')


def _banner(flash: dict[str, Any] | None) -> str:
    """把 CLI 信封渲染成横幅 —— 而不是「操作成功」四个字。

    失败时 `error.message` + `error.hint` 都要露出来：hint 里通常就是下一步该敲的命令。
    """
    if not flash:
        return ""
    ok = bool(flash.get("ok"))
    label = html.escape(str(flash.get("label") or "操作"))
    if ok:
        head, body = f"✅ {label} 完成", html.escape(str(flash.get("summary") or "完成"))
    else:
        head = f"⛔ {label} 失败"
        body = html.escape(str(flash.get("message") or "未知错误"))
        if flash.get("hint"):
            body += f"<br>→ {html.escape(str(flash['hint']))}"
    detail = html.escape(str(flash.get("detail") or ""))
    cli = html.escape(str(flash.get("cli") or ""))
    return (f'<div class="banner {"ok" if ok else "err"}"><b>{head}</b>'
            f'<div>{body}</div>'
            + (f"<pre>{detail}</pre>" if detail else "")
            + (f'<div class="note">CLI：<code>{cli}</code></div>' if cli else "")
            + "</div>")


def _link(title: str, url: str, *, limit: int = 110) -> str:
    text = html.escape(title[:limit])
    if not url:
        return text
    return f'<a href="{html.escape(url)}" target="_blank" rel="noopener">{text}</a>'


#: 四个 Tab：服务端路由（可深链、可加书签、无 JS）。`GET /view` 仍是同一进程里的 JSON 契约。
TABS: tuple[tuple[str, str, str], ...] = (
    ("cockpit", "驾驶舱", "/"),
    ("items", "条目", "/items"),
    ("config", "配置", "/config"),
    ("ops", "运维", "/ops"),
)


def _nav(tab: str) -> str:
    links = "".join(
        f'<a class="tab{" on" if key == tab else ""}" href="{href}">{label}</a>'
        for key, label, href in TABS)
    return f'<nav class="tabs">{links}</nav>'


def _tab_label(tab: str) -> str:
    return next((label for key, label, _ in TABS if key == tab), tab)


def _bar(value: int, peak: int) -> str:
    """纯 CSS 条形：不引图表库、不发外部请求（页面有一条「不许出现 http://」的测试）。"""
    pct = 0 if not peak else max(2, round(value / peak * 100))
    cls = "bar zero" if not value else "bar"
    return f'<span class="{cls}" style="width:{pct}%"></span><span class="bar-n">{value}</span>'


def _history_html(view: dict[str, Any]) -> str:
    """近 N 天趋势表。**页面不发明数据**：列头就是落盘的东西。"""
    days = view.get("history") or []
    if not days:
        return ""
    peak_items = max((int(d.get("items") or 0) for d in days), default=0)
    peak_cards = max((int(d.get("cards") or 0) for d in days), default=0)
    peak_pushed = max((int(d.get("pushed") or 0) for d in days), default=0)
    body = []
    for d in days:
        rate = "—" if d.get("read_rate") is None else f"{round(d['read_rate'] * 100)}%"
        calls, degraded = int(d.get("calls") or 0), int(d.get("degraded") or 0)
        ai = "—" if not calls else (f"{degraded} / {calls}" if degraded else f"0 / {calls}")
        srcs = d.get("sources") or []
        body.append(
            "<tr>"
            f'<td><code>{html.escape(str(d.get("date"))[5:])}</code></td>'
            f'<td class="barcell">{_bar(int(d.get("items") or 0), peak_items)}</td>'
            f"<td>{rate}</td>"
            f'<td class="barcell">{_bar(int(d.get("cards") or 0), peak_cards)}</td>'
            f'<td class="barcell">{_bar(int(d.get("pushed") or 0), peak_pushed)}</td>'
            f"<td>{ai}</td>"
            f'<td class="hint">{html.escape("、".join(str(s) for s in srcs)) or "—"}</td>'
            "</tr>")
    total_items = sum(int(d.get("items") or 0) for d in days)
    total_cards = sum(int(d.get("cards") or 0) for d in days)
    total_pushed = sum(int(d.get("pushed") or 0) for d in days)
    return (
        f'<h3>近 {len(days)} 天 <span class="hint">合计 {total_items} 条 / {total_cards} 张卡 / '
        f"{total_pushed} 条入库；只算落盘过的（批次 / 预算 / 用量 / 推送台账）—— "
        "各源历史成功率没落盘，所以不给</span></h3>"
        '<table class="trend"><thead><tr><th>日期</th><th>条目</th><th>已标记率</th><th>发卡</th>'
        "<th>入库</th><th>AI 降级/调用</th><th>有产出的源</th></tr></thead>"
        f'<tbody>{"".join(body)}</tbody></table>')


def _today_batches_html(view: dict[str, Any]) -> str:
    """今日批次表（驾驶舱「深入诊断」折叠区用；运维页不再重复渲染）。"""
    batches = []
    for b in view["today"]:
        batches.append(
            "<tr>"
            f"<td><code>{html.escape(str(b['file']))}</code></td>"
            f"<td>{html.escape(str(b['title'] or b['source'] or ''))}</td>"
            f"<td>{html.escape(str(b['slot'] or ''))}</td>"
            f"<td>{b['marked']}/{b['count']}</td>"
            f"<td>{'已发卡' if b['has_card'] else '仅原料'}</td>"
            f"<td>{b['overflow'] or ''}</td>"
            "</tr>")
    return ('<h3>今日批次</h3><table><thead><tr><th>文件</th><th>标题</th><th>槽位</th>'
            "<th>已标记</th><th>卡片</th><th>顺延</th></tr></thead>"
            '<tbody>' + ("".join(batches) or '<tr><td colspan="6" class="empty">今天还没有批次</td></tr>')
            + "</tbody></table>")


def _drawer_body(src: dict[str, Any], hist: list[dict[str, Any]]) -> str:
    """单源诊断抽屉的正文：点开信源行看到的「最近一次为什么这样 / 可能拦下它的闸」。

    诚实边界：**历史拦截原因没有落盘**（`state/status/<源>.json` 只留最后一次心跳），
    所以抽屉给的是「最近一次计数」+「生效中的闸」，不是「历史上被哪道闸拦了几次」。
    """
    name = str(src.get("name"))
    produced = [str(d.get("date"))[5:] for d in hist if name in (d.get("sources") or [])]
    detail = src.get("status_detail") or {}
    detail_txt = " · ".join(f"{k}={v}" for k, v in detail.items()) or "（没有计数）"
    quiet = f"静默时段 <code>{html.escape(str(src.get('quiet_hours') or '无'))}</code>"
    if src.get("priority") == "high":
        quiet += "（<code>high</code> 优先级可越过）"
    gates = [
        f"卡片形态 <code>{html.escape(str(src.get('form')))}</code>"
        + ("" if src.get("sends_card") else " —— <b>不发卡</b>，只落 state"),
        quiet,
        f"每日上限 <code>{src.get('max_cards_per_day')}</code> 张 · 最小间隔 "
        f"<code>{src.get('min_gap_min')}</code> 分钟",
        f"条数上限 <code>{src.get('max_items')}</code> · 最少 <code>{src.get('min_items')}</code>"
        f"（不够就攒批）· 触发 <code>{html.escape(str(src.get('trigger')))}</code>"
        + (f"（间隔 {src.get('interval_min')} 分钟）" if src.get("trigger") == "poll" else ""),
    ]
    queue_note = (f'顺延队列 <b>{src.get("pending_total")}</b> 条 —— 明细与「现在发」在 '
                  f'<a href="/ops">运维页</a>' if src.get("pending_total")
                  else "顺延队列 0 条")
    note = html.escape(str(src.get("status_note") or ""))
    # 没有历史就**不提产出** —— 「近 0 天产出 0 天」会被读成「源一直没拉到东西」，
    # 而无历史只是「还没有历史」。
    if hist:
        produced_line = (
            f'<p class="note">近 {len(hist)} 天有产出：'
            + (f'{len(produced)} 天（{html.escape("、".join(produced))}）' if produced
               else "<b>一天都没有</b> —— 源可能一直没拉到东西")
            + "</p>")
    else:
        produced_line = '<p class="note">还没有历史（没有批次落盘），所以不给产出统计。</p>'
    return (
        f'<p class="note">最近心跳 <code>{html.escape(str(src.get("status_ts") or "从未"))}</code>'
        + (f' · {note}' if note else "") + "</p>"
        f'<p class="note">最近一次计数：<code>{html.escape(detail_txt)}</code></p>'
        + produced_line
        + '<p class="note">生效中的闸（可能拦下它的）：<br>' + "<br>".join(gates) + "</p>"
        f'<p class="note">{queue_note} · 去重台账 {src.get("pushed_total")} 条</p>'
        '<p class="note">⚠️ 历史拦截原因<b>没有落盘</b>（只留最后一次心跳）—— 要精确归因看 '
        "<code>actions.log</code> 或 <code>state/status/</code>。")


def _health_sort_key(src: dict[str, Any]) -> tuple[int, str]:
    """异常源置顶（danger/warn），健康的跟后，停用的垫底 —— 首屏先看到需要处理的。"""
    tone = str(src.get("status_tone") or "")
    rank = {"danger": 0, "warn": 1}.get(tone, 2 if src.get("enabled") else 3)
    return (rank, str(src.get("name")))


def _health_rows(view: dict[str, Any]) -> str:
    """驾驶舱的信源区：**一行一源**（异常置顶），行内 `<details>` 展开诊断抽屉。

    单行只回答「好不好、为什么」；完整运行态表在运维页，不在这里重复。
    """
    hist = view.get("history") or []
    sources = sorted(view.get("sources") or [], key=_health_sort_key)
    if not sources:
        return ('<h2>信源心跳 <span class="hint">异常的排前面 · 点开任何一行是诊断抽屉</span></h2>'
                '<p class="empty">注册表里还没有信源</p>')
    rows = []
    for src in sources:
        label = "超期未运行" if src.get("stale") else str(src.get("status_label") or "未知")
        pill = _pill(str(src.get("status_tone") or ""), label)
        trigger = (f"轮询 {src['interval_min']:g}min" if src["trigger"] == "poll"
                   else ("定时 " + "/".join(src["slots"]) if src["slots"] else "手动"))
        note = str(src.get("status_note") or "")
        if not note:
            detail = {k: v for k, v in (src.get("status_detail") or {}).items()
                      if k not in ("enrich", "note", "card")}
            note = " · ".join(f"{k}={v}" for k, v in detail.items()) if detail else "—"
        rows.append(
            '<details class="srcrow">'
            f"<summary><code>{html.escape(str(src.get('name')))}</code>{pill}"
            f'<span class="hint">{html.escape(trigger)} · {html.escape(note[:80])}</span></summary>'
            f"{_drawer_body(src, hist)}"
            "</details>")
    return ('<h2>信源心跳 <span class="hint">异常的排前面 · 点开任何一行是诊断抽屉：'
            "最近一次为什么这样 / 生效中的闸</span></h2>" + "".join(rows))


def _cockpit_stats(view: dict[str, Any], page: dict[str, Any] | None) -> str:
    """KPI 收敛到 4 个：今日条目 / 今日发卡 / ⭐待入库 / AI 降级（仅异常时出现）。

    ⭐ 待入库是唯一带入口的 KPI —— 它指向运维页的入库回执（「做」的部分去运维页）。
    """
    s = view["summary"]
    pending = page.get("queue_pending") if isinstance(page, dict) else 0
    pending = pending if isinstance(pending, int) else 0
    cells = [
        f'<div class="stat"><b>{s["items"]}</b><span>今日条目</span></div>',
        f'<div class="stat"><b>{s["cards_today"] or 0}</b><span>今日发卡</span></div>',
        (f'<a class="statlink" href="/ops#queue"><div class="stat attn"><b>{pending}</b>'
         "<span>待入库 ⭐</span></div></a>" if pending
         else f'<div class="stat"><b>{pending}</b><span>待入库 ⭐</span></div>'),
    ]
    degraded = int(view["llm"]["degraded"] or 0)
    if degraded:
        cells.append(f'<div class="stat danger"><b>{degraded}</b><span>AI 降级</span></div>')
    return f'<div class="stats">{"".join(cells)}</div>'


def as_html(view: dict[str, Any], *, flash: dict[str, Any] | None = None,
            page: dict[str, Any] | None = None) -> str:
    """驾驶舱（`GET /`）：只回答「今天发了什么、是否正常」——只读，一个表单都没有。

    深入材料（14 天趋势 / 今日批次）折叠进「深入诊断」；信源完整运行态表在运维页；
    单源诊断抽屉并进信源行的展开区。首屏只留「看」的东西（审计 §4）。
    """
    svc = view["service"]
    diag = _history_html(view) + _today_batches_html(view)
    body = (
        _banner(flash)
        + _cockpit_stats(view, page)
        + _recent_html(page)
        + _health_rows(view)
        + f'<details class="drawer deep"><summary>深入诊断 · 趋势 / 今日批次</summary>{diag}</details>')
    return _page(tab="cockpit", sub_meta=view, svc=svc, body=body)


def cockpit_html(view: dict[str, Any], *, flash: dict[str, Any] | None = None,
                 page: dict[str, Any] | None = None) -> str:
    """驾驶舱：全局总览，**只读**（没有任何表单）。"""
    return as_html(view, flash=flash, page=page)


def ops_html(view: dict[str, Any], *, actions: dict[str, Any] | None = None,
             flash: dict[str, Any] | None = None) -> str:
    """运维：**动作面** —— 跑一轮 / 启停 / ⭐入库回执 / 顺延队列。

    与驾驶舱拆开（审计 §4）：这里不再重复渲染趋势 / 今日批次 / 诊断抽屉；
    未消费事件不再给人点 —— `events-ack` 动作保留在白名单里，消费归宿主/CLI。
    """
    act = actions or {}
    token = str(act.get("token") or "")
    on = bool(token)
    svc = view["service"]
    if not on:
        # 动作页没开动作就直说 —— 别渲染一堆按不动的按钮，也别装作这页有内容
        body = (_banner(flash) + '<h2>运维</h2><p class="empty">写操作未开启'
                "（<code>service.yaml: view.actions</code>）—— 跑一轮 / 启停 / ⭐入库回执 / "
                "现在发顺延，都在开启后出现在本页。</p>")
        return _page(tab="ops", sub_meta=view, svc=svc, body=body)

    src_hash = str(act.get("sources_hash") or "")
    targets = act.get("run_targets") or ["am", "noon", "pm", "poll"]
    opts = "".join(f'<option value="{html.escape(str(t))}">{html.escape(str(t))}</option>'
                   for t in targets)
    toolbar = (
        '<form method="post" action="/api/actions/run" class="toolbar">'
        f'<input type="hidden" name="token" value="{html.escape(token)}">'
        f'<label>目标 <select name="target">{opts}</select></label>'
        '<label><input type="checkbox" name="dry" value="1" checked> 试运行（不发卡）</label>'
        '<button class="btn primary" type="submit">跑一轮</button>'
        "</form>")

    note_input = '<input type="text" name="note" placeholder="备注" size="10">'
    qrows = []
    for e in act.get("queue") or []:
        p = e.get("payload") or {}
        title = str(p.get("title") or p.get("item_id") or "（无标题）")
        qrows.append(
            "<tr>"
            f'<td><code>{html.escape(str(e.get("ts") or ""))}</code></td>'
            f'<td>{html.escape(str(e.get("source") or ""))}</td>'
            f'<td>{_link(title, str(p.get("url") or ""))}</td>'
            f'<td class="actions">{_form("queue-ack", token, {"event_id": str(e.get("id") or "")}, "标记已入库", cls="primary", extra=note_input)}</td>'
            "</tr>")
    queue_section = (
        '<h2 id="queue">待入库队列 ⭐ <span class="hint">回执 = 已真的写进知识库；'
        "自动入库失败时在这里手动补</span></h2>"
        "<table><thead><tr><th>时间</th><th>来源</th><th>标题</th><th>操作</th></tr></thead>"
        "<tbody>" + ("".join(qrows) or '<tr><td colspan="4" class="empty">没有待入库的收藏</td></tr>')
        + "</tbody></table>")

    rows = []
    for src in sorted(view["sources"], key=_health_sort_key):
        label = "超期未运行" if src.get("stale") else str(src.get("status_label") or "未知")
        bits = [_form("source-toggle", token,
                      {"name": src["name"], "state": "disable" if src["enabled"] else "enable",
                       "base_hash": src_hash},
                      "停用" if src["enabled"] else "启用")]
        bits.append(_form("run", token, {"target": f"source:{src['name']}", "dry": "1"}, "试跑"))
        if src["pending_total"]:
            bits.append(_form("flush", token, {"name": src["name"]},
                              f"现在发 {src['pending_total']}", danger=True))
        rows.append(
            "<tr>"
            f"<td><code>{html.escape(src['name'])}</code></td>"
            f"<td>{_pill(str(src['status_tone']), label)}</td>"
            f'<td class="actions">{"".join(bits)}</td>'
            "</tr>")
    sources_section = (
        '<h2>信源 <span class="hint">启停与手动补跑；「为什么是这样」回驾驶舱点开信源行</span></h2>'
        '<table><thead><tr><th>源</th><th>心跳</th><th>操作</th></tr></thead><tbody>'
        + ("".join(rows) or '<tr><td colspan="3" class="empty">注册表里还没有信源</td></tr>')
        + "</tbody></table>")

    pend_blocks = []
    for name, items in (act.get("pending") or {}).items():
        prows = "".join(
            "<tr>"
            f"<td>{_link(str(it.get('title') or it.get('ext_id') or ''), str(it.get('url') or ''), limit=120)}</td>"
            f'<td class="note">{html.escape(str(it.get("source") or ""))}</td>'
            f'<td>{html.escape(str(it.get("score") or ""))}</td>'
            "</tr>" for it in items)
        pend_blocks.append(
            f'<h3><code>{html.escape(str(name))}</code> · 显示 {len(items)} 条 '
            + _form("flush", token, {"name": str(name)}, "现在发", danger=True)
            + "</h3><table><thead><tr><th>标题</th><th>来源</th><th>分</th></tr></thead>"
            + f"<tbody>{prows}</tbody></table>")
    pending_section = ""
    if pend_blocks:
        pending_section = (
            '<details class="drawer deep"><summary>顺延队列明细 · 点「现在发」立刻补跑</summary>'
            + "".join(pend_blocks) + "</details>")

    body = _banner(flash) + toolbar + queue_section + sources_section + pending_section
    return _page(tab="ops", sub_meta=view, svc=svc, body=body)


# ---------------------------------------------------------------- 配置页（表单驱动）
def _field_input(field: dict[str, Any], value: Any, *, prefix: str = "f.") -> str:
    """按 schema 的一项渲染一个控件。**加字段不用改这里** —— 类型只有 6 种。"""
    path = str(field.get("path") or "")
    kind = str(field.get("type") or "str")
    name = f"{prefix}{path}"
    label = str(field.get("label") or path)
    hint = str(field.get("help") or "")
    title = f' title="{html.escape(hint)}"' if hint else ""
    if kind == "bool":
        checked = " checked" if value else ""
        # 隐藏的 0：没勾也会提交，翻译层才能区分「关掉」与「没这个字段」
        return (f'<label class="f"{title}><input type="hidden" name="{name}" value="0">'
                f'<input type="checkbox" name="{name}" value="1"{checked}> {html.escape(label)}</label>')
    if kind == "enum":
        options = "".join(
            f'<option value="{html.escape(str(c))}"{" selected" if str(c) == str(value) else ""}>'
            f"{html.escape(str(c))}</option>" for c in (field.get("choices") or []))
        return (f'<label class="f"{title}>{html.escape(label)} '
                f'<select name="{name}">{options}</select></label>')
    if kind == "slots":
        boxes = "".join(
            f'<label class="f"><input type="checkbox" name="{name}" value="{html.escape(str(c))}"'
            f'{" checked" if c in (value or []) else ""}> {html.escape(str(c))}</label>'
            for c in (field.get("choices") or []))
        return f'<span class="f"{title}>{html.escape(label)} {boxes}</span>'
    if kind == "list":
        text = ", ".join(str(v) for v in (value or []))
        return (f'<label class="f"{title}>{html.escape(label)} '
                f'<input type="text" name="{name}" value="{html.escape(text)}" size="34" '
                f'placeholder="逗号分隔"></label>')
    itype = "number" if kind in ("int", "float") else "text"
    step = ' step="any"' if kind == "float" else ""
    shown = "" if value is None else str(value)
    return (f'<label class="f"{title}>{html.escape(label)} '
            f'<input type="{itype}"{step} name="{name}" value="{html.escape(shown)}" size="26"></label>')


def _source_form(schema: dict[str, Any], *, name: str, values: dict[str, Any], token: str,
                 base_hash: str, is_new: bool, submit: str) -> str:
    """四轴分组表单。字段来自 schema，值来自 `config.source_values`（编辑）或默认值（新增）。"""
    groups = []
    for axis in schema.get("axes") or []:
        fields = [f for f in schema.get("fields") or [] if f.get("axis") == axis.get("key")]
        if not fields:
            continue
        basic = "".join(_field_input(f, values.get(f["path"], f.get("default")))
                        for f in fields if not f.get("advanced"))
        adv_fields = [f for f in fields if f.get("advanced")]
        advanced = ""
        if adv_fields:
            inner = "".join(_field_input(f, values.get(f["path"], f.get("default")))
                            for f in adv_fields)
            advanced = (f'<details><summary>高级（{len(adv_fields)} 项）</summary>{inner}</details>')
        groups.append(f'<fieldset><legend>{html.escape(str(axis.get("label")))} '
                      f'<span class="hint">{html.escape(str(axis.get("hint")))}</span></legend>'
                      f'{basic}{advanced}</fieldset>')
    if is_new:
        head = ('<p><label class="f">信源名（英文/数字/下划线） '
                f'<input type="text" name="name" value="{html.escape(name)}" size="20"></label></p>')
    else:
        head = (f'<p>信源名 <code>{html.escape(name)}</code>'
                f'<input type="hidden" name="name" value="{html.escape(name)}"></p>')
    return (f'<form method="post" action="/api/actions/source-save">'
            f'<input type="hidden" name="token" value="{html.escape(token)}">'
            f'<input type="hidden" name="base_hash" value="{html.escape(base_hash)}">'
            f"{head}{''.join(groups)}"
            '<p><label class="f"><input type="checkbox" name="dry" value="1" checked>'
            " 先演练（--dry-run，不落盘）</label>"
            f'<button class="btn danger" type="submit">{html.escape(submit)}</button>'
            '<span class="hint">取消勾选「先演练」才是真写盘。'
            "保存 = 整体替换该源的配置块（块内注释会丢，CLI 会提示）</span></p></form>")


def config_html(view: dict[str, Any], *, schema: dict[str, Any], actions: dict[str, Any] | None = None,
                flash: dict[str, Any] | None = None, page: dict[str, Any] | None = None) -> str:
    """配置页：信源增删改 + 四轴策略表单 + RSS 搜索/订阅。

    表单字段由 `config.describe_schema()` 生成 —— 这是「表单不是第二份 schema」的落点。
    所有写操作仍然只经 `source set|remove|enable|disable`（CLI 的校验与原子写）。
    """
    pg = page or {}
    act = actions or {}
    token = str(act.get("token") or "")
    on = bool(token)
    src_hash = str(act.get("sources_hash") or pg.get("hash") or "")
    svc = view["service"]
    rows = []
    for src in view["sources"]:
        if on:
            toggle = _form("source-toggle", token,
                           {"name": src["name"], "state": "disable" if src["enabled"] else "enable",
                            "base_hash": src_hash}, "停用" if src["enabled"] else "启用")
            edit = f'<a class="btn" href="/config?edit={html.escape(str(src["name"]))}">编辑</a>'
            remove = _form("source-remove", token, {"name": src["name"], "base_hash": src_hash},
                           "删除", danger=True)
            cell = f"<td>{edit} {toggle} {remove}</td>"
        else:
            # 没开写操作就**不渲染按钮** —— 给一个按下去只会 403 的按钮是骗人
            cell = '<td class="hint">写操作未开启</td>'
        rows.append(
            "<tr>"
            f'<td><code>{html.escape(str(src["name"]))}</code></td>'
            f'<td>{html.escape(str(src["adapter"]))}</td>'
            f'<td>{html.escape(str(src["trigger"]))}{"/" + ",".join(src["slots"]) if src["slots"] else ""}</td>'
            f'<td>{html.escape(str(src["form"]))}/{html.escape(str(src["summary_mode"]))}</td>'
            f'<td>{"✅" if src["enabled"] else "⛔"}</td>'
            f"{cell}"
            "</tr>")
    source_table = (
        '<h2>信源清单 <span class="hint">编辑 / 启停 / 删除；保存前默认先演练</span></h2>'
        "<table><thead><tr><th>源</th><th>适配器</th><th>节奏</th><th>形态/加工</th><th>启用</th>"
        "<th>操作</th></tr></thead><tbody>"
        + ("".join(rows) or '<tr><td colspan="6" class="empty">还没有信源</td></tr>')
        + "</tbody></table>")

    edit_section = ""
    edit_name = str(pg.get("edit") or "")
    if on and edit_name and pg.get("values") is not None:
        edit_section = (f'<h2>编辑 <code>{html.escape(edit_name)}</code></h2>'
                        + _source_form(schema, name=edit_name, values=pg["values"], token=token,
                                       base_hash=src_hash, is_new=False, submit="保存这个信源"))
    if on:
        new_section = ('<h3 id="new">路径② 直接手填</h3>'
                       + _source_form(schema, name=str(pg.get("prefill_name") or ""),
                                      values=pg.get("prefill") or {}, token=token,
                                      base_hash=src_hash, is_new=True, submit="新建信源"))
    else:
        # 没开写操作就不渲染表单：按下去只会 403 的按钮是骗人
        new_section = ('<h3 id="new">路径② 直接手填</h3><p class="empty">写操作未开启'
                       '（<code>service.yaml: view.actions</code>）—— 表单不渲染。</p>')

    # ---- RSS 搜索
    q = str(pg.get("q") or "")
    hits = pg.get("catalog") or []
    cand_rows = []
    for i, row in enumerate(hits):
        feed = str(row.get("feed") or "")
        cand_rows.append(
            '<div class="cand">'
            f'<b>{html.escape(str(row.get("name") or ""))}</b> '
            f'<span class="hint">{" · ".join(row.get("tags") or [])}'
            f'{" · 实测 " + str(row["verified"]) if row.get("verified") else ""}</span><br>'
            f'<code>{html.escape(feed)}</code>'
            + (f'<div class="note">{html.escape(str(row.get("note")))}</div>' if row.get("note") else "")
            + f'<div><a class="btn" href="/config?use={i}#new">用这个新建信源</a></div></div>')
    discover = pg.get("discover")
    found_rows = []
    if discover:
        for i, cand in enumerate(discover.get("candidates") or []):
            found_rows.append(
                '<div class="cand">'
                f'<code>{html.escape(str(cand.get("url") or ""))}</code>'
                f'<div class="note">{html.escape(str(cand.get("how") or ""))}'
                f'{" · " + html.escape(str(cand["title"])) if cand.get("title") else ""}</div>'
                f'<div><a class="btn" href="/config?feed={urlquote(str(cand.get("url") or ""))}#new">'
                "用这个新建信源</a></div></div>")
    rss_section = (
        '<h3 id="rss">路径① RSS 搜索 / 订阅 <span class="hint">目录里的条目都在本机 RSSHub 上实测可用</span></h3>'
        '<form method="get" action="/config" class="toolbar">'
        f'<label class="f">关键词 <input type="text" name="q" value="{html.escape(q)}" size="24" '
        'placeholder="如 AI、羊毛、少数派"></label>'
        '<button class="btn primary" type="submit">搜目录</button></form>'
        + ("".join(cand_rows) or ('<p class="empty">目录里没有匹配的条目</p>' if q else ""))
        + '<form method="get" action="/config" class="toolbar">'
        f'<label class="f">站点地址 <input type="text" name="u" size="30" '
        f'value="{html.escape(str(pg.get("discover_url") or ""))}" '
        'placeholder="如 sspai.com"></label>'
        '<button class="btn primary" type="submit">发现 feed</button>'
        '<span class="hint">抓页面里的 &lt;link rel=alternate&gt;，没有再探 /feed 等常见路径</span></form>'
        + (f'<div class="banner err">{html.escape(str(pg.get("discover_error")))}</div>'
           if pg.get("discover_error") else "")
        + (f'<div class="note">{html.escape(str(discover.get("note") or ""))}</div>' if discover else "")
        + ("".join(found_rows) or ('<p class="empty">没发现 feed</p>'
                                   if discover else "")))

    # 动线重排（审计 §4）：「添加信源」是这页的高频意图，搜索与手填表单合成一个区块置顶，
    # 消掉原来「页中搜索 → 跳页底 #new」的两头跳；清单降为第二块。
    add_section = ('<h2 id="add">添加信源</h2>' + rss_section + new_section)
    body = _banner(flash) + edit_section + add_section + source_table
    return _page(tab="config", sub_meta=view, svc=svc, body=body)


# ------------------------------------------------------------------ 内容层（条目浏览）
def _item_rows(rows: list[dict[str, Any]], *, show_batch: bool = False,
               with_summary: bool = True) -> str:
    """条目表格行 —— 摘要并入标题行下方（第二行小字），表格少一列、扫读快一截。

    标题链 **`original_url` 优先** —— 那才是真实出处；`url` 常是抓取侧的代理页
    （aihot 那种 `/items/<id>`）。两个都没有就不加链接。
    """
    out = []
    for idx, row in enumerate(rows, 1):
        title = _link(str(row.get("title") or row.get("ext_id") or "（无标题）"),
                      str(row.get("original_url") or row.get("url") or ""))
        meta = [html.escape(str(row[k])) for k in ("source", "category") if row.get(k)]
        if row.get("score") not in (None, ""):
            meta.append(f"分 {html.escape(str(row['score']))}")
        status = str(row.get("status") or "unread")
        tone = {"read": "ok", "favorite": "warn", "dismissed": "muted"}.get(status, "muted")
        summary = html.escape(str(row.get("summary") or "")[:160])
        cells = [f"<td>{idx}</td>", f"<td>{_pill(tone, html.escape(status))}</td>"]
        if show_batch:
            date = html.escape(str(row.get("date") or ""))
            stem = html.escape(str(row.get("batch_file") or ""))
            label = (f'{date[5:]} {html.escape(str(row.get("batch_source") or ""))}'
                     f'/{html.escape(str(row.get("slot") or ""))}')
            cells.append(f'<td><a href="/batch/{date}/{stem}"><code>{label}</code></a></td>')
        title_cell = f"<td>{title}"
        if with_summary and summary:
            title_cell += f'<div class="hint clamp2">{summary}</div>'
        title_cell += "</td>"
        cells.append(title_cell)
        cells.append(f'<td class="hint">{" · ".join(meta) or "—"}</td>')
        out.append("<tr>" + "".join(cells) + "</tr>")
    return "".join(out)


def _recent_html(page: dict[str, Any] | None) -> str:
    """驾驶舱的「最近入库」—— 之前整页只有计数，看不到收进来的任何一条标题。"""
    pg = page or {}
    rows = pg.get("recent") or []
    if not rows:
        return ('<h2>最近入库</h2><p class="empty">近 7 天没有条目落盘。'
                '（<a href="/items">条目</a> 页可回看更多天、也能筛选）</p>')
    total = pg.get("recent_total")
    tail = f"，近 7 天共 {total} 条" if isinstance(total, int) and total > len(rows) else ""
    return (
        f'<h2>最近入库 <span class="hint">最新 {len(rows)} 条{tail} · '
        '全部与筛选在 <a href="/items">条目</a> 页</span></h2>'
        "<table><thead><tr><th>#</th><th>状态</th><th>标题</th><th>发布者/分类/分</th></tr></thead>"
        f'<tbody>{_item_rows(rows, with_summary=False)}</tbody></table>')


def items_html(view: dict[str, Any], *, page: dict[str, Any] | None = None,
               flash: dict[str, Any] | None = None) -> str:
    """「条目」Tab：近 N 天全部条目 + 服务端筛选（无 JS，`curl` 也能筛）。"""
    pg = page or {}
    all_rows = pg.get("items") or []
    q = str(pg.get("q") or "")
    source = str(pg.get("source") or "")
    status = str(pg.get("status") or "")
    days = int(pg.get("days") or ITEMS_DAYS)
    rows = filter_items(all_rows, q=q, source=source, status=status)
    shown = rows[:ITEMS_RENDER_CAP]

    def opts(values: list[str], current: str) -> str:
        return "".join(
            f'<option value="{html.escape(v)}"{" selected" if v == current else ""}>'
            f"{html.escape(v)}</option>" for v in values)

    day_opts = "".join(
        f'<option value="{d}"{" selected" if d == days else ""}>{d} 天</option>' for d in (3, 7, 14))
    truncation = (f"，只渲染前 {ITEMS_RENDER_CAP} 条（用筛选缩小范围）"
                  if len(rows) > ITEMS_RENDER_CAP else "")
    form = (
        '<form method="get" action="/items" class="toolbar">'
        f'<label class="f">关键词 <input type="text" name="q" value="{html.escape(q)}" size="24" '
        'placeholder="标题 / 摘要 / 发布者 / 分类"></label>'
        f'<label class="f">源 <select name="source"><option value="">全部</option>'
        f'{opts(list(pg.get("sources") or []), source)}</select></label>'
        f'<label class="f">状态 <select name="status"><option value="">全部</option>'
        f'{opts(list(pg.get("statuses") or []), status)}</select></label>'
        f'<label class="f">回看 <select name="days">{day_opts}</select></label>'
        '<button class="btn primary" type="submit">筛选</button> '
        '<a class="btn" href="/items">清空</a>'
        f'<span class="hint">近 {days} 天共 {len(all_rows)} 条，命中 {len(rows)} 条{truncation}</span>'
        "</form>")
    fallback = ('<tr><td colspan="5" class="empty">没有命中的条目'
                "（换个关键词、放宽天数，或去 <a href=\"/config\">配置</a> 加信源）</td></tr>")
    body = (
        f'{_banner(flash)}{form}'
        "<table><thead><tr><th>#</th><th>状态</th><th>批次</th><th>标题</th>"
        "<th>发布者/分类/分</th></tr></thead>"
        f'<tbody>{_item_rows(shown, show_batch=True) or fallback}</tbody></table>')
    return _page(tab="items", sub_meta=view, svc=view["service"], body=body)


def batch_html(view: dict[str, Any], *, page: dict[str, Any] | None = None,
               flash: dict[str, Any] | None = None) -> str:
    """批次详情：这一个批次里到底有哪些条目（标题/发布者/分类/分数/摘要/原文链接/状态）。"""
    pg = page or {}
    batch = pg.get("batch") or {}
    items = pg.get("items") or []
    date = str(pg.get("date") or "")
    stem = str(pg.get("stem") or "")
    if not batch:
        body = (f'{_banner(flash)}<p class="empty">没有这个批次：'
                f"<code>{html.escape(date)}/{html.escape(stem)}</code></p>"
                '<p class="note">批次文件在 <code>state/batches/&lt;日期&gt;/&lt;源&gt;-&lt;槽位&gt;.json</code>。'
                '列表见 <a href="/">驾驶舱</a>「今日批次」，或去 <a href="/items">条目</a> 页。</p>')
        return _page(tab="items", sub_meta=view, svc=view["service"], body=body)

    marked = sum(1 for i in items if str(i.get("status") or "unread") != "unread")
    stats = "".join([
        f'<div class="stat"><b>{len(items)}</b><span>条目</span></div>',
        f'<div class="stat"><b>{marked}</b><span>已标记</span></div>',
        f'<div class="stat"><b>{"已发卡" if batch.get("card_id") else "仅原料"}</b><span>卡片</span></div>',
        f'<div class="stat"><b>{batch.get("overflow") or 0}</b><span>顺延</span></div>',
    ])
    meta = " · ".join(filter(None, [
        f'源 <code>{html.escape(str(batch.get("source") or ""))}</code>',
        f'槽位 <code>{html.escape(str(batch.get("slot") or ""))}</code>',
        f'适配器 <code>{html.escape(str(batch.get("adapter") or ""))}</code>',
        f'形态 <code>{html.escape(str(batch.get("form") or ""))}</code>',
        f'生成 <code>{html.escape(str(batch.get("created_at") or ""))}</code>',
        f'更新 <code>{html.escape(str(batch.get("updated_at") or ""))}</code>',
    ]))
    fallback = '<tr><td colspan="4" class="empty">这个批次没有条目</td></tr>'
    body = (
        f'{_banner(flash)}'
        '<p class="note"><a href="/items">← 条目</a> · <a href="/">驾驶舱</a> · '
        f'<code>state/batches/{html.escape(date)}/{html.escape(stem)}.json</code></p>'
        f'<h2>{html.escape(str(batch.get("title") or stem))}</h2>'
        f'<p class="sub">{meta}</p><div class="stats">{stats}</div>'
        "<h2>条目</h2><table><thead><tr><th>#</th><th>状态</th><th>标题</th>"
        "<th>发布者/分类/分</th></tr></thead>"
        f'<tbody>{_item_rows(items) or fallback}</tbody></table>')
    return _page(tab="items", sub_meta=view, svc=view["service"], body=body)


def _page(*, tab: str, sub_meta: dict[str, Any], svc: dict[str, Any], body: str,
          footer_extra: str = "") -> str:
    """**所有页面共用的唯一外壳**：导航 + 标题 + 正文 + 页脚。

    部署元信息（生成时间 / 通道 / 入站 / 投递群）是排障用的，压进页脚小字 ——
    它不该占每页正文的第一行（审计 §4「折叠」）。
    """
    meta = (f"生成于 <code>{html.escape(str(sub_meta.get('generated_at')))}</code>"
            f" · 通道 {html.escape(str(svc.get('channel')))}"
            f" · 入站 {html.escape(str(svc.get('inbound_mode')))}"
            f" · 投递群 <code>{html.escape(str(sub_meta.get('chat')))}</code>")
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>newspipe · {_tab_label(tab)}</title><style>{_CSS}</style></head><body>
{_nav(tab)}
<h1>newspipe · {_tab_label(tab)}</h1>
{body}
<footer>只读契约 <code>GET /view</code> v{sub_meta.get('contract_version')}（宿主/面板的数据面）· {meta}。
{footer_extra}</footer>
</body></html>
"""
