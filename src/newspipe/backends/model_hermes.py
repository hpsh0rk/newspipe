"""模型解析实现 —— 跟随 Hermes 主模型（原 `llm.py` 的解析逻辑，行为不变）。

这是**宿主耦合的那一半**：读 `$HERMES_HOME/config.yaml` 的 `model.*` / `providers` /
`model_aliases`，以及 `$HERMES_HOME/.env` 的密钥。解析顺序与 v2 完全一致：

  1. `capabilities.<cap>.model` 显式指定：`hermes` / 别名（tr/ds/dsv4/qw）/ `<provider>:<model>` / 裸模型名；
  2. `hermes` = 跟随 Hermes 当前主模型；
  3. 解析不出来 → 抛 `ConfigError`，由调用方降级（**绝不阻塞发卡**）。

密钥顺序：provider 的 `key_env`（读 `.env`）→ `model.api_key`。**env 优先**——2026-10-03 实测
`config.yaml` 的 `model.api_key` 已失效（401），而 `.env` 里的可用。密钥永不打印、永不落盘。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from newspipe.backends.model_ref import ModelRef
from newspipe.errors import ConfigError

_config_cache: dict[str, tuple[float, dict]] = {}


def hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))


def _cached_yaml(path: Path) -> dict:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    hit = _config_cache.get(str(path))
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    _config_cache[str(path)] = (mtime, data)
    return data


def read_env(path: Path | None = None) -> dict[str, str]:
    """读 `~/.hermes/.env` 的键值（只返回值，调用方绝不打印）。"""
    path = path or (hermes_home() / ".env")
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        m = re.match(r"^\s*([A-Za-z0-9_]+)\s*=\s*(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out


class HermesModelResolver:
    """满足 `ports.ModelResolver`：从 Hermes 配置解析模型与密钥。"""

    name = "hermes"

    def resolve(self, capability: str, models_cfg: dict, *,
                hermes_config_path: Path | None = None,
                env_path: Path | None = None) -> ModelRef:
        home = hermes_home()
        hermes_cfg = _cached_yaml(hermes_config_path or (home / "config.yaml"))
        env = read_env(env_path)
        h_model = hermes_cfg.get("model") or {}
        h_providers = hermes_cfg.get("providers") or {}
        h_aliases = hermes_cfg.get("model_aliases") or {}
        own_providers = models_cfg.get("providers") or {}

        spec = (models_cfg.get("capabilities") or {}).get(capability) or {}
        if isinstance(spec, str):
            spec = {"model": spec}
        name = str(spec.get("model") or models_cfg.get("default") or "hermes")

        provider = str(h_model.get("provider") or "")
        base_url = str(h_model.get("base_url") or "")
        model = str(h_model.get("default") or "")
        source = "hermes"

        if name and name != "hermes":
            source = f"capability:{capability}"
            if name in h_aliases:
                alias = h_aliases[name] or {}
                model = str(alias.get("model") or name)
                provider = str(alias.get("provider") or provider)
                base_url = str(alias.get("base_url") or base_url)
            elif ":" in name and name.split(":", 1)[0] in (own_providers | h_providers):
                provider, model = name.split(":", 1)
                base_url = str((own_providers.get(provider) or h_providers.get(provider) or {})
                               .get("base_url") or base_url)
            else:
                model = name
                default_provider = str(models_cfg.get("default_provider") or "")
                if default_provider:
                    provider = default_provider
                    base_url = str((own_providers.get(provider) or {}).get("base_url") or base_url)

        if not base_url or not model:
            raise ConfigError(
                f"模型解析失败：capability={capability} name={name!r} —— "
                f"检查 models.yaml 与 ~/.hermes/config.yaml 的 model.*")

        pcfg = own_providers.get(provider) or h_providers.get(provider) or {}
        key_env = pcfg.get("key_env")
        api_key = str(env.get(str(key_env)) or "") if key_env else ""
        if not api_key:
            api_key = str(h_model.get("api_key") or "")
        if not api_key:
            raise ConfigError(f"模型 {model}（provider={provider}）缺密钥："
                              f"在 ~/.hermes/.env 里设置 {key_env or '<provider>.key_env'}")
        return ModelRef(model=model, provider=provider, base_url=base_url.rstrip("/"),
                        api_key=api_key, source=source)
