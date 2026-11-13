"""Small shared source-result helpers; no state, UI or linked-page collection."""
import hashlib
import urllib.error

from newspipe import feedparse, net
from newspipe.errors import ConfigError
from newspipe.source_contract import FetchResult, SubscriptionReport


def subscriptions(cfg, defaults=()):
    entries = cfg.get("feeds") or defaults
    if (not isinstance(entries, (list, tuple)) or not entries or len(entries) > 100
            or any(not isinstance(entry, str) or not entry.strip() or len(entry) > 8192 for entry in entries)):
        raise ConfigError("订阅需要 1..100 个有效地址或名称")
    return list(dict.fromkeys(entries))


def issue_code(error):
    if isinstance(error, ConfigError):
        return "configuration"
    if isinstance(error, feedparse.FeedError):
        return {"unsupported_feed": "unsupported", "too_large": "incomplete"}.get(error.reason, "invalid_response")
    if isinstance(error, net.ResponseLimitError):
        return "incomplete"
    if isinstance(error, urllib.error.HTTPError):
        return {401: "credentials", 403: "permission", 429: "rate_limit"}.get(error.code, "network")
    if isinstance(error, TimeoutError) or isinstance(error, urllib.error.URLError) and isinstance(error.reason, TimeoutError):
        return "timeout"
    return "invalid_response" if isinstance(error, (ValueError, TypeError, KeyError, UnicodeError)) else "network"


def failed(key, error):
    return SubscriptionReport(key, "failure", code=issue_code(error))


def configuration_failure(adapter, error):
    return FetchResult([], (failed(adapter, error),))


def partition(key):
    return "feed-" + hashlib.sha256(key.encode()).hexdigest()


def entry_fields(entry, key):
    return {"subscription": partition(key), "source_text": entry.source_text,
            "source_markup": entry.source_markup, "source_text_complete": False,
            "source_text_truncated": entry.text_truncated, "pub_ts": entry.published_ts,
            "updated_ts": entry.updated_ts, "referenced_urls": list(entry.references),
            "metadata": {"body_field": entry.body_field}}


def entry_time(entry):
    dates = [v for v in (entry.published_ts, entry.updated_ts) if v is not None]
    return max(dates) if dates else None


def success_report(key, items, dates):
    watermark = max(dates) if dates and all(date is not None for date in dates) else None
    return SubscriptionReport(key, "success" if items else "empty", len(items), watermark=watermark)
