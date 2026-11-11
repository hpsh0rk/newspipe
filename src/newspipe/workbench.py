"""Source/policy workbench rendering; schema in, escaped HTML out, no writers."""
from __future__ import annotations

import html
from urllib.parse import quote
from newspipe.notification_view import REASONS
from newspipe.policy import human_summary as rule_summary

E = lambda value: html.escape(str(value), quote=True)


def _hidden(name, value):
    return f'<input type="hidden" name="{E(name)}" value="{E(value)}">'


def _start(action, token, base_hash, route, *, cls="editor", confirm=""):
    return (f'<form method="post" action="/api/actions/{E(action)}" class="{E(cls)}"'
            + (f' data-confirm="{E(confirm)}"' if confirm else "") + ">"
            + _hidden("token", token) + _hidden("base_hash", base_hash) + _hidden("return_to", route)
            + (f'<label class="check"><input type="checkbox" name="confirm_delete" value="1" required>{E(confirm)}</label>' if confirm else ""))


def _buttons(*, preview=True):
    return ('<div class="form-actions"><label class="check"><input type="checkbox" name="dry" value="1" checked>'
            '仅校验，不应用</label>'
            + ('<button class="btn" type="submit" formaction="/api/actions/notification-preview">预览变化</button>' if preview else "")
            + '<button class="btn primary" type="submit">验证 / 保存</button></div>')


def _field(field, value, *, prefix, scope, slots):
    path, kind = field["path"], field["type"]
    name = prefix + path
    ident = scope + "-" + name.replace(".", "-")
    help_text = field.get("help", "")
    described = f'aria-describedby="{E(ident)}-help {E(ident)}-error" aria-errormessage="{E(ident)}-error"'
    attr = f'id="{E(ident)}" name="{E(name)}" {described}'
    if kind == "bool":
        control = _hidden(name, "0") + f'<input type="checkbox" {attr} value="1"' + (" checked" if value else "") + ">"
    elif kind == "enum":
        labels = {"immediate": "有新内容即通知", "digest": "按槽位汇总", "collect": "只采集，不通知", "card": "每批新卡", "append_card": "追加已有卡片"}
        options = "".join(f'<option value="{E(c)}"' + (" selected" if str(c) == str(value) else "") + f'>{E(labels.get(c, c))}</option>'
                          for c in field.get("choices", []))
        control = f'<select {attr}>{options}</select>'
    elif kind == "slots":
        control = _hidden(name, "") + f'<div class="slot-options" role="group" aria-labelledby="{E(ident)}-label">' + "".join(
            f'<label class="check"><input type="checkbox" id="{E(ident)}-{index}" name="{E(name)}" {described} value="{E(c)}"'
            + (" checked" if c in (value or []) else "") + f'> {E(c)} · {E(slots.get(c, ""))}</label>' for index, c in enumerate(slots)) + "</div>"
    else:
        shown = ", ".join(str(v) for v in (value or [])) if kind == "list" else "" if value is None else str(value)
        numeric = kind in ("int", "float")
        extra = (' min="1"' if path.endswith("max_items") else ' min="0"') if numeric else ""
        if kind == "float":
            extra += ' step="any"'
        if path == "quiet_hours" or path.endswith(".quiet_hours"):
            extra += ' placeholder="23:00-07:30 或留空" pattern="([01][0-9]|2[0-3]):[0-5][0-9]-([01][0-9]|2[0-3]):[0-5][0-9]"'
        control = f'<input type="{"number" if numeric else "text"}" {attr} value="{E(shown)}"{extra}>'
    label = f'<label for="{E(ident)}">{E(field["label"])}</label>' if kind != "slots" else f'<span id="{E(ident)}-label">{E(field["label"])}</span>'
    return (f'<div class="field">{label}{control}<small id="{E(ident)}-help">{E(help_text)}</small>'
            f'<small class="field-error" id="{E(ident)}-error"></small></div>')


