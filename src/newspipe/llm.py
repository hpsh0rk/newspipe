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
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from newspipe.backends import get_model_resolver
from newspipe.backends.model_ref import ModelRef
from newspipe.errors import ConfigError, ModelError
from newspipe.state import now_iso, today

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
    return sum(1 for ts in recent if isinstance(ts, (int, float)) and ts >= cutoff)


def over_budget(budget: dict, usage: dict, now: datetime) -> str | None:
    """超预算返回原因字符串（调用方降级），未超返回 None。"""
    if int(usage.get("calls") or 0) >= int(budget.get("max_calls_per_day") or 0):
        return "daily_calls"
    if _hourly_calls(usage, now) >= int(budget.get("max_calls_per_hour") or 0):
        return "hourly_calls"
    return None


def chat_json(capability: str, *, system: str, user: str, prompt_version: str,
              store: Any, models_cfg: dict, budget: dict, date: str | None = None,
              now: datetime | None = None, max_tokens: int | None = None,
              timeout: float | None = None) -> ChatResult:
    """一次模型调用（带回执与预算）。任何失败都返回结果对象，不抛异常。

    调用方（enrich）据此降级：`data is None` → 用原文标题继续发卡。

    `max_tokens` 缺省取 `budget.max_tokens`。**给推理模型留够思考预算**：思考型模型的
    reasoning 与答案共用同一个 completion 预算，给小了会出现 `finish_reason=length`、
    `content` 为空、reasoning 却写满的响应（实测 1024 全被思考吃掉 ⇒ 整批降级）。
    """
    date = date or today()
    now = now or datetime.now()
    usage = store.usage(date)
    blocked = over_budget(budget, usage, now)
    if blocked:
        store.add_usage(date, degraded=1)
        return ChatResult(None, "degraded", f"预算熔断（{blocked}）")

    try:
        ref = resolve_model(capability, models_cfg)
    except ConfigError as exc:
        store.add_usage(date, degraded=1)
        return ChatResult(None, "degraded", str(exc)[:200])

    payload = f"{system}\x00{user}"
    key = store.receipt_key(prompt_version, ref.model, payload)
    cached = store.receipt(key)
    if cached and isinstance(cached.get("data"), dict):
        return ChatResult(cached["data"], "reused", "")

    tmo = float(timeout or budget.get("timeout_seconds") or 90)
    limit = int(max_tokens or budget.get("max_tokens") or 1200)
    ceiling = int(budget.get("max_tokens_ceiling") or 8192)
    note = ""
    content = ""
    finish = ""
    attempts = 0
    while True:
        attempts += 1
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
        try:
            with urllib.request.urlopen(req, timeout=tmo) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:200]
            except Exception:
                pass
            store.add_usage(date, calls=attempts, chars=len(payload), degraded=1,
                            reason=f"http_{exc.code}")
            return ChatResult(None, "failed", f"HTTP {exc.code} {detail}")
        except Exception as exc:
            store.add_usage(date, calls=attempts, chars=len(payload), degraded=1,
                            reason=type(exc).__name__)
            return ChatResult(None, "failed", f"{type(exc).__name__}: {exc}"[:200])

        choices = raw.get("choices") or []
        choice = choices[0] if choices else {}
        finish = str(choice.get("finish_reason") or "")
        content = str(((choice.get("message") or {}).get("content")) or "")
        if content.strip():
            break
        if finish == "length" and limit < ceiling:
            # 思考把预算吃光了：翻倍重试一次（思考 + 答案要一起装得下），仍不行就降级
            limit = min(limit * 2, ceiling)
            note = "length_empty"
            continue
        note = "length_empty" if finish == "length" else "empty_response"
        break

    data = parse_json_object(content)
    store.add_usage(date, calls=attempts, chars=len(payload))
    if data is None:
        store.add_usage(date, degraded=1, reason=note or "not_json")
        return ChatResult(None, "failed",
                          f"空响应（finish_reason={finish!r}，思考吃满 {limit} tokens）"
                          if not content.strip() else f"响应不是 JSON：{content[:120]!r}")
    store.save_receipt(key, {"prompt_version": prompt_version, "model": ref.model,
                             "capability": capability, "data": data, "ts": now_iso()})
    return ChatResult(data, "ok", "")


def probe(capability: str, models_cfg: dict) -> dict[str, str]:
    """只读探针：解析结果（不含密钥），供 --verbose / 测试使用。"""
    try:
        return resolve_model(capability, models_cfg).describe()
    except ConfigError as exc:
        return {"error": str(exc)[:200]}
