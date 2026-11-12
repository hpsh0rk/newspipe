"""Source/policy workbench rendering; schema in, escaped HTML out, no writers."""
from __future__ import annotations

import html
import json
from urllib.parse import quote
from newspipe.notification_view import REASONS
from newspipe.policy import human_summary as rule_summary
from newspipe.webconfirmation import delete_link, delete_dialog
from newspipe.webui import editor_link, editor_panel, editor_dialog, help_tip

E = lambda value: html.escape(str(value), quote=True)
ADAPTER_LABELS = {"rsshub": "RSS / RSSHub"}


def _hidden(name, value):
    return f'<input type="hidden" name="{E(name)}" value="{E(value)}">'


def _start(action, token, base_hash, route, *, cls="editor", confirm=""):
    validation = ' novalidate data-server-validation' if action in {
        "source-save", "source-patch", "source-copy", "policy-save", "global-save", "rsshub-save"} else ""
    return (f'<form method="post" action="/api/actions/{E(action)}" class="{E(cls)}"'
            + validation + (f' data-confirm="{E(confirm)}"' if confirm else "") + ">"
            + _hidden("token", token) + _hidden("base_hash", base_hash) + _hidden("return_to", route)
            # Implicit Enter validates, never clicks an auxiliary submitter or writes.
            + ('<button type="submit" name="validate_only" value="1" hidden></button>' if validation else '')
            + (f'<label class="check"><input type="checkbox" name="confirm_delete" value="1" required>{E(confirm)}</label>' if confirm else ""))


def _buttons(*, preview=True):
    return ('<div class="form-actions"><label class="check"><input type="checkbox" name="dry" value="1" checked>'
            '仅校验，不应用</label>'
            + ('<button class="btn" type="submit" formaction="/api/actions/notification-preview">预览变化</button>' if preview else "")
            + '<button class="btn primary" type="submit" data-save-button><span class="save-dry">校验配置</span>'
            '<span class="save-actual">保存配置</span></button></div>')


def _brief_rule(rule, slots):
    parts = rule_summary(rule, slots).split(" · ")
    return " · ".join(parts[:3 if (rule or {}).get("values", {}).get("mode") == "digest" else 2])


def _field(field, value, *, prefix, scope, slots):
    path, kind = field["path"], field["type"]
    name = prefix + path
    ident = scope + "-" + name.replace(".", "-")
    help_text = field.get("help", "")
    if prefix == "f." and kind in ("int", "float"):
        help_text += f' 主动清空会恢复默认值 {field["default"]}；0 是明确设置，不等于清空。'
    described = f'aria-describedby="{E(ident)}-help {E(ident)}-error" aria-errormessage="{E(ident)}-error"'
    attr = f'id="{E(ident)}" name="{E(name)}" {described}'
    if kind == "bool":
        control = _hidden(name, "0") + f'<input type="checkbox" {attr} value="1"' + (" checked" if value else "") + ">"
    elif kind == "enum":
        labels = {**ADAPTER_LABELS, "immediate": "有新内容即通知", "digest": "定时汇总", "collect": "只采集，不通知", "card": "每批新卡", "append_card": "追加已有卡片"}
        labels.update(field.get("labels", {}))
        options = "".join(f'<option value="{E(c)}"' + (" selected" if str(c) == str(value) else "") + f'>{E(labels.get(c, c))}</option>'
                          for c in field.get("choices", []))
        control = f'<select {attr}>{options}</select>'
    elif kind == "slots":
        control = _hidden(name, "") + f'<div class="slot-options" id="{E(ident)}" data-slot-choices role="group" aria-labelledby="{E(ident)}-label">'
        for index, (key, clock) in enumerate(slots.items()):
            label = clock + (f" · 计划 {index + 1}" if list(slots.values()).count(clock) > 1 else "")
            control += f'<label class="check"><input type="checkbox" name="{E(name)}" value="{E(key)}" data-slot-clock="{E(clock)}" {described}' + (' checked' if key in (value or []) else '') + f'><span>{E(label)}</span></label>'
        control += ('<p class="note">尚未设置采集时间，请先点击“管理采集时间”。</p>' if not slots else '') + '</div>'
    elif kind == "times":
        entries = list(value.items()) if isinstance(value, dict) else list(value or [])
        if not entries:
            entries = [("", "")]
        control = '<div data-times-editor><div data-time-rows>'
        for index, (key, time) in enumerate(entries):
            control += ('<div class="time-edit-row">' + _hidden(name + "_key", key)
                        + f'<input type="time" id="{E(ident)}-{index}" name="{E(name)}" value="{E(time)}" {described} aria-label="发送时间 {index + 1}">'
                        + f'<button class="btn" type="submit" name="editor_intent" value="remove-time:{index}" formnovalidate data-remove-time aria-label="移除发送时间">移除</button></div>')
        control += '</div><button class="btn" type="submit" name="editor_intent" value="add-time" formnovalidate data-add-time>添加发送时间</button><p class="note">24 小时制，可填写多个发送时间。</p></div>'
    else:
        shown = ", ".join(str(v) for v in (value or [])) if kind == "list" else "" if value is None else str(value)
        numeric = kind in ("int", "float")
        extra = (' min="1"' if path.endswith("max_items") else ' min="0"') if numeric else ""
        if kind == "float":
            extra += ' step="any"'
        if path == "quiet_hours" or path.endswith(".quiet_hours"):
            extra += ' placeholder="23:00-07:30 或留空" pattern="([01][0-9]|2[0-3]):[0-5][0-9]-([01][0-9]|2[0-3]):[0-5][0-9]"'
        control = f'<input type="{"number" if numeric else "text"}" {attr} value="{E(shown)}"{extra}>'
    label = f'<label for="{E(ident)}">{E(field["label"])}</label>' if kind not in {"times", "slots"} else f'<span id="{E(ident)}-label">{E(field["label"])}</span>'
    help_node = help_tip(help_text, ident + "-help", field["label"] + "说明") if help_text else f'<small id="{E(ident)}-help"></small>'
    if kind == "bool":
        label, control = control + label, ''
    return (f'<div class="field{" boolean-field" if kind == "bool" else ""}"' + (' data-digest-times' if kind == "times" else '')
            + (f' data-fetch-for="{E(field["active_for"])}"' if field.get("active_for") else '') + f'><div class="field-label">{label}{help_node}</div>{control}'
            f'<small class="field-error" id="{E(ident)}-error"></small></div>')


