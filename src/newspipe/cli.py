"""资讯管线 —— 命令行入口。

两种调用风格并存：

| 风格 | 给谁用 | 例子 |
|---|---|---|
| **子命令**（新，推荐） | Agent / 人 / 脚本 | `newspipe status --json`、`newspipe source list`、`newspipe queue ack ev_xxx` |
| **旧 flag**（保留别名） | 4 个 cron wrapper 与插件薄壳 | `cli.py --slot am`、`cli.py --card '<json>'` |

**契约（`api describe --json` 是权威）：**

1. 任何命令都输出**一个**结构化结果对象：`ok` / `command` / `changed` / `data` / `error` /
   `warnings` / `next`。未捕获异常转成 `E_INTERNAL`，绝不吐堆栈。
2. 退出码：`0` 成功 / `1` 运行时故障 / `2` 用法或配置错 / `3` 网络或投递错 / `4` 校验失败。
3. 写操作支持 `--dry-run`（演练）、`--base-hash`（乐观并发）、`--operation-id`（幂等重试）。
4. **旧 flag 的成功路径 stdout 仍为空**（no_agent cron 任务把非空 stdout 当告警投递给用户）；
   子命令一律输出结果对象——两种受众，两套约定，别混。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from newspipe import (channel, config, edit, events, llm, pipeline, render, result,  # noqa: E402
                      service, state)
from newspipe.errors import ConfigError, DeliveryError, NewsError  # noqa: E402

CONTRACT_VERSION = 1

#: 自描述契约：`api describe --json` 输出它。新增/修改命令**必须**同步这里——
#: 它是 Agent 与（将来的）MCP 投影的唯一来源。
CONTRACT: list[dict[str, Any]] = [
    {"name": "api describe", "summary": "输出本契约（命令、参数、退出码、示例）",
     "usage": "newspipe api describe --json", "args": [], "writes": False,
     "examples": ["newspipe api describe --json"]},
    {"name": "doctor", "summary": "自检：配置能否加载、hooks 是否可用、凭据、元素预算、事件积压",
     "usage": "newspipe doctor [--json]", "args": [], "writes": False,
     "examples": ["newspipe doctor --json"]},
    {"name": "status", "summary": "运行态一览（各源心跳、AI 用量与降级原因、事件积压）",
     "usage": "newspipe status [--json] [--days N]", "args": ["--days"], "writes": False,
     "examples": ["newspipe status --json"]},
    {"name": "list-sources", "summary": "信源四轴一览",
     "usage": "newspipe list-sources [--json]", "args": [], "writes": False,
     "examples": ["newspipe list-sources --json"]},
    {"name": "source list", "summary": "列出信源（含 enabled / 触发 / 形态）",
     "usage": "newspipe source list [--json]", "args": [], "writes": False,
     "examples": ["newspipe source list --json"]},
    {"name": "source show", "summary": "看单个信源的完整四轴配置",
     "usage": "newspipe source show <name> [--json]", "args": ["name"], "writes": False,
     "examples": ["newspipe source show aihot --json"]},
    {"name": "source set", "summary": "新增或整体替换一个信源（先校验再落盘）",
     "usage": "newspipe source set <name> --from-json <json|-> [--dry-run] "
              "[--base-hash H] [--operation-id ID]",
     "args": ["name", "--from-json", "--dry-run", "--base-hash", "--operation-id"],
     "writes": True,
     "examples": ["echo '{\"adapter\":\"aihot\",\"fetch\":{\"trigger\":\"slot\",\"slots\":[\"am\"]}}'"
                  " | newspipe source set aihot --from-json - --dry-run --json"]},
    {"name": "source enable", "summary": "启用/禁用一个信源（只翻 enabled，其余字段原样保留）",
     "usage": "newspipe source enable|disable <name> [--dry-run] [--base-hash H]",
     "args": ["name", "--dry-run", "--base-hash"], "writes": True,
     "examples": ["newspipe source disable producthunt --json"]},
    {"name": "source remove", "summary": "删除一个信源",
     "usage": "newspipe source remove <name> [--dry-run] [--base-hash H]",
     "args": ["name", "--dry-run", "--base-hash"], "writes": True,
     "examples": ["newspipe source remove producthunt --dry-run --json"]},
    {"name": "hooks list", "summary": "列出 hooks.yaml 里的声明与被跳过的声明",
     "usage": "newspipe hooks list [--json]", "args": [], "writes": False,
     "examples": ["newspipe hooks list --json"]},
    {"name": "hooks add", "summary": "新增或替换一个 hook（按 id）",
     "usage": "newspipe hooks add --from-json <json|-> [--dry-run] [--base-hash H]",
     "args": ["--from-json", "--dry-run", "--base-hash"], "writes": True,
     "examples": ["newspipe hooks add --from-json '{\"id\":\"hermes.wiki\","
                  "\"label\":\"⭐ 入库\",\"action\":\"hermes.wiki\",\"handler\":\"~/x.sh\"}' --json"]},
    {"name": "hooks remove", "summary": "删除一个 hook",
     "usage": "newspipe hooks remove <id> [--dry-run] [--base-hash H]",
     "args": ["id", "--dry-run", "--base-hash"], "writes": True,
     "examples": ["newspipe hooks remove hermes.wiki --json"]},
    {"name": "events list", "summary": "读事件流（投递结果、点击、降级、失败）",
     "usage": "newspipe events list [--type T] [--unconsumed] [--days N] [--limit N] [--json]",
     "args": ["--type", "--unconsumed", "--days", "--limit"], "writes": False,
     "examples": ["newspipe events list --unconsumed --json",
                  "newspipe events list --type delivered --days 1 --json"]},
    {"name": "events ack", "summary": "确认消费事件（明确区分 acked/already/unknown）",
     "usage": "newspipe events ack <id...> [--by NAME] [--note TEXT] [--json]",
     "args": ["ids", "--by", "--note"], "writes": True,
     "examples": ["newspipe events ack ev_20261003T180000_ab12 --by hermes --json"]},
    {"name": "events prune", "summary": "删除过期事件日文件",
     "usage": "newspipe events prune [--keep-days N] [--json]",
     "args": ["--keep-days"], "writes": True, "examples": ["newspipe events prune --json"]},
    {"name": "queue list", "summary": "待入库队列（未被确认的收藏事件）",
     "usage": "newspipe queue list [--json] [--days N]", "args": ["--days"], "writes": False,
     "examples": ["newspipe queue list --json"]},
    {"name": "queue ack", "summary": "标记某条收藏已入库（人确认后才调它）",
     "usage": "newspipe queue ack <event_id> [--note <入库路径>] [--by NAME] [--json]",
     "args": ["event_id", "--note", "--by"], "writes": True,
     "examples": ["newspipe queue ack ev_20261003T180000_ab12 --note "
                  "wiki/ai/xxx.md --by hermes --json"]},
    {"name": "run", "summary": "跑一轮（槽位 / 轮询 / 单源 / 补加工）",
     "usage": "newspipe run --slot am|noon|pm | --poll | --source NAME | --enrich-only "
              "[--dry] [--json]",
     "args": ["--slot", "--poll", "--source", "--enrich-only", "--dry", "--force", "--date"],
     "writes": True, "examples": ["newspipe run --slot am --dry --json",
                                  "newspipe run --poll --json"]},
    {"name": "probe-channel", "summary": "直连通道自检（真发一张卡，输出不含任何密钥）",
     "usage": "newspipe probe-channel [--chat oc_xxx] [--json]", "args": ["--chat"],
     "writes": True, "examples": ["newspipe probe-channel --json"]},
    {"name": "probe-model", "summary": "模型解析探针（绝不打印密钥）",
     "usage": "newspipe probe-model [--capability summarize] [--json]",
     "args": ["--capability"], "writes": False, "examples": ["newspipe probe-model --json"]},
    {"name": "migrate", "summary": "把 v1 状态迁到 v2 布局（幂等）",
     "usage": "newspipe migrate [--json]", "args": [], "writes": True,
     "examples": ["newspipe migrate --json"]},
    {"name": "serve", "summary": "常驻服务：自带调度 + 入站（独立运行形态）",
     "usage": "newspipe serve [--once] [--dry]", "args": ["--once", "--dry"], "writes": True,
     "examples": ["newspipe serve --once --dry"]},
]

SUBCOMMANDS = ("api", "doctor", "status", "list-sources", "source", "hooks", "events", "queue",
               "run", "serve", "migrate", "probe-channel", "probe-model", "card",
               "card-preview", "enrich-only")


# --------------------------------------------------------------------- 工具
def _emit(text: str) -> None:
    """失败/需人知的信息走这里（no_agent 任务据此投递）。"""
    print(text)


def _now():
    from datetime import datetime

    return datetime.now()


def _load(news_dir: Path | None = None) -> tuple[config.Config, state.Store]:
    news_dir = Path(news_dir or config.default_news_dir())
    return config.load(news_dir), state.Store(news_dir)


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


def _status_rows(store: state.Store, cfg: config.Config) -> tuple[list[dict], dict, dict | None]:
    rows = pipeline.stats(store, cfg)
    usage = store.usage(state.today())
    svc = service.describe_service(cfg) if _service_is_custom(cfg) else None
    return rows, usage, svc


# ------------------------------------------------------- 旧 flag 路径（别名）
def cmd_status(store: state.Store, cfg: config.Config, *, json_out: bool) -> int:
    rows, usage, svc = _status_rows(store, cfg)
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
    """补加工：把当天批次里还没加工的条目补上（回执保证已加工的不重算）。"""
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
                card = render.render(batch, cfg.hooks)
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
        chan = backends.make_channel("feishu_direct", service_cfg=cfg.service.feishu,
                                     news_dir=news_dir)
        probe = chan.probe(chat)
    except (ConfigError, DeliveryError) as exc:
        _emit(f"⚠️ 直连通道自检失败：{type(exc).__name__}: {exc}")
        return 1
    if json_out:
        print(json.dumps(probe, ensure_ascii=False, indent=2))
        return 0
    creds = probe.get("creds") or {}
    print(f"通道 {probe['channel']} 自检（目标 {chat}）")
    print(f"  凭据：app_id={creds.get('app_id') or '-'} "
          f"app_secret={creds.get('app_secret') or '-'} 来源={creds.get('source') or '-'}")
    for step in probe.get("steps") or []:
        mark = "✅" if step.get("ok") else "❌"
        detail = step.get("result") or step.get("error") or ""
        print(f"  {mark} {step['step']:<20} {step.get('ms', 0):>5}ms  {detail}")
    if probe.get("message_id"):
        print(f"  卡片已发出：card_id={probe.get('card_id')} message_id={probe['message_id']}")
    return 0


# ------------------------------------------------------- 子命令（结果信封）
def _sub_status(args: argparse.Namespace, news_dir: Path) -> result.Result:
    cfg, store = _load(news_dir)
    rows, usage, svc = _status_rows(store, cfg)
    data: dict[str, Any] = {"sources": rows, "llm_usage": usage,
                            "events": events.stats(news_dir, days=args.days)}
    if svc is not None:
        data["service"] = svc
    if not args.json:
        cmd_status(store, cfg, json_out=False)
        stat = data["events"]
        print(f"\n事件（近 {args.days} 天）：{stat['total']} 条"
              f"，未消费 {stat['unconsumed']}，待入库 {stat['queue']}")
    return result.ok("status", data, printed=not args.json)


def _sub_doctor(args: argparse.Namespace, news_dir: Path) -> result.Result:
    """自检。硬问题 → ok=false；软问题 → ok=true + warnings（都要人看见）。"""
    warnings: list[str] = []
    problems: list[dict[str, Any]] = []
    data: dict[str, Any] = {"news_dir": str(news_dir), "contract_version": CONTRACT_VERSION}

    try:
        cfg, store = _load(news_dir)
    except ConfigError as exc:
        return result.fail("doctor", "E_CONFIG", str(exc),
                           hint="修好 info/news/ 下的 YAML；`newspipe status --json` 也会报同样的错",
                           details={"news_dir": str(news_dir)})

    data["sources"] = {"total": len(cfg.sources), "enabled": len(cfg.enabled_sources()),
                       "disabled": [s.name for s in cfg.sources.values() if not s.enabled]}
    data["slots"] = cfg.slots
    data["channel"] = cfg.service.channel
    data["inbound"] = cfg.service.inbound_mode

    # hooks：被跳过的声明必须报出来（否则"按钮没出现"没人知道为什么）
    data["hooks"] = {"count": len(cfg.hooks.hooks),
                     "actions": cfg.hooks.actions(),
                     "problems": cfg.hooks.problems}
    if cfg.hooks.problems:
        warnings.extend(f"hooks.yaml 跳过：{p}" for p in cfg.hooks.problems)

    # 元素预算：加了 hook 之后还剩多少余量（列表页 hook 每行都吃预算）
    budget = render.projected_elements(config.CARD_MAX_ITEMS, cfg.hooks)
    data["card_budget"] = budget
    if budget["headroom"] < 0:
        problems.append({"code": "E_VALIDATION",
                         "message": f"列表页超元素上限：{budget['elements']} > {budget['limit']}"
                                    f"（{config.CARD_MAX_ITEMS} 条 + 当前 hooks）",
                         "hint": "把列表页 hook 改成 scope: detail，或调小 max_items"})

    # 凭据：只报可用性，绝不打印值
    if cfg.service.channel == "feishu_direct":
        from newspipe import credentials
        try:
            creds = credentials.resolve_feishu(cfg.service.feishu, news_dir=news_dir)
            data["creds"] = creds.describe()
        except ConfigError as exc:
            problems.append({"code": "E_CONFIG", "message": str(exc),
                             "hint": "设 NEWSPIPE_FEISHU_APP_ID / _APP_SECRET，"
                                     "或写进 service.yaml，或用 --probe-channel 复现"})

    stat = events.stats(news_dir, days=30)
    data["events"] = stat
    if stat["unconsumed"]:
        warnings.append(f"有 {stat['unconsumed']} 条事件未被消费"
                        f"（`newspipe events list --unconsumed --json`）")
    if stat["queue"]:
        warnings.append(f"待入库队列有 {stat['queue']} 条"
                        f"（`newspipe queue list --json`）")

    data["writable"] = _writable(news_dir)
    if not data["writable"]:
        problems.append({"code": "E_RUNTIME", "message": f"{news_dir} 不可写",
                         "hint": "检查权限；状态与事件都要写进这里"})

    if problems:
        first = problems[0]
        return result.fail("doctor", first["code"], first["message"],
                           hint=first.get("hint", ""), details={"problems": problems},
                           warnings=warnings,
                           next=["修完后重跑：newspipe doctor --json"])
    return result.ok("doctor", data, warnings=warnings,
                     next=["newspipe status --json", "newspipe events list --unconsumed --json"])


def _writable(path: Path) -> bool:
    import os

    return os.access(Path(path), os.W_OK)


def _sub_api_describe(args: argparse.Namespace, news_dir: Path) -> result.Result:
    return result.ok("api describe", {
        "contract_version": CONTRACT_VERSION,
        "exit_codes": {"0": "成功", "1": "运行时故障", "2": "用法或配置错",
                       "3": "网络或投递错", "4": "校验失败（写入被拒）"},
        "result_shape": {"ok": "bool", "command": "str", "contract_version": "int",
                         "changed": "bool（这次调用到底改没改东西）", "data": "object|null",
                         "error": "null|{code,message,hint,details}", "warnings": "[str]",
                         "next": "[str]（建议的下一步命令）"},
        "error_codes": sorted(result.ERROR_EXIT),
        "legacy_flags": "旧 flag（--slot/--alert/--card/--status/…）仍可用，行为不变；"
                        "成功路径 stdout 为空（cron wrapper 依赖这一点）",
        "commands": CONTRACT,
    })


def _sub_source(args: argparse.Namespace, news_dir: Path) -> result.Result:
    cfg, _store = _load(news_dir)
    command = f"source.{args.action}"
    if args.action == "list":
        rows = config.summarize(cfg)
        return result.ok(command, {"sources": rows,
                                   "hash": edit.file_hash(news_dir / "sources.yaml")})
    if args.action == "show":
        row = next((r for r in config.summarize(cfg) if r["source"] == args.name), None)
        if row is None:
            return result.fail(command, "E_NOT_FOUND", f"没有信源 {args.name!r}",
                               hint="newspipe source list --json")
        return result.ok(command, {"source": row,
                                   "hash": edit.file_hash(news_dir / "sources.yaml")})
    if args.action in ("set",):
        try:
            body = edit.parse_json_body(args.from_json or "")
        except (ValueError, json.JSONDecodeError) as exc:
            return result.fail(command, "E_USAGE", f"--from-json 解析失败：{exc}",
                               hint="传 JSON 文本，或 `-` 从 stdin 读")
        if not isinstance(body, dict):
            return result.fail(command, "E_USAGE", "--from-json 必须是 JSON 对象")
        out = edit.set_source(news_dir, args.name, body, base_hash=args.base_hash,
                              dry_run=args.dry_run)
    elif args.action in ("enable", "disable"):
        out = edit.set_source_enabled(news_dir, args.name, args.action == "enable",
                                      base_hash=args.base_hash, dry_run=args.dry_run)
    elif args.action == "remove":
        out = edit.remove_source(news_dir, args.name, base_hash=args.base_hash,
                                 dry_run=args.dry_run)
    else:
        return result.fail(command, "E_USAGE", f"未知动作 {args.action!r}")
    return _write_result(command, out, news_dir, next_ok=["newspipe doctor --json",
                                                          "newspipe source list --json"])


def _sub_hooks(args: argparse.Namespace, news_dir: Path) -> result.Result:
    cfg, _store = _load(news_dir)
    command = f"hooks.{args.action}"
    if args.action == "list":
        return result.ok(command, {
            "hooks": [h.describe() for h in cfg.hooks.hooks],
            "problems": cfg.hooks.problems,
            "hash": edit.file_hash(news_dir / "hooks.yaml"),
        }, warnings=[f"被跳过：{p}" for p in cfg.hooks.problems])
    if args.action == "add":
        try:
            hook = edit.parse_json_body(args.from_json or "")
        except (ValueError, json.JSONDecodeError) as exc:
            return result.fail(command, "E_USAGE", f"--from-json 解析失败：{exc}")
        if not isinstance(hook, dict):
            return result.fail(command, "E_USAGE", "--from-json 必须是 JSON 对象")
        out = edit.set_hook(news_dir, hook, base_hash=args.base_hash, dry_run=args.dry_run)
    elif args.action == "remove":
        out = edit.remove_hook(news_dir, args.id, base_hash=args.base_hash,
                               dry_run=args.dry_run)
    else:
        return result.fail(command, "E_USAGE", f"未知动作 {args.action!r}")
    return _write_result(command, out, news_dir, next_ok=["newspipe hooks list --json",
                                                          "newspipe doctor --json"])


def _write_result(command: str, out: dict[str, Any], news_dir: Path,
                  *, next_ok: list[str]) -> result.Result:
    """把 `edit.*` 的结构化返回包成 Result（成功/失败都明确）。"""
    if out.get("ok"):
        warnings = [w for w in [out.get("warning")] if w]
        return result.ok(command, out, changed=bool(out.get("changed")),
                         warnings=warnings, next=next_ok)
    return result.fail(command, str(out.get("code") or "E_RUNTIME"),
                       str(out.get("message") or "写入失败"),
                       hint=str(out.get("hint") or ""), details=out)


def _sub_events(args: argparse.Namespace, news_dir: Path) -> result.Result:
    command = f"events.{args.action}"
    if args.action == "list":
        items = events.list_events(news_dir, days=args.days,
                                   types=args.type or None,
                                   unconsumed=args.unconsumed, limit=args.limit)
        return result.ok(command, {"count": len(items), "events": items,
                                   "unconsumed_only": bool(args.unconsumed)})
    if args.action == "ack":
        if not args.ids:
            return result.fail(command, "E_USAGE", "需要至少一个事件 id",
                               hint="newspipe events list --unconsumed --json 拿到 id")
        out = events.ack(news_dir, args.ids, by=args.by, note=args.note or "")
        nexts = ["newspipe events list --unconsumed --json"]
        if out["unknown"]:
            return result.fail(command, "E_NOT_FOUND",
                               f"{len(out['unknown'])} 个事件 id 不存在",
                               hint="id 可能来自已被 prune 的旧日期文件；用 events list 核对",
                               details=out, next=nexts)
        return result.ok(command, out, changed=bool(out["acked"]), next=nexts)
    if args.action == "prune":
        out = events.prune(news_dir, keep_days=args.keep_days)
        return result.ok(command, out, changed=bool(out["removed"]))
    return result.fail(command, "E_USAGE", f"未知动作 {args.action!r}")


def _sub_queue(args: argparse.Namespace, news_dir: Path) -> result.Result:
    command = f"queue.{args.action}"
    if args.action == "list":
        items = events.queue(news_dir, days=args.days)
        return result.ok(command, {"count": len(items), "items": items},
                         next=["处理完入库后：newspipe queue ack <event_id> --note <路径> --json"]
                         if items else [])
    if args.action == "ack":
        if not args.event_id:
            return result.fail(command, "E_USAGE", "需要事件 id")
        out = events.ack(news_dir, [args.event_id], by=args.by, note=args.note or "")
        if out["unknown"]:
            return result.fail(command, "E_NOT_FOUND", f"事件 id 不存在：{args.event_id}",
                               hint="newspipe queue list --json 拿正确的 id", details=out)
        return result.ok(command, out, changed=bool(out["acked"]),
                         next=["newspipe queue list --json"])
    return result.fail(command, "E_USAGE", f"未知动作 {args.action!r}")


def _sub_run(args: argparse.Namespace, news_dir: Path) -> result.Result:
    cfg, store = _load(news_dir)
    runs: list[pipeline.SourceRun] = []
    if args.enrich_only:
        code = cmd_enrich_only(store, cfg, date=args.date or state.today(), dry=args.dry,
                               verbose=args.verbose, json_out=args.json,
                               retry_degraded=args.retry_degraded)
        return result.ok("run.enrich-only", {"exit_code": code})
    if args.source:
        runs = pipeline.run_source(cfg, store, args.source, slot=args.slot, dry=args.dry,
                                   force=True, now=_now())
    elif args.slot:
        runs = pipeline.run_slot(cfg, store, args.slot, dry=args.dry, now=_now())
    elif args.poll:
        runs = pipeline.run_poll(cfg, store, dry=args.dry, force=args.force, now=_now())
    else:
        return result.fail("run", "E_USAGE", "需要 --slot / --poll / --source / --enrich-only")
    failed = _failed(runs)
    data = {"runs": [r.as_dict() for r in runs], "failed": [r.source for r in failed]}
    if not args.json:
        _print(args.verbose or True, False, runs)
    warnings = [f"{r.source} {r.status}：{r.note}" for r in failed]
    if failed:
        return result.ok("run", data, changed=bool(runs), warnings=warnings,
                         printed=not args.json,
                         next=["newspipe status --json", "newspipe events list --json"])
    return result.ok("run", data, changed=bool(runs), printed=not args.json,
                     next=["newspipe status --json"])


def _sub_serve(args: argparse.Namespace, news_dir: Path) -> result.Result:
    cfg, store = _load(news_dir)
    code = service.run_forever(cfg, store, news_dir=news_dir, dry=args.dry, once=args.once,
                               with_inbound=not args.no_inbound)
    return result.ok("serve", {"exit_code": code, "once": bool(args.once), "dry": bool(args.dry)})


def _sub_probe_channel(args: argparse.Namespace, news_dir: Path) -> result.Result:
    cfg, _store = _load(news_dir)
    chat = args.chat or cfg.chat
    from newspipe import backends

    try:
        chan = backends.make_channel("feishu_direct", service_cfg=cfg.service.feishu,
                                     news_dir=news_dir)
        probe = chan.probe(chat)
    except (ConfigError, DeliveryError) as exc:
        return result.fail("probe-channel", "E_DELIVERY", f"{type(exc).__name__}: {exc}",
                           hint="检查凭据（NEWSPIPE_FEISHU_APP_ID/_APP_SECRET 或钥匙串）与群 id")
    if not args.json:
        cmd_probe_channel(cfg, chat=chat, json_out=False)
    return result.ok("probe-channel", probe, changed=bool(probe.get("message_id")),
                     printed=not args.json)


def _sub_probe_model(args: argparse.Namespace, news_dir: Path) -> result.Result:
    cfg, _store = _load(news_dir)
    return result.ok("probe-model", llm.probe(args.capability, cfg.models))


def _sub_migrate(args: argparse.Namespace, news_dir: Path) -> result.Result:
    _cfg, store = _load(news_dir)
    out = store.migrate_legacy()
    return result.ok("migrate", out, changed=bool(out))


def _sub_card(args: argparse.Namespace, news_dir: Path) -> result.Result:
    """插件薄壳走的入口（旧 flag `--card` 的等价子命令）。"""
    from newspipe import interaction

    cfg, _store = _load(news_dir)
    message = interaction.handle_json(args.payload, news_dir=news_dir, hook_set=cfg.hooks)
    return result.ok("card", {"message": message}, changed=bool(message))


def _sub_card_preview(args: argparse.Namespace, news_dir: Path) -> result.Result:
    cfg, store = _load(news_dir)
    found = store.load_batch_by_rel(args.batch) or store.find_batch(args.batch)
    if found is None:
        return result.fail("card-preview", "E_NOT_FOUND", f"找不到批次：{args.batch}")
    _path, batch = found
    card = render.render(batch, cfg.hooks)
    return result.ok("card-preview", {"elements": render.count_elements(card),
                                      "card": card})


def _build_sub_parser() -> argparse.ArgumentParser:
    # `--json` / `--news-dir` 放在父解析器里并挂到每个子命令上 ⇒ `newspipe --json doctor`
    # 和 `newspipe doctor --json` 都能用（Agent 不必记住位置）。用 SUPPRESS 让子命令在
    # 未显式给出时不覆盖顶层值——argparse 的经典坑。
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="机器可读输出（结果信封）")
    common.add_argument("--news-dir", default=argparse.SUPPRESS,
                        help="覆盖数据目录（默认 NEWSPIPE_HOME/info/news）")

    ap = argparse.ArgumentParser(prog="newspipe", description="资讯管线（子命令风格）")
    ap.add_argument("--json", action="store_true", help="机器可读输出（结果信封）")
    ap.add_argument("--news-dir", help="覆盖数据目录（默认 NEWSPIPE_HOME/info/news）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name: str, **kw: Any) -> argparse.ArgumentParser:
        return sub.add_parser(name, parents=[common], **kw)

    api = add("api")
    api.add_argument("action", choices=["describe"])

    add("doctor")
    st = add("status")
    st.add_argument("--days", type=int, default=7, help="事件统计的天数窗口")
    add("list-sources")

    src = add("source")
    src.add_argument("action", choices=["list", "show", "set", "enable", "disable", "remove"])
    src.add_argument("name", nargs="?", help="信源名")
    src.add_argument("--from-json", help="该源的四轴配置（JSON 文本或 `-` 读 stdin）")
    src.add_argument("--dry-run", action="store_true", help="只校验与演练，不落盘")
    src.add_argument("--base-hash", help="乐观并发：与当前文件哈希不符则拒绝写入")

    hk = add("hooks")
    hk.add_argument("action", choices=["list", "add", "remove"])
    hk.add_argument("id", nargs="?", help="hook id（remove 用）")
    hk.add_argument("--from-json", help="hook 声明（JSON 文本或 `-`）")
    hk.add_argument("--dry-run", action="store_true")
    hk.add_argument("--base-hash")

    ev = add("events")
    ev.add_argument("action", choices=["list", "ack", "prune"])
    ev.add_argument("ids", nargs="*", help="ack 的事件 id")
    ev.add_argument("--type", action="append", help="只看某类事件（可重复）")
    ev.add_argument("--unconsumed", action="store_true", help="只看未消费的")
    ev.add_argument("--days", type=int, default=7)
    ev.add_argument("--limit", type=int)
    ev.add_argument("--by", default="hermes", help="消费方名字（写进 acks）")
    ev.add_argument("--note", help="备注（例如入库路径）")
    ev.add_argument("--keep-days", type=int, default=30, help="prune 保留天数")

    qu = add("queue")
    qu.add_argument("action", choices=["list", "ack"])
    qu.add_argument("event_id", nargs="?", help="ack 的事件 id")
    qu.add_argument("--days", type=int, default=30)
    qu.add_argument("--by", default="hermes")
    qu.add_argument("--note", help="入库路径等")

    rn = add("run")
    rn.add_argument("--slot", choices=["am", "noon", "pm"])
    rn.add_argument("--poll", action="store_true")
    rn.add_argument("--source")
    rn.add_argument("--enrich-only", action="store_true")
    rn.add_argument("--date")
    rn.add_argument("--retry-degraded", action="store_true")
    rn.add_argument("--dry", action="store_true")
    rn.add_argument("--force", action="store_true")
    rn.add_argument("--verbose", "-v", action="store_true")

    sv = add("serve")
    sv.add_argument("--once", action="store_true")
    sv.add_argument("--dry", action="store_true")
    sv.add_argument("--no-inbound", action="store_true", help="只调度，不起入站")

    pc = add("probe-channel")
    pc.add_argument("--chat")
    pm = add("probe-model")
    pm.add_argument("--capability", default="summarize")
    add("migrate")

    cd = add("card")
    cd.add_argument("payload", help="卡片回调 payload（JSON 文本）")
    cp = add("card-preview")
    cp.add_argument("batch")
    return ap


SUB_HANDLERS = {
    ("api", "describe"): _sub_api_describe,
    ("status", None): _sub_status,
    ("doctor", None): _sub_doctor,
    ("list-sources", None): lambda args, news_dir: result.ok(
        "list-sources", {"sources": config.summarize(_load(news_dir)[0])}),
    ("source", None): _sub_source,
    ("hooks", None): _sub_hooks,
    ("events", None): _sub_events,
    ("queue", None): _sub_queue,
    ("run", None): _sub_run,
    ("serve", None): _sub_serve,
    ("probe-channel", None): _sub_probe_channel,
    ("probe-model", None): _sub_probe_model,
    ("migrate", None): _sub_migrate,
    ("card", None): _sub_card,
    ("card-preview", None): _sub_card_preview,
}


def _main_sub(argv: list[str]) -> int:
    ap = _build_sub_parser()
    args = ap.parse_args(argv)
    news_dir = Path(args.news_dir).expanduser() if args.news_dir else config.default_news_dir()
    handler = SUB_HANDLERS.get((args.cmd, getattr(args, "action", None))) or SUB_HANDLERS.get(
        (args.cmd, None))
    if handler is None:
        return result.emit(result.fail(args.cmd, "E_USAGE", f"未知命令 {args.cmd!r}"),
                           json_out=args.json)
    command = args.cmd if not hasattr(args, "action") else f"{args.cmd}.{args.action}"
    return result.guard(command, lambda: handler(args, news_dir), json_out=args.json)


# --------------------------------------------------------------- 旧 flag 路径
def _build_legacy_parser() -> argparse.ArgumentParser:
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
    ap.add_argument("--news-dir", help="覆盖数据目录（默认 NEWSPIPE_HOME/info/news）")
    return ap


def _main_legacy(argv: list[str]) -> int:
    ap = _build_legacy_parser()
    args = ap.parse_args(argv)
    modes = [bool(args.alert), bool(args.source), args.enrich_only, args.status,
             args.list_sources, args.migrate, args.probe_model,
             bool(args.card_preview), bool(args.card), args.serve, args.probe_channel]
    if not any(modes) and not args.slot:
        ap.error("需要指定一个模式：--slot / --alert / --serve / --status / --list-sources / …")

    news_dir = Path(args.news_dir).expanduser() if args.news_dir else config.default_news_dir()
    try:
        cfg = config.load(news_dir)
    except ConfigError as exc:
        _emit(f"⚠️ 资讯管线配置错误：{exc}")
        return 2
    store = state.Store(news_dir)

    if args.status:
        return cmd_status(store, cfg, json_out=args.json)
    if args.list_sources:
        return cmd_list_sources(cfg, json_out=args.json)
    if args.probe_model:
        print(json.dumps(llm.probe(args.capability, cfg.models), ensure_ascii=False, indent=2))
        return 0
    if args.migrate:
        print(json.dumps(store.migrate_legacy(), ensure_ascii=False, indent=2))
        return 0
    if args.card_preview:
        found = store.load_batch_by_rel(args.card_preview) or store.find_batch(args.card_preview)
        if found is None:
            _emit(f"⚠️ 找不到批次：{args.card_preview}")
            return 2
        _path, batch = found
        print(render.card_json(render.render(batch, cfg.hooks)))
        return 0
    if args.card:
        # 插件薄壳走这里：成功路径 stdout 为空（不在聊天里插消息）
        from newspipe import interaction

        message = interaction.handle_json(args.card, hook_set=cfg.hooks)
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


#: **只在**旧 flag 风格里出现的开关。出现任意一个 ⇒ 一定是旧调用
#: （哪怕位置参数恰好叫 `status`：`--card-preview status` 的批次名可以是任何字符串）。
#: 注意 `--slot` / `--source` / `--once` / `--dry` / `--force` **不在这里**——子命令也有它们。
LEGACY_ONLY_FLAGS = frozenset((
    "--alert", "--status", "--list-sources", "--migrate", "--probe-model", "--card-preview",
    "--card", "--serve", "--probe-channel",
))

#: 两种风格都认的全局开关（可以出现在子命令之前）。
GLOBAL_FLAGS = frozenset(("--json", "--verbose", "-v", "--news-dir"))


def _subcommand_of(argv: list[str]) -> str | None:
    """找出子命令名；只有**确实**是子命令风格才返回它。

    规则：第一个非开关的位置参数必须是子命令名，且它前面只允许出现全局开关。
    这样 `--source status`（源名恰好叫 status）不会被误判成子命令，而
    `serve --once --dry`（子命令带自己的开关）能正确走到子解析器。
    """
    if any(tok in LEGACY_ONLY_FLAGS for tok in argv):
        return None
    skip = False
    for tok in argv:
        if skip:
            skip = False
            continue
        if tok == "--news-dir":
            skip = True
            continue
        if tok.startswith("-"):
            if tok.split("=", 1)[0] not in GLOBAL_FLAGS:
                return None                    # 见到非全局开关 ⇒ 旧风格调用
            continue
        return tok if tok in SUBCOMMANDS else None
    return None


def main(argv: list[str] | None = None) -> int:
    """子命令风格优先；否则走旧 flag 路径（4 个 cron wrapper 与插件靠它）。"""
    argv = list(sys.argv[1:] if argv is None else argv)
    if _subcommand_of(argv):
        return _main_sub(argv)
    return _main_legacy(argv)


if __name__ == "__main__":
    raise SystemExit(main())
