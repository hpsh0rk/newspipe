"""HN front-page posts: original discussion evidence is not a linked article body."""
from __future__ import annotations

import json
import math
import time
from urllib.parse import urlencode

from newspipe import feedparse
from newspipe.adapters import _common
from newspipe.net import request
from newspipe.source_contract import FetchResult

URL = "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=30"


def _number(value):
    if value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("Invalid provider number.")
    return value


def fetch(cfg: dict | None = None, since: float | None = None) -> FetchResult:
    key = "front_page"
    try:
        data = json.loads(request(URL, timeout=25, max_bytes=feedparse.MAX_BODY, max_redirects=5).decode("utf-8"))
        hits = data.get("hits") if isinstance(data, dict) else None
        if not isinstance(hits, list) or len(hits) > 1000:
            raise ValueError("Invalid provider result.")
        cutoff = max(time.time() - 86400, since or 0)
        items, dates = [], []
        for hit in hits:
            native = hit.get("objectID") if isinstance(hit, dict) else None
            if type(native) not in (str, int) or not str(native).strip():
                raise ValueError("Missing provider identity.")
            stamp = _number(hit.get("created_at_i")) or None
            dates.append(stamp)
            if stamp is not None and stamp <= cutoff:
                continue
            for field in ("title", "url", "story_text"):
                if hit.get(field) is not None and not isinstance(hit[field], str):
                    raise ValueError("Invalid provider text.")
            score, comments = _number(hit.get("points")), _number(hit.get("num_comments"))
            permalink = "https://news.ycombinator.com/item?" + urlencode({"id": str(native)})
            article = feedparse.source_link(hit.get("url") or "")
            text, raw, truncated, references = feedparse.text_evidence(hit.get("story_text") or "", base_url=permalink)
            refs = list(references)
            if article and article != permalink:
                refs.insert(0, {"role": "article", "url": article})
            title = hit.get("title") or ""
            items.append({"ext_id": f"hn:{native}", "title": title[:500], "url": article or permalink,
                          "original_url": permalink, "source": f"HackerNews · {score or 0}分/{comments or 0}评论",
                          "summary": text[:300], "category": "", "score": score,
                          "pub_ts": stamp, "subscription": key, "content_kind": "post",
                          "source_text": text, "source_markup": raw, "source_text_complete": False,
                          "source_text_truncated": truncated or len(title) > 500, "referenced_urls": refs[:20],
                          "metadata": {"native_id": native, "points": score, "comments": comments}})
        items.sort(key=lambda item: item.get("score") or 0, reverse=True)
        return FetchResult(items, (_common.success_report(key, items, dates),))
    except Exception as error:
        return FetchResult([], (_common.failed(key, error),))
