"""Read-only story drill-down and schema-driven editor, never a second writer."""
from datetime import datetime
import html
from urllib.parse import urlencode

from newspipe.story_state import StoryStorageError
from newspipe.webui import editor_link, editor_panel, editor_dialog, help_tip

E = lambda value: html.escape(str(value), quote=True)
MODES = {"off": "关闭", "observe": "记录原始版本与候选"}
RELATIONS = {"exact_copy": "完整正文相同（提案）", "candidate": "可能相关（候选）",
             "unclassified_change": "同一条目有未分类变化"}
LIMITS = {"scan_limit_reached": "候选扫描达到上限，可能漏掉相关内容",
          "receipt_display_truncated": "本次回执展示已截断，原始历史仍保留",
          "migration_baseline_missing_material": "旧原文缺失或不完整，只建立迁移基线，不判断重复或进展"}
FIELDS = {"title": "标题", "text": "正文", "facts_json": "结构化事实", "references": "引用链接",
          "permalink": "来源链接", "text_complete": "正文完整性", "source_material": "来源材料（具体差异尚未验证）"}
STATUSES = {"recorded": "已记录", "stale_settings": "设置已变更：仅保留原文，不生成提案",
            "stale_history": "历史已更新：仅保留原文，不生成提案"}
UI_LIMIT = 5
COMPARISONS = {"duplicate": "重复报道（提案）", "progress": "后续进展（提案）", "viewpoint": "不同观点（提案）",
               "unrelated": "未发现事件关联", "uncertain": "不确定"}
DIFFERENCES = {"new_fact": "新增事实（来源声称）", "changed_claim": "主张对照", "viewpoint": "不同观点", "unclassified": "原文变化（未分类）"}
REASONS = {"invalid_model_evidence": "模型结构或引用不合格", "missing_citations": "缺少原文引用",
           "insufficient_cited_material": "只有标题或空白引用，不能确认事件关联",
           "incompatible_difference_category": "差异类别与关系结论冲突，保留为不确定",
           "incomplete_duplicate_evidence": "正文不完整或比较材料已截断，不能确认重复",
           "chronology_unverified": "发布时间未知或倒序，不能确认后续进展",
           "missing_viewpoint_citations": "缺少不同观点的双侧引用", "local_candidate_only": "仅本地候选，未作语义判断",
           "local_change_unclassified": "只展示原文片段差异，尚未判断进展或观点",
           "model_budget_fallback": "比较预算不足，保留为不确定", "model_unavailable": "模型不可用，保留为不确定",
           "model_uncertain": "模型认为证据不足", "model_unrelated": "未发现关联，不代表一定无关",
           "model_cited_proposal": "引用已核对；语义判断仍需自行检查，不代表事实验证",
           "complete_evidence_hash_equal": "完整原文证据指纹一致，仅提出副本关系"}


def _url(**query):
    return "/items?" + urlencode(query)


def _time(value):
    try:
        return datetime.fromtimestamp(float(value)).isoformat(sep=" ", timespec="seconds") if value is not None else "未知"
    except (ValueError, TypeError, OverflowError, OSError):
        return "未知"


def load(store, query):
    """Bound reads and title lookups; errors stay distinct from an empty history."""
    first = lambda key, default="": (query.get(key) or [default])[0]
    page = {"preview_open": bool(first("story_preview")), "detail_id": first("observation"),
            "settings_open": bool(first("story_settings"))}
    if not (page["preview_open"] or page["detail_id"]):
        return page
    try:
        after, limit = int(first("after", "0")), int(first("limit", str(UI_LIMIT)))
        if not 1 <= limit <= UI_LIMIT:
            raise ValueError
        page["after"], page["limit"] = after, limit
        if page["detail_id"]:
            page["detail"] = store.observation(page["detail_id"])
            page["comparisons"] = store.comparisons(page["detail_id"], after=int(first("comparison_after", "0")), limit=UI_LIMIT)
        page["preview"] = store.story_preview(observation_id=page["detail_id"], after=after, limit=limit)
        labels = {}
        for receipt in page["preview"]["receipts"]:
            ids = [r["observation_id"] for r in receipt["observations"][:UI_LIMIT]]
            ids += [p[key] for p in receipt["proposals"][:UI_LIMIT] for key in ("current_id", "previous_id")]
            for ident in ids:
                if ident not in labels and len(labels) < 40:
                    row = store.observation(ident)
                    if row:
                        labels[ident] = row["source"] + " · " + row["evidence"]["title"]
        for receipt in page.get("comparisons", {}).get("receipts", []):
            for pair in receipt["pairs"][:UI_LIMIT]:
                for side in ("current", "previous"):
                    row = pair["result"]["evidence"][side]
                    title = next((r["text"] for r in row["excerpts"] if r["field"] == "title"), "原始版本")
                    labels.setdefault(row["observation_id"], str(row["source"]) + " · " + title)
        page["labels"] = labels
    except StoryStorageError as error:
        page["error"] = "历史暂不可用（" + error.code + "）。检查存储与备份；读取不会修复历史。仍可关闭关联预览。"
    except ValueError:
        page["error"] = "分页参数需要 after >= 0，limit 为 1..5；缩小范围后重试。"
    return page