def _rss(page):
    rows = []
    for index, item in enumerate(page.get("catalog") or []):
        rows.append(f'<div class="feed-result"><b>{E(item["name"])}</b><span>{E(item.get("verified", ""))} 本机实测</span>'
                    f'<code>{E(item.get("feed", ""))}</code><a href="/config?use={index}#new">用这个新建信源</a></div>')
    for item in (page.get("discover") or {}).get("candidates", []):
        rows.append(f'<div class="feed-result"><code>{E(item.get("url", ""))}</code><small>{E(item.get("how", ""))}</small>'
                    f'<a href="/config?feed={quote(str(item.get("url", "")), safe="")}#new">用这个新建信源</a></div>')
    return ('<details class="rss-panel"' + (" open" if page.get("q") or page.get("discover") else "") + '><summary>RSS 搜索 / 订阅</summary>'
            '<p class="note">目录条目已在本机 RSSHub 实测；支持直接填写 RSS 地址，不承诺全网搜索。</p>'
            '<form method="get" action="/config" class="toolbar"><label>关键词 '
            f'<input name="q" value="{E(page.get("q", ""))}" placeholder="少数派 / AI"></label>'
            '<button class="btn">搜目录</button></form>'
            '<form method="get" action="/config" class="toolbar"><label>站点地址 '
            f'<input name="u" value="{E(page.get("discover_url", ""))}" placeholder="example.com"></label>'
            '<button class="btn">发现 feed</button></form>'
            + (f'<p class="banner err">{E(page["discover_error"])}</p>' if page.get("discover_error") else "")
            + "".join(rows) + '</details>')


def _source_editor(payload, schema, token, page):
    snap = payload["notification"]
    name = str(page.get("edit") or "")
    existing = bool(name)
    values = page.get("values") or payload.get("source_values", {}).get(name) or page.get("prefill") or {}
    row = next((r for r in snap["sources"] if r["source"] == name), None)
    if existing and row is None:
        return '<p class="notice">消息源不存在，请返回清单重新选择；不会把编辑转换为新建。</p>'
    binding = row["after"] if row else None
    action = "source-patch" if existing else "source-save"
    route = "/config"
    form = _start(action, token, payload["sources_hash"], route)
    form += _hidden("preview_kind", "source")
    if existing:
        form += f'<p>稳定 ID <code>{E(name)}</code></p>' + _hidden("name", name)
    else:
        form += _hidden("create_only", "1")
        form += ('<label class="field">消息源 ID<input name="name" required pattern="[A-Za-z0-9_][A-Za-z0-9_-]*" '
                 f'value="{E(page.get("prefill_name", ""))}" placeholder="my_feed"></label>')
    groups = []
    for axis in schema["axes"]:
        fields = [f for f in schema["fields"] if f["axis"] == axis["key"]]
        # Retained legacy values stay editable for rollback, but never participate while bound.
        inner = "".join(_field(f, values.get(f["path"], False if f["path"] == "enabled" and not existing else f.get("default")),
                               prefix="f.", scope=action, slots=payload["slots"]) for f in fields if not f.get("advanced"))
        advanced = [f for f in fields if f.get("advanced")]
        if advanced:
            inner += f'<details><summary>高级（{len(advanced)} 项）</summary><div class="field-grid">' + "".join(
                _field(f, values.get(f["path"], f.get("default")), prefix="f.", scope=action, slots=payload["slots"]) for f in advanced) + '</div></details>'
        group = f'<fieldset><legend>{E(axis["label"])}</legend><p class="note">{E(axis["hint"])}</p><div class="field-grid">{inner}</div></fieldset>'
        if axis["key"] in ("filter", "enrich", "card", "deliver"):
            group = f'<details><summary>{E(axis["label"])}' + (" · 旧设置，绑定策略后不参与通知" if axis["key"] == "deliver" else "") + f'</summary>{group}</details>'
        groups.append(group)
    # Source identity/acquisition first, then notification choice, then optional processing.
    form += "".join(groups[:2])
    options = ('<option value="">旧式内联策略（不绑定）</option>' if existing else '<option value="">请选择已保存的策略</option>')
    for ident, template in snap["policies"].items():
        selected = ' selected' if binding and binding["policy_id"] == ident else ""
        options += f'<option value="{E(ident)}"{selected}>{E(template["values"].get("label") or ident)}</option>'
    form += '<fieldset><legend>通知策略</legend><label class="field">绑定模板 '
    form += f'<select name="notify_policy"' + (" required" if not existing else "") + f'>{options}</select></label>'
    if not snap["policies"]:
        form += '<p class="notice">先创建通知策略，再添加消息源。<a href="/notifications?new=1">创建策略</a></p>'
    form += '<p class="note">未提交字段保持原样；继承不等于把模板值复制进源。旧式预算是全局计数，新模板预算按每个源独立计算。</p>'
    form += '<details><summary>自定义覆盖 · 每个字段可独立继承</summary>'
    for field in schema["policy_fields"]:
        key = field["path"]
        if key == "label":
            continue
        custom = bool(binding and binding.get("origins", {}).get(key, "").startswith("source:"))
        value = (binding or {}).get("values", {}).get(key, field.get("default"))
        form += f'<div class="override" data-override="{E(key)}"><label>值来源 · {E(field["label"])} '
        form += f'<select name="inherit.{E(key)}"><option value="inherit"' + ("" if custom else " selected") + '>继承模板</option>'
        form += '<option value="custom"' + (" selected" if custom else "") + '>自定义</option></select></label><div data-custom>'
        form += _field(field, value, prefix="n.", scope=action, slots=payload["slots"]) + '</div></div>'
    form += '</details>'
    if existing:
        form += '<label class="check"><input type="checkbox" name="confirm_conversion" value="1">我已预览并确认绑定/解绑引起的行为与预算范围变化</label>'
    form += '</fieldset>' + "".join(groups[2:]) + _buttons() + '</form>'
    aside = '<aside class="rule-aside"><p class="eyebrow">EFFECTIVE STRATEGY</p><h3>实际会怎样通知</h3>'
    aside += f'<p data-effective="{E(name)}">{E(rule_summary(binding, payload["slots"]))}</p>'
    if row:
        aside += f'<p>{E(REASONS.get(row["reason"], row["reason"]))} · 待通知 {row["pending"]} 条</p>'
    aside += '<p class="note">预览只读取现有队列与预算，不抓取、不调模型、不发卡。保存配置也不会暗中发送通知。</p></aside>'
    return '<div class="workbench-grid">' + form + aside + '</div>'


