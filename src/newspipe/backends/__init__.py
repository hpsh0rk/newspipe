"""后端注册与选择 —— 宿主耦合实现的唯一入口。

选择顺序（模型）：
  1. `models_cfg["backend"]`（写进 `models.yaml` 最直白）；
  2. 环境变量 `NEWSPIPE_MODEL_BACKEND`；
  3. 默认 `hermes`（跟随宿主主模型，与抽离前行为一致）。

选择顺序（通道）：
  1. `set_channel()` 注入的对象（嵌入式用法 / 测试）；
  2. 环境变量 `NEWSPIPE_CHANNEL`；
  3. 默认 `feishu_lark_cli`（与抽离前行为一致）。

**默认值刻意保持「和抽离前一样」**：包化这一步不改行为，只把可替换点显式化。
"""

from __future__ import annotations

import os
from typing import Any

from newspipe.backends.model_ref import ModelRef
from newspipe.backends.model_hermes import HermesModelResolver
from newspipe.backends.model_openai import ExplicitModelResolver
from newspipe.backends.feishu_lark_cli import LarkCliChannel

__all__ = ["ModelRef", "HermesModelResolver", "ExplicitModelResolver", "LarkCliChannel",
           "get_model_resolver", "get_channel", "set_channel"]

_MODEL_RESOLVERS = {"hermes": HermesModelResolver, "explicit": ExplicitModelResolver}
_CHANNELS = {"feishu_lark_cli": LarkCliChannel}

_channel_override: Any = None
_resolver_cache: dict[str, Any] = {}


def get_model_resolver(models_cfg: dict | None = None) -> Any:
    name = ((models_cfg or {}).get("backend")
            or os.environ.get("NEWSPIPE_MODEL_BACKEND") or "hermes")
    name = str(name)
    if name not in _MODEL_RESOLVERS:
        raise KeyError(f"未知的模型后端 {name!r}；可选：{sorted(_MODEL_RESOLVERS)}")
    if name not in _resolver_cache:
        _resolver_cache[name] = _MODEL_RESOLVERS[name]()
    return _resolver_cache[name]


def set_channel(channel: Any | None) -> None:
    """注入投递通道（None = 恢复按配置选择）。测试与嵌入式用法用。"""
    global _channel_override
    _channel_override = channel


def get_channel() -> Any:
    if _channel_override is not None:
        return _channel_override
    name = str(os.environ.get("NEWSPIPE_CHANNEL") or "feishu_lark_cli")
    if name not in _CHANNELS:
        raise KeyError(f"未知的通道后端 {name!r}；可选：{sorted(_CHANNELS)}")
    return _CHANNELS[name]()