def entry(status, *, token=""):
    saved = MODES.get(status.get("saved_mode"), "关闭")
    if status.get("availability") == "unavailable":
        message = "历史暂不可用，计数与激活状态未知"
    elif status.get("availability") == "not_initialized":
        message = "尚未记录原始版本"
    else:
        message = f'{status.get("observation_count", 0)} 个原始版本 · {status.get("capture_count", 0)} 次采集回执'
    if status.get("awaiting_collection"):
        message += " · 上次激活：" + MODES.get(status.get("activated_mode"), "尚未激活") + " · 新设置等待真实采集激活"
    return ('<section class="rsshub-panel summary-strip story-entry"><div><h2>关联预览 '
            + help_tip("页面只读取已保存结果，不调 AI。显式比较任务可提出重复、进展与观点关系，不自动合并、不改变通知或收藏。", "story-help", "关联预览说明")
            + f'</h2><p class="hint" data-story-status>已保存：{E(saved)} · {E(message)}</p></div><div class="row-actions">'
            + '<a class="btn" href="/items?story_preview=1">查看关联预览</a>'
            + (editor_link("/items?story_settings=1", "预览设置") if token else "") + '</div></section>')


def _version_link(ident, labels, *, fallback="原始版本"):
    title = labels.get(ident, fallback + " · " + str(ident)[-10:])
    return f'<a href="{E(_url(observation=ident))}">{E(title)}</a>'


def _receipts(data, labels, *, observation="", after=0):
    if data.get("availability") == "not_initialized":
        return '<p class="empty">尚未记录原始版本。开启预览后，下一次真实采集才会开始记录。</p>'
    body = ""
    for receipt in data.get("receipts", []):
        proposals, observations = receipt["proposals"], receipt["observations"]
        body += ('<article class="story-receipt"><header><h3>采集回执 #' + E(receipt["capture_sequence"])
                 + '</h3><span class="hint">' + E(_time(receipt["at"])) + '</span></header><p>'
                 + E(STATUSES.get(receipt["status"], receipt["status"])) + ' · ' + E(MODES.get(receipt["mode"], receipt["mode"]))
                 + f' · {len(observations)} 个版本 · {len(proposals)} 个提案</p>')
        body += '<ul class="story-relations">'
        for proposal in proposals[:UI_LIMIT]:
            fields = "、".join(FIELDS.get(key, key) for key in proposal["evidence_fields"])
            description = "仅为本地词面相似，不能确认同一事件或事实一致。" if proposal["relation"] == "candidate" else "尚未判断是后续进展、纠正还是不同观点。" if proposal["relation"] == "unclassified_change" else "只提出正文相同，不合并条目、不减少通知。"
            body += ('<li><strong>' + E(RELATIONS.get(proposal["relation"], "未分类提案")) + '</strong>'
                     + '<p>' + _version_link(proposal["current_id"], labels, fallback="当前版本")
                     + ' <span class="hint">对照</span> ' + _version_link(proposal["previous_id"], labels, fallback="对照版本") + '</p>'
                     + f'<p class="hint">{E(description)}{E(" 变化证据：" + fields) if fields else ""}</p></li>')
        body += '</ul>'
        if not proposals:
            body += '<p class="hint">本次未保存关联提案。这不代表内容一定无关。</p>'
        if len(proposals) > UI_LIMIT:
            body += '<p class="hint">此处只展示前 5 个提案；完整有界回执可用 stories preview --json 查看。</p>'
        if observations:
            body += '<details><summary>本次原始版本</summary><ul>'
            for row in observations[:UI_LIMIT]:
                body += '<li>' + _version_link(row["observation_id"], labels)
                if row["baseline_reason"]:
                    body += '<span class="hint"> · 迁移基线：' + E(row["baseline_reason"]) + '</span>'
                body += '</li>'
            body += '</ul>' + ('<p class="hint">版本列表仅展示前 5 条。</p>' if len(observations) > UI_LIMIT else '') + '</details>'
        bounds = receipt["bounds"]
        body += '<details><summary>检索依据与限制</summary><dl class="receipt">'
        for key, label in (("window_days", "回看天数"), ("candidate_limit", "每条候选上限"), ("scan_limit", "扫描上限")):
            body += f'<dt>{E(label)}</dt><dd>{E(bounds.get(key, "未记录"))}</dd>'
        body += f'<dt>设置修订</dt><dd><code>{E(receipt["revision"][:12])}</code> · 保存时的范围，不按当前设置重算</dd></dl>'
        for limitation in receipt["limitations"]:
            body += '<p class="hint">' + E(LIMITS.get(limitation, limitation)) + '</p>'
        body += '</details></article>'
    if not data.get("receipts"):
        body += '<p class="empty">没有匹配的采集回执；不代表没有原始版本。</p>'
    if data.get("has_more"):
        body += f'<a class="btn" href="{E(_url(**({"observation": observation} if observation else {"story_preview": 1}), after=data["next_after"]))}">下一页回执</a>'
    if after:
        body += f' <a class="btn" href="{E(_url(**({"observation": observation} if observation else {"story_preview": 1})))}">回到首批</a>'
    return body


