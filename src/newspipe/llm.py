"""加工层 —— 模型客户端（模型解析 / 回执 / 预算熔断）。

**模型解析已抽到 `backends/`**（可替换的那一半）：
  - `backends.model_host`：跟随**宿主 Agent** 当前主模型（读宿主配置目录的 `model.*`，见 `hostenv`）；
  - `backends.model_openai`：纯显式配置，不读任何宿主文件。
本模块只负责**发请求**：HTTP 调用、回执复用、预算熔断——两种后端完全共用。
`resolve_model` 保留原签名并转发给选中的后端，调用方与测试无需改动。

解析顺序（见 `models.yaml` 注释）：
  1. `capabilities.<cap>.model` 显式指定：可以是 `host`、宿主 `model_aliases` 里的别名、
     `<provider>:<model>`，或裸模型名（用 default_provider）；
  2. `host` = 跟随宿主当前主模型；
  3. 解析不出来 → 调用方降级（不加工，用原文标题），**绝不阻塞发卡**。

回执（receipt）：键 = sha256(prompt_version + model + system + user)。同一 prompt 版本 + 同一输入
→ 直接复用已付费结果；prompt 内容改了（哈希变）才重算。进程重启、重试都不会重复花钱。
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from newspipe.backends import get_model_resolver
from newspipe.backends.model_ref import ModelRef
from newspipe.errors import ConfigError
from newspipe.state import TZ

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)
def resolve_model(capability: str, models_cfg: dict, *,
                  host_config_path: Path | None = None,
                  env_path: Path | None = None) -> ModelRef:
    """转发给选中的模型后端（`backends/`）；签名与原实现一致，调用方与测试无需改动。

    宿主专属参数（`host_config_path` / `env_path`）只有 host 后端会用到，
    显式配置后端用 `**kwargs` 吸收——两者都满足 `ports.ModelResolver`。
    """
    return get_model_resolver(models_cfg).resolve(
        capability, models_cfg, host_config_path=host_config_path, env_path=env_path)




@dataclass
class ChatResult:
    data: dict | None
    state: str  # ok | reused | degraded | failed
    note: str = ""
    accounting: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.data is not None


def parse_json_object(text: str) -> dict | None:
    """从模型输出里抠出一个 JSON 对象：先去代码围栏，再退化为大括号切片。"""
    if not text:
        return None
    cleaned = _FENCE.sub("", text).strip()
    try:
        data = json.loads(cleaned)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(cleaned[start:end + 1])
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _hourly_calls(usage: dict, now: datetime) -> int:
    recent = usage.get("recent") or []
    cutoff = now.timestamp() - 3600
    return sum(1 for ts in recent if isinstance(ts, (int, float)) and ts > cutoff)


def over_budget(budget: dict, usage: dict, now: datetime) -> str | None:
    """超预算返回原因字符串（调用方降级），未超返回 None。"""
    if int(usage.get("calls") or 0) >= int(budget.get("max_calls_per_day") or 0):
        return "daily_calls"
    if _hourly_calls(usage, now) >= int(budget.get("max_calls_per_hour") or 0):
        return "hourly_calls"
    return None


def _accounting():
    return {"attempts": 0, "estimated_tokens": 0, "measured_tokens": 0,
            "unknown_tokens": 0, "cache_hit": False}


def _legacy_tokens(budget, usage):
    return int(usage.get("calls", 0)) * (4 * int(budget.get("max_chars_per_call", 8000)) +
                                       int(budget.get("max_tokens_ceiling", 8192))) if "reserved_tokens" not in usage else 0


def _spend_blocked(budget, usage, stamp, reservation, scope):
    blocked = over_budget(budget, usage, stamp)
    if blocked:
        return blocked
    # Old call ledgers have no token meter. Keep a conservative envelope, not a free balance.
    reserved = usage.get("reserved_tokens", _legacy_tokens(budget, usage))
    total_limit = int(budget.get("max_tokens_per_day", 1000000))
    if reserved + reservation > total_limit:
        return "daily_tokens"
    if scope:
        own = usage.get("scopes", {}).get(scope["name"], {})
        if int(own.get("calls", 0)) >= scope["max_calls_per_day"]:
            return "comparison_daily_calls"
        if _hourly_calls(own, stamp) >= scope["max_calls_per_hour"]:
            return "comparison_hourly_calls"
        if int(own.get("reserved_tokens", 0)) + reservation > scope["max_tokens_per_day"]:
            return "comparison_daily_tokens"
        if (int(usage.get("calls", 0)) + 1 > int(budget.get("max_calls_per_day", 0)) - scope["reserve_calls_per_day"]
                or _hourly_calls(usage, stamp) + 1 > int(budget.get("max_calls_per_hour", 0)) - scope["reserve_calls_per_hour"]
                or reserved + reservation > total_limit - scope["reserve_tokens_per_day"]):
            return "comparison_summary_headroom"
    return None


def _measured_tokens(raw):
    usage = raw.get("usage") if isinstance(raw, dict) else None
    if not isinstance(usage, dict):
        return None
    total, prompt, completion = (usage.get(k) for k in ("total_tokens", "prompt_tokens", "completion_tokens"))
    if all(type(value) is int and value >= 0 for value in (prompt, completion)):
        # OpenAI completion_tokens already includes optional reasoning tokens.
        return max(prompt + completion, total if type(total) is int and total >= 0 else 0)
    return total if type(total) is int and total >= 0 else None


def chat_json(capability: str, *, system: str, user: str, prompt_version: str,
              store: Any, models_cfg: dict, budget: dict, date: str | None = None,
              now: datetime | None = None, max_tokens: int | None = None,
              timeout: float | None = None,
              now_fn: Callable[[], datetime] | None = None, spend_scope: dict | None = None,
              retry_empty: bool = True, response_limit: int | None = None) -> ChatResult:
    """一次模型调用（带回执与预算）。任何失败都返回结果对象，不抛异常。

    调用方（enrich）据此降级：`data is None` → 用原文标题继续发卡。

    `max_tokens` 缺省取 `budget.max_tokens`。**给推理模型留够思考预算**：思考型模型的
    reasoning 与答案共用同一个 completion 预算，给小了会出现 `finish_reason=length`、
    `content` 为空、reasoning 却写满的响应（实测 1024 全被思考吃掉 ⇒ 整批降级）。
    """
    clock = now_fn or ((lambda: now) if now is not None else (lambda: datetime.now(TZ)))
    stamp = clock()
    try:
        ref = resolve_model(capability, models_cfg)
    except ConfigError as exc:
        store.add_usage(date or stamp.date().isoformat(), degraded=1, reason="config", now=stamp)
        return ChatResult(None, "degraded", "模型配置不可用；运行 probe-model 检查配置", _accounting())

    payload = f"{system}\x00{user}"
    identity = json.dumps([ref.provider, ref.base_url, ref.model], separators=(",", ":"))
    key = store.receipt_key(prompt_version, identity, payload)
    with store.receipt_transaction(key):
        cached = store.receipt(key)
        if cached and isinstance(cached.get("data"), dict):
            return ChatResult(cached["data"], "reused", "", {**_accounting(), "cache_hit": True})
        return _chat_json_locked(capability, ref=ref, key=key, system=system, user=user,
                                 prompt_version=prompt_version, store=store, budget=budget,
                                 date=date, clock=clock, max_tokens=max_tokens, timeout=timeout,
                                 spend_scope=spend_scope, retry_empty=retry_empty, response_limit=response_limit)


def _chat_json_locked(capability: str, *, ref: ModelRef, key: str, system: str, user: str,
                      prompt_version: str, store: Any, budget: dict, date: str | None,
                      clock: Callable[[], datetime], max_tokens: int | None,
                      timeout: float | None, spend_scope: dict | None,
                      retry_empty: bool, response_limit: int | None) -> ChatResult:
    accounting = _accounting()
    chars = len(system) + len(user)
    input_limit = int(budget.get("max_chars_per_call", 8000))
    if chars > input_limit:
        stamp = clock()
        store.add_usage(date or stamp.date().isoformat(), degraded=1, reason="max_chars_per_call", now=stamp)
        return ChatResult(None, "degraded", f"输入超过 max_chars_per_call（{chars}/{input_limit}），保留原文", accounting)

    tmo = float(timeout or budget.get("timeout_seconds") or 90)
    limit = int(max_tokens or budget.get("max_tokens") or 1200)
    ceiling = int(budget.get("max_tokens_ceiling") or 8192)
    limit = min(limit, ceiling)
    note = ""
    content = ""
    finish = ""
    while True:
        body = json.dumps({
            "model": ref.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": 0,
            "max_tokens": limit,
        }).encode()
        req = urllib.request.Request(
            f"{ref.base_url}/chat/completions", data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {ref.api_key}"})
        stamp = clock()
        day = date or stamp.date().isoformat()
        reservation = len(system.encode("utf-8")) + len(user.encode("utf-8")) + limit + 256
        metrics = {"scope": spend_scope["name"] if spend_scope else "",
                   "reserved_tokens": reservation, "estimated_tokens": reservation, "unknown_tokens": reservation}
        # Reserve before transport; a timeout or interrupted process may still have spent money.
        with store.usage_transaction():
            usage = store.usage_for_budget(day, stamp)
            blocked = _spend_blocked(budget, usage, stamp, reservation, spend_scope)
            if blocked:
                store.add_usage(day, degraded=1, reason=blocked, now=stamp)
                return ChatResult(None, "degraded", f"预算熔断（{blocked}）", accounting)
            store.add_usage(day, calls=1, chars=chars, now=stamp,
                            model_metrics={**metrics, "legacy_tokens": _legacy_tokens(budget, usage)})
        accounting["attempts"] += 1
        accounting["estimated_tokens"] += reservation
        accounting["unknown_tokens"] += reservation
        try:
            with urllib.request.urlopen(req, timeout=tmo) as resp:
                payload = resp.read(response_limit + 1) if response_limit is not None else resp.read()
                if response_limit is not None and len(payload) > response_limit:
                    raise ValueError("response_limit")
                raw = json.loads(payload.decode("utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError("response_shape")
        except urllib.error.HTTPError as exc:
            store.add_usage(day, degraded=1, reason=f"http_{exc.code}", now=stamp)
            return ChatResult(None, "failed", f"HTTP {exc.code}", accounting)
        except Exception as exc:
            store.add_usage(day, degraded=1, reason=type(exc).__name__, now=stamp)
            return ChatResult(None, "failed", type(exc).__name__, accounting)

        measured = _measured_tokens(raw)
        if measured is not None:
            store.add_usage(day, now=stamp, model_metrics={"scope": metrics["scope"], "unknown_tokens": -reservation,
                            "measured_tokens": measured, "reserved_tokens": max(0, measured - reservation)})
            accounting["unknown_tokens"] -= reservation
            accounting["measured_tokens"] += measured

        choices = raw.get("choices") or []
        choice = choices[0] if choices else {}
        finish = str(choice.get("finish_reason") or "")
        content = str(((choice.get("message") or {}).get("content")) or "")
        if content.strip():
            break
        if retry_empty and finish == "length" and limit < ceiling:
            # 思考把预算吃光了：翻倍重试一次（思考 + 答案要一起装得下），仍不行就降级
            limit = min(limit * 2, ceiling)
            note = "length_empty"
            continue
        note = "length_empty" if finish == "length" else "empty_response"
        break

    data = parse_json_object(content)
    if data is None:
        store.add_usage(day, degraded=1, reason=note or "not_json", now=stamp)
        return ChatResult(None, "failed",
                          f"空响应（finish_reason={finish!r}，思考吃满 {limit} tokens）"
                          if not content.strip() else "响应不是 JSON；保留原文", accounting)
    store.save_receipt(key, {"prompt_version": prompt_version, "model": ref.model,
                             "capability": capability, "data": data,
                             "ts": clock().isoformat(timespec="seconds")})
    return ChatResult(data, "ok", "", accounting)


def probe(capability: str, models_cfg: dict) -> dict[str, str]:
    """只读探针：解析结果（不含密钥），供 --verbose / 测试使用。"""
    try:
        return resolve_model(capability, models_cfg).describe()
    except ConfigError as exc:
        return {"error": str(exc)[:200]}