def sources_body(payload, schema, actions, page):
    page, actions = page or {}, actions or {}
    token = actions.get("token", "")
    query = str(page.get("search") or "").casefold()
    policy_filter = page.get("policy_filter", "")
    cards = []
    snapshot = payload["notification"]
    for src in payload["sources"]:
        row = next(r for r in snapshot["sources"] if r["source"] == src["name"])
        if query and query not in (src["name"] + " " + src["card_title"] + " " + src["adapter"]).casefold():
            continue
        if policy_filter and (row["after"]["policy_id"] or "legacy") != policy_filter:
            continue
        if page.get("enabled_filter") in ("on", "off") and src["enabled"] != (page["enabled_filter"] == "on"):
            continue
        if page.get("adapter_filter") and src["adapter"] != page["adapter_filter"]:
            continue
        cadence = ("每 " + str(src["interval_min"]) + " 分钟采集" if src["trigger"] == "poll" else
                   "手动采集（通知仍由策略决定）" if src["trigger"] == "manual" else
                   "采集 " + " / ".join(f"{s} {payload['slots'].get(s, '')}" for s in src["slots"]))
        links = ''
        if token:
            links = (f'<a class="btn" href="/config?edit={quote(src["name"])}">编辑</a>'
                     f'<a class="btn" href="/config?copy={quote(src["name"])}">复制</a>')
            links += _start("source-toggle", token, payload["sources_hash"], "/config", cls="inline")
            links += _hidden("name", src["name"]) + _hidden("state", "disable" if src["enabled"] else "enable")
            links += f'<button class="btn">{"停用" if src["enabled"] else "启用"}</button></form>'
            links += _start("source-remove", token, payload["sources_hash"], "/config", cls="inline", confirm="删除源配置？待处理内容和历史会保留。")
            links += _hidden("name", src["name"]) + '<button class="btn danger">删除</button></form>'
        cards.append(f'<article class="source-row" data-source="{E(src["name"])}"><div><h3>{E(src["card_title"])}</h3>'
                     f'<p class="note"><code>{E(src["name"])}</code> · {E(src["adapter"])} · {E(cadence)}</p>'
                     f'<p data-effective="{E(src["name"])}">{E(rule_summary(row["after"], payload["slots"]))}</p></div>'
                     f'<div class="source-state"><span class="pill {"ok" if src["enabled"] else "warn"}">{E(src["status_label"] if src["enabled"] else "已停用")}</span>'
                     f'<span data-pending="{E(src["name"])}">{row["pending"]} 条待通知</span></div><div class="row-actions">{links}</div></article>')
    filter_form = '<form method="get" action="/config" class="source-filters"><label>搜索消息源 '
    filter_form += f'<input name="search" value="{E(page.get("search", ""))}" placeholder="名称、标题、适配器"></label>'
    for key, label, choices in (("policy_filter", "通知策略", [("legacy", "旧式策略"), *[(n, p["values"].get("label") or n) for n, p in snapshot["policies"].items()]]),
                                 ("enabled_filter", "启用状态", [("on", "已启用"), ("off", "已停用")]),
                                 ("adapter_filter", "适配器", [(a, a) for a in schema["adapters"]])):
        filter_form += f'<label>{label}<select name="{key}"><option value="">全部</option>' + "".join(
            f'<option value="{E(value)}"' + (" selected" if page.get(key) == value else "") + f'>{E(text)}</option>' for value, text in choices) + '</select></label>'
    filter_form += '<button class="btn">筛选</button><a href="/config">清除</a></form>'
    body = ('<div class="page-intro"><div><p class="eyebrow">SOURCE LIBRARY</p><p class="lead">采集什么，与何时打扰你，是两件事。</p></div>'
            '<a class="btn primary" href="/config?new=1#new">添加消息源</a></div>' + filter_form
            + '<h2>信源清单</h2><div class="source-list">' + ("".join(cards) or '<p class="empty">还没有匹配的消息源。先创建通知策略，再添加消息源。</p>') + '</div>')
    if page.get("copy") and token:
        body += '<h2>复制消息源 · 新源默认停用</h2>' + _start("source-copy", token, payload["sources_hash"], "/config")
        body += _hidden("name", page["copy"]) + '<label class="field">新的稳定 ID<input name="new_name" required pattern="[A-Za-z0-9_][A-Za-z0-9_-]*"></label>' + _buttons(preview=False) + '</form>'
    if token:
        edit_name = str(page.get("edit") or "")
        body += f'<details id="new" class="source-editor"' + (" open" if edit_name or page.get("new") or page.get("prefill") else "") + '><summary>'
        body += (f'编辑 <code>{E(edit_name)}</code>' if edit_name else "添加消息源 · 直接手填") + '</summary>' + _source_editor(payload, schema, token, page) + '</details>'
    else:
        body += '<p class="notice">写操作未开启，表单不渲染。需要 service.yaml 的 view.actions。</p>'
    return body + _rss(page)