def _source_link(reference):
    if reference.get("clickable"):
        return f'<a href="{E(reference["url"])}" target="_blank" rel="noopener noreferrer">{E(reference["url"])}</a>'
    return '<span class="hint">' + E(reference.get("url") or "来源链接不可用") + (' · 已脱敏，不提供原文跳转' if reference.get("redacted") else '') + '</span>'


def _comparisons(data, labels, observation):
    body = '<section class="story-comparisons"><h3>关联与差异</h3><p class="hint">只读已保存的比较。提案不等于事实验证，不合并条目、不减少通知。</p>'
    for receipt in data.get("receipts", []):
        body += '<article class="story-receipt"><header><h3>比较 #' + E(receipt["sequence"]) + '</h3><span class="hint">' + E(_time(receipt["at"])) + '</span></header>'
        if not receipt.get("active"):
            body += '<p class="notice">仅历史记录：设置或激活范围已变化，不作为当前关联提案。</p>'
        for pair in receipt["pairs"][:UI_LIMIT]:
            result = pair["result"]
            body += '<p><strong>' + E(COMPARISONS.get(result["relation"], "不确定")) + '</strong> · ' + _version_link(pair["previous_id"], labels, fallback="对照版本") + '</p>'
            body += '<p class="hint">' + E(REASONS.get(result["reason"], "证据不足")) + '</p>'
            if result["differences"]:
                body += '<details><summary>查看原文差异</summary>'
                for diff in result["differences"]:
                    body += '<h4>' + E(DIFFERENCES.get(diff["kind"], "未分类对照")) + '</h4><div class="field-grid story-difference">'
                    for side, label in (("previous", "对照原文"), ("current", "当前原文")):
                        citation = diff[side]
                        body += '<div><p class="hint">' + E(label) + ' · ' + _version_link(citation["observation_id"], labels) + '</p><blockquote class="story-text">' + E(citation["text"]) + '</blockquote></div>'
                    body += '</div>'
                body += '</details>'
        if not receipt["pairs"]:
            body += '<p class="hint">没有可评价的已保存候选；不代表没有相关内容。</p>'
        if receipt.get("has_more_candidates"):
            body += '<p class="hint">本次只评价前几个候选，其他候选未评价。</p>'
        usage = receipt["accounting"]
        body += '<details><summary>模型用量与比较依据</summary><dl class="receipt">'
        for key, label in (("attempts", "请求次数"), ("estimated_tokens", "预留估计 token"),
                           ("measured_tokens", "供应商实测 token"), ("unknown_tokens", "未知消耗预留 token"),
                           ("cache_hits", "缓存命中"), ("budget_fallbacks", "预算回退")):
            body += '<dt>' + E(label) + '</dt><dd>' + E(usage.get(key, 0)) + '</dd>'
        body += '<dt>耗时</dt><dd>' + E(receipt["latency_ms"]) + ' ms</dd><dt>采集基线</dt><dd>#' + E(receipt["capture_sequence"]) + '</dd>'
        body += '<dt>比较版本</dt><dd>' + E(receipt["prompt_version"]) + '</dd></dl><p class="hint">预留是保守估计，不是账单；未返回用量的请求仍按未知消耗保留预算。缓存命中不产生新请求。</p>'
        for pair in receipt["pairs"]:
            model = pair.get("accounting", {})
            if model.get("model"):
                body += '<p class="hint">模型：' + E(model["model"]) + ' · ' + E(model.get("provider", "")) + '</p>'
        for limitation in receipt.get("limitations", []):
            body += '<p class="hint">' + E(LIMITS.get(limitation, "设置变化，任务提前停止" if limitation == "settings_changed_job_stopped" else limitation)) + '</p>'
        body += '</details></article>'
    if not data.get("receipts"):
        body += '<p class="empty">尚未执行这个版本的比较任务。</p>'
    if data.get("has_more"):
        body += '<a class="btn" href="' + E(_url(observation=observation, comparison_after=data["next_after"])) + '">下一页比较</a>'
    body += '<details><summary>如何执行比较</summary><p class="hint">先开启观察并真实采集，再用 CLI 显式执行。以下命令只作本地评价；追加 --allow-model 会请求模型，可能收费，并要求 AI 比较设置已启用。</p><code>newspipe stories evaluate --observation ' + E(observation) + ' --limit 1 --json</code></details></section>'
    return body


