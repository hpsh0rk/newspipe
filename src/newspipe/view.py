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
from urllib.parse import quote as urlquote

from newspipe import config, edit, state

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
form.inline{display:inline;margin:0 4px 0 0}
.btn{font:inherit;font-size:12px;padding:3px 10px;border-radius:6px;cursor:pointer;
     border:1px solid rgba(128,128,128,.5);background:transparent;color:inherit}
.btn:hover{border-color:#2f9e44}
.btn.danger{border-color:#e03131;color:#e03131}
.btn.danger:hover{background:rgba(224,49,49,.12)}
.btn.primary{border-color:#2f9e44;color:#2f9e44}
select,input[type=text]{font:inherit;font-size:12px;padding:3px 6px;border-radius:6px;
     border:1px solid rgba(128,128,128,.5);background:transparent;color:inherit}
.banner{border:1px solid;border-radius:8px;padding:10px 12px;margin:14px 0;font-size:13px}
.banner.ok{border-color:#2f9e44}
.banner.err{border-color:#e03131}
.banner pre{white-space:pre-wrap;word-break:break-all;margin:6px 0 0;font-size:12px;opacity:.85}
.actions{white-space:nowrap}
.toolbar{display:flex;gap:14px;align-items:center;flex-wrap:wrap;margin:14px 0;
     border:1px solid rgba(128,128,128,.3);border-radius:8px;padding:10px 12px}
.toolbar label{font-size:12px;display:flex;gap:6px;align-items:center}
.hint{font-size:12px;opacity:.65;font-weight:400}
h2 .hint{margin-left:6px}
h3{font-size:13px;margin:14px 0 6px}
details{margin:2px 0}
summary{cursor:pointer;font-size:12px;opacity:.8}
.note{font-size:12px;opacity:.7}
nav.tabs{display:flex;gap:6px;margin:0 0 14px;border-bottom:1px solid rgba(128,128,128,.3);
     padding-bottom:0}
nav.tabs a.tab{padding:6px 14px;border:1px solid transparent;border-bottom:none;
     border-radius:8px 8px 0 0;text-decoration:none;color:inherit;opacity:.7;font-size:14px}
nav.tabs a.tab:hover{opacity:1;background:rgba(128,128,128,.08)}
nav.tabs a.tab.on{opacity:1;border-color:rgba(128,128,128,.3);background:rgba(128,128,128,.10);
     font-weight:600}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}
.card{border:1px solid rgba(128,128,128,.3);border-radius:8px;padding:10px 12px}
.card h3{margin:0 0 8px}
label.f{margin:2px 10px 2px 0;font-size:12px;display:inline-flex;align-items:center;gap:4px}
label.f input[type=text],label.f select,label.f input[type=number]{min-width:88px}
fieldset{border:1px solid rgba(128,128,128,.3);border-radius:8px;margin:0 0 12px;padding:8px 12px}
legend{font-size:12px;opacity:.75;padding:0 6px}
.cand{border:1px solid rgba(128,128,128,.3);border-radius:8px;padding:8px 10px;margin:6px 0}
.cand code{word-break:break-all}
"""


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


#: 三个 Tab：服务端路由（可深链、可加书签、无 JS）。`GET /view` 仍是同一进程里的 JSON 契约。
TABS: tuple[tuple[str, str, str], ...] = (
    ("cockpit", "驾驶舱", "/"),
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


def as_html(view: dict[str, Any], *, actions: dict[str, Any] | None = None,
            flash: dict[str, Any] | None = None, tab: str = "cockpit") -> str:
    """同一份视图的服务端渲染（`GET /`）：不引前端、不发任何外部请求。

    面板（宿主）走 JSON 契约；这一页是给「从服务门户点进来」的场景用的。

    `actions` 非空时这一页从「仪表盘」变成「控制台」：每个动作都是一个 POST 表单，
    由 `/api/actions/<name>` 翻译成 CLI 命令，用**同一份 handler** 执行。传 `token` 才
    渲染按钮（服务端没开写操作时，页面里连表单都不该出现）。
    """
    act = actions or {}
    token = str(act.get("token") or "")
    on = bool(token)
    src_hash = str(act.get("sources_hash") or "")
    s = view["summary"]
    rows = []
    for src in view["sources"]:
        detail = " ".join(f"{k}:{v}" for k, v in (src.get("status_detail") or {}).items()
                          if k not in ("enrich", "note", "card"))
        if src.get("status_note"):
            detail = f"{detail} {src['status_note']}".strip()
        trigger = (f"轮询 {src['interval_min']:g}min" if src["trigger"] == "poll"
                   else "定时 " + "/".join(src["slots"]) or "手动")
        cell = ""
        if on:
            bits = [_form("source-toggle", token,
                          {"name": src["name"], "state": "disable" if src["enabled"] else "enable",
                           "base_hash": src_hash},
                          "停用" if src["enabled"] else "启用")]
            bits.append(_form("run", token, {"target": f"source:{src['name']}", "dry": "1"}, "试跑"))
            if src["pending_total"]:
                bits.append(_form("flush", token, {"name": src["name"]},
                                  f"现在发 {src['pending_total']}", danger=True))
            cell = f'<td class="actions">{"".join(bits)}</td>'
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
            f"{cell}"
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
    # ---- 写操作面：只有服务端开了 `view.actions` 才会传 token 进来
    toolbar = queue_section = pending_section = events_section = ""
    op_th = ""
    colspan = 8
    if on:
        colspan = 9
        op_th = "<th>操作</th>"
        targets = act.get("run_targets") or ["am", "noon", "pm", "poll"]
        opts = "".join(f'<option value="{html.escape(str(t))}">{html.escape(str(t))}</option>'
                       for t in targets)
        toolbar = (
            '<form method="post" action="/api/actions/run" class="toolbar">'
            f'<input type="hidden" name="token" value="{html.escape(token)}">'
            f'<label>目标 <select name="target">{opts}</select></label>'
            '<label><input type="checkbox" name="dry" value="1" checked> 试运行（不发卡）</label>'
            '<button class="btn primary" type="submit">跑一轮</button>'
            '<span class="hint">按钮 → CLI（同一份 handler；校验 / 原子写 / 审计都在 CLI）</span>'
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
                f'<td class="actions">{_form("queue-ack", token, {"event_id": str(e.get("id") or "")}, "确认入库", cls="primary", extra=note_input)}</td>'
                "</tr>")
        queue_section = (
            f'<h2>待入库队列 ⭐ <span class="hint">{len(qrows)} 条待你确认</span></h2>'
            "<table><thead><tr><th>时间</th><th>来源</th><th>标题</th><th>操作</th></tr></thead>"
            "<tbody>" + ("".join(qrows) or '<tr><td colspan="4" class="empty">没有待确认的收藏</td></tr>')
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
        if pend_blocks:
            pending_section = ('<h2>顺延队列明细 <span class="hint">点「现在发」= 立刻跑这个源，'
                               '把队列里的条目真发出去</span></h2>' + "".join(pend_blocks))

        erows = []
        for e in act.get("events") or []:
            p = e.get("payload") or {}
            erows.append(
                "<tr>"
                f'<td><code>{html.escape(str(e.get("ts") or ""))}</code></td>'
                f'<td>{html.escape(str(e.get("type") or ""))}</td>'
                f'<td>{html.escape(str(e.get("source") or ""))}</td>'
                f"<td>{_link(str(p.get('title') or ''), str(p.get('url') or ''))}</td>"
                f'<td class="actions">{_form("events-ack", token, {"ids": str(e.get("id") or "")}, "已消费")}</td>'
                "</tr>")
        events_section = (
            f'<h2>未消费事件 <span class="hint">{len(erows)} 条（近 7 天）</span></h2>'
            "<table><thead><tr><th>时间</th><th>类型</th><th>来源</th><th>标题</th><th>操作</th></tr></thead>"
            "<tbody>" + ("".join(erows) or '<tr><td colspan="5" class="empty">没有未消费事件</td></tr>')
            + "</tbody></table>")

    svc = view["service"]
    read_rate = "—" if s["read_rate"] is None else f"{round(s['read_rate'] * 100)}%"
    if on:
        write_note = ("写操作走 <code>POST /api/actions/*</code> → CLI 的同一份 handler；"
                      "动作白名单在代码里，不是配置。")
    elif tab == "cockpit":
        # 别在只读页上撒谎说「写操作未开启」—— 开着，只是这页不做
        write_note = "本页只读；增删改在「配置」页，管理动作在「运维」页。"
    else:
        write_note = "写操作未开启（<code>service.yaml: view.actions</code>）。"
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>newspipe · {_tab_label(tab)}</title><style>{_CSS}</style></head><body>
{_nav(tab)}
<h1>newspipe · {_tab_label(tab)}</h1>
<p class="sub">生成于 <code>{html.escape(view['generated_at'])}</code> ·
通道 {html.escape(svc['channel'])} · 入站 {html.escape(svc['inbound_mode'])} ·
槽位 {html.escape(', '.join(f'{k}:{v}' for k, v in view['slots'].items()))} ·
投递群 <code>{html.escape(str(view['chat']))}</code></p>
{_banner(flash)}{toolbar}
<div class="stats">
  <div class="stat"><b>{s['batches']}</b><span>今日批次</span></div>
  <div class="stat"><b>{s['items']}</b><span>今日条目</span></div>
  <div class="stat"><b>{read_rate}</b><span>已标记率</span></div>
  <div class="stat"><b>{s['actions_today'] or 0}</b><span>今日点击</span></div>
  <div class="stat"><b>{s['cards_today'] or 0}</b><span>今日发卡</span></div>
  <div class="stat"><b>{view['llm']['calls']}</b><span>AI 调用</span></div>
  <div class="stat"><b>{view['llm']['degraded']}</b><span>AI 降级</span></div>
</div>
{queue_section}
<h2>信源</h2>
<table><thead><tr><th>源</th><th>卡片标题</th><th>节奏</th><th>形态/加工</th><th>心跳</th>
<th>最近</th><th>去重/顺延</th><th>明细</th>{op_th}</tr></thead>
<tbody>{''.join(rows) or f'<tr><td colspan="{colspan}" class="empty">注册表里还没有信源</td></tr>'}</tbody></table>
{pending_section}
<h2>今日批次</h2>
<table><thead><tr><th>文件</th><th>标题</th><th>槽位</th><th>已标记</th><th>卡片</th><th>顺延</th></tr></thead>
<tbody>{''.join(batches) or '<tr><td colspan="6" class="empty">今天还没有批次</td></tr>'}</tbody></table>
{events_section}
<footer>本页与 <code>GET {html.escape(str(svc['view_path']))}</code> 同源（只读视图契约 v{view['contract_version']}）。
行为权威 = <code>sources.yaml</code>（改配置走 CLI 或 Git）。{write_note}</footer>
</body></html>
"""


def cockpit_html(view: dict[str, Any], *, flash: dict[str, Any] | None = None) -> str:
    """驾驶舱：全局总览，**只读**（没有 actions 就没有任何表单）。"""
    return as_html(view, actions=None, flash=flash, tab="cockpit")


def ops_html(view: dict[str, Any], *, actions: dict[str, Any] | None = None,
             flash: dict[str, Any] | None = None) -> str:
    """运维：跑一轮 / 启停 / ⭐确认入库 / 事件消费 / 顺延队列。写操作都走 CLI 同一份 handler。"""
    return as_html(view, actions=actions, flash=flash, tab="ops")


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
        "<h2>信源清单 <span class=\"hint\">改配置走 CLI：这里是表单，落盘仍是 `source set`</span></h2>"
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
        new_section = ('<h2 id="new">新增信源</h2>'
                       + _source_form(schema, name=str(pg.get("prefill_name") or ""),
                                      values=pg.get("prefill") or {}, token=token,
                                      base_hash=src_hash, is_new=True, submit="新建信源"))
    else:
        # 没开写操作就不渲染表单：按下去只会 403 的按钮是骗人
        new_section = ('<h2 id="new">新增信源</h2><p class="empty">写操作未开启'
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
        '<h2 id="rss">RSS 搜索 / 订阅</h2>'
        '<form method="get" action="/config" class="toolbar">'
        f'<label class="f">关键词 <input type="text" name="q" value="{html.escape(q)}" size="24" '
        'placeholder="如 AI、羊毛、少数派"></label>'
        '<button class="btn primary" type="submit">搜目录</button>'
        '<span class="hint">目录里的条目都是在本机 RSSHub 上实测可用的</span></form>'
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

    body = (f'<p class="sub">配置权威 = <code>sources.yaml</code> · 当前哈希 '
            f'<code>{html.escape(src_hash[:12])}</code> · 写操作走 CLI 的同一份 handler'
            f'{"（<b>写操作未开启</b>：service.yaml: view.actions）" if not on else ""}</p>'
            + _banner(flash) + edit_section + source_table + rss_section + new_section)
    return _page(tab="config", sub_meta=view, svc=svc, body=body,
                 footer_extra="本页的表单由 <code>newspipe config describe --json</code> 生成"
                              "（字段的唯一事实来源），落盘一律经 CLI。")


def _page(*, tab: str, sub_meta: dict[str, Any], svc: dict[str, Any], body: str,
          footer_extra: str = "") -> str:
    """三页共用的外壳：导航 + 标题 + 元信息行 + 正文 + 页脚。"""
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>newspipe · {_tab_label(tab)}</title><style>{_CSS}</style></head><body>
{_nav(tab)}
<h1>newspipe · {_tab_label(tab)}</h1>
<p class="sub">生成于 <code>{html.escape(str(sub_meta.get('generated_at')))}</code> ·
通道 {html.escape(str(svc.get('channel')))} · 入站 {html.escape(str(svc.get('inbound_mode')))} ·
槽位 {html.escape(', '.join(f'{k}:{v}' for k, v in (sub_meta.get('slots') or {}).items()))} ·
投递群 <code>{html.escape(str(sub_meta.get('chat')))}</code></p>
{body}
<footer>只读契约 <code>GET /view</code> v{sub_meta.get('contract_version')}（宿主/面板的数据面）。
{footer_extra}</footer>
</body></html>
"""
