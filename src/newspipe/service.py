"""常驻服务 —— 自带调度 + 入站，独立运行的最终形态。

抽离前，三个槽位由宿主 cron 按点拉起、轮询由 `*/5` 的 job 拉起、卡片回调由宿主网关转进来。
独立运行后这三件事都在**一个进程**里：

```
newspipe --serve
  ├─ 调度线程（本模块）：到点跑 run_slot(am/noon/pm)；每 tick 跑一次 run_poll（节流在 state 里）
  ├─ 入站线程（inbound.py）：长连接或 HTTP，接卡片回调 → 持久化 inbox
  └─ 消费线程（callback_jobs.py）：读 inbox → interaction.handle（使用服务当前有效配置）
```

为什么不是一个 cron 表：槽位时刻写在 `sources.yaml: slots`（权威），进程自己按它算下一次触发，
所以「改配置即改调度」，不需要同步第二份 cron 表达式——这正是抽离前最容易出错的地方
（改 sources.yaml 忘了改 schedules.md）。

`--once` 只跑一轮到期的活就退出，给「不想常驻、仍想用系统 cron 拉起」的人留后路。
"""

from __future__ import annotations

import json
import os
import threading
import hashlib
from contextlib import nullcontext
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from newspipe import (backends, callback_jobs, clock, config, credentials, hostenv, inbound as inbound_mod, pipeline, policy, runtime,
                      state)
from newspipe._atomic import file_lock, write_json_atomic
from newspipe.errors import ConfigError, DeliveryError, InboundError, NewsError

# 槽位到点后多久内仍算「该跑」：进程在 08:15 没起、08:20 起了 → 补跑；
# 09:30 才起 → 不补（补发一份早间简报比不发更烦人，只在日志里说明错过）。
SLOT_TOLERANCE = timedelta(minutes=15)
SLOT_MAX_ATTEMPTS = 3
SLOT_RETRY_SECONDS = 60
SLOT_FAILURES = {"error", "partial_failure", "config_missing", "send_failed", "overflow", "retry_exhausted", "send_result_unknown", "retry_wait"}


def parse_slot_time(text: str) -> tuple[int, int]:
    """`"08:15"` → `(8, 15)`。配置错误要吵，不静默取默认。"""
    raw = str(text or "").strip()
    parts = raw.split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise ConfigError(f"槽位时刻格式应为 HH:MM，得到 {text!r}")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ConfigError(f"槽位时刻越界：{text!r}")
    return hour, minute


def slot_datetime(cfg: config.Config, slot: str, now: datetime) -> datetime:
    hour, minute = parse_slot_time(cfg.slots[slot])
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0)


# ------------------------------------------------------------------ 到期判定
def due_slots(cfg: config.Config, now: datetime, last_runs: dict[str, Any]) -> list[str]:
    """已到点、在容差内、且今天还没跑过的槽位。"""
    done = set((last_runs.get("slots") or {}).get(now.date().isoformat(), []))
    out: list[str] = []
    for slot in cfg.slots:
        if slot in done:
            continue
        when = slot_datetime(cfg, slot, now)
        if when <= now <= when + SLOT_TOLERANCE:
            out.append(slot)
    return sorted(out, key=lambda s: cfg.slots[s])


def missed_slots(cfg: config.Config, now: datetime, last_runs: dict[str, Any]) -> list[str]:
    """今天已过容差、且没跑过的槽位（只用于日志/告警，不补跑）。"""
    done = set((last_runs.get("slots") or {}).get(now.date().isoformat(), []))
    out = []
    for slot in cfg.slots:
        if slot in done:
            continue
        when = slot_datetime(cfg, slot, now)
        if now > when + SLOT_TOLERANCE:
            out.append(slot)
    return sorted(out, key=lambda s: cfg.slots[s])


def compute_wait(cfg: config.Config, now: datetime, tick_seconds: float) -> float:
    """下一次该醒来的秒数 = min(下一个未来槽位, tick)。"""
    waits = [float(tick_seconds)]
    for slot in cfg.slots:
        when = slot_datetime(cfg, slot, now)
        if when <= now:
            when = when + timedelta(days=1)
        waits.append((when - now).total_seconds())
    return max(1.0, min(waits))


# ------------------------------------------------------------------ 运行记录
def runs_path(news_dir: Path) -> Path:
    return Path(news_dir) / "state" / "service.json"


