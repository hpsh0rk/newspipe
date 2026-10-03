"""通用 RSS 适配器：本地自建 RSSHub（127.0.0.1:1200）或任意 feed URL 均可承载。

定位："没有公开 API 的网页信源"的统一出口。feeds = RSSHub 路由路径
（如 /twitter/user/thsottiaux，鉴权路由需 RSSHub 容器 env 注 cookie）或完整 URL
（如 https://www.trumpstruth.org/feed 这类直接出 RSS 的镜像站，免凭据）。
支持 since（epoch 秒）：poll 回路靠它做增量；slot 源忽略。
"""
from __future__ import annotations

import email.utils
import os
import re
import time
import xml.etree.ElementTree as ET

from newspipe import net
from newspipe.errors import ConfigError

BASE = os.environ.get("RSSHub_BASE", "http://127.0.0.1:1200")

_TAG = re.compile(r"<[^>]+>")


def _clean(html: str, limit: int = 200) -> str:
    txt = _TAG.sub(" ", html or "")
    return re.sub(r"\s+", " ", txt).strip()[:limit]


def fetch(cfg: dict | None = None, since: float | None = None) -> list[dict]:
    cfg = cfg or {}
    h2 = bool(cfg.get("http2"))  # CF Bot Management 只放行 h2 的站点（如 linux.do）须声明
    items: list[dict] = []
    failed: list[str] = []
    ok_feeds = 0
    for entry in cfg.get("feeds") or []:
        if entry.startswith("http"):
            url, label = entry, cfg.get("feed_label") or entry.split("/")[2]
        else:
            url = f"{BASE}{entry}"
            label = cfg.get("feed_label") or entry.strip("/").split("/")[0]
        root = None
        last_err: Exception | None = None
        for _ in range(3):  # RSSHub 冷路由首发常见 503（内部重试后），退避再试
            try:
                root = ET.fromstring(net.request(url, timeout=40, http2=h2))
                break
            except Exception as exc:
                last_err = exc
                time.sleep(3)
        if root is None:
            msg = str(last_err or "")[:200]
            if "not configured" in msg.lower() or "401" in msg or "cookie" in msg.lower():
                raise ConfigError(f"{label}: RSSHub 路由缺凭据 — {msg}") from last_err
            failed.append(f"{label}: {msg}")
            continue
        ok_feeds += 1
        for node in root.iter("item"):
            def get(t: str, _node=node) -> str:
                return (_node.findtext(t) or "").strip()

            ext_id = get("guid") or get("link")
            if not ext_id:
                continue
            pub = get("pubDate")
            try:
                ts = email.utils.parsedate_to_datetime(pub).timestamp() if pub else 0
            except Exception:
                ts = 0
            if since and ts and ts <= since:
                continue
            items.append({
                "ext_id": ext_id[:120],
                "title": _clean(get("title"), 160),
                "url": get("link"),
                "original_url": get("link"),
                "source": f"{label}",
                "summary": _clean(get("description"), 300),
                "category": "",
                "score": None,
                "pub_ts": ts,
            })
    items.sort(key=lambda i: -(i.get("pub_ts") or 0))
    # 全 feed 失败 ≠ 没有新内容。静默返回 [] 会让"死了"和"今天很安静"在心跳里长得一模一样
    # （fetched:0/status:quiet），单 feed 信源被上游持续拒绝后永远无声。
    if failed and not ok_feeds:
        raise RuntimeError("; ".join(failed)[:300])
    return items
