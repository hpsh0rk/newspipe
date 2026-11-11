"""Optional RSSHub endpoint resolution and network-free configuration metadata."""
from __future__ import annotations

import os
from urllib.parse import unquote, urlsplit, urlunsplit

from newspipe.errors import ConfigError

DEFAULT_BASE = "http://127.0.0.1:1200"


def _safe_path(path: str) -> bool:
    for _ in range(3):
        path = unquote(path)
    return not any(part in (".", "..") for part in path.split("/")) and "\\" not in path


def _url(value: str):
    if not isinstance(value, str) or any(c.isspace() for c in value) or "\\" in value:
        raise ConfigError("地址必须是有效的 HTTP(S) URL，不能包含空白或反斜线")
    try:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.port == 0:
            raise ValueError
        if parts.username is not None or parts.password is not None:
            raise ValueError
    except ValueError:
        raise ConfigError("地址必须包含 HTTP(S) 协议和有效主机/端口，不能内嵌凭据") from None
    if not _safe_path(parts.path):
        raise ConfigError("地址不能包含路径跳转片段")
    return parts


def base_url(value: str) -> str:
    if not isinstance(value, str):
        raise ConfigError("RSSHub 地址需要字符串；留空表示继承")
    value = value.strip()
    if not value:
        return ""
    parts = _url(value)
    if parts.query or parts.fragment:
        raise ConfigError("RSSHub 基础地址不能含查询参数或片段；不要把密钥写进地址")
    return urlunsplit(parts._replace(path=parts.path.rstrip("/")))


def effective(configured: str = "") -> tuple[str, str]:
    explicit = base_url(configured)
    if explicit:
        return explicit, "config"
    inherited = os.environ.get("RSSHub_BASE", "").strip()
    return (base_url(inherited), "env:RSSHub_BASE") if inherited else (DEFAULT_BASE, "default")


def feed_url(entry: str, configured: str = "") -> str:
    if not isinstance(entry, str):
        raise ConfigError("订阅地址需要字符串")
    entry = entry.strip()
    parts = urlsplit(entry)
    if parts.scheme:
        parsed = _url(entry)
        if parsed.fragment:
            raise ConfigError("订阅地址不能含片段")
        return urlunsplit(parsed)
    if not entry.startswith("/") or entry.startswith("//") or parts.fragment or not _safe_path(parts.path) or "\\" in entry:
        raise ConfigError("RSSHub 路由必须以单个 / 开头，不能包含路径跳转或片段")
    return effective(configured)[0] + entry


def describe(cfg) -> dict:
    configured = cfg.rsshub.get("base_url", "")
    dependent = [s.name for s in cfg.enabled_sources() if s.adapter == "rsshub" and
                 any(isinstance(f, str) and not urlsplit(f).scheme for f in s.extra.get("feeds", []))]
    try:
        url, origin = effective(configured)
        error = ""
    except ConfigError as exc:
        url, origin, error = "", "env:RSSHub_BASE", str(exc)
    return {"configured_url": configured, "effective_url": url, "origin": origin,
            "dependent_sources": sorted(dependent), "error": error, "checked": False}
