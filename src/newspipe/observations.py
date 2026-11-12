"""Immutable original evidence; no acquisition, model or delivery dependencies."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import unicodedata
from urllib.parse import unquote, urlsplit, urlunsplit

from newspipe.source_contract import SourceContractError, identity_key

EXTRACTOR_VERSION = "source-text-v1"
KINDS = frozenset({"article", "post", "comment", "release", "activity", "snapshot", "unknown"})


def _json(value) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8").decode("utf-8")
    except (TypeError, ValueError, OverflowError, UnicodeError, RecursionError):
        raise SourceContractError("Observation evidence must be JSON-safe UTF-8.") from None


def _hash(value) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _text(value) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise SourceContractError("Original evidence fields must be text.")
    return unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))


def _facts(value):
    if isinstance(value, str):
        return _text(value)
    if isinstance(value, list):
        return [_facts(v) for v in value]
    if isinstance(value, dict):
        if any(not isinstance(k, str) for k in value):
            raise SourceContractError("Structured fact keys must be text.")
        return {k: _facts(value[k]) for k in sorted(value)}
    return value


def canonical_reference(url: str, role: str) -> str:
    """Comparison key only; never resolve redirects or export it as a source link."""
    if not isinstance(url, str) or not isinstance(role, str) or not role:
        raise SourceContractError("A reference needs a text role and URL.")
    try:
        parts = urlsplit(url)
        if parts.scheme.lower() not in {"http", "https"}:
            return _json([role, url])
        if not parts.hostname or any(ord(c) < 32 for c in url):
            raise ValueError
        host = parts.hostname.lower()
        host = "[" + host + "]" if ":" in host else host
        port = parts.port
        if port is not None and (parts.scheme.lower(), port) not in {("http", 80), ("https", 443)}:
            host += ":" + str(port)
        if "@" in parts.netloc:
            host = parts.netloc.rsplit("@", 1)[0] + "@" + host
        query = "&".join(part for part in parts.query.split("&")
                         if not (unquote(part.split("=", 1)[0]).lower().startswith("utm_")
                                 or unquote(part.split("=", 1)[0]).lower() in {"fbclid", "gclid"}))
        return _json([role, urlunsplit((parts.scheme.lower(), host, parts.path, query, parts.fragment))])
    except (ValueError, UnicodeError):
        raise SourceContractError("A source reference URL is malformed.") from None


@dataclass(frozen=True)
class SourceReference:
    role: str
    url: str


@dataclass(frozen=True)
class SourceEvidence:
    title: str
    text: str
    facts_json: str
    references: tuple[SourceReference, ...]
    permalink: str
    text_complete: bool


@dataclass(frozen=True)
class ObservationDraft:
    observation_id: str
    identity_hash: str
    version_hash: str
    source: str
    adapter: str
    partition: str
    native_id: str | int
    kind: str
    evidence: SourceEvidence
    original_json: str
    extractor_version: str
    pub_ts: float | None
    observed_ts: float | None
    copy_hash: str


def _timestamp(value) -> float | None:
    if value is None:
        return None
    try:
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError
        return float(value)
    except (ValueError, OverflowError):
        raise SourceContractError("Observation timestamps must be finite, nonnegative or unknown.") from None


def prepare_observation(source: str, adapter: str, item: dict) -> ObservationDraft:
    if not isinstance(item, dict) or not isinstance(source, str) or not source or not isinstance(adapter, str) or not adapter:
        raise SourceContractError("An observation needs source, adapter and original item.")
    native = item.get("ext_id")
    if type(native) not in (str, int) or not identity_key(native).strip():
        raise SourceContractError("An observation needs a stable native identity.")
    original = _json(item)
    partition, kind = item.get("subscription", ""), item.get("content_kind", "unknown")
    if not isinstance(partition, str) or not isinstance(kind, str):
        raise SourceContractError("Subscription and content kind must be text.")
    kind = kind if kind in KINDS else "unknown"
    facts = item.get("facts", {})
    if not isinstance(facts, dict):
        raise SourceContractError("Structured facts must be a mapping.")
    references = item.get("referenced_urls", [])
    if not isinstance(references, list):
        raise SourceContractError("Referenced URLs must be a list of typed references.")
    refs = []
    for ref in references:
        if not isinstance(ref, dict) or not isinstance(ref.get("url"), str):
            raise SourceContractError("A reference needs a text role and URL.")
        canonical_reference(ref["url"], ref.get("role"))
        refs.append(SourceReference(ref["role"], ref["url"]))
    complete = item.get("source_text_complete", False)
    if type(complete) is not bool:
        raise SourceContractError("Source-text completeness must be an explicit boolean.")
    evidence = SourceEvidence(_text(item.get("title")),
                              _text(item.get("source_text", item.get("summary"))),
                              _json(_facts(facts)), tuple(refs),
                              _text(item.get("original_url") or item.get("url")),
                              complete and bool(item.get("source_text")))
    identity = _hash([source, adapter, partition, identity_key(native)])
    content = [kind, evidence.title, evidence.text, evidence.facts_json,
               sorted(canonical_reference(r.url, r.role) for r in refs), evidence.text_complete]
    version = _hash([EXTRACTOR_VERSION, identity, content, canonical_reference(evidence.permalink, "permalink")])
    eligible = kind == "article" and evidence.text_complete and sum(not c.isspace() for c in evidence.text) >= 160
    return ObservationDraft("ob_" + _hash([identity, version]), identity, version,
                            source, adapter, partition, native, kind, evidence, original,
                            EXTRACTOR_VERSION, _timestamp(item.get("pub_ts")),
                            _timestamp(item.get("observed_ts")), _hash(content) if eligible else "")
