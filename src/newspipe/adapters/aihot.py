"""AIHOT 适配器：GET /api/v1/items?mode=selected&window=24h。匿名只读，无 key。

AIHOT 条目本身已是中文标题 + 中文摘要（它自己那套流水线产出的），所以 sources.yaml 里
这个源 `enrich.summary: "off"` —— 再加工一次是纯浪费。
"""
from __future__ import annotations

import json

from newspipe.net import request

API = "https://aihot.news/api/v1/items?mode=selected&window=24h&limit=50"
CATS = {"ai-models": "模型", "ai-products": "产品", "industry": "行业",
        "paper": "论文", "tip": "观点"}


def fetch(cfg: dict | None = None, since: float | None = None) -> list[dict]:
    data = json.loads(request(API, timeout=30).decode("utf-8"))
    out = []
    for it in data.get("items", []):
        if not it.get("selected"):
            continue
        links = it.get("links") or {}
        out.append({
            "ext_id": it["id"],
            "title": it.get("title", ""),
            "url": links.get("aihot") or links.get("original", ""),
            "original_url": links.get("original", ""),
            "source": (it.get("source") or {}).get("name", "AIHOT"),
            "summary": it.get("summary", ""),
            "category": CATS.get(it.get("category"), it.get("category") or ""),
            "score": it.get("score"),
        })
    return out
