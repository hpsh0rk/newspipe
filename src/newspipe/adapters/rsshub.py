"""RSS/Atom from explicit feed URLs or configured local/remote RSSHub routes."""
from __future__ import annotations

from urllib.parse import urlsplit

from newspipe import feedparse, net, rssconnect
from newspipe.adapters import _common
from newspipe.errors import ConfigError
from newspipe.source_contract import FetchResult


def fetch(cfg: dict | None = None, since: float | None = None) -> FetchResult:
    cfg = cfg or {}
    try:
        feeds = _common.subscriptions(cfg)
        if not isinstance(cfg.get("feed_label", ""), str):
            raise ConfigError("来源标签需要字符串")
    except ConfigError as error:
        return _common.configuration_failure("rsshub", error)
    items, reports = [], []
    for key in feeds:
        try:
            url = rssconnect.feed_url(key, cfg.get("rsshub_base_url", ""))
            parts = urlsplit(key)
            label = cfg.get("feed_label") or (parts.hostname if parts.scheme else parts.path.strip("/").split("/")[0]) or "RSS"
            entries = feedparse.parse(net.request(url, timeout=40, http2=bool(cfg.get("http2")),
                                                 max_bytes=feedparse.MAX_BODY, max_redirects=5), base_url=url)
            rows = []
            for entry in entries:
                stamp = _common.entry_time(entry)
                if since is not None and stamp is not None and stamp <= since:
                    continue
                item = {"ext_id": entry.native_id, "identity_version": "rss-full-v2", "title": entry.title, "url": entry.permalink,
                        "original_url": entry.permalink, "source": label,
                        "summary": entry.source_text[:300], "category": "", "score": None,
                        "content_kind": "unknown", **_common.entry_fields(entry, key)}
                if len(entry.native_id) > 120:
                    item.update(identity_version="rss-full-v2", legacy_ext_id=entry.native_id[:120])
                rows.append(item)
            reports.append(_common.success_report(key, rows, [_common.entry_time(entry) for entry in entries]))
            items.extend(rows)
        except Exception as error:
            reports.append(_common.failed(key, error))
    items.sort(key=lambda item: -(item.get("pub_ts") or 0))
    return FetchResult(items, tuple(reports))
