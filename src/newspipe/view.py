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
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from newspipe import config, state

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
        "preferences_preview": (preferences.read_text(encoding="utf-8")[-600:]
                                if preferences.is_file() else ""),
    }


# ------------------------------------------------------------------ 服务端渲染页
_CSS = """
:root{color-scheme:light dark}
body{font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC",sans-serif;
     margin:0;padding:24px;max-width:1100px}
h1{font-size:20px;margin:0 0 4px}
h2{font-size:15px;margin:22px 0 10px}
.sub{opacity:.7;margin:0 0 16px}
.stats{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:8px}
.stat{border:1px solid rgba(128,128,128,.35);border-radius:8px;padding:8px 12px;min-width:92px}
.stat b{display:block;font-size:18px}
.stat span{font-size:12px;opacity:.7}
table{border-collapse:collapse;width:100%}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid rgba(128,128,128,.25);vertical-align:top}
th{font-size:12px;opacity:.7;font-weight:600}
code{font-size:12px;opacity:.85}
.pill{display:inline-block;border-radius:999px;padding:1px 8px;font-size:12px;
      border:1px solid rgba(128,128,128,.45)}
.pill.ok{border-color:#2f9e44;color:#2f9e44}
.pill.warn{border-color:#e8a33d;color:#e8a33d}
.pill.danger{border-color:#e03131;color:#e03131}
.pill.muted{opacity:.6}
.empty{opacity:.6}
footer{margin-top:26px;font-size:12px;opacity:.6}
"""


def _pill(tone: str, label: str) -> str:
    return f'<span class="pill {html.escape(tone)}">{html.escape(label)}</span>'


def as_html(view: dict[str, Any]) -> str:
    """同一份视图的服务端渲染（`GET /`）：不引前端、不发任何外部请求。

    面板（宿主）走 JSON 契约；这一页是给「从服务门户点进来」的场景用的。
    """
    s = view["summary"]
    rows = []
    for src in view["sources"]:
        detail = " ".join(f"{k}:{v}" for k, v in (src.get("status_detail") or {}).items()
                          if k not in ("enrich", "note", "card"))
        if src.get("status_note"):
            detail = f"{detail} {src['status_note']}".strip()
        trigger = (f"轮询 {src['interval_min']:g}min" if src["trigger"] == "poll"
                   else "定时 " + "/".join(src["slots"]) or "手动")
        rows.append(
            "<tr>"
            f"<td><code>{html.escape(src['name'])}</code></td>"
            f"<td>{html.escape(str(src['card_title']))}</td>"
            f"<td>{html.escape(trigger)}</td>"
            f"<td>{html.escape(src['form'])}/{html.escape(src['summary_mode'])}</td>"
            f"<td>{_pill(str(src['status_tone']), str(src['status_label']))}</td>"
            f"<td><code>{html.escape(str(src['status_ts'] or '从未'))}</code></td>"
            f"<td>{src['pushed_total']}{' / 顺延 ' + str(src['pending_total']) if src['pending_total'] else ''}</td>"
            f"<td><code>{html.escape(detail)}</code></td>"
            "</tr>")
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
    svc = view["service"]
    read_rate = "—" if s["read_rate"] is None else f"{round(s['read_rate'] * 100)}%"
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>newspipe · 资讯管线</title><style>{_CSS}</style></head><body>
<h1>newspipe · 资讯管线</h1>
<p class="sub">生成于 <code>{html.escape(view['generated_at'])}</code> ·
通道 {html.escape(svc['channel'])} · 入站 {html.escape(svc['inbound_mode'])} ·
槽位 {html.escape(', '.join(f'{k}:{v}' for k, v in view['slots'].items()))} ·
投递群 <code>{html.escape(str(view['chat']))}</code></p>
<div class="stats">
  <div class="stat"><b>{s['batches']}</b><span>今日批次</span></div>
  <div class="stat"><b>{s['items']}</b><span>今日条目</span></div>
  <div class="stat"><b>{read_rate}</b><span>已标记率</span></div>
  <div class="stat"><b>{s['actions_today'] or 0}</b><span>今日点击</span></div>
  <div class="stat"><b>{s['cards_today'] or 0}</b><span>今日发卡</span></div>
  <div class="stat"><b>{view['llm']['calls']}</b><span>AI 调用</span></div>
  <div class="stat"><b>{view['llm']['degraded']}</b><span>AI 降级</span></div>
</div>
<h2>信源</h2>
<table><thead><tr><th>源</th><th>卡片标题</th><th>节奏</th><th>形态/加工</th><th>心跳</th>
<th>最近</th><th>去重/顺延</th><th>明细</th></tr></thead>
<tbody>{''.join(rows) or '<tr><td colspan="8" class="empty">注册表里还没有信源</td></tr>'}</tbody></table>
<h2>今日批次</h2>
<table><thead><tr><th>文件</th><th>标题</th><th>槽位</th><th>已标记</th><th>卡片</th><th>顺延</th></tr></thead>
<tbody>{''.join(batches) or '<tr><td colspan="6" class="empty">今天还没有批次</td></tr>'}</tbody></table>
<footer>本页与 <code>GET {html.escape(str(svc['view_path']))}</code> 同源（只读视图契约 v{view['contract_version']}）。
行为权威 = <code>sources.yaml</code>（改配置走 CLI 或 Git，这里只读）。</footer>
</body></html>
"""
