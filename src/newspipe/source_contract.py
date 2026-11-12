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


class SourceContractError(ValueError):
    """An adapter result cannot safely be persisted or interpreted."""


def _timestamp(value) -> bool:
    return (value is None or isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0)


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
                or report.status not in {"success", "empty", "failure"}
                or type(report.fetched) is not int or report.fetched < 0
                or not isinstance(report.code, str) or report.code not in ISSUE_CODES
                or not _timestamp(report.watermark)):
            raise SourceContractError("Invalid subscription report; use documented status and error codes.")
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
        return {"contract_version": 1, "status": status, "fetched": len(self.items),
                "successful": sum(report.status != "failure" for report in self.reports),
                "failed": sum(report.status == "failure" for report in self.reports),
                "reports": [{"key": "subscription-" + hashlib.sha256(report.key.encode()).hexdigest()[:16],
                             "index": index + 1, "status": report.status,
                             "fetched": report.fetched, "code": report.code}
                            for index, report in enumerate(self.reports)]}


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
        try:
            json.dumps(item, allow_nan=False)
            items.append(deepcopy(item))
        except (TypeError, ValueError, OverflowError, RecursionError):
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
    payload = json.dumps([adapter, partition, native_id], ensure_ascii=False,
                         separators=(",", ":")).encode("utf-8")
    return adapter + ":" + hashlib.sha256(payload).hexdigest()
