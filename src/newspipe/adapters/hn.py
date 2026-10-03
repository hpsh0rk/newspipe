"""Hacker News 适配器：Algolia front_page API（免 key，境内直连可达）。

只给标题 + 链接 + 分数，没有正文——正文由 enrich 层按 `fetch_body: true` 去抓。
"""
from __future__ import annotations

import json
import time

from newspipe.net import request

URL = "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=30"


def fetch(cfg: dict | None = None, since: float | None = None) -> list[dict]:
    data = None
    for _ in range(3):  # 偶发截断/限流响应不是合法 JSON：快速重试，别把一次抖动算成故障
        try:
            data = json.loads(request(URL, timeout=25).decode("utf-8"))
            break
        except json.JSONDecodeError:
            time.sleep(2)
    if data is None:
        raise RuntimeError("HN Algolia returned invalid JSON 3x")
    now = time.time()
    items = []
    for h in data.get("hits", []):
        if now - (h.get("created_at_i") or 0) > 86400:
            continue
        url = h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}"
        items.append({
            "ext_id": f"hn:{h['objectID']}",
            "title": h.get("title", ""),
            "url": url,
            "original_url": url,
            "source": f"HackerNews · {h.get('points', 0)}分/{h.get('num_comments', 0)}评论",
            "summary": "",
            "category": "",
            "score": h.get("points"),
        })
    items.sort(key=lambda x: x.get("score") or 0, reverse=True)
    return items
