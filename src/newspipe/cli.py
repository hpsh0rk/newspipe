"""资讯管线 v2 —— 命令行入口（cron 与手工调试都走它）。

    cli.py --slot am|noon|pm            槽位批次（digest 源）
    cli.py --alert                      分钟级轮询（poll 源）
    cli.py --source <n> [--slot s]      手工跑单个源
    cli.py --enrich-only [--date d] [--retry-degraded]   只补加工（不抓取、不发卡）
    cli.py --status                     运行态一览
    cli.py --migrate                    把 v1 的 pushed-ids/cursors 迁到 v2 state/
    cli.py --probe-model [--capability] 只读探针：模型解析结果（绝不打印密钥）
    cli.py --card-preview <batch>       把某个批次渲染成卡片 JSON（不发送）
    cli.py --list-sources               信源四轴一览
    cli.py --serve [--once]             常驻服务：自带调度 + 入站（独立运行形态）
    cli.py --probe-channel [--chat id]  直连通道自检：真发一张卡（不打印任何密钥）

通用开关：`--dry`（零副作用：不写游标/心跳/批次、不调模型）、`--verbose`（人读输出）、
`--json`（机器读输出）。

**成功路径 stdout 必须为空**（no_agent cron 任务把非空 stdout 当告警投递给用户）：
只有失败、需要人知道的事才写 stdout。日志一律进 state/status 心跳。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


from newspipe import channel, config, llm, pipeline, render, service, state  # noqa: E402
from newspipe.errors import ConfigError, DeliveryError, NewsError  # noqa: E402


def _emit(text: str) -> None:
    """失败/需人知的信息走这里（no_agent 任务据此投递）。"""
    print(text)


def _print(verbose: bool, json_out: bool, runs: list[pipeline.SourceRun]) -> None:
    if json_out:
        print(json.dumps([r.as_dict() for r in runs], ensure_ascii=False, indent=2))
        return
    if not verbose:
        return
    for r in runs:
        parts = [f"{r.source:<18} {r.status:<16}",
                 f"抓 {r.fetched} / 留 {r.kept} / 新 {r.new} / 发 {r.planned}"]
        if r.queued:
            parts.append(f"顺延 {r.queued}")
        if r.card != "none":
            parts.append(f"卡片 {r.card}")
        if r.enrich:
            parts.append("加工 " + ",".join(f"{k}×{v}" for k, v in sorted(r.enrich.items())))
        if r.note:
            parts.append(f"({r.note})")
        print("  ".join(parts))


def _failed(runs: list[pipeline.SourceRun]) -> list[pipeline.SourceRun]:
    return [r for r in runs if r.status in ("error", "config_missing", "send_failed", "overflow")]


def _service_is_custom(cfg: config.Config) -> bool:
    """服务配置是否偏离默认（默认 = 与阶段 1 完全一致）。"""
    return (cfg.service.channel != "feishu_lark_cli"
            or cfg.service.inbound_mode != "none"
            or (config.default_news_dir() / "service.yaml").is_file())


def cmd_status(store: state.Store, cfg: config.Config, *, json_out: bool) -> int:
    rows = pipeline.stats(store, cfg)
    usage = store.usage(state.today())
    # 服务自述只在**非默认**时出现：没配 service.yaml 时输出与阶段 1 逐字节一致
    svc = service.describe_service(cfg) if _service_is_custom(cfg) else None
    if json_out:
        payload = {"sources": rows, "llm_usage": usage}
        if svc is not None:
            payload["service"] = svc
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    print(f"{'信源':<20}{'触发':<6}{'形态':<12}{'优先级':<8}{'加工':<8}{'状态':<14}{'抓/发':<10}{'顺延':<6}备注")
    for r in rows:
        flag = "" if r["enabled"] else "（已禁用）"
        print(f"{r['source']:<20}{r['trigger']:<6}{r['form']:<12}{r['priority']:<8}"
              f"{r['summary']:<8}{r['status']:<14}"
              f"{str(r['fetched'] or '-'):>4}/{str(r['planned'] or '-'):<5}{r['pending']:<6}"
              f"{r['note']}{flag}")
    # 加工层用量与**失败原因**：降级是"静默回退原文"，不打出原因就没人知道是模型/网络/预算的问题
    calls, degraded = int(usage.get("calls") or 0), int(usage.get("degraded") or 0)
    if calls or degraded:
        reasons = usage.get("reasons") or {}
        top = "、".join(f"{k}×{v}" for k, v in sorted(reasons.items(), key=lambda kv: -kv[1]))
        print(f"\nAI 加工（今日）：调用 {calls} 次，降级 {degraded} 次"
              + (f"（原因：{top}）" if top else ""))
    if svc is not None:
        detail = svc.get("channel_detail") or {}
        creds = detail.get("creds") or {}
        print(f"\n服务：通道={svc['channel']} 入站={svc['inbound_mode']} tick={svc['tick_seconds']:g}s")
        if creds:
            print(f"  凭据：app_id={creds.get('app_id') or '-'} "
                  f"secret={creds.get('app_secret') or '-'} 来源={creds.get('source') or '-'}")
        if detail.get("error"):
            print(f"  通道错误：{detail['error']}")
        last = (svc.get("last_runs") or {}).get("last_run_at")
        print(f"  最近一轮：{last or '（本进程还没跑过）'}")
    return 0


def cmd_list_sources(cfg: config.Config, *, json_out: bool) -> int:
    rows = config.summarize(cfg)
    if json_out:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    print(f"{'信源':<20}{'适配器':<12}{'触发':<22}{'形态':<12}{'加工':<10}{'优先级':<8}闸门")
    for r in rows:
        fetch, deliver, enrich = r["fetch"], r["deliver"], r["enrich"]
        if fetch["trigger"] == "slot":
            trigger = "slot " + "/".join(fetch["slots"])
        elif fetch["trigger"] == "poll":
            trigger = f"poll {fetch['interval_min'] or 0:g}min"
        else:
            trigger = fetch["trigger"]
        gates = []
        if r["filter"]["include"] or r["filter"]["exclude"]:
            gates.append(f"词闸 {r['filter']['include']}+/{r['filter']['exclude']}-")
        if deliver["quiet_hours"]:
            gates.append(f"静默 {deliver['quiet_hours']}")
        if deliver["max_cards_per_day"]:
            gates.append(f"≤{deliver['max_cards_per_day']}卡/日")
        if deliver["min_gap_min"]:
            gates.append(f"间隔≥{deliver['min_gap_min']:g}min")
        if enrich["fetch_body"]:
            gates.append("抓正文")
        if enrich["translate_body"]:
            gates.append("译正文")
        flag = "" if r["enabled"] else "（已禁用）"
        print(f"{r['source']:<20}{r['adapter']:<12}{trigger:<22}{deliver['form']:<12}"
              f"{enrich['summary']:<10}{deliver['priority']:<8}{' · '.join(gates)}{flag}")
    return 0


def cmd_enrich_only(store: state.Store, cfg: config.Config, *, date: str, dry: bool,
                    verbose: bool, json_out: bool, retry_degraded: bool = False) -> int:
    """补加工：把当天批次里还没加工的条目补上（回执保证已加工的不重算）。

    `retry_degraded`：连**加工失败**过的条目一起重来。修完模型/预算/网络问题后需要它——
    否则 `enrich_state="degraded"` 的条目会被当成"已处理"永远跳过，修了也白修。
    有 card_id 的批次会重渲染并更新卡片实体（seq 递增），否则补出来的中文摘要只落在 state 里，
    用户看到的还是原来那张英文卡。
    """
    day = store.root / "batches" / date
    if not day.is_dir():
        _emit(f"没有 {date} 的批次")
        return 0
    total = {"ok": 0, "degraded": 0, "skipped": 0}
    for path in sorted(day.glob("*.json")):
        batch = state.read_json(path)
        if not isinstance(batch, dict):
            continue
        scfg = cfg.sources.get(str(batch.get("source") or ""))
        if scfg is None or scfg.enrich.summary == "off":
            continue
        todo = [i for i in batch.get("items") or []
                if not i.get("enrich_state")
                or (retry_degraded and i.get("enrich_state") == "degraded")]
        if not todo:
            continue
        if dry:
            print(f"  {batch.get('source')}-{batch.get('slot')}: 待加工 {len(todo)} 条")
            continue
        items, counts = pipeline._enrich_new(cfg, store, scfg, batch.get("items") or [], _now(),
                                            retry_degraded=retry_degraded)
        batch["items"] = items
        store.save_batch_at(path, batch)
        for k, v in counts.items():
            total[k] = total.get(k, 0) + v
        if batch.get("card_id"):
            try:
                card = render.render(batch)
                render.assert_within_limit(card, what=f"{batch.get('source')}-{batch.get('slot')}")
                seq = int(batch.get("seq") or 1) + 1
                if channel.update_entity(str(batch["card_id"]), seq, card):
                    batch["seq"] = seq
                    store.save_batch_at(path, batch)
            except Exception as exc:  # 补加工失败不该让整个命令挂掉
                _emit(f"⚠️ {batch.get('source')} 卡片更新失败：{type(exc).__name__}: {exc}"[:200])
        if verbose:
            print(f"  {batch.get('source')}-{batch.get('slot')}: {counts}")
    if json_out:
        print(json.dumps(total, ensure_ascii=False))
    elif verbose:
        print(f"补加工完成：{total}")
    return 0


def cmd_probe_channel(cfg: config.Config, *, chat: str, json_out: bool) -> int:
    """直连通道自检：token → 建实体 → 发卡 → 更新实体。输出里**不含任何密钥**。"""
    from newspipe import backends

    news_dir = config.default_news_dir()
    if cfg.service.channel != "feishu_direct":
        _emit(f"⚠️ service.yaml 的 channel 是 {cfg.service.channel!r}，"
              "但本命令始终用 feishu_direct 自检（这正是要验证的路径）")
    try:
        channel = backends.make_channel("feishu_direct", service_cfg=cfg.service.feishu,
                                        news_dir=news_dir)
        result = channel.probe(chat)
    except (ConfigError, DeliveryError) as exc:
        _emit(f"⚠️ 直连通道自检失败：{type(exc).__name__}: {exc}")
        return 1
    if json_out:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    creds = result.get("creds") or {}
    print(f"通道 {result['channel']} 自检（目标 {chat}）")
    print(f"  凭据：app_id={creds.get('app_id') or '-'} "
          f"app_secret={creds.get('app_secret') or '-'} 来源={creds.get('source') or '-'}")
    for step in result.get("steps") or []:
        mark = "✅" if step.get("ok") else "❌"
        detail = step.get("result") or step.get("error") or ""
        print(f"  {mark} {step['step']:<20} {step.get('ms', 0):>5}ms  {detail}")
    if result.get("message_id"):
        print(f"  卡片已发出：card_id={result.get('card_id')} message_id={result['message_id']}")
    return 0


def _now():
    from datetime import datetime

    return datetime.now()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="news", description="资讯管线 v2")
    ap.add_argument("--slot", choices=["am", "noon", "pm"],
                    help="跑槽位批次；与 --source 同用 = 只跑该槽位里的那一个源")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--alert", action="store_true", help="跑分钟级轮询")
    g.add_argument("--source", help="只跑某个信源")
    g.add_argument("--enrich-only", action="store_true", help="只补加工当天批次")
    g.add_argument("--status", action="store_true", help="运行态一览")
    g.add_argument("--list-sources", action="store_true", help="信源四轴一览")
    g.add_argument("--migrate", action="store_true", help="迁移 v1 状态到 v2 布局")
    g.add_argument("--probe-model", action="store_true", help="模型解析探针（不打印密钥）")
    g.add_argument("--card-preview", metavar="BATCH", help="渲染某个批次为卡片 JSON")
    g.add_argument("--card", metavar="PAYLOAD", help="卡片回调（插件薄壳调它）：payload 为 JSON 文本")
    g.add_argument("--serve", action="store_true", help="常驻服务：自带调度 + 入站（独立运行）")
    g.add_argument("--probe-channel", action="store_true", help="直连通道自检：真发一张卡")
    ap.add_argument("--once", action="store_true", help="--serve 时只跑一轮到期任务就退出")
    ap.add_argument("--chat", help="--probe-channel 的目标会话（默认 sources.yaml 的 chat）")
    ap.add_argument("--capability", default="summarize", help="--probe-model 的 capability")
    ap.add_argument("--dry", action="store_true", help="零副作用（不写状态、不调模型）")
    ap.add_argument("--verbose", "-v", action="store_true")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--date", help="--enrich-only 的日期（默认今天）")
    ap.add_argument("--retry-degraded", action="store_true",
                    help="--enrich-only 时连加工失败过的条目一起重来（修完模型/预算后用）")
    ap.add_argument("--force", action="store_true", help="跳过节流闸（手工调试）")
    args = ap.parse_args(argv)
    modes = [bool(args.alert), bool(args.source), args.enrich_only, args.status,
             args.list_sources, args.migrate, args.probe_model,
             bool(args.card_preview), bool(args.card), args.serve, args.probe_channel]
    if not any(modes) and not args.slot:
        ap.error("需要指定一个模式：--slot / --alert / --serve / --status / --list-sources / …")

    try:
        cfg = config.load()
    except ConfigError as exc:
        _emit(f"⚠️ 资讯管线配置错误：{exc}")
        return 2
    store = state.Store(config.default_news_dir())

    if args.status:
        return cmd_status(store, cfg, json_out=args.json)
    if args.list_sources:
        return cmd_list_sources(cfg, json_out=args.json)
    if args.probe_model:
        print(json.dumps(llm.probe(args.capability, cfg.models), ensure_ascii=False, indent=2))
        return 0
    if args.migrate:
        result = store.migrate_legacy()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.card_preview:
        found = store.load_batch_by_rel(args.card_preview) or store.find_batch(args.card_preview)
        if found is None:
            _emit(f"⚠️ 找不到批次：{args.card_preview}")
            return 2
        _path, batch = found
        print(render.card_json(render.render(batch)))
        return 0
    if args.card:
        # 插件薄壳走这里：成功路径 stdout 为空（不在聊天里插消息）
        from newspipe import interaction

        message = interaction.handle_json(args.card)
        if message:
            print(message)
        return 0
    if args.probe_channel:
        return cmd_probe_channel(cfg, chat=args.chat or cfg.chat, json_out=args.json)
    if args.serve:
        return service.run_forever(cfg, store, dry=args.dry, once=args.once)
    if args.enrich_only:
        return cmd_enrich_only(store, cfg, date=args.date or state.today(), dry=args.dry,
                               verbose=args.verbose, json_out=args.json,
                               retry_degraded=args.retry_degraded)

    runs: list[pipeline.SourceRun] = []
    try:
        if args.source:
            runs = pipeline.run_source(cfg, store, args.source, slot=args.slot,
                                       dry=args.dry, force=True, now=_now())
        elif args.slot:
            runs = pipeline.run_slot(cfg, store, args.slot, dry=args.dry, now=_now())
        elif args.alert:
            runs = pipeline.run_poll(cfg, store, dry=args.dry, force=args.force, now=_now())
    except ConfigError as exc:
        _emit(f"⚠️ 资讯管线配置错误：{exc}")
        return 2
    except NewsError as exc:
        _emit(f"⚠️ 资讯管线故障：{exc}")
        return 1

    _print(args.verbose, args.json, runs)
    bad = _failed(runs)
    if bad and not args.json:
        for r in bad:
            _emit(f"⚠️ 资讯源 {r.source} {r.status}：{r.note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
