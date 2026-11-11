"""Optional RSSHub endpoint resolution and network-free configuration metadata."""
from __future__ import annotations

import os
import urllib.error
import urllib.request
from urllib.parse import unquote, urlsplit, urlunsplit
import xml.etree.ElementTree as ET

from newspipe.errors import ConfigError
from newspipe import result

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


def safe_url(value: str) -> str:
    """Diagnostic URL only: never credentials, query values or fragments."""
    if not value:
        return ""
    try:
        parts = urlsplit(value)
        if parts.username is not None or parts.password is not None:
            return "<含凭据的地址已隐藏>"
        if not parts.scheme and value.startswith("/"):
            return parts.path
        parts = _url(value)
        return urlunsplit(parts._replace(query="", fragment=""))
    except (ValueError, ConfigError):
        return "<无效地址已隐藏>"


class ProbeError(ValueError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


MESSAGES = {
    "network": ("无法连接 RSSHub / RSS 地址", "检查地址、DNS、TLS 和网络；容器里的 localhost 指容器自身"),
    "authentication": ("订阅端点要求鉴权", "在 RSSHub 实例侧配置所需凭据；不要把密钥放进基础地址"),
    "not_found": ("端点或 RSSHub 路由不存在", "检查基础地址、反向代理路径前缀和订阅路由"),
    "http_error": ("上游拒绝请求或暂时不可用", "检查上游状态或稍后手动测试；不会自动重试"),
    "invalid_feed": ("返回内容不是有效的 RSS 订阅", "检查是否返回了登录页、HTML 错误页或不完整 XML"),
    "unsupported_feed": ("当前适配器暂不支持该订阅格式", "目前支持 RSS 2.0；请使用 RSS 格式地址，而非 Atom/RDF"),
    "too_large": ("检测响应超过 1 MiB 限制", "使用条目较少的订阅进行检测；这不等于采集失败"),
}


def parse_rss(body: bytes):
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        raise ProbeError("invalid_feed") from None
    if root.tag.rsplit("}", 1)[-1].lower() in ("feed", "rdf"):
        raise ProbeError("unsupported_feed")
    if root.tag != "rss" or root.find("channel") is None:
        raise ProbeError("invalid_feed")
    return root


def _probe_request(url: str) -> tuple[int, bytes]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(urllib.request.Request(url, headers={"User-Agent": "newspipe-rss-probe"}), timeout=5) as response:
        body = response.read(1024 * 1024 + 1)
        if len(body) > 1024 * 1024:
            raise ProbeError("too_large")
        return response.status, body


def probe(configured: str = "", feed: str | None = None) -> result.Result:
    command = "rsshub.probe"
    try:
        url = feed_url(feed, configured) if feed else effective(configured)[0]
    except (ConfigError, ValueError) as exc:
        message = str(exc) if isinstance(exc, ConfigError) else "订阅地址无效"
        return result.fail(command, "E_VALIDATION", message, hint="检查 HTTP(S) 基础地址或以 / 开头的路由")
    http_status = None
    try:
        http_status, body = _probe_request(url)
        if not 200 <= http_status < 300:
            raise ProbeError("authentication" if http_status in (401, 403) else "not_found" if http_status == 404 else "http_error")
        if feed:
            root = parse_rss(body)
            return result.ok(command, {"status": "feed_valid", "checked_url": safe_url(url), "http_status": http_status,
                "items": sum(1 for _ in root.iter("item")), "message": "RSS 订阅有效；仅检测，没有采集入队或发送通知"})
        return result.ok(command, {"status": "reachable", "checked_url": safe_url(url), "http_status": http_status,
            "message": "端点可达；未验证 RSSHub 身份或具体订阅路由，没有修改配置"})
    except urllib.error.HTTPError as exc:
        http_status = exc.code
        exc.close()
        reason = "authentication" if exc.code in (401, 403) else "not_found" if exc.code == 404 else "http_error"
    except ProbeError as exc:
        reason = exc.reason
    except Exception:
        reason = "network"
    message, hint = MESSAGES[reason]
    return result.fail(command, "E_VALIDATION" if reason in ("invalid_feed", "unsupported_feed", "too_large") else "E_NETWORK",
        message, hint=hint, details={"reason": reason, "checked_url": safe_url(url), "http_status": http_status})
