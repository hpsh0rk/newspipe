"""模型解析实现 —— **跟随宿主 Agent 的主模型**。

宿主耦合的这一半：读宿主配置目录（见 `newspipe.hostenv`）里的 `model.*` / `providers` /
`model_aliases`，密钥优先取 `.env`。这样不必为本项目再配一份 API key，宿主换模型时这里跟着换。

解析顺序：

  1. `capabilities.<cap>.model` 显式指定：`host`（跟随宿主）/ `<provider>:<model>` / 裸模型名
     / 宿主的 `model_aliases` 别名；
  2. `host` = 跟随宿主当前主模型；
  3. 解析不出来 → 抛 `ConfigError`，由调用方降级（**绝不阻塞发卡**）。

密钥顺序：provider 的 `key_env`（读宿主 `.env`）→ 宿主 `model.api_key`。**env 优先** —— 实测
配置里的 `model.api_key` 可能早已失效，而 `.env` 里的仍可用。密钥永不打印、永不落盘。
"""
from __future__ import annotations

from pathlib import Path

from newspipe import hostenv
from newspipe.backends.model_ref import ModelRef
from newspipe.errors import ConfigError

_config_cache: dict[str, tuple[float, dict]] = {}

# 跟随宿主的哨兵值。`hermes` 是历史写法，保留兼容。
HOST_SENTINELS = ("host", "hermes")


def _cached_yaml(path: Path | None) -> dict:
    if path is None:
        return {}
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


class HostModelResolver:
    """满足 `ports.ModelResolver`：从宿主配置解析模型与密钥。"""

    name = "host"

    def resolve(self, capability: str, models_cfg: dict, *,
                host_config_path: Path | None = None,
                env_path: Path | None = None) -> ModelRef:
        home = hostenv.host_home(required=True)
        host_cfg = _cached_yaml(host_config_path or (home / "config.yaml"))
        env = hostenv.read_env(env_path if env_path is not None else (home / ".env"))
        h_model = host_cfg.get("model") or {}
        h_providers = host_cfg.get("providers") or {}
        h_aliases = host_cfg.get("model_aliases") or {}
        own_providers = models_cfg.get("providers") or {}

        spec = (models_cfg.get("capabilities") or {}).get(capability) or {}
        if isinstance(spec, str):
            spec = {"model": spec}
        name = str(spec.get("model") or models_cfg.get("default") or "host")

        provider = str(h_model.get("provider") or "")
        base_url = str(h_model.get("base_url") or "")
        model = str(h_model.get("default") or "")
        source = "host"

        if name and name not in HOST_SENTINELS:
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

        host_cfg_hint = hostenv.host_config_path()
        if not base_url or not model:
            raise ConfigError(
                f"模型解析失败：capability={capability} name={name!r} —— "
                f"检查 models.yaml 与宿主配置 {host_cfg_hint} 的 model.*")

        pcfg = own_providers.get(provider) or h_providers.get(provider) or {}
        key_env = pcfg.get("key_env")
        api_key = str(env.get(str(key_env)) or "") if key_env else ""
        if not api_key:
            api_key = str(h_model.get("api_key") or "")
        if not api_key:
            raise ConfigError(f"模型 {model}（provider={provider}）缺密钥："
                              f"在宿主 .env（{hostenv.host_env_path()}）里设置 "
                              f"{key_env or '<provider>.key_env'}")
        return ModelRef(model=model, provider=provider, base_url=base_url.rstrip("/"),
                        api_key=api_key, source=source)
