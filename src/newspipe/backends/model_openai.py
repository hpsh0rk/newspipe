"""模型解析实现 —— 纯显式配置，**不读任何宿主文件**。

这是抽离后的默认目标形态：`models.yaml` 自带 provider 的 `base_url` 与 `key_env`，
密钥从进程环境（或显式指定的 dotenv 文件）取。**不读任何宿主文件，完全可独立运行。**

解析顺序：
  1. `capabilities.<cap>.model`（或 `default`）：`<provider>:<model>` / 裸模型名 / `default_provider`；
  2. `providers.<name>` 提供 `base_url` 与 `key_env`；
  3. 密钥：`os.environ[key_env]` → 可选 dotenv 文件 → `providers.<name>.api_key`（不推荐，会落盘）。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from newspipe.backends.model_ref import ModelRef
from newspipe.errors import ConfigError

_ENV_LINE = re.compile(r"^\s*([A-Za-z0-9_]+)\s*=\s*(.*)$")


def _read_dotenv(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    out: dict[str, str] = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        m = _ENV_LINE.match(line)
        if m:
            out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out


class ExplicitModelResolver:
    """满足 `ports.ModelResolver`：只依赖 models_cfg（+ 环境变量/dotenv）。"""

    name = "explicit"

    def __init__(self, *, dotenv_path: Path | None = None) -> None:
        self.dotenv_path = dotenv_path

    def resolve(self, capability: str, models_cfg: dict, **_: object) -> ModelRef:
        providers = models_cfg.get("providers") or {}
        aliases = models_cfg.get("aliases") or {}
        spec = (models_cfg.get("capabilities") or {}).get(capability) or {}
        if isinstance(spec, str):
            spec = {"model": spec}

        raw = str(spec.get("model") or models_cfg.get("default") or "")
        if not raw:
            raise ConfigError(f"capability={capability} 未指定模型，且没有 default")

        provider = str(spec.get("provider") or models_cfg.get("default_provider") or "")
        model = raw
        if raw in aliases:
            alias = aliases[raw] or {}
            model = str(alias.get("model") or raw)
            provider = str(alias.get("provider") or provider)
        elif ":" in raw:
            head, tail = raw.split(":", 1)
            if head in providers:
                provider, model = head, tail

        pcfg = providers.get(provider) or {}
        base_url = str(spec.get("base_url") or pcfg.get("base_url") or "")
        if not base_url:
            raise ConfigError(f"capability={capability} 的 provider={provider!r} 缺 base_url")

        env = _read_dotenv(self.dotenv_path)
        key_env = pcfg.get("key_env")
        api_key = ""
        if key_env:
            api_key = os.environ.get(str(key_env)) or env.get(str(key_env)) or ""
        if not api_key:
            api_key = str(pcfg.get("api_key") or "")
        if not api_key:
            raise ConfigError(f"provider={provider!r} 缺密钥：设置环境变量 "
                              f"{key_env or '<provider>.key_env'}")

        return ModelRef(model=model, provider=provider, base_url=base_url.rstrip("/"),
                        api_key=api_key, source=f"capability:{capability}")
