"""Pure, backward-compatible acquisition results and safe public diagnostics."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import re

ISSUE_CODES = frozenset({"", "network", "timeout", "rate_limit", "permission",
                         "credentials", "invalid_response", "configuration",
                         "unsupported", "incomplete"})
MAX_DIAGNOSTIC_REPORTS = 100


class SourceContractError(ValueError):
    """An adapter result cannot safely be persisted or interpreted."""


def identity_key(value) -> str:
    """Comparison-only key; durable native identities retain their original value/type."""
    return "" if value is None else str(value)


def _finite_number(value) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _timestamp(value) -> bool:
    return value is None or _finite_number(value) and value >= 0


@dataclass(frozen=True)
class SubscriptionReport:
    key: str
    status: str
    fetched: int = 0
    code: str = ""
    watermark: float | None = None


def _validate_reports(reports: tuple[SubscriptionReport, ...]) -> None:
    if not isinstance(reports, tuple) or not reports:
        raise SourceContractError("Source reports must identify at least one subscription.")
    keys: set[str] = set()
    for report in reports:
        if (not isinstance(report, SubscriptionReport) or not isinstance(report.key, str)
                or not report.key or report.key in keys
                or not isinstance(report.status, str)
                or report.status not in {"success", "empty", "failure"}
                or type(report.fetched) is not int or report.fetched < 0
                or not isinstance(report.code, str) or report.code not in ISSUE_CODES
                or not _timestamp(report.watermark)):
            raise SourceContractError("Invalid subscription report; use documented status and error codes.")
        try:
            report.key.encode("utf-8")
        except UnicodeError:
            raise SourceContractError("Subscription keys must be valid UTF-8 text.") from None
        if (report.status in {"empty", "failure"} and report.fetched
                or report.status != "failure" and report.code
                or report.status == "failure" and report.watermark is not None):
            raise SourceContractError("Subscription counts, error code and watermark contradict its status.")
        keys.add(report.key)


@dataclass(frozen=True)
class FetchResult:
    items: list[dict]
    reports: tuple[SubscriptionReport, ...]
    reported: bool = True

    @property
    def status(self) -> str:
        _validate_reports(self.reports)
        failed = sum(report.status == "failure" for report in self.reports)
        if failed:
            return "failure" if failed == len(self.reports) else "partial_failure"
        return "success" if self.items else "empty"

    @property
    def complete(self) -> bool:
        return self.status not in {"failure", "partial_failure"}

    def summary(self) -> dict:
        """Only categorical diagnostics leave the source boundary; never raw URLs."""
        status = self.status
        out = {"contract_version": 1, "status": status, "fetched": len(self.items),
                "successful": sum(report.status != "failure" for report in self.reports),
                "failed": sum(report.status == "failure" for report in self.reports),
                "reports": [{"key": "subscription-" + hashlib.sha256(report.key.encode()).hexdigest()[:16],
                             "index": index + 1, "status": report.status,
                             "fetched": report.fetched, "code": report.code}
                            for index, report in enumerate(self.reports[:MAX_DIAGNOSTIC_REPORTS])]}
        if len(self.reports) > MAX_DIAGNOSTIC_REPORTS:
            out["omitted_reports"] = len(self.reports) - MAX_DIAGNOSTIC_REPORTS
        return out


def safe_summary(value: dict) -> dict:
    """Revalidate stored diagnostics and discard unknown fields before public export."""
    if not isinstance(value, dict) or value.get("contract_version") != 1:
        raise SourceContractError("Invalid collection diagnostic contract.")
    counts = [value.get(key) for key in ("fetched", "successful", "failed")]
    omitted = value.get("omitted_reports", 0)
    rows = value.get("reports")
    if (any(type(v) is not int or v < 0 for v in [*counts, omitted])
            or not isinstance(rows, list) or not 1 <= len(rows) <= MAX_DIAGNOSTIC_REPORTS
            or counts[1] + counts[2] != len(rows) + omitted):
        raise SourceContractError("Invalid collection diagnostic counts.")
    reports = []
    safe_rows = []
    for index, row in enumerate(rows):
        if (not isinstance(row, dict) or not isinstance(row.get("key"), str)
                or not re.fullmatch(r"subscription-[0-9a-f]{16}", row["key"])
                or type(row.get("index")) is not int or row["index"] != index + 1):
            raise SourceContractError("Invalid public subscription diagnostic.")
        reports.append(SubscriptionReport(row["key"], row.get("status"), row.get("fetched"), row.get("code")))
        safe_rows.append({k: row[k] for k in ("key", "index", "status", "fetched", "code")})
    _validate_reports(tuple(reports))
    status = ("failure" if counts[2] and not counts[1] else "partial_failure" if counts[2]
              else "success" if counts[0] else "empty")
    if (value.get("status") != status or status == "failure" and counts[0]
            or not omitted and (sum(r.fetched for r in reports) != counts[0]
                                or sum(r.status == "failure" for r in reports) != counts[2])):
        raise SourceContractError("Collection diagnostic status contradicts its counts.")
    out = {"contract_version": 1, "status": status, "fetched": counts[0],
           "successful": counts[1], "failed": counts[2], "reports": safe_rows}
    if omitted:
        out["omitted_reports"] = omitted
    return out


def normalize_fetch(value, *, source: str, adapter: str) -> FetchResult:
    """Accept old list/generator adapters without rewriting their native identities."""
    reported = isinstance(value, FetchResult) and value.reported
    raw = value.items if isinstance(value, FetchResult) else value
    if not isinstance(raw, Iterable) or isinstance(raw, (Mapping, str, bytes, bytearray)):
        raise SourceContractError("Source fetch must return items or FetchResult, not a response envelope.")
    items: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            raise SourceContractError("Source items must be dictionaries with a stable ext_id.")
        identity = item.get("ext_id")
        if (not isinstance(identity, (str, int)) or isinstance(identity, bool)
                or not str(identity).strip() or not _timestamp(item.get("pub_ts"))):
            raise SourceContractError("Source item needs a stable ext_id and a finite or unknown pub_ts.")
        if (any(item.get(key) is not None and not isinstance(item[key], str)
                for key in ("title", "url", "original_url", "source", "summary", "category"))
                or item.get("score") is not None and not _finite_number(item["score"])):
            raise SourceContractError("Known source fields need text, and score needs a finite number or null.")
        try:
            json.dumps(item, allow_nan=False, ensure_ascii=False).encode("utf-8")
            items.append(deepcopy(item))
        except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError):
            raise SourceContractError("Source items must contain JSON-safe values for durable queuing.") from None
    if isinstance(value, FetchResult):
        if type(value.reported) is not bool:
            raise SourceContractError("FetchResult.reported must be a boolean.")
        reports = value.reports
        _validate_reports(reports)
        if sum(report.fetched for report in reports) != len(items):
            raise SourceContractError("Subscription item counts do not match the returned items.")
    else:
        reports = (SubscriptionReport(source, "success" if items else "empty", len(items)),)
    return FetchResult(items, reports, reported=reported)


def native_identity(adapter: str, native_id: str, *, partition: str = "") -> str:
    """New adapters use full identity hashes; existing ext_id values remain untouched."""
    if (not isinstance(adapter, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", adapter)
            or not isinstance(native_id, str) or not native_id
            or not isinstance(partition, str)):
        raise SourceContractError("Native identity needs an adapter, native ID and subscription partition.")
    try:
        payload = json.dumps([adapter, partition, native_id], ensure_ascii=False,
                             separators=(",", ":")).encode("utf-8")
    except UnicodeError:
        raise SourceContractError("Native identities must be valid UTF-8 text.") from None
    return adapter + ":" + hashlib.sha256(payload).hexdigest()
