"""Bounded public GitHub activity/releases; no state, model or linked-page fetches."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import json
import os
import re
from urllib.parse import quote, urlencode, urlsplit
import urllib.error

from newspipe import feedparse, source_registry
from newspipe.adapters import _common
from newspipe.errors import ConfigError
from newspipe.net import request
from newspipe.source_contract import FetchResult, SubscriptionReport, native_identity


class _MissingToken(Exception):
    pass


class _NonPublicRepository(Exception):
    pass


EVENT_FIELDS = {
    "PushEvent": ("ref", "head", "before", "size", "distinct_size", "commits"),
    "CreateEvent": ("ref", "ref_type"), "DeleteEvent": ("ref", "ref_type"),
    "CommitCommentEvent": ("comment",), "IssueCommentEvent": ("action", "issue", "comment"),
    "IssuesEvent": ("action", "issue"), "PullRequestEvent": ("action", "pull_request"),
    "PullRequestReviewEvent": ("action", "pull_request", "review"),
    "PullRequestReviewCommentEvent": ("action", "pull_request", "comment"),
    "PullRequestReviewThreadEvent": ("action", "pull_request"),
    "ReleaseEvent": ("action", "release"), "DiscussionEvent": ("action", "discussion"),
    "MemberEvent": ("action",), "WatchEvent": ("action",),
}


def _settings(cfg):
    settings = cfg.get("settings", {})
    if not isinstance(settings, dict):
        raise ConfigError("GitHub 参数需要映射")
    values = {}
    for field in source_registry.describe_adapter("github").fields:
        key = field["path"].split(".")[1]
        value = settings.get(key, field["default"])
        try:
            source_registry.validate_value(value, field)
        except ValueError:
            raise ConfigError("GitHub 参数格式或范围无效；检查用户名、owner/repo、页数及 token 环境变量名") from None
        values[key] = value
    partitions = [("user", v.lower()) for v in values["users"]]
    partitions += [("repo", v.lower()) for v in values["repositories"]]
    if not partitions or len(partitions) > 100 or any(v.rsplit("/", 1)[-1] in {".", ".."} for _, v in partitions):
        raise ConfigError("GitHub 需要合计 1..100 个公开用户或仓库订阅")
    partitions = list(dict.fromkeys(partitions))
    env = values["token_env"]
    token = os.environ.get(env, "").strip() if env else ""
    if env and (not token or len(token) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in token)):
        raise _MissingToken()
    return partitions, values["max_pages"], token


def _stamp(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Invalid provider timestamp.")
    date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if date.tzinfo is None or date.timestamp() < 0:
        raise ValueError("Invalid provider timestamp.")
    return date.timestamp()


def _text(data, key, default=""):
    value = data.get(key, default)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("Invalid provider text.")
    return value


def _object(data, key):
    value = data.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("Invalid provider object.")
    return value


def _number(value):
    if type(value) is not int or value < 0:
        raise ValueError("Invalid provider number.")
    return value


def _native(value):
    if type(value) not in (str, int) or not str(value) or type(value) is int and value < 0:
        raise ValueError("Missing provider identity.")
    return value


def _permalink(value):
    if not value:
        return ""
    parts = urlsplit(value)
    if (parts.scheme != "https" or parts.hostname != "github.com" or parts.port not in (None, 443)
            or parts.username is not None or parts.password is not None
            or any(ord(c) < 32 for c in value)):
        raise ValueError("Invalid GitHub permalink.")
    return value


def _evidence(body):
    # Markdown stays original source evidence, not a rendered/full linked article.
    text, raw, truncated, _ = feedparse.text_evidence(body, plain=True)
    return text, raw, truncated


def _release(row, repository, key):
    if type(row.get("draft")) is not bool or type(row.get("prerelease")) is not bool:
        raise ValueError("Invalid release visibility.")
    if row["draft"]:
        return None
    native = _native(row.get("id"))
    tag = _text(row, "tag_name")
    if not tag:
        raise ValueError("Missing release tag.")
    title = _text(row, "name") or tag
    body = _text(row, "body")
    text, raw, truncated = _evidence(body)
    permalink = _permalink(_text(row, "html_url"))
    if not permalink:
        raise ValueError("Missing release permalink.")
    repo_url = "https://github.com/" + repository
    author = _text(_object(row, "author"), "login")[:500]
    return {"ext_id": native_identity("github", str(native), partition="release"),
            "identity_version": "github-native-v1", "title": title[:500], "url": permalink,
            "original_url": permalink, "source": "GitHub · " + repository, "category": "github",
            "summary": text[:600], "score": None, "subscription": key, "content_kind": "release",
            "pub_ts": _stamp(row.get("published_at")), "updated_ts": _stamp(row.get("updated_at")),
            "source_text": text, "source_markup": raw, "source_text_complete": bool(body),
            "source_text_truncated": truncated or len(title) > 500,
            "referenced_urls": [{"role": "repository", "url": repo_url}],
            "facts": {"repository": repository, "tag": tag[:500], "author": author,
                      "prerelease": row["prerelease"], "target": _text(row, "target_commitish")[:500]},
            "metadata": {"native_id": native, "permalink_available": True}}


def _activity(row, key):
    if type(row.get("public")) is not bool:
        raise ValueError("Missing event visibility.")
    if not row["public"]:
        return None
    native, kind = _native(row.get("id")), _text(row, "type")
    repo = _text(_object(row, "repo"), "name")
    pattern = source_registry.describe_adapter("github").fields[1]["item_pattern"]
    if not kind or not re.fullmatch(pattern, repo) or repo.rsplit("/", 1)[-1] in {".", ".."}:
        raise ValueError("Missing public event repository/type.")
    repo_url = "https://github.com/" + repo
    actor = _text(_object(row, "actor"), "login")[:500]
    original_payload = _object(row, "payload")
    payload = {k: v for k, v in original_payload.items() if k in EVENT_FIELDS.get(kind, ())}
    if kind == "ReleaseEvent":
        nested = _object(payload, "release")
        if type(nested.get("draft")) is not bool:
            raise ValueError("Unknown nested release visibility.")
        if nested["draft"]:
            return None
    facts = {"event_type": kind, "repository": repo, "author": actor}
    for field in ("action", "ref", "ref_type", "head", "before"):
        if field in payload:
            facts[field] = _text(payload, field)[:1000]
    for field in ("size", "distinct_size"):
        if field in payload:
            facts[field] = _number(payload[field])
    body, permalink, clipped = "", "", False
    for field in ("issue", "pull_request", "discussion", "release", "comment", "review"):
        if field not in payload:
            continue
        obj = _object(payload, field)
        detail = {}
        for name in ("id", "number"):
            if name in obj:
                detail[name] = _number(obj[name])
        for name in ("title", "state", "tag_name"):
            if name in obj:
                value = _text(obj, name)
                detail[name] = value[:1000]
                clipped |= len(value) > 1000
        facts[field] = detail
        if "body" in obj:
            body = _text(obj, "body")
        link = _permalink(_text(obj, "html_url"))
        if link:
            permalink = link
    if "commits" in payload:
        commits = payload["commits"]
        if not isinstance(commits, list) or any(not isinstance(c, dict) for c in commits):
            raise ValueError("Invalid commit evidence.")
        facts["commits"] = [{"sha": _text(c, "sha")[:100], "message": _text(c, "message")[:2000]}
                            for c in commits[:20]]
        body = "\n\n".join(_text(c, "message") for c in commits[:20])
        clipped |= len(commits) > 20 or any(len(_text(c, "message")) > 2000 for c in commits[:20])
    text, raw, truncated = _evidence(body)
    title = f"{actor} · {kind} · {repo}"
    return {"ext_id": native_identity("github", str(native), partition="activity"),
            "identity_version": "github-native-v1", "title": title[:500], "url": permalink or repo_url,
            "original_url": permalink, "source": "GitHub · " + repo, "category": "github",
            "summary": text[:600], "score": None, "subscription": key, "content_kind": "activity",
            "pub_ts": _stamp(row.get("created_at")), "source_text": text, "source_markup": raw,
            "source_text_complete": False, "source_text_truncated": truncated or clipped or len(title) > 500,
            "referenced_urls": [{"role": "repository", "url": repo_url}], "facts": facts,
            "metadata": {"native_id": native, "permalink_available": bool(permalink)}}


def _quota(headers):
    headers = {k.lower(): v for k, v in (headers or {}).items()}
    out = {}
    for header, key in (("x-ratelimit-remaining", "rate_remaining"), ("x-ratelimit-limit", "rate_limit"),
                        ("x-ratelimit-reset", "rate_reset_ts")):
        value = headers.get(header)
        if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 10:
            number = int(value)
            if number <= 2**31 - 1 or key == "rate_reset_ts":
                out[key] = number
    return out


def fetch_partitions(cfg: dict | None = None, since: float | None = None):
    try:
        subscriptions, pages, token = _settings(cfg or {})
    except _MissingToken:
        yield FetchResult([], (SubscriptionReport("github", "failure", code="credentials"),))
        return
    except ConfigError as error:
        yield _common.configuration_failure("github", error)
        return
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = "Bearer " + token
    for kind, name in subscriptions:
        key = kind + ":" + name
        diagnostic = {"credential_mode": "token" if token else "anonymous"}
        try:
            path = ("users/" + quote(name, safe="") + "/events/public" if kind == "user" else
                    "repos/" + "/".join(quote(part, safe="") for part in name.split("/")) + "/releases")
            if kind == "repo" and token:
                response_headers = {}
                raw = request("https://api.github.com/" + path.removesuffix("/releases"),
                              headers=headers, timeout=20, max_bytes=feedparse.MAX_BODY,
                              max_redirects=0, response_headers=response_headers)
                diagnostic.update(_quota(response_headers))
                repository = json.loads(raw.decode("utf-8"))
                if (not isinstance(repository, dict) or repository.get("private") is not False
                        or repository.get("visibility") not in (None, "public")):
                    raise _NonPublicRepository()
            items, dates, seen = [], [], set()
            for page in range(1, pages + 1):
                response_headers = {}
                raw = request("https://api.github.com/" + path + "?" + urlencode({"per_page": 100, "page": page}),
                              headers=headers, timeout=20, max_bytes=feedparse.MAX_BODY,
                              max_redirects=0, response_headers=response_headers)
                diagnostic.update(_quota(response_headers))
                rows = json.loads(raw.decode("utf-8"))
                if not isinstance(rows, list) or len(rows) > 100 or any(not isinstance(r, dict) for r in rows):
                    raise ValueError("Invalid provider result.")
                for row in rows:
                    item = _activity(row, key) if kind == "user" else _release(row, name, key)
                    if item is None:
                        continue
                    stamp = max((v for v in (item.get("pub_ts"), item.get("updated_ts")) if v is not None), default=None)
                    dates.append(stamp)
                    if stamp is not None and since is not None and stamp < since or item["ext_id"] in seen:
                        continue
                    seen.add(item["ext_id"])
                    items.append(item)
                if len(rows) < 100:
                    break
            diagnostic["window_limited"] = len(rows) == 100
            report = _common.success_report(key, items, dates)
            if diagnostic["window_limited"]:
                report = replace(report, watermark=None)
            yield FetchResult(items, (replace(report, diagnostics=diagnostic),))
        except Exception as error:
            code = _common.issue_code(error)
            if isinstance(error, _NonPublicRepository):
                code = "permission"
            if isinstance(error, urllib.error.HTTPError):
                quota = _quota(error.headers)
                diagnostic.update(quota)
                if error.code == 403 and (quota.get("rate_remaining") == 0 or (error.headers or {}).get("Retry-After")):
                    code = "rate_limit"
                elif error.code == 404:
                    code = "permission"
            yield FetchResult([], (SubscriptionReport(key, "failure", code=code, diagnostics=diagnostic),))


def fetch(cfg: dict | None = None, since: float | None = None) -> FetchResult:
    items, reports = [], []
    for part in fetch_partitions(cfg, since=since):
        items.extend(part.items)
        reports.extend(part.reports)
    return FetchResult(items, tuple(reports))