def _policy_records(snap, schema):
    body = ""
    labels = {"immediate": "有新内容即通知", "digest": "定时汇总", "collect": "只采集，不通知",
              "card": "每批新卡", "append_card": "追加已有卡片"}
    for name, item in snap["policies"].items():
        values = item["values"]
        body += f'<article data-policy-record="{E(name)}" data-policy-label="{E(values.get("label") or name)}"><h4>{E(values.get("label") or name)}</h4>'
        body += f'<p data-policy-description>{E(item["summary"])}</p><dl class="policy-parameters">'
        for field in schema["policy_fields"]:
            key = field["path"]
            if key == "label" or (key == "times" and values["mode"] != "digest"):
                continue
            value = values.get(key, field.get("default"))
            if key == "times":
                value = " / ".join(sorted(value.values()))
            elif isinstance(value, bool):
                value = "是" if value else "否"
            else:
                value = labels.get(value, value) if isinstance(value, str) else value
            body += f'<dt>{E(field["label"])}</dt><dd>{E(value if value != "" else "未设置")}</dd>'
        body += '</dl></article>'
    return body


def _policy_catalog(payload, schema):
    return ('<div data-read-region="policy-catalog" hidden data-config-hash="' + E(payload["sources_hash"]) + '">'
            + _policy_records(payload["notification"], schema) + '</div>')


def _rss(page):
    rows = []
    for index, item in enumerate(page.get("catalog") or []):
        rows.append(f'<div class="feed-result"><b>{E(item["name"])}</b><span>历史验证 · {E(item.get("verified", ""))}</span>'
                    f'<code>{E(item.get("feed", ""))}</code>' + editor_link(f'/config?use={index}', "用这个新建信源") + '</div>')
    for item in (page.get("discover") or {}).get("candidates", []):
        rows.append(f'<div class="feed-result"><code>{E(item.get("url", ""))}</code><small>{E(item.get("how", ""))}</small>'
                    + editor_link(f'/config?feed={quote(str(item.get("url", "")), safe="")}', "用这个新建信源") + '</div>')
    return ('<details class="rss-panel"' + (" open" if page.get("q") or page.get("discover") else "") + '><summary>RSS 搜索 / 订阅</summary>'
            + help_tip("目录是历史验证记录，不代表当前实例可用。支持直接填写 RSS 地址；相对路由需配置 RSSHub 后测试，不承诺全网搜索。", "rss-directory-help", "RSS 搜索说明") +
            '<form method="get" action="/config" class="toolbar"><label>关键词 '
            f'<input name="q" value="{E(page.get("q", ""))}" placeholder="少数派 / AI"></label>'
            '<button class="btn">搜目录</button></form>'
            '<form method="get" action="/config" class="toolbar"><label>站点地址 '
            f'<input name="u" value="{E(page.get("discover_url", ""))}" placeholder="example.com"></label>'
            '<button class="btn">发现 feed</button></form>'
            + (f'<p class="banner err">{E(page["discover_error"])}</p>' if page.get("discover_error") else "")
            + "".join(rows) + '</details>')