def notifications_body(payload, schema, actions, page):
    actions, page = actions or {}, page or {}
    token = actions.get("token", "")
    snap = payload["notification"]
    gates = snap["notifications"] or {}
    body = '<p class="eyebrow">NOTIFICATION CONTROL</p><p class="lead">一处调整模板，多个消息源共享行为，各自独立计数。</p>'
    body += '<section class="global-panel"><h2>全局硬限制</h2>'
    body += f'<p>今日通知操作 <b data-global-count>{snap["global_budget"].get("cards", 0)}</b> 次 · 时区 <code>{E(snap["time_zone"])}</code></p>'
    body += '<p class="note">更新已有卡片也计一次。全局限制约束全部信源，源级例外不能越过；暂停通知不会停止采集。</p>'
    if token:
        body += _start("global-save", token, payload["sources_hash"], "/notifications") + _hidden("preview_kind", "global")
        body += '<label class="field">通知目标（唯一群）<input name="chat" required pattern="oc_.+" value="' + E(payload["chat"]) + '"></label>'
        body += '<div class="field-grid">' + "".join(_field(f, gates.get(f["path"], f.get("default")), prefix="g.", scope="global",
                                                           slots=payload["slots"]) for f in schema["notification_fields"]) + '</div>'
        body += '<p class="note">全局上限留空 = 未配置硬上限；0 = 完全不发送。策略的每源限制仍独立生效。</p>'
        body += '<details><summary>通知 / 采集槽位 · 修改会校验全部引用</summary><div class="slot-editor">'
        for ident, clock in [*payload["slots"].items(), ("", ""), ("", "")]:
            body += f'<div class="slot-edit-row"><label>槽位 ID<input name="slot_id" value="{E(ident)}"></label><label>时刻 HH:MM<input name="slot_time" value="{E(clock)}" placeholder="08:15"></label></div>'
        body += '</div><button class="btn" type="button" data-add-slot>添加槽位行</button><p class="note">移除仍被引用的槽位会被拒绝；先改信源采集槽位及策略。</p></details>' + _buttons() + '</form>'
    else:
        body += '<p class="notice">写操作未开启，配置表单不渲染。</p>'
    body += '</section><div class="section-heading"><h2>通知策略模板</h2><a class="btn primary" href="/notifications?new=1#policy-editor">创建策略</a></div><div class="policy-grid">'
    for ident, template in snap["policies"].items():
        values = template["values"]
        rule = {"policy_id": ident, "values": values}
        body += f'<article class="policy-card"><h3>{E(values.get("label") or ident)}</h3><code>{E(ident)}</code><p data-policy-summary="{E(ident)}">{E(rule_summary(rule, payload["slots"]))}</p>'
        body += f'<p class="note">绑定 {len(template["bound_sources"])} 个源，含停用源；额度不共享。</p>'
        if token:
            body += f'<a class="btn" href="/notifications?edit={quote(ident)}#policy-editor">编辑</a> <a class="btn" href="/notifications?copy={quote(ident)}#policy-editor">复制</a>'
            body += _start("policy-remove", token, payload["sources_hash"], "/notifications", cls="inline", confirm="删除策略？仍有信源引用时会被拒绝。") + _hidden("name", ident) + '<button class="btn danger">删除</button></form>'
        body += '</article>'
    body += '</div>'
    if not snap["policies"]:
        body += '<p class="empty">还没有模板。选择下面的起点，保存后才能绑定消息源。</p>'
    if token:
        ident = str(page.get("edit") or page.get("copy") or "")
        if ident and ident not in snap["policies"]:
            return body + '<p class="notice">策略不存在，请返回模板清单重新选择；不会把编辑转换为新建。</p>'
        values = dict(snap["policies"].get(ident, {}).get("values") or {})
        preset = page.get("preset", "")
        if preset == "continuous":
            values.update(mode="immediate", form="append_card", min_gap_min=10, quiet_hours="23:00-07:30")
        elif preset == "digest":
            values.update(mode="digest", slots=[s for s in ("am", "pm") if s in payload["slots"]])
        elif preset == "collect":
            values.update(mode="collect")
        body += '<details class="policy-editor" id="policy-editor"' + (" open" if ident or page.get("new") or preset or not snap["policies"] else "") + '><summary>编辑 / 创建策略</summary>'
        body += '<p class="preset-links">起点（保存后仍可自由修改）：<a href="/notifications?new=1&preset=digest#policy-editor">早晚汇总</a> · <a href="/notifications?new=1&preset=continuous#policy-editor">持续更新</a> · <a href="/notifications?new=1&preset=collect#policy-editor">只采集</a></p>'
        body += _start("policy-save", token, payload["sources_hash"], "/notifications") + _hidden("preview_kind", "policy")
        copying = bool(page.get("copy"))
        if not page.get("edit"):
            body += _hidden("create_only", "1")
        body += '<label class="field">稳定策略 ID<input name="name" required pattern="[A-Za-z0-9_][A-Za-z0-9_-]*" value="' + E("" if copying else ident) + '"' + (" readonly" if page.get("edit") else "") + '></label>'
        body += '<div class="field-grid">' + "".join(_field(f, values.get(f["path"], f.get("default")), prefix="p.", scope="policy",
                                                    slots=payload["slots"]) for f in schema["policy_fields"]) + '</div>'
        if ident:
            body += '<h3>受影响的信源</h3><ul>' + "".join(f'<li>{E(name)} · {"已启用" if payload["source_values"][name]["enabled"] else "已停用"}</li>' for name in snap["policies"].get(ident, {}).get("bound_sources", [])) + '</ul>'
        body += _buttons() + '</form></details>'
    return body


