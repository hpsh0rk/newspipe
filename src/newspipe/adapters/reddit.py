"""Reddit 适配器：subreddit top/.rss（免 key；境内经 Clash）。

RSS 而非 .json：json 端点被 403（block via IP/UA），.rss 通道可用。
摘要取帖子自文本（HTML 去标签），给痛点提炼 agent job 当原料。
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from newspipe.net import request

_NS = {"a": "http://www.w3.org/2005/Atom"}
DEFAULT_SUBS = ["SideProject", "SaaS", "EntrepreneurRideAlong"]


def _clean(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&\w+;|&#\d+;", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:600]


def _post_id(link: str) -> str:
    m = re.search(r"/comments/([0-9a-z]+)", link)
    return m.group(1) if m else link


def fetch(cfg: dict | None = None, since: float | None = None) -> list[dict]:
    items = []
    for sub in ((cfg or {}).get("feeds") or DEFAULT_SUBS):
        url = f"https://www.reddit.com/r/{sub}/top/.rss?t=day&limit=25"
        try:
            raw = request(url, timeout=15).decode("utf-8", "replace")
        except Exception:
            continue  # 单 sub 失败不拖垮整批（心跳里体现 fetched 少）
        root = ET.fromstring(raw)
        for e in root.findall("a:entry", _NS):
            link = ""
            for l in e.findall("a:link", _NS):
                link = l.get("href") or ""
            title = (e.findtext("a:title", "", _NS) or "").strip()
            content = e.findtext("a:content", "", _NS) or ""
            author = (e.findtext("a:author/a:name", "", _NS) or "").strip()
            items.append({
                "ext_id": f"reddit:{sub}:{_post_id(link)}",
                "title": title,
                "url": link,
                "original_url": link,
                "source": f"r/{sub} · u/{author.removeprefix('/u/')}",
                "summary": _clean(content),
                "category": "reddit",
                "score": None,
            })
    return items