def _rsshub_settings(payload, schema, token, page):
    panel = ""
    hub = payload.get("rsshub") or {}
    origin = {"config": "显式配置 · sources.yaml", "env:RSSHub_BASE": "环境变量 · RSSHub_BASE", "default": "本地默认"}.get(hub.get("origin"), "未知")
    dependencies = hub.get("dependent_sources") or []
    explanation = ("完整 RSS 地址直接访问，不依赖 RSSHub；只有相对路由需要它。支持本地、远端和反向代理路径前缀。"
                   f"配置来源：{origin}。启用且依赖的信源：{'、'.join(dependencies) or '无'}。"
                   "页面加载时尚未检测连接；不会自动发现或部署 RSSHub。手动测试的结果仅代表本次请求。")
    body = '<section class="rsshub-panel summary-strip" data-read-region="rsshub"><div><h2>RSSHub · 可选连接'
    body += help_tip(explanation, "rsshub-summary-help", "RSSHub 连接说明") + '</h2><p><code>' + E(hub.get("effective_url") or "地址不可用") + '</code></p>'
    body += f'<p class="note">{E(origin)} · {len(dependencies)} 个启用源依赖</p></div>'
    if hub.get("error"):
        body += f'<p class="notice">{E(hub["error"])}。修正环境变量或保存显式地址；完整 RSS 地址仍可独立使用。</p>'
    if token:
        body += editor_link("/config?rsshub=1", "设置地址与测试订阅")
    if token and page.get("rsshub"):
        form = _start("rsshub-save", token, payload["sources_hash"], "/config")
        form += "".join(_field(field, hub.get("configured_url", field.get("default")), prefix="r.", scope="rsshub", slots=payload["slots"])
                        for field in schema.get("rsshub_fields", []))
        form += '<p class="note">填写地址可覆盖环境变量；留空并保存恢复继承。地址会在保存后供后续采集使用，无需重启。</p>'
        form += '<fieldset><legend>只读检测 · 不保存、不采集、不通知</legend><label class="field" for="rsshub-probe-feed">订阅地址 / 相对路由'
        form += '<input id="rsshub-probe-feed" name="probe_feed" aria-describedby="rsshub-probe-feed-help" placeholder="https://example.com/feed.xml 或 /sspai/matrix"></label>'
        form += help_tip("使用上面的候选基础地址测试；留空则按环境变量 / 本地默认解析。完整 RSS 地址忽略基础地址。连接测试仅验证端点可达，不保证是 RSSHub 或所有路由可用。", "rsshub-probe-feed-help", "订阅检测说明")
        form += '<div class="row-actions"><button class="btn" type="submit" formaction="/api/actions/rsshub-probe" name="probe_kind" value="connection">测试连接</button>'
        form += '<button class="btn" type="submit" formaction="/api/actions/rsshub-probe" name="probe_kind" value="feed">测试订阅</button></div>'
        form += help_tip("单次请求、无自动重试；响应最多 1 MiB、套接字超时 5 秒，DNS / TLS / 跳转可能增加总耗时。仅支持 RSS 2.0。", "rsshub-probe-limits", "检测限制") + '</fieldset>'
        form += _buttons(preview=False) + '</form>'
        panel = editor_panel("rsshub", "RSSHub 设置", form, "/config", "rsshub")
    return panel + body + '</section>'