def preview_result(data):
    return '<section class="preview-result"><h2>只读预览 · 不写入、不发送</h2>' + "".join(
        f'<article><h3>{E(row["source"])}</h3><p>之前：{E(row["before_summary"])}</p><p>之后：{E(row["summary"])}</p><p>{row["pending"]} 条待处理 · {E(row["reason_label"])}</p><p>{E(row["consequence_label"])}</p></article>'
        for row in data.get("sources", [])) + '</section>'


def notification_status(payload):
    snap = payload.get("notification", {})
    rows = snap.get("sources", [])
    pending = sum(r["pending"] for r in rows)
    paused = (snap.get("notifications") or {}).get("paused", False)
    issues = [r for r in rows if r.get("unfinished") or (r["pending"] and r["reason"] not in ("ok", "nothing_new"))]
    body = f'<section class="notification-status"><h2>通知进度</h2><p><b>{pending}</b> 条待处理 · 今日 {snap.get("global_budget", {}).get("cards", 0)} 次通知操作 · {"全局已暂停（采集继续）" if paused else "全局未暂停"}</p>'
    if issues:
        body += '<ul>' + "".join(f'<li><a href="/ops#source-{quote(r["source"])}">{E(r["source"])}</a> · {E(r["reason_label"])}</li>' for r in issues) + '</ul>'
    return body + '<a href="/notifications">调整通知策略与全局限制</a></section>'


