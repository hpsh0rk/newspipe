"""宿主 Agent 集成 —— 读宿主既有配置/凭据的**唯一入口**（永不打印值）。

本项目可以完全独立运行（自带凭据、自带模型配置）。但有两处「复用宿主已有的东西」比重复
配置更省事：

1. **模型路由**：跟随宿主当前主模型，而不是再维护一份 API key；
2. **飞书凭据**：与宿主共用同一个应用 —— 同一份 app_secret、同一个 bot 身份、同一批群。

宿主配置目录由环境变量给出：

    NEWSPIPE_HOST_HOME     宿主配置目录（该目录下应有 `config.yaml` 与 `.env`）

**刻意不设默认值。** 猜一个目录会让服务读到别人的配置，而这类错配是静默的：历史上出现过
「宿主目录没设 → 外部 CLI 回落到另一个 bot 应用 → 发出去的消息来自一个没人认识的机器人」。
所以没设而某个后端又需要它时，直接报 `ConfigError` 并给出可执行的修法。

兼容：历史变量 `HERMES_HOME` 仍被接受（同义，见 `HOST_HOME_ENV_KEYS`）。
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal, overload

from newspipe.errors import ConfigError

# 宿主配置目录的环境变量名，按优先级排列。第二个是兼容别名。
HOST_HOME_ENV_KEYS: tuple[str, ...] = ("NEWSPIPE_HOST_HOME", "HERMES_HOME")


@overload
def host_home(*, required: Literal[True]) -> Path: ...


@overload
def host_home(*, required: bool = False) -> Path | None: ...


def host_home(*, required: bool = False) -> Path | None:
    """宿主配置目录；未配置时返回 `None`（`required=True` 则抛错）。"""
    for key in HOST_HOME_ENV_KEYS:
        raw = os.environ.get(key)
        if raw:
            return Path(raw).expanduser()
    if required:
        raise ConfigError(
            "这个后端需要宿主配置目录，但环境里既没有 NEWSPIPE_HOST_HOME 也没有 "
            f"{HOST_HOME_ENV_KEYS[1]}。两种修法："
            "① export NEWSPIPE_HOST_HOME=/path/to/host/home"
            "（该目录下应有 config.yaml 与 .env）；"
            "② 不用宿主 —— models.yaml 里写 backend: explicit 并自带 provider 配置",
        )
    return None


def host_config_path() -> Path | None:
    """宿主主配置文件（`<host_home>/config.yaml`）。"""
    home = host_home()
    return (home / "config.yaml") if home else None


def host_env_path() -> Path | None:
    """宿主 dotenv 文件（`<host_home>/.env`）—— 密钥只经此读取，永不打印。"""
    home = host_home()
    return (home / ".env") if home else None


def export_host_home_for_child() -> str | None:
    """把宿主目录同步给要 fork 的外部 CLI，返回实际使用的变量名。

    有些外部 CLI 靠环境变量定位自己的配置目录；调度器（launchd / cron / systemd）会
    sanitize 环境，变量丢了就会静默回落到另一个身份。这里只做「补默认」，不覆盖已有值。

    变量名默认 `HERMES_HOME`（Hermes Agent 的布局，见 AGENTS.md 的宿主接入示例），
    可用 `NEWSPIPE_CLI_HOME_ENV` 改成目标 CLI 期望的名字。
    """
    name = os.environ.get("NEWSPIPE_CLI_HOME_ENV") or "HERMES_HOME"
    home = host_home()
    if home is not None:
        os.environ.setdefault(name, str(home))
        return name
    return None


def read_env(path: Path | None) -> dict[str, str]:
    """读 dotenv 的键值。**只返回值**，调用方负责永不打印。"""
    out: dict[str, str] = {}
    if path is None:
        return out
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        m = re.match(r"^\s*([A-Za-z0-9_]+)\s*=\s*(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out