def probe_result(envelope):
    """Escaped no-JS feedback, kept beside the retained settings draft."""
    ok = bool(envelope.get("ok"))
    error = envelope.get("error") or {}
    data = envelope.get("data") or error.get("details") or {}
    lines = [data.get("message") if ok else error.get("message"), error.get("hint"),
             "只读检测，没有保存地址、采集入队或发送通知；草稿和版本保持不变。"]
    if data.get("checked_url"):
        lines.append("检测地址：" + str(data["checked_url"]))
    if data.get("http_status") is not None:
        lines.append("HTTP 状态：" + str(data["http_status"]))
    if data.get("items") is not None:
        lines.append("RSS 条目：" + str(data["items"]))
    return (f'<div class="banner {"ok" if ok else "err"}" data-action-feedback role="status" aria-live="polite" tabindex="-1">'
            f'<h3>只读检测 · {"完成" if ok else "失败"}</h3>' + "".join(f'<p>{E(line)}</p>' for line in lines if line) + '</div>')


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
    form += _hidden("source_revision", payload.get("source_revisions", {}).get(name, "new"))
    form += _hidden("preview_kind", "source")
    if existing:
        form += f'<p>稳定 ID <code>{E(name)}</code></p>' + _hidden("name", name)
    else:
        form += _hidden("create_only", "1")
        form += ('<label class="field">消息源 ID<input name="name" required pattern="[A-Za-z0-9_][A-Za-z0-9_-]*" '
                 f'value="{E(page.get("prefill_name", ""))}" placeholder="my_feed"></label>')
    groups = []
    for axis in schema["axes"]:
        if axis["key"] == "deliver" and (binding and binding["policy_id"] or not existing):
            continue
        fields = [f for f in schema["fields"] if f["axis"] == axis["key"]]
        # Retained legacy values stay editable for rollback, but never participate while bound.
        inner = "".join(_field(f, values.get(f["path"], False if f["path"] == "enabled" and not existing else f.get("default")),
                               prefix="f.", scope=action, slots=payload["slots"]) for f in fields if not f.get("advanced"))
        advanced = [f for f in fields if f.get("advanced")]
        if advanced:
            inner += f'<details><summary>高级（{len(advanced)} 项）</summary><div class="field-grid">' + "".join(
                _field(f, values.get(f["path"], f.get("default")), prefix="f.", scope=action, slots=payload["slots"]) for f in advanced) + '</div></details>'
        if axis["key"] == "fetch":
            inner += '<div class="field-grid-link">' + editor_link('/config?acquisition=1', '管理采集时间') + '</div>'
        group = f'<fieldset><legend>{E(axis["label"])}' + help_tip(axis["hint"], action + "-axis-" + axis["key"], axis["label"] + "说明") + f'</legend><div class="field-grid">{inner}</div></fieldset>'
        if axis["key"] in ("filter", "enrich", "card", "deliver"):
            group = '<details' + (' data-legacy-deliver' if axis["key"] == "deliver" else '') + f'><summary>{E(axis["label"])}' + (" · 旧设置，绑定策略后不参与通知" if axis["key"] == "deliver" else "") + f'</summary>{group}</details>'
        groups.append(group)
    # Source identity/acquisition first, then notification choice, then optional processing.
    form += "".join(groups[:2])
    options = ('<option value="">旧式内联策略（不绑定）</option>' if existing else '<option value="">请选择已保存的策略</option>')
    for ident, template in snap["policies"].items():
        selected = ' selected' if binding and binding["policy_id"] == ident else ""
        options += f'<option value="{E(ident)}"{selected}>{E(template["values"].get("label") or ident)}</option>'
    form += '<fieldset><legend>通知策略</legend><label class="field">选择通知策略 '
    form += f'<select name="notify_policy"' + (" required" if not existing else "") + f'>{options}</select></label>'
    form += '<div class="row-actions">' + editor_link("/notifications?new=1", "创建策略")
    policy_id = (binding or {}).get("policy_id")
    if policy_id:
        form += f'<a class="btn" href="/notifications?edit={quote(policy_id)}" data-editor-open data-edit-bound-policy>编辑所选策略</a>'
    else:
        form += '<a class="btn" href="/notifications" data-editor-open data-edit-bound-policy hidden>编辑所选策略</a>'
    form += '<a class="btn" href="/notifications">管理全部策略</a></div>'
    form += '<p class="note">参数由策略统一管理，这里只读展示。需要不同设置时，请复制策略后绑定。</p>'
    form += '<details data-bound-policy-details><summary>策略参数（只读）</summary>' + _policy_records(snap, schema) + '</details>'
    if name in snap.get("migration", {}).get("sources", []):
        form += '<p class="notice">此源含旧版独立设置，当前行为见右侧。先到通知页迁移为独立策略，再修改绑定；不会静默删除旧设置。<a href="/notifications">查看迁移</a></p>'
    form += '<button class="btn" type="button" data-sync-source-version>核对并同步最新配置版本</button><p class="note" data-source-version-note hidden></p>'
    if existing:
        form += '<label class="check"><input type="checkbox" name="confirm_conversion" value="1">我已预览并确认绑定/解绑引起的行为与预算范围变化</label>'
    form += '</fieldset>' + "".join(groups[2:]) + _buttons() + '</form>'
    aside = '<aside class="rule-aside"><p class="eyebrow">EFFECTIVE STRATEGY</p><h3>实际会怎样通知</h3>'
    aside += f'<p data-effective="{E(name)}">{E(rule_summary(binding, payload["slots"]))}</p>'
    if row:
        aside += f'<p>{E(REASONS.get(row["reason"], row["reason"]))} · 待通知 {row["pending"]} 条</p>'
    aside += help_tip("预览只读取现有队列与预算，不抓取、不调模型、不发卡。保存配置也不会暗中发送通知。", "source-preview-help", "预览与保存说明") + '</aside>'
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
                   "采集 " + " / ".join(payload['slots'].get(s, '') for s in src["slots"]))
        links = ''
        if token:
            links = (editor_link(f'/config?edit={quote(src["name"])}', "编辑")
                     + editor_link(f'/config?copy={quote(src["name"])}', "复制"))
            links += _start("source-toggle", token, payload["sources_hash"], "/config", cls="inline")
            links += _hidden("name", src["name"]) + _hidden("state", "disable" if src["enabled"] else "enable")
            links += f'<button class="btn">{"停用" if src["enabled"] else "启用"}</button></form>'
            links += delete_link(payload, token, "source", src["name"])
        cards.append(f'<article class="source-row" data-source="{E(src["name"])}"><div><h3>{E(src["card_title"])}</h3>'
                     f'<p class="note"><code>{E(src["name"])}</code> · {E(ADAPTER_LABELS.get(src["adapter"], src["adapter"]))} · {E(cadence)}</p>'
                     f'<p><span data-effective="{E(src["name"])}">{E(_brief_rule(row["after"], payload["slots"]))}</span>'
                     + help_tip(rule_summary(row["after"], payload["slots"]), "source-rule-" + src["name"], "完整通知策略") + '</p></div>'
                     f'<div class="source-state"><span class="pill {E(src["status_tone"] or "muted") if src["enabled"] else "warn"}">{E(src["status_label"] if src["enabled"] else "已停用")}</span>'
                     f'<span data-pending="{E(src["name"])}">{row["pending"]} 条待通知</span></div><div class="row-actions">{links}</div></article>')
    filter_form = '<form method="get" action="/config" class="source-filters"><label>搜索消息源 '
    filter_form += f'<input name="search" value="{E(page.get("search", ""))}" placeholder="名称、标题、适配器"></label>'
    for key, label, choices in (("policy_filter", "通知策略", [("legacy", "旧式策略"), *[(n, p["values"].get("label") or n) for n, p in snapshot["policies"].items()]]),
                                 ("enabled_filter", "启用状态", [("on", "已启用"), ("off", "已停用")]),
                                 ("adapter_filter", "适配器", [(a, ADAPTER_LABELS.get(a, a)) for a in schema["adapters"]])):
        filter_form += f'<label>{label}<select name="{key}"><option value="">全部</option>' + "".join(
            f'<option value="{E(value)}"' + (" selected" if page.get(key) == value else "") + f'>{E(text)}</option>' for value, text in choices) + '</select></label>'
    filter_form += '<button class="btn">筛选</button><a href="/config">清除</a></form>'
    body = ('<div class="page-intro"><div><p class="eyebrow">SOURCE LIBRARY</p><p class="lead">采集什么，与何时打扰你，是两件事。</p></div>'
            + (editor_link("/config?new=1", "添加消息源", primary=True) if token else "") + '</div>'
            + '<div data-editor-result></div><p data-editor-drafts class="note" hidden></p>'
            + _acquisition_settings(payload, token, page) + _rsshub_settings(payload, schema, token, page) + filter_form
            + '<h2>信源清单</h2><div class="source-list" data-read-region="sources">' + ("".join(cards) or '<p class="empty">还没有匹配的消息源。先创建通知策略，再添加消息源。</p>') + '</div>')
    if page.get("acquisition") and token:
        pass
    elif page.get("copy") and token:
        form = '<p class="note">新源默认停用。</p>' + _start("source-copy", token, payload["sources_hash"], "/config")
        form += _hidden("name", page["copy"]) + '<label class="field">新的稳定 ID<input name="new_name" required pattern="[A-Za-z0-9_][A-Za-z0-9_-]*"></label>' + _buttons(preview=False) + '</form>'
        body = editor_panel("source-copy", "复制消息源", form, "/config", "source-copy:" + str(page["copy"])) + body
    elif token and (page.get("edit") or page.get("new") or page.get("prefill")):
        edit_name = str(page.get("edit") or "")
        body = editor_panel("source", "编辑消息源" if edit_name else "添加消息源", _source_editor(payload, schema, token, page),
                            "/config", "source:" + (edit_name or "new:" + str(page.get("prefill") or ""))) + body
    elif not token:
        body += '<p class="notice">写操作未开启，表单不渲染。需要 service.yaml 的 view.actions。</p>'
    return body + _rss(page) + _policy_catalog(payload, schema) + (delete_dialog() + editor_dialog() if token else "")


