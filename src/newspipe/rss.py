"""RSS 发现与候选源目录 —— 配置页「添加订阅」的后端。

**为什么不用现成服务。** RSSHub 本地实例（`127.0.0.1:1200`）没有 `/routes` 目录（实测 404），
第三方 feed 搜索服务在境内不可靠，还要多一个外部依赖。所以只做两件本机能保证的事：

1. `discover(url)` —— 给一个站点，找出它的 feed：先看 HTML 里的
   `<link rel="alternate" type="application/rss+xml">`，再探常见路径。
2. `search(query)` —— 在包内 `CATALOG` 里按名称/标签/URL 匹配。

两者都只产出**候选**；落盘一律走 `source set`（CLI 的校验、原子写、乐观并发都在那边）。

**目录里的 `verified` 字段是实测记录，不是装饰**：只有真在用户那台 RSSHub 上返回过 200 的
路由才收进来。写目录时凭空添一条「看起来合理」的路由，等于把失败推给用户的心跳面板。
"""
from __future__ import annotations

import json
import re
from html import unescape
from typing import Any
from urllib.parse import urljoin, urlparse

from newspipe import net

#: 认作 feed 的 MIME / rel 片段
FEED_HINTS = ("rss", "atom", "feed", "xml")
COMMON_PATHS = ("/feed", "/rss", "/atom.xml", "/index.xml", "/feed.xml", "/rss.xml",
                "/feed/", "/rss/", "/atom", "/feeds/posts/default")
FEED_BODY_MARKERS = ("<rss", "<feed", "<rdf:rdf", '{"version"', '"items"')


class RssError(RuntimeError):
    """发现过程失败（取不到页面等）。消息直接给人看。"""


#: 候选源目录。**只收实测可用的**；`route=True` 表示 RSSHub 路由（相对路径，交给 rsshub 适配器）。
CATALOG: list[dict[str, Any]] = [
    {"name": "少数派 · Matrix", "feed": "/sspai/matrix", "route": True,
     "tags": ["中文", "科技", "深度"], "verified": "2026-10-04 200"},
    {"name": "36氪 · 快讯", "feed": "/36kr/newsflashes", "route": True,
     "tags": ["中文", "商业", "快讯"], "verified": "2026-10-04 200"},
    {"name": "豆瓣 · 正在上映", "feed": "/douban/movie/playing", "route": True,
     "tags": ["中文", "影视"], "verified": "2026-10-04 200"},
    {"name": "掘金 · AI 分类", "feed": "/juejin/category/ai", "route": True,
     "tags": ["中文", "技术", "AI"], "verified": "2026-10-04 200"},
    {"name": "Telegram · 再花一点（AI 资讯频道）", "feed": "/telegram/channel/zaihuapd", "route": True,
     "tags": ["中文", "AI", "资讯"], "verified": "2026-10-04 200"},
    {"name": "X · 任意用户（把 openai 换成 handle）", "feed": "/twitter/user/openai", "route": True,
     "tags": ["英文", "X", "模板"], "verified": "2026-10-04 200",
     "note": "RSSHub 需注入 TWITTER_AUTH_TOKEN（本机容器已注入）"},
    {"name": "LINUX DO · 福利羊毛", "feed": "https://linux.do/tag/福利羊毛.rss", "route": False,
     "tags": ["中文", "羊毛", "AI 额度"], "verified": "在用（linuxdo_deals 信源）"},
]


def _attr(tag: str, name: str) -> str:
    m = re.search(rf'{name}\s*=\s*"([^"]*)"', tag, re.I) or re.search(rf"{name}\s*=\s*'([^']*)'", tag, re.I)
    return unescape(m.group(1)).strip() if m else ""


def normalize_url(url: str) -> str:
    """补协议、去空白。给「用户手抄了一半的地址」一点容错。"""
    url = (url or "").strip()
    if not url:
        raise RssError("先给一个站点地址或关键词")
    if not urlparse(url).scheme:
        url = "https://" + url
    return url


def _looks_like_feed(url: str, timeout: float) -> bool:
    try:
        body = net.request(url, timeout=timeout)
    except Exception:                                  # noqa: BLE001 —— 探测失败就是「不是」
        return False
    head = body[:400].decode("utf-8", "replace").lower()
    return any(marker in head for marker in FEED_BODY_MARKERS)


def discover(url: str, *, timeout: float = 12.0, probe_common: bool = True) -> dict[str, Any]:
    """给站点找 feed。返回 `{"url", "candidates": [...], "note"}`；取不到页面抛 `RssError`。"""
    target = normalize_url(url)
    try:
        page = net.request(target, timeout=timeout).decode("utf-8", "replace")
    except Exception as exc:                           # noqa: BLE001
        raise RssError(f"取不到 {target}（{type(exc).__name__}: {exc}）") from exc

    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for tag in re.findall(r"<link\b[^>]*>", page, re.I):
        rel = _attr(tag, "rel").lower()
        type_ = _attr(tag, "type").lower()
        href = _attr(tag, "href")
        if "alternate" not in rel or not href:
            continue
        if not any(hint in type_ for hint in FEED_HINTS):
            continue
        full = urljoin(target, href)
        if full in seen:
            continue
        seen.add(full)
        found.append({"url": full, "title": _attr(tag, "title"),
                      "how": f"页面声明的 <link rel=alternate type={type_ or '?'}>"})
    note = ""
    if not found and probe_common:
        for path in COMMON_PATHS:
            candidate = urljoin(target, path)
            if candidate in seen:
                continue
            if _looks_like_feed(candidate, timeout):
                seen.add(candidate)
                found.append({"url": candidate, "title": "", "how": f"探测到常见路径 {path}"})
        note = "页面没有声明 feed，以下是常见路径探测的结果" if found else "页面没声明 feed，常见路径也没探到"
    elif not found:
        note = "页面没声明 feed"
    return {"url": target, "candidates": found, "note": note}


def search(query: str, *, limit: int = 20) -> list[dict[str, Any]]:
    """在本地目录里按名称/标签/地址匹配。空查询返回全部（当目录浏览用）。"""
    q = (query or "").strip().lower()
    out = []
    for row in CATALOG:
        haystack = " ".join([str(row["name"]), str(row["feed"]), " ".join(row.get("tags") or [])]).lower()
        if not q or q in haystack:
            out.append(dict(row))
    return out[:limit]


def as_candidate(row: dict[str, Any]) -> dict[str, Any]:
    """把目录项转成「可以一键订阅」的候选：预填 adapter=feeds 与 feed_label。"""
    label = str(row.get("name") or "")
    return {"feed": row.get("feed"), "feed_label": label, "adapter": "rsshub",
            "name_hint": _slug(label), "tags": row.get("tags") or [],
            "verified": row.get("verified") or "", "note": row.get("note") or ""}


def _slug(text: str) -> str:
    """给信源名一个默认值：保留字母数字与中文，其余压成下划线。"""
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", text).strip("_").lower()
    return cleaned[:40] or "new_source"


def catalog_json() -> str:
    return json.dumps(CATALOG, ensure_ascii=False)
