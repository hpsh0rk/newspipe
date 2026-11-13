"""Bounded public Google News RSS searches, not full articles or durable history."""
from __future__ import annotations

import json
import math
import time
from urllib.parse import urlencode

from newspipe import feedparse, source_registry
from newspipe.adapters import _common
from newspipe.errors import ConfigError
from newspipe.net import request
from newspipe.source_contract import FetchResult, SubscriptionReport, native_identity

FETCH_RESULT_ORDER = "pub_desc"

def _settings(cfg, since):
    if not isinstance(cfg, dict) or not isinstance(cfg.get("settings", {}), dict):
        raise ConfigError("Google News 参数需要映射")
    fields = source_registry.describe_adapter("google_news").fields
    values = {}
    for field in fields:
        key = field["path"].split(".")[1]
        value = cfg.get("settings", {}).get(key, field["default"])
        try:
            source_registry.validate_value(value, field)
        except ValueError:
            raise ConfigError("Google News 参数 " + field["path"] + " 无效；检查查询、语言地区、版面和范围") from None
        values[key] = value
    try:
        source_registry.validate_relations({"settings": values}, fields)
        if since is not None and (type(since) not in (int, float) or not math.isfinite(since) or since < 0):
            raise ValueError
    except (ValueError, OverflowError):
        raise ConfigError("Google News 版面必须匹配地区，采集时间须为非负有限时间戳") from None
    queries = list(dict.fromkeys(query.strip() for query in values["queries"]))
    if not queries or any(not query for query in queries):
        raise ConfigError("Google News 需要 1..25 条非空新闻查询")
    if not isinstance(cfg.get("feed_label", ""), str):
        raise ConfigError("Google News 来源标签需要字符串")
    language = values["language"]
    chinese = {"zh-cn": "zh-Hans", "zh-sg": "zh-Hans", "zh-hans": "zh-Hans",
               "zh-tw": "zh-Hant", "zh-hk": "zh-Hant", "zh-hant": "zh-Hant"}
    edition = values["edition"] or values["region"] + ":" + chinese.get(language.lower(), language.split("-")[0].lower())
    return values, queries, edition


def _order(item):
    return item.get("pub_ts") is None, -(item.get("pub_ts") or 0)


def fetch_partitions(cfg: dict | None = None, since: float | None = None):
    cfg = {} if cfg is None else cfg
    try:
        values, queries, edition = _settings(cfg, since)
    except ConfigError as error:
        yield _common.configuration_failure("google_news", error)
        return
    cutoff = max(time.time() - values["lookback_days"] * 86400, since or 0)
    for query in queries:
        key = _common.partition(json.dumps([query, values["language"], values["region"], edition,
                                            values["lookback_days"], values["max_results"]], ensure_ascii=False))
        diagnostic = {"window_limited": True, "window_kind": "search_window"}
        try:
            url = "https://news.google.com/rss/search?" + urlencode({"q": query + f' when:{values["lookback_days"]}d',
                "hl": values["language"], "gl": values["region"], "ceid": edition})
            entries = feedparse.parse(request(url, timeout=20, max_bytes=feedparse.MAX_BODY, max_redirects=0), base_url=url)
            items = []
            for entry in entries:
                if not entry.title or not entry.permalink:
                    raise ValueError("Search item needs a title and source permalink.")
                if entry.published_ts is not None and entry.published_ts < cutoff:
                    continue
                # Discovery queries are provenance, not the article's identity partition.
                fields = _common.entry_fields(entry, "google_news")
                if entry.publisher_name:
                    fields["facts"]["publisher"] = entry.publisher_name
                references = ([{"role": "publisher", "url": entry.publisher_url}] if entry.publisher_url else [])
                references.extend(ref for ref in entry.references if ref not in references)
                fields["referenced_urls"] = references[:20]
                fields["metadata"].update(native_id=entry.native_id, aggregator="google_news",
                                         publisher=entry.publisher_name, publisher_url=entry.publisher_url,
                                         query_partition=key)
                items.append({"ext_id": native_identity("google_news", entry.native_id),
                    "identity_version": "google-news-native-v1", "title": entry.title, "url": entry.permalink,
                    "original_url": entry.permalink, "source": cfg.get("feed_label") or "Google News",
                    "category": "", "score": None, "summary": entry.source_text[:300],
                    "content_kind": "article", **fields})
            items.sort(key=_order)
            if len(items) > values["max_results"]:
                diagnostic["window_kind"] = "result_cap"
            items = items[:values["max_results"]]
            yield FetchResult(items, (SubscriptionReport(key, "success" if items else "empty", len(items),
                                                        diagnostics=diagnostic),))
        except Exception as error:
            yield FetchResult([], (SubscriptionReport(key, "failure", code=_common.issue_code(error),
                                                      diagnostics=diagnostic),))


def fetch(cfg: dict | None = None, since: float | None = None) -> FetchResult:
    items, reports = [], []
    for part in fetch_partitions(cfg, since):
        items.extend(part.items)
        reports.extend(part.reports)
    items.sort(key=_order)
    return FetchResult(items, tuple(reports))