def _acquisition_settings(payload, token, page):
    clocks = payload["slots"]
    body = '<section class="acquisition-panel summary-strip" data-read-region="acquisition" data-acquisition-clocks="' + E(json.dumps(clocks)) + '"><div><h2>采集时间'
    body += help_tip("只决定消息源何时抓取，不控制通知策略的发送时间。共用一个采集时间的源同步变化；内部关联不会因修改时刻而改变。", 'acquisition-summary-help', '采集时间说明') + '</h2>'
    body += '<p>' + E(' / '.join(sorted(clocks.values())) or '尚未设置采集时间') + '</p></div>'
    if token:
        body += editor_link('/config?acquisition=1', '管理采集时间')
    body += '</section>'
    if not token or not page.get("acquisition"):
        return body
    form = _start('global-save', token, payload["sources_hash"], '/config')
    form += _hidden('preview_kind', 'global') + _hidden('global_scope', 'acquisition') + _hidden('slots_present', '1')
    form += '<p class="note">24 小时制。保存后，共用此时间的消息源会同步更新采集时刻；通知策略的独立发送时间不变。</p><div data-slot-rows>'
    entries = page.get("slot_rows", list(clocks.items()))
    for index, (key, clock) in enumerate(entries):
        sources = [(name, values) for name, values in payload["source_values"].items() if key and key in values.get("fetch.slots", [])]
        policies = [name for name, item in payload["notification"]["policies"].items() if key and key in item["values"].get("slots", [])]
        legacy = [r["source"] for r in payload["notification"]["sources"] if key and key in r["after"]["values"].get("slots", [])]
        locked = bool(sources or policies or legacy)
        form += '<div class="slot-edit-row">' + _hidden('slot_id', key)
        form += f'<label>采集时间 {index + 1}<input type="time" name="slot_time" value="{E(clock)}" aria-label="采集时间 {index + 1}"></label>'
        form += f'<button class="btn" type="submit" name="editor_intent" value="remove-slot:{index}" formnovalidate data-remove-slot' + (' disabled' if locked else '') + '>移除</button>'
        if locked:
            form += f'<details class="slot-references"><summary>{len(sources)} 个源引用 · 查看影响范围</summary><ul>'
            for name, values in sources:
                state = ('已停用' if not values["enabled"] else '定时采集' if values["fetch.trigger"] == 'slot' else '保留引用，当前不按此时间采集')
                form += f'<li>{E(name)} · {E(state)}</li>'
            form += '</ul>'
            if policies or legacy:
                form += '<p class="notice">旧通知策略仍引用此采集时间，修改也会改变旧发送时刻。请先迁移为独立发送时间：' + E('、'.join(policies + legacy)) + '。<a href="/notifications">查看迁移</a></p>'
            form += '<p class="note">解除以上全部引用后才能移除。</p></details>'
        form += '</div>'
    if not entries:
        form += '<p class="empty" data-slot-empty>尚未设置采集时间，点击下方添加。</p>'
    form += '</div><button class="btn" type="submit" name="editor_intent" value="add-slot" formnovalidate data-add-slot>添加采集时间</button>'
    form += _buttons() + '</form>'
    return editor_panel('acquisition', '采集时间', form, '/config', 'acquisition') + body


