"""飞书应用凭据解析 —— 独立运行时的凭据来源（**永不打印值**）。

解析顺序（取到即停），设计目标是「不把密钥写进仓库，也不要求用户贴进聊天」：

1. `service.yaml` 的显式值（`feishu.app_id` / `feishu.app_secret`）—— 仅 app_id 建议写这里；
2. 进程环境：`NEWSPIPE_FEISHU_*` → 回落宿主命名 `FEISHU_*`；
3. dotenv 文件：`$NEWSPIPE_HOME/.env` → `<news_dir>/.env` → `~/.hermes/.env`（**迁移桥**：
   与宿主共用同一个飞书应用时，独立服务能直接复用既有密钥，无需重新粘贴）；
4. macOS 钥匙串（仅当 `service.yaml` 填了 `keychain_service` 才走）。

`describe()` 只回「是否已设置」，任何日志/输出路径都不得出现密钥值——这是硬约束，
因为凭据一旦进了 cron 日志或卡片，就等于泄露。
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from newspipe.errors import ConfigError

APP_ID_KEYS = ("NEWSPIPE_FEISHU_APP_ID", "FEISHU_APP_ID")
APP_SECRET_KEYS = ("NEWSPIPE_FEISHU_APP_SECRET", "FEISHU_APP_SECRET")
VERIFY_KEYS = ("NEWSPIPE_FEISHU_VERIFICATION_TOKEN", "FEISHU_VERIFICATION_TOKEN")
ENCRYPT_KEYS = ("NEWSPIPE_FEISHU_ENCRYPT_KEY", "FEISHU_ENCRYPT_KEY")


@dataclass
class FeishuCreds:
    app_id: str
    app_secret: str = ""
    domain: str = "feishu"
    verification_token: str = ""
    encrypt_key: str = ""
    source: str = ""

    @property
    def base_url(self) -> str:
        return "https://open.larksuite.com" if self.domain == "lark" else "https://open.feishu.cn"

    def describe(self) -> dict[str, object]:
        """可安全打印的摘要（**只有布尔与 id，没有密钥**）。"""
        return {
            "app_id": self.app_id,
            "domain": self.domain,
            "app_secret": "已设置" if self.app_secret else "缺失",
            "verification_token": "已设置" if self.verification_token else "未配置",
            "encrypt_key": "已设置" if self.encrypt_key else "未配置",
            "source": self.source,
        }


def read_dotenv(path: Path) -> dict[str, str]:
    """极简 dotenv 解析：`KEY=VALUE`、`#` 注释、可选的引号包裹。"""
    out: dict[str, str] = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in out:
            out[key] = value
    return out


def default_dotenv_paths(news_dir: Path | None = None) -> list[Path]:
    """按优先级列出 dotenv 候选：项目自己的 → 宿主共用的（迁移桥）。"""
    paths: list[Path] = []
    home = os.environ.get("NEWSPIPE_HOME")
    if home:
        paths.append(Path(home).expanduser() / ".env")
    if news_dir is not None:
        paths.append(Path(news_dir) / ".env")
    paths.append(Path.home() / ".hermes" / ".env")
    return paths


def _keychain(service: str, account: str) -> str:
    """从 macOS 钥匙串取密钥。失败只报「取不到」，不回显任何值。"""
    try:
        proc = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ConfigError(f"读钥匙串失败（service={service}）：{type(exc).__name__}") from None
    if proc.returncode != 0 or not proc.stdout.strip():
        raise ConfigError(
            f"钥匙串里找不到凭据（service={service} account={account}）。"
            "用 `security find-generic-password -s <service> -a <account> -w` 确认名字，"
            "或改用环境变量 / .env。")
    return proc.stdout.strip()


def resolve_feishu(service_cfg: dict | None = None, *, news_dir: Path | None = None,
                   dotenv_paths: list[Path] | None = None,
                   env: dict[str, str] | None = None) -> FeishuCreds:
    """解析飞书应用凭据。缺关键项时抛 ConfigError，消息里给可执行的修法（不含任何值）。"""
    cfg = dict(service_cfg or {})
    environ = dict(os.environ) if env is None else dict(env)
    files: dict[str, str] = {}
    origin: dict[str, Path] = {}
    for path in (dotenv_paths if dotenv_paths is not None else default_dotenv_paths(news_dir)):
        for key, value in read_dotenv(path).items():
            if key not in files:
                files[key] = value
                origin[key] = Path(path)

    def pick(explicit: str, env_keys: tuple[str, ...]) -> tuple[str, str]:
        if explicit:
            return explicit, "service.yaml"
        for key in env_keys:
            if environ.get(key):
                return environ[key], f"env:{key}"
        for key in env_keys:
            if files.get(key):
                return files[key], f"dotenv:{origin[key]}"
        return "", ""

    app_id, src_id = pick(str(cfg.get("app_id") or ""), APP_ID_KEYS)
    app_secret, src_secret = pick(str(cfg.get("app_secret") or ""), APP_SECRET_KEYS)
    if not app_secret and cfg.get("keychain_service"):
        app_secret = _keychain(str(cfg["keychain_service"]),
                               str(cfg.get("keychain_account") or f"appsecret={app_id}"))
        src_secret = "keychain"

    # 允许用自定义的环境变量名（service.yaml 里指定）
    if not app_id and cfg.get("app_id_env"):
        app_id = environ.get(str(cfg["app_id_env"]), "") or files.get(str(cfg["app_id_env"]), "")
        src_id = f"env:{cfg['app_id_env']}"
    if not app_secret and cfg.get("app_secret_env"):
        app_secret = (environ.get(str(cfg["app_secret_env"]), "")
                      or files.get(str(cfg["app_secret_env"]), ""))
        src_secret = f"env:{cfg['app_secret_env']}"

    def named(field_name: str, default_keys: tuple[str, ...]) -> str:
        named_env = str(cfg.get(f"{field_name}_env") or "")
        if named_env:
            if environ.get(named_env):
                return environ[named_env]
            if files.get(named_env):
                return files[named_env]
        value, _ = pick("", default_keys)
        return value

    missing = []
    if not app_id:
        missing.append("app_id（NEWSPIPE_FEISHU_APP_ID 或 FEISHU_APP_ID）")
    if not app_secret:
        missing.append("app_secret（NEWSPIPE_FEISHU_APP_SECRET 或 FEISHU_APP_SECRET）")
    if missing:
        where = news_dir or "<news_dir>"
        raise ConfigError(
            "缺少飞书应用凭据：" + "；".join(missing) + "。"
            f"把密钥写进 {where}/.env（例如 NEWSPIPE_FEISHU_APP_SECRET=…）或导出为环境变量；"
            "app_id/app_secret 在飞书开放平台 → 应用 → 凭证与基础信息。"
            "（本项目不会打印、也不会落盘任何密钥值。）")

    return FeishuCreds(
        app_id=app_id,
        app_secret=app_secret,
        domain=str(cfg.get("domain") or environ.get("NEWSPIPE_FEISHU_DOMAIN") or "feishu"),
        verification_token=named("verification_token", VERIFY_KEYS),
        encrypt_key=named("encrypt_key", ENCRYPT_KEYS),
        source=f"app_id={src_id} app_secret={src_secret}",
    )