def operation_controls(payload, token):
    body = '<section><h2>采集与通知</h2><p class="note">采集只入待处理队列；通知只读取队列，不抓取。两者都保留当前策略。手动越过汇总时钟仍不能越过暂停或预算。</p>'
    for row in payload.get("notification", {}).get("sources", []):
        name = row["source"]
        body += f'<article class="operation-row" id="source-{E(name)}"><h3>{E(name)}</h3><p>{E(row["reason_label"])} · {row["pending"]} 条待处理</p>'
        body += _start("collect", token, payload["sources_hash"], "/ops", cls="inline") + _hidden("name", name) + '<button class="btn">只采集一次</button></form>'
        body += _start("notify", token, payload["sources_hash"], "/ops", cls="notify-form") + _hidden("name", name)
        body += '<label class="check"><input type="checkbox" name="bypass_clock" value="1">越过汇总时钟</label><label class="check"><input type="checkbox" name="confirm" value="1">确认只越过时钟，不越过限制</label><button class="btn">通知待处理内容</button></form>'
        receipt = row.get("unfinished")
        if receipt:
            body += '<details open><summary>未完成原操作 · 身份和目标固定</summary><dl class="receipt">' + "".join(
                f'<dt>{E(k)}</dt><dd><code>{E(v)}</code></dd>' for k, v in receipt.items() if v is not None) + '</dl>'
            if row["reason"] == "send_result_unknown":
                body += '<p class="banner err">结果未知且已超幂等窗口：请按目标、卡片、操作 ID 与时间核对平台回执。保持阻塞，禁止强制重发；不能伪造成功或改私有状态。</p>'
            elif row["reason"] == "receipt_repair":
                body += '<p>远端已确认成功，服务下一轮只补本地回执；不需要再次发送。</p>'
            else:
                body += _start("notification-retry", token, payload["sources_hash"], "/ops") + _hidden("name", name) + _hidden("operation_id", receipt["delivery_id"])
                body += '<label class="check"><input type="checkbox" name="confirm" value="1" required>确认重试同一原操作；不生成新身份、不立刻发送</label>' + _buttons(preview=False) + '</form>'
            body += '</details>'
        body += '</article>'
    return body + '</section>'