def _chat_picker(payload, page):
    state = page.get("chat_directory") or {}
    groups = state.get("groups") or []
    target = page.get("chat_value", payload["chat"])
    manual = page.get("chat_mode") == "manual"
    query = page.get("chat_query", "")
    matches = [g for g in groups if query.casefold() in (g["name"] + " " + g["chat_id"]).casefold()]
    selected = next((g for g in groups if g["chat_id"] == target), None)
    options = ''
    if not any(g["chat_id"] == target for g in matches):
        options += f'<option value="{E(target)}" selected>{E(selected["name"] if selected else "当前通知群（名称未加载 / 未在已加载列表中）")}</option>'
    for group in matches:
        options += (f'<option value="{E(group["chat_id"])}" data-directory-group data-group-name="{E(group["name"])}" data-group-external="{int(group["external"])}"'
                    + (' selected' if group["chat_id"] == target else '') + f'>{E(group["name"] or "未命名群")} · {E(group["chat_id"][-8:])}'
                    + (' · 外部群' if group["external"] else '') + '</option>')
    body = '<fieldset data-chat-picker data-chat-groups="' + E(json.dumps(groups, ensure_ascii=False)) + '" data-chat-loaded="' + str(int(bool(state.get("loaded")))) + '" data-chat-more="' + str(int(bool(state.get("has_more")))) + '" data-chat-limited="' + str(int(bool(state.get("limited")))) + '"><legend>通知群' + help_tip("列表是当前发送机器人已加入的群，不是个人账号的全部群。选择只改变草稿，保存后才切换通知目标；不会发测试消息。", "chat-picker-help", "群选择说明") + '</legend>'
    body += _hidden("chat_mode", "manual" if manual else "select") + _hidden("chat_handle", state.get("handle", "")) + _hidden("page_token", state.get("page_token", ""))
    body += '<div class="field" data-chat-select-area><label>选择通知群</label><select ' + ('disabled ' if manual else 'name="chat" ') + 'data-chat-select aria-label="选择通知群">' + options + '</select></div>'
    body += '<div class="field"><label>查找已加载的群</label><input name="chat_query" data-chat-query value="' + E(query) + '" placeholder="输入群名称或关键词" aria-label="查找已加载的群"></div>'
    body += '<div class="row-actions"><button class="btn" type="submit" formaction="/api/actions/chat-list" formnovalidate name="chat_load" value="refresh" data-chat-load>加载 / 刷新群列表</button>'
    body += '<button class="btn" type="submit" formaction="/api/actions/chat-list" formnovalidate name="chat_load" value="more" data-chat-more' + ('' if state.get("has_more") and not state.get("limited") else ' disabled') + '>加载更多群</button>'
    body += '<button class="btn" type="submit" formaction="/api/actions/chat-list" formnovalidate name="editor_intent" value="filter-chat" data-chat-filter>筛选已加载</button></div>'
    status = ("已加载 " + str(len(groups)) + " 个群" if state.get("loaded") else "尚未加载机器人群列表")
    if state.get("has_more"):
        status += "；还有更多群尚未加载，搜索只匹配已加载的群"
    if state.get("limited"):
        status += "。已达列表容量，请刷新或手动填写群 ID"
    if state.get("loaded") and not groups:
        status = "机器人还没有加入任何群；可先将机器人加入目标群后刷新"
    body += '<p class="note" data-chat-status role="status">' + E(status) + '</p>'
    body += '<details' + (' open' if manual else '') + '><summary>群 ID 与手动填写（备用）</summary><p class="note" data-chat-id>当前选择：' + E(target) + '</p>'
    body += '<label class="field" data-chat-manual-field>群 ID<input data-chat-manual ' + ('name="chat"' if manual else 'disabled') + ' value="' + E(target) + '" pattern="oc_.+" aria-label="手动群 ID"></label>'
    body += '<button class="btn" type="submit" formaction="/api/actions/chat-list" formnovalidate name="editor_intent" value="' + ('select-chat' if manual else 'manual-chat') + '" data-chat-mode>' + ('返回列表选择' if manual else '改为手动填写') + '</button></details>'
    result = page.get("chat_result")
    if result and not (result.get("data") or {}).get("draft_only"):
        error = result.get("error") or {}
        body += '<div class="banner ' + ('ok' if result.get("ok") else 'err') + '" data-chat-feedback role="status"><h3>只读群列表 · ' + ('已加载' if result.get("ok") else '加载失败') + '</h3>'
        body += ''.join('<p>' + E(line) + '</p>' for line in [error.get("message"), error.get("hint"), "没有保存配置或发送消息；草稿与原版本保持不变。"] if line) + '</div>'
    return body + '</fieldset>'


