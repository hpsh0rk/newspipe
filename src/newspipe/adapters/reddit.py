"""Public subreddit Atom posts, with explicit blocking and partial-failure reports."""
from __future__ import annotations

import re

from newspipe import feedparse
from newspipe.adapters import _common
from newspipe.errors import ConfigError
from newspipe.net import request
from newspipe.source_contract import FetchResult

DEFAULT_SUBS = ["SideProject", "SaaS", "EntrepreneurRideAlong"]


def _post_id(link: str) -> str:
    m = re.search(r"/comments/([0-9a-z]+)", link)
    return m.group(1) if m else link


def fetch(cfg: dict | None = None, since: float | None = None) -> FetchResult:
    try:
        feeds = _common.subscriptions(cfg or {}, DEFAULT_SUBS)
    except ConfigError as error:
        return _common.configuration_failure("reddit", error)
    items, reports = [], []
    for sub in feeds:
        try:
            if not re.fullmatch(r"[A-Za-z0-9_]{1,100}", sub):
                raise ConfigError("社区名称仅支持字母、数字和下划线")
            url = f"https://www.reddit.com/r/{sub}/top/.rss?t=day&limit=25"
            entries = feedparse.parse(request(url, timeout=15, max_bytes=feedparse.MAX_BODY, max_redirects=5), base_url=url)
            rows = []
            for entry in entries:
                stamp = _common.entry_time(entry)
                if since is not None and stamp is not None and stamp <= since:
                    continue
                rows.append({"ext_id": f"reddit:{sub}:{_post_id(entry.permalink) or entry.native_id}",
                             "title": entry.title, "url": entry.permalink, "original_url": entry.permalink,
                             "source": f"r/{sub} · u/{entry.author.removeprefix('/u/')}",
                             "summary": entry.source_text[:600], "category": "reddit", "score": None,
                             "content_kind": "post", **_common.entry_fields(entry, sub)})
            reports.append(_common.success_report(sub, rows, [_common.entry_time(entry) for entry in entries]))
            items.extend(rows)
        except Exception as error:
            reports.append(_common.failed(sub, error))
    return FetchResult(items, tuple(reports))