def load_runs(news_dir: Path) -> dict[str, Any]:
    path = runs_path(news_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def mark_slot(news_dir: Path, slot: str, now: datetime) -> dict[str, Any]:
    with file_lock(runs_path(news_dir).with_suffix(".lock")):
        return _mark_slot(news_dir, slot, now)


def _save_runs(news_dir: Path, data: dict[str, Any]) -> None:
    for field in ("slots", "slot_progress", "notification_slots"):
        days = data.get(field) or {}
        for key in sorted(days)[:-14]:
            days.pop(key, None)
    write_json_atomic(runs_path(news_dir), data)


def _mark_slot(news_dir: Path, slot: str, now: datetime) -> dict[str, Any]:
    data = load_runs(news_dir)
    slots = data.setdefault("slots", {})
    today = now.date().isoformat()
    entries = slots.setdefault(today, [])
    if slot not in entries:
        entries.append(slot)
    data["last_run_at"] = now.isoformat(timespec="seconds")
    _save_runs(news_dir, data)
    return data


# ------------------------------------------------------------------ 跑一轮
@clock.scoped
def run_once(cfg: config.Config, store: state.Store, *, now: datetime,
             news_dir: Path | None = None, dry: bool = False,
             logger: Callable[[str], None] = print) -> list[pipeline.SourceRun]:
    """跑一轮：所有到期的槽位 + 一次轮询检查。返回所有 SourceRun。"""
    news_dir = Path(news_dir or store.news_dir)
    # Serialize the scheduling decision as well as its per-source receipts.
    with nullcontext() if dry else file_lock(runs_path(news_dir).with_suffix(".lock")):
        return _run_once(cfg, store, now=now, news_dir=news_dir, dry=dry, logger=logger)


def _run_once(cfg: config.Config, store: state.Store, *, now: datetime,
              news_dir: Path, dry: bool, logger: Callable[[str], None]) -> list[pipeline.SourceRun]:
    now = clock.now()
    last_runs = load_runs(news_dir)
    runs: list[pipeline.SourceRun] = []

    for slot in due_slots(cfg, now, last_runs):
        logger(f"调度：槽位 {slot}（{cfg.slots[slot]}）到期，开始")
        progress = last_runs.setdefault("slot_progress", {}).setdefault(now.date().isoformat(), {}).setdefault(slot, {})
        sources = cfg.slot_sources(slot)
        # Empty slots still go through the pipeline's normal empty-run contract.
        targets = [(s.name, s) for s in sources] or [("__slot__", None)]
        for name, source in targets:
            signature = hashlib.sha256(json.dumps(
                {"source": asdict(source) if source is not None else None,
                 "chat": cfg.chat, "channel": _channel_sig(cfg)}, sort_keys=True).encode()).hexdigest()
            entry = progress.get(name) or {}
            if entry.get("complete"):
                continue
            if entry.get("signature") != signature:
                entry = progress[name] = {"attempts": 0, "signature": signature}
            if not dry and (entry.get("status") == "config_missing" or
                            int(entry.get("attempts") or 0) >= SLOT_MAX_ATTEMPTS or
                            float(entry.get("retry_after") or 0) > now.timestamp()):
                continue
            if not dry:
                entry.update(attempts=int(entry.get("attempts") or 0) + 1, status="running",
                             retry_after=now.timestamp() + SLOT_RETRY_SECONDS,
                             attempted_at=now.isoformat(timespec="seconds"))
                _save_runs(news_dir, last_runs)
            try:
                output = pipeline.run_slot(cfg, store, slot, only=source.name if source else None,
                                           dry=dry, now=now,
                                           **({"collect_only": True} if source and source.notify else {}))
            except NewsError as exc:
                logger(f"调度：槽位 {slot} 信源 {name} 失败（{type(exc).__name__}: {exc}）")
                output = [pipeline.SourceRun(source=name, status="error", note=str(exc)[:200])]
            runs.extend(output)
            if not dry:
                statuses = [run.status for run in output]
                statuses += ["partial_failure" for run in output
                             if run.collection and run.collection["status"] == "partial_failure"]
                complete = bool(output or source is None) and not any(s in SLOT_FAILURES for s in statuses)
                entry.update(complete=complete, status=next((s for s in statuses if s in SLOT_FAILURES),
                                                           "ok" if complete else "error"))
                last_runs["last_run_at"] = now.isoformat(timespec="seconds")
                _save_runs(news_dir, last_runs)
        if not dry and all(progress.get(name, {}).get("complete") for name, _ in targets):
            last_runs = mark_slot(news_dir, slot, now)

    for slot in missed_slots(cfg, now, last_runs):
        logger(f"调度：槽位 {slot}（{cfg.slots[slot]}）已错过容差窗口，跳过（不补发）")

    if cfg.poll_sources():
        try:
            runs.extend(pipeline.run_poll(cfg, store, dry=dry, now=now,
                         **({"collect_only": True} if any(s.notify for s in cfg.sources.values()) else {})))
        except NewsError as exc:
            logger(f"调度：轮询失败（{type(exc).__name__}: {exc}）")
    runs.extend(_run_notifications(cfg, store, last_runs, now=now, news_dir=news_dir, dry=dry))
    return runs


def _run_notifications(cfg: config.Config, store: state.Store, last_runs: dict, *, now: datetime,
                       news_dir: Path, dry: bool) -> list[pipeline.SourceRun]:
    """Notification progress is independent of acquisition slots and fetch throttles."""
    runs = []
    for source in cfg.sources.values():
        now = clock.now()
        day = now.date().isoformat()
        unfinished = store.unfinished_batch(source.name)
        if unfinished and unfinished.get("delivery_effect_done"):
            result = pipeline.notify_source(cfg, store, source.name, now=now, dry=dry)
            runs.append(result)
            if not dry and unfinished.get("notification_scheduled") and result.status == "ok":
                entry = (last_runs.setdefault("notification_slots", {})
                         .setdefault(unfinished["delivery_date"], {})
                         .setdefault(unfinished.get("notification_slot") or unfinished["slot"], {})
                         .setdefault(source.name, {}))
                entry.update(complete=True, status="ok")
                _save_runs(news_dir, last_runs)
            continue
        if not source.enabled or source.notify is None:
            continue
        rule = policy.resolve(cfg, source)
        if rule.values["mode"] != "digest":
            runs.append(pipeline.notify_source(cfg, store, source.name, now=now, dry=dry))
            continue
        slots = last_runs.setdefault("notification_slots", {}).setdefault(day, {})
        for slot, time in sorted(policy.sending_times(cfg, rule.values).items(), key=lambda item: item[1]):
            when = policy.notification_datetime(cfg, source, slot, now)
            if not when <= now <= when + SLOT_TOLERANCE:
                continue
            entry = slots.setdefault(slot, {}).setdefault(source.name, {})
            generation = int((unfinished or {}).get("notification_retry_generation") or 0)
            if generation > int(entry.get("retry_generation") or 0):
                entry.update(attempts=0, retry_after=0, retry_generation=generation)
            if entry.get("complete"):
                continue
            if not dry and (int(entry.get("attempts") or 0) >= SLOT_MAX_ATTEMPTS
                            or float(entry.get("retry_after") or 0) > now.timestamp()):
                continue
            if not dry:
                entry.update(attempts=int(entry.get("attempts") or 0) + 1,
                             retry_after=now.timestamp() + SLOT_RETRY_SECONDS, status="running")
                _save_runs(news_dir, last_runs)
            result = pipeline.notify_source(cfg, store, source.name, slot=slot, now=now, dry=dry)
            runs.append(result)
            if not dry:
                entry.update(status=result.status, complete=result.status not in SLOT_FAILURES)
                _save_runs(news_dir, last_runs)
            # At most one new notification operation per source per service tick.
            break
    return runs


def report(runs: list[pipeline.SourceRun], logger: Callable[[str], None]) -> None:
    for run in runs:
        if run.status in ("quiet", "throttled", "baseline", "nothing_new"):
            continue
        logger(f"  {run.source:<18} {run.status:<12} {run.note}")


# ------------------------------------------------------------------ 主循环
def prepare_channel(cfg: config.Config, *, news_dir: Path, creds=None) -> Any:
    """把 `service.yaml: channel` 装到后端注册表上（默认是独立直连）。"""
    channel = runtime.channel_for(cfg, news_dir, creds=creds)
    backends.set_default_channel_name(cfg.service.channel)
    backends.set_channel(channel)
    return channel


def make_logger(cfg: config.Config) -> Callable[[str], None]:
    path_text = str((cfg.service.log or {}).get("path") or "")
    path = Path(path_text).expanduser() if path_text else None
    lock = threading.Lock()

    def log(message: str) -> None:
        line = f"[{datetime.now().isoformat(timespec='seconds')}] {message}"
        with lock:
            print(line, flush=True)
            if path is not None:
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open("a", encoding="utf-8") as handle:
                        handle.write(line + "\n")
                except OSError as exc:
                    print(f"日志写入失败（{exc}）", flush=True)

    return log


def start_inbound_for(cfg: config.Config, *, news_dir: Path, hook_set: Any,
                      logger: Callable[[str], None], creds=None) -> inbound_mod.InboundHandle:
    """按 `service.yaml: inbound.mode` 起入站。缺凭据/缺依赖都抛错（不静默降级）。

    只读视图是**独立开关**（`view.enabled`）：`mode=none` 时也要能出视图，否则面板
    在「不收回调」的部署里就瞎了。`mode=none` 不需要凭据，所以别在这里解析它。
    """
    mode = cfg.service.inbound_mode
    view = {"enabled": cfg.service.view_enabled, "path": cfg.service.view_path,
            "actions": cfg.service.view_actions}
    if mode == "none" and not view["enabled"]:
        return inbound_mod.InboundHandle(mode="none")
    from newspipe import credentials

    creds = creds or (credentials.resolve_feishu(cfg.service.feishu, news_dir=news_dir)
             if mode != "none" else None)
    return inbound_mod.start_inbound(mode=mode, service_cfg=cfg.service.inbound, creds=creds,
                                     news_dir=news_dir, hook_set=hook_set, logger=logger,
                                     view=view)


def host_dotenv_path() -> Path:
    """宿主 `.env` 的位置（飞书应用归属的宿主侧真相）。没配宿主目录则返回空路径。"""
    return hostenv.host_env_path() or Path(os.devnull)


def shared_app_ws_conflict(cfg: config.Config, *, news_dir: Path | None = None,
                           host_env: Path | None = None,
                           dotenv_paths: list[Path] | None = None,
                           env: dict[str, str] | None = None,
                           creds: credentials.FeishuCreds | None = None) -> str | None:
    """`inbound.mode: ws` 且与宿主用**同一个飞书应用** ⇒ 硬冲突，必须报出来。

    飞书长连接是**集群模式、不支持广播**：同一应用部署多个 client 时，事件只会随机落到其中
    一个。后果不对称——资讯卡片回调落到哪边都能处理，但**普通聊天消息**只有宿主认识；落到
    本项目这一侧会被 `extract_action()` 静默丢弃，用户看到的是「机器人不回话」。

    所以「一个机器人」和「一条长连接」是同一件事：一个应用只能有一个 client 持有连接。
    `mode: http` 不占长连接，没有这个问题（由持有连接的一方转发过来）。

    返回 `None` = 没冲突或无法判定（无法判定时不抢话，交给 doctor 的凭据检查去报）。
    """
    if cfg.service.inbound_mode != "ws":
        return None
    try:
        creds = creds or credentials.resolve_feishu(cfg.service.feishu, news_dir=news_dir,
                                                    dotenv_paths=dotenv_paths, env=env)
    except ConfigError:
        return None
    host_app = credentials.read_dotenv(host_env or host_dotenv_path()).get("FEISHU_APP_ID", "")
    if not host_app or not creds.app_id or host_app != creds.app_id:
        return None
    return (f"入站用了长连接（inbound.mode=ws），且 app_id 与宿主网关相同（{creds.app_id}）。"
            "飞书长连接是集群模式：同一应用的多个 client 只会随机投递，"
            "普通聊天消息可能落到本项目并被静默丢弃。"
            "改成 inbound.mode=http（由持有连接的一方转发），或确保本机没有第二个 client。")


def run_forever(cfg: config.Config, store: state.Store, *, news_dir: Path | None = None,
                dry: bool = False, once: bool = False,
                with_inbound: bool = True, logger: Callable[[str], None] | None = None,
                now_fn: Callable[[], datetime] = datetime.now,
                wait_fn: Callable[[float], bool] | None = None,
                stop_event: threading.Event | None = None,
                reload_fn: Callable[[], config.Config] | None = None) -> int:
    """常驻主循环。`once=True` 跑一轮就返回（给系统 cron / 测试用）。

    每轮重载业务策略和 hooks；监听资源、权限与凭据变更明确标为需重启。
    改坏了也不会静默——重载失败时沿用上一份并吵一声。

    等待用 `stop_event.wait()`（可被信号打断），`wait_fn` 可注入以便测试不真等：
    返回 True 表示「要求停止」。
    """
    news_dir = Path(news_dir or store.news_dir)
    logger = logger or make_logger(cfg)
    stop_event = stop_event or threading.Event()
    waiter = wait_fn or stop_event.wait
    reloader = reload_fn or (lambda: config.load(news_dir))
    # 入站线程按需读 hooks（lambda 而不是快照）⇒ 热加载的 hooks 立刻对点击生效
    live: dict[str, tuple[config.Config, Any]] = {}
    started_cfg = cfg
    desired_cfg = cfg
    restart_required: list[str] = []
    resource_error = ""

    channel_sig = _channel_sig(cfg)
    handle = inbound_mod.InboundHandle(mode="none")
    worker = None
    startup_creds = None
    try:
        if with_inbound and not dry and cfg.service.inbound_mode != "none":
            try:
                startup_creds = credentials.resolve_feishu(cfg.service.feishu, news_dir=news_dir)
            except ConfigError as exc:
                logger(f"⚠️ 入站未启动：ConfigError: {exc}")
                resource_error = "ConfigError"
                return 2
        conflict = (shared_app_ws_conflict(cfg, news_dir=news_dir, creds=startup_creds)
                    if with_inbound and not dry else None)
        if conflict:
            logger(f"⚠️ 拒绝启动：{conflict}")
            resource_error = "inbound_conflict"
            return 2
        channel = prepare_channel(cfg, news_dir=news_dir, creds=startup_creds)
        live["runtime"] = (cfg, channel)
        if with_inbound and not dry:
            worker = callback_jobs.Worker(news_dir, runtime_provider=lambda: live["runtime"], logger=logger)
            worker.start()
            try:
                handle = start_inbound_for(cfg, news_dir=news_dir,
                                           hook_set=lambda: live["runtime"][0].hooks,
                                           logger=logger, creds=startup_creds)
            except (ConfigError, InboundError) as exc:
                logger(f"⚠️ 入站未启动：{type(exc).__name__}: {exc}")
                resource_error = type(exc).__name__
                return 2 if isinstance(exc, ConfigError) else 1
            if handle.error:
                resource_error = "inbound_unavailable"
        if handle.mode != "none":
            logger(f"入站已启动：{handle.describe()}")

        logger(f"服务启动：通道={cfg.service.channel} 入站={handle.mode} tick={cfg.service.tick_seconds:g}s "
               f"槽位={{{', '.join(f'{k}:{v}' for k, v in cfg.slots.items())}}}"
               f"{' [dry]' if dry else ''}{' [once]' if once else ''}")

        exit_code = 1 if resource_error else 0
        while not stop_event.is_set():
            try:
                fresh = reloader()
            except ConfigError as exc:
                logger(f"⚠️ 配置重载失败，沿用上一份：{exc}")
            else:
                if fresh is not None and fresh is not cfg:
                    desired_cfg = fresh
                    fields = ["log"]
                    if with_inbound and not dry:
                        fields += ["inbound", "view", "feishu"]
                    pending = [f"service.{field}" for field in fields
                               if getattr(fresh.service, field) != getattr(started_cfg.service, field)]
                    if startup_creds is not None:
                        try:
                            wanted_creds = credentials.resolve_feishu(fresh.service.feishu, news_dir=news_dir)
                            credential_change = _credential_sig(wanted_creds) != _credential_sig(startup_creds)
                        except ConfigError:
                            credential_change = True
                        if credential_change and "service.feishu" not in pending:
                            pending.append("service.feishu")
                    if pending != restart_required:
                        if pending:
                            logger(f"配置已保存但需重启服务：{', '.join(pending)}；当前资源保持不变")
                        elif restart_required:
                            logger("待重启配置已撤回；当前资源与配置一致")
                    restart_required = pending
                    fresh = replace(fresh, service=replace(fresh.service, **{
                        field: getattr(started_cfg.service, field) for field in fields}))
                    if _channel_sig(fresh) != channel_sig:
                        # Rebuild only after configuration changes, preserving token caches otherwise.
                        try:
                            channel = prepare_channel(fresh, news_dir=news_dir, creds=startup_creds)
                            channel_sig = _channel_sig(fresh)
                            logger(f"通道已按新配置重建：{fresh.service.channel}")
                        except (ConfigError, DeliveryError) as exc:
                            logger(f"⚠️ 新通道配置不可用，沿用上一份：{type(exc).__name__}: {exc}")
                            fresh = cfg
                    cfg = fresh
                    live["runtime"] = (cfg, channel)
            if not dry:
                if handle.error:
                    resource_error = handle.error
                    exit_code = 1
                _record_runtime(news_dir, cfg, desired_cfg, handle, restart_required,
                                running=True, error=resource_error)
            try:
                with runtime.bind_channel(news_dir, channel):
                    runs = run_once(cfg, store, now=now_fn(), news_dir=news_dir, dry=dry, logger=logger)
                report(runs, logger)
                if any(run.status in SLOT_FAILURES for run in runs):
                    exit_code = 1
            except NewsError as exc:
                exit_code = 1
                logger(f"⚠️ 本轮失败：{type(exc).__name__}: {exc}")
            if once:
                break
            wait = compute_wait(cfg, now_fn(), cfg.service.tick_seconds)
            logger(f"下一轮：{wait:.0f}s 后（{now_fn().isoformat(timespec='seconds')}）")
            if waiter(wait):
                break
    finally:
        if handle.error:
            resource_error = handle.error
            exit_code = 1
        try:
            handle.stop()
        finally:
            if handle.error:
                resource_error = handle.error
                exit_code = 1
            if worker is not None:
                worker.stop()
            if not dry:
                _record_runtime(news_dir, cfg, desired_cfg, handle, restart_required,
                                running=False, error=resource_error)
    logger("服务退出")
    return exit_code


def _channel_sig(cfg: config.Config) -> tuple[str, tuple[tuple[str, str], ...]]:
    """通道签名：通道名 + 凭据配置。变了才重建通道实例（保住 token 缓存）。"""
    return (cfg.service.channel,
            tuple(sorted((str(k), str(v)) for k, v in (cfg.service.feishu or {}).items())))


def _credential_sig(creds: credentials.FeishuCreds) -> tuple[str, ...]:
    return (creds.app_id, creds.app_secret, creds.domain, creds.verification_token, creds.encrypt_key)


def _resource_values(cfg: config.Config) -> dict[str, Any]:
    """Only explicitly public fields; never serialize the credential mapping."""
    http = cfg.service.inbound.get("http") or {}
    return {"channel": cfg.service.channel, "inbound_mode": cfg.service.inbound_mode,
            "http_host": http.get("host", "127.0.0.1"), "http_port": http.get("port", 8787),
            "http_path": http.get("path", "/feishu/events"),
            "view_enabled": cfg.service.view_enabled, "view_path": cfg.service.view_path,
            "view_actions": cfg.service.view_actions}


def _record_runtime(news_dir: Path, effective: config.Config, desired: config.Config,
                    handle: inbound_mod.InboundHandle, pending: list[str], *,
                    running: bool, error: str = "") -> None:
    with file_lock(runs_path(news_dir).with_suffix(".lock")):
        data = load_runs(news_dir)
        effective_values = _resource_values(effective)
        if handle.server is not None:
            effective_values["http_host"], effective_values["http_port"] = handle.server.server_address
        data["runtime"] = {"pid": os.getpid(), "running": running, "updated_at": state.now_iso(),
                           "effective": effective_values, "desired": _resource_values(desired),
                           "restart_required": list(pending), "inbound": handle.describe(), "error": error}
        _save_runs(news_dir, data)


def runtime_status(news_dir: Path) -> dict[str, Any]:
    """Last observed runtime, not an assertion based on desired disk configuration."""
    return load_runs(news_dir).get("runtime") or {}


def describe_service(cfg: config.Config, *, news_dir: Path | None = None) -> dict[str, Any]:
    """`--status` 里那段服务自述（只读，不起任何东西）。"""
    news_dir = Path(news_dir or config.default_news_dir())
    info: dict[str, Any] = {
        "channel": cfg.service.channel,
        "available_channels": backends.available_channels(),
        "inbound_mode": cfg.service.inbound_mode,
        "view_enabled": cfg.service.view_enabled,
        "view_path": cfg.service.view_path if cfg.service.view_enabled else None,
        "tick_seconds": cfg.service.tick_seconds,
        "slots": dict(cfg.slots),
        "last_runs": load_runs(news_dir),
        "runtime": runtime_status(news_dir),
    }
    if cfg.service.channel == "feishu_direct":
        try:
            channel = backends.make_channel("feishu_direct", service_cfg=cfg.service.feishu,
                                            news_dir=news_dir)
            info["channel_detail"] = channel.describe()
        except Exception as exc:
            info["channel_detail"] = {"error": f"{type(exc).__name__}: {exc}"}
    return info