def notifications_body(payload, schema, actions, page):
    actions, page = actions or {}, page or {}
    token = actions.get("token", "")
    snap = payload["notification"]
    gates = snap["notifications"] or {}
    body = '<p class="eyebrow">NOTIFICATION CONTROL</p><p class="lead">每个策略独立管理发送时间；绑定同一策略的消息源同步生效，各自计数。</p>'
    migration = snap.get("migration", {})
    if migration.get("policies") or migration.get("sources"):
        body += '<details class="notice" data-read-region="migration"><summary>旧通知配置待迁移</summary><p>旧发送时间仍引用全局采集计划；旧源覆盖值将转为独立策略。迁移保留当前行为和投递台账。</p>'
        body += f'<p>{len(migration.get("policies", []))} 个旧时间策略 · {len(migration.get("sources", []))} 个源含独立设置</p>'
        if token:
            body += _start("notification-migrate", token, payload["sources_hash"], "/notifications") + _buttons(preview=False) + '</form>'
        body += '</details>'
    else:
        body += '<div data-read-region="migration" hidden></div>'
    body += '<section class="global-panel summary-strip" data-read-region="global"><div><h2>全局硬限制'
    body += help_tip("更新已有卡片也计一次。全局限制约束全部信源，源级例外不能越过；暂停通知不会停止采集。全局上限留空 = 未配置硬上限；0 = 完全不发送。", "global-summary-help", "全局限制说明") + '</h2>'
    limit = gates.get("max_cards_per_day")
    body += f'<p>{"通知已暂停" if gates.get("paused") else "通知未暂停"} · 今日 <b data-global-count>{snap["global_budget"].get("cards", 0)}</b> / {E(limit if limit is not None else "不限")} 次</p>'
    body += f'<p class="note">{E(snap["time_zone"])}' + (f' · 静默 {E(gates["quiet_hours"])}' if gates.get("quiet_hours") else '') + '</p></div>'
    if token:
        body += editor_link("/notifications?global=1", "全局设置")
    body += '</section>'
    if token and page.get("global"):
        form = _start("global-save", token, payload["sources_hash"], "/notifications") + _hidden("preview_kind", "global") + _hidden("global_scope", "notifications")
        form += _chat_picker(payload, page)
        form += '<div class="field-grid">' + "".join(_field(f, gates.get(f["path"], f.get("default")), prefix="g.", scope="global",
                                                           slots=payload["slots"]) for f in schema["notification_fields"]) + '</div>'
        form += '<p class="note">全局上限留空 = 未配置硬上限；0 = 完全不发送。策略的每源限制仍独立生效。</p>'
        form += '<p class="note">采集时刻在消息源页管理：' + editor_link('/config?acquisition=1', '管理采集时间') + '</p>' + _buttons() + '</form>'
        body += editor_panel("global", "全局通知设置", form, "/notifications", "global")
    elif not token:
        body += '<p class="notice">写操作未开启，配置表单不渲染。</p>'
    body += '<div data-editor-result></div><p data-editor-drafts class="note" hidden></p><div class="section-heading"><h2>通知策略模板</h2>'
    if token:
        body += editor_link("/notifications?new=1", "创建策略", primary=True)
    body += '</div><div class="policy-grid" data-read-region="policies">'
    for ident, template in snap["policies"].items():
        values = template["values"]
        rule = {"policy_id": ident, "values": values}
        body += f'<article class="policy-card" data-policy="{E(ident)}"><h3>{E(values.get("label") or ident)}</h3><p><span data-policy-summary="{E(ident)}">{E(_brief_rule(rule, payload["slots"]))}</span>'
        body += help_tip(rule_summary(rule, payload["slots"]), "policy-rule-" + ident, "完整策略与额度") + '</p>'
        body += f'<p class="note">绑定 {len(template["bound_sources"])} 个源' + help_tip(f"策略 ID：{ident}。含停用源；额度按每源独立计数，不共享。", "policy-meta-" + ident, "策略标识与绑定说明") + '</p>'
        if token:
            body += editor_link(f'/notifications?edit={quote(ident)}', "编辑") + " " + editor_link(f'/notifications?copy={quote(ident)}', "复制")
            body += " " + delete_link(payload, token, "policy", ident)
        body += '</article>'
    if not snap["policies"]:
        body += '<p class="empty">还没有模板。点击“创建策略”选择起点，保存后才能绑定消息源。</p>'
    body += '</div>'
    if token and any(page.get(key) for key in ("new", "edit", "copy", "preset")):
        ident = str(page.get("edit") or page.get("copy") or "")
        if ident and ident not in snap["policies"]:
            return body + '<p class="notice">策略不存在，请返回模板清单重新选择；不会把编辑转换为新建。</p>'
        values = dict(snap["policies"].get(ident, {}).get("values") or {})
        if "time_rows" in page:
            values["times"] = page["time_rows"]
        preset = page.get("preset", "")
        if preset == "continuous":
            values.update(mode="immediate", form="append_card", min_gap_min=10, quiet_hours="23:00-07:30")
        elif preset == "digest":
            values.update(mode="digest", times={"morning": "08:15", "evening": "18:35"})
        elif preset == "collect":
            values.update(mode="collect")
        form = '<p class="preset-links">起点：' + " · ".join(editor_link(f'/notifications?new=1&preset={key}', label) for key, label in (("digest", "早晚汇总"), ("continuous", "持续更新"), ("collect", "只采集"))) + '</p>'
        if ident:
            form = ""
        form += _start("policy-save", token, payload["sources_hash"], "/notifications") + _hidden("preview_kind", "policy")
        copying = bool(page.get("copy"))
        if copying:
            form += _hidden("copy_from", ident)
        if not page.get("edit"):
            form += _hidden("create_only", "1")
        form += '<div class="field"><div class="field-label"><label for="policy-name">策略 ID</label>'
        form += help_tip("唯一标识，创建后不能修改；复制策略需填写新 ID。用户看到的名称可在下方修改。", "policy-id-help", "策略 ID 说明") + '</div>'
        form += '<input id="policy-name" name="name" required pattern="[A-Za-z0-9_][A-Za-z0-9_-]*" value="' + E("" if copying else ident) + '"' + (" readonly" if page.get("edit") else "") + '></div>'
        for advanced in (False, True):
            if advanced:
                form += '<details data-policy-advanced><summary>高级设置 · 卡片、静默与额度</summary>'
            form += '<div class="field-grid">' + "".join(_field(f, values.get(f["path"], f.get("default")), prefix="p.", scope="policy", slots=payload["slots"])
                    for f in schema["policy_fields"] if (f["path"] not in {"label", "mode", "times"}) == advanced) + '</div>'
            if advanced:
                form += '</details>'
        if ident and not copying:
            form += '<p class="notice">保存后，下列绑定源将同步使用此策略的新参数与发送时间；其他策略不受影响。</p>'
            form += '<details><summary>受影响的信源</summary><ul>' + "".join(f'<li>{E(name)} · {"已启用" if payload["source_values"][name]["enabled"] else "已停用"}</li>' for name in snap["policies"].get(ident, {}).get("bound_sources", [])) + '</ul></details>'
        form += _buttons() + '</form>'
        body = editor_panel("policy", "编辑策略" if page.get("edit") else "复制策略" if copying else "创建策略", form, "/notifications",
                            "policy:" + ("copy:" if copying else "edit:" if ident else "new:") + (ident or preset)) + body
    return body + _policy_catalog(payload, schema) + (delete_dialog() + editor_dialog() if token else "")