def drilldown(page):
    if not (page.get("preview_open") or page.get("detail_id")):
        return ""
    body = '<div class="section-heading"><h2>' + ('原始版本' if page.get("detail_id") else '关联预览') + '</h2><a class="btn" href="/items">返回条目</a></div>'
    body += '<p class="note">读取已保存的原文与提案，不重新采集或比较。通知与收藏保持独立。</p>'
    if page.get("error"):
        return body + '<p class="notice">' + E(page["error"]) + '</p>'
    if page.get("detail_id"):
        row = page.get("detail")
        if not row:
            return body + '<p class="empty">没有这个原始版本，请从关联预览选择有效版本。</p>'
        evidence = row["evidence"]
        body += ('<article class="story-version"><h3>' + E(evidence["title"] or "无原文标题")
                 + '</h3><p class="hint">' + E(row["source"]) + ' · ' + E(row["kind"]) + ' · 首次记录 '
                 + E(_time(row["first_seen"])) + '</p><p>' + _source_link(evidence["permalink"]) + '</p>')
        body += '<details><summary>原始正文</summary><pre class="story-text">' + E(evidence["text"] or "原文不可用") + '</pre>'
        if evidence["text_truncated"]:
            body += '<p class="hint">此处正文已截断至 20,000 字符；私有原始记录仍完整保留。</p>'
        if not evidence["text_complete"]:
            body += '<p class="hint">来源未提供可用的完整正文声明，不作为确定副本证据。</p>'
        body += '</details><details><summary>证据与溯源</summary><dl class="receipt">'
        for label, value in (("版本 ID", row["observation_id"]), ("提取版本", row["extractor_version"]),
                             ("历史来源", row["origin"]), ("发布时间", _time(row["pub_ts"])), ("版本指纹", row["version_hash"])):
            body += f'<dt>{E(label)}</dt><dd>{E(value)}</dd>'
        body += '</dl><pre class="story-text">' + E(evidence["facts_text"]) + '</pre>'
        for ref in evidence["references"]:
            body += '<p>' + E(ref["role"]) + ' · ' + _source_link(ref) + '</p>'
        if evidence["facts_truncated"] or evidence["references_truncated"] or not evidence.get("facts_projection_complete", True):
            body += '<p class="hint">部分事实或引用已省略、脱敏或截断，原始记录仍保留。</p>'
        body += '</details></article>' + _comparisons(page.get("comparisons") or {}, page.get("labels") or {}, page["detail_id"]) + '<h3>这个版本的已保存候选</h3>'
    return body + _receipts(page.get("preview") or {}, page.get("labels") or {},
                            observation=page.get("detail_id", ""), after=page.get("after", 0))


def settings_editor(settings, fields, *, token, base_hash):
    from newspipe.workbench import _start, _field, _buttons
    if not token:
        return ""
    form = _start("story-settings", token, base_hash, "/items")
    form += '<p class="note">仅记录原始版本和候选。保存后从下一次真实采集激活；已有队列、通知、收藏不迁移，历史不会自动删除。</p>'
    advanced = ""
    for field in fields:
        control = _field(field, settings[field["path"]], prefix="s.", scope="stories", slots={})
        if field.get("advanced"):
            advanced += control
        else:
            form += control
    form += '<details><summary>检索范围与比较预算</summary><div class="field-grid">' + advanced + '</div><p class="hint">本地检索不消耗比较 token。显式 AI 任务另受比较与整体模型预算限制；保留 20 次每日、5 次每小时请求及 100,000 预留 token 给摘要等其他工作。模型整体额度过小时可能完全无法比较。</p></details>'
    form += _buttons(preview=False) + '</form>'
    return editor_panel("story-settings", "关联预览设置", form, "/items", "story-settings")


def render(status, page, *, token="", base_hash="", settings=None, fields=()):
    body = entry(status, token=token)
    if page.get("settings_open"):
        body = settings_editor(settings or {}, fields, token=token, base_hash=base_hash) + body
    body += drilldown(page)
    return body + ('<div data-editor-result></div>' + editor_dialog() if token else '')