def preview_result(data):
    return '<section class="preview-result"><h2>只读预览 · 不写入、不发送</h2>' + "".join(
        f'<article><h3>{E(row["source"])}</h3><p>之前：{E(row["before_summary"])}</p><p>之后：{E(row["summary"])}</p><p>{row["pending"]} 条待处理 · {E(row["reason_label"])}</p><p>{E(row["consequence_label"])}</p></article>'
        for row in data.get("sources", [])) + '</section>'


def migration_result(data):
    from newspipe.config import describe_schema
    body = '<section class="preview-result"><h2>' + ("迁移预览 · 尚未应用" if data.get("dry_run") else "通知配置已迁移") + '</h2>'
    for change in data.get("changes", []):
        if change.get("source"):
            body += f'<article><h3>{E(change["source"])} → {E(change["policy"])}</h3>'
            body += f'<p>原策略：{E(change["before_policy"])}</p><details><summary>保留的策略参数（只读）</summary>'
            values = change["values"]
            body += _policy_records({"policies": {change["policy"]: {
                "values": values, "summary": rule_summary({"policy_id": change["policy"], "values": values}, {})}}}, describe_schema())
            body += '</details></article>'
        else:
            body += f'<article><h3>策略 {E(change["policy"])}</h3><p>固定发送时间：{E(" / ".join(change["times"].values()))}</p></article>'
    return body + '</section>'


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
    body = '<section data-read-region="operations"><h2>采集与通知' + help_tip("采集只入待处理队列；通知只读取队列，不抓取。两者都保留当前策略。手动越过汇总时钟仍不能越过暂停或预算。", "operations-help", "采集与通知说明") + '</h2>'
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
