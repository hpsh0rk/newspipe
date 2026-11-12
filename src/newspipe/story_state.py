"""Store-owned original-version ledger. No configuration, network or delivery."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid
from urllib.parse import urlsplit

from newspipe.observations import ObservationDraft, SourceEvidence, SourceReference
from newspipe.rssconnect import safe_url


class StoryStorageError(RuntimeError):
    def __init__(self, code="unavailable"):
        self.code = code
        super().__init__("Original-version history " + code + "; retry collection or inspect a backup. No history was repaired.")


@dataclass(frozen=True)
class CaptureSnapshot:
    activation_id: str | None
    capture_sequence: int
    latest_by_identity: dict[str, ObservationDraft]
    candidates: tuple[ObservationDraft, ...]
    bounds: dict
    limitations: tuple[str, ...]


SCHEMA = """
CREATE TABLE metadata (id INTEGER PRIMARY KEY, mode TEXT, revision TEXT, activation TEXT,
 frontier INTEGER NOT NULL DEFAULT 0, observations INTEGER NOT NULL DEFAULT 0, captures INTEGER NOT NULL DEFAULT 0);
INSERT INTO metadata(id) VALUES (1);
CREATE TABLE observations (sequence INTEGER PRIMARY KEY AUTOINCREMENT, observation_id TEXT NOT NULL UNIQUE,
 identity_hash TEXT NOT NULL, version_hash TEXT NOT NULL, source TEXT NOT NULL, adapter TEXT NOT NULL,
 partition TEXT NOT NULL, native_json TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL,
 body TEXT NOT NULL, facts_json TEXT NOT NULL, refs_json TEXT NOT NULL, reference_count INTEGER NOT NULL,
 permalink TEXT NOT NULL, complete INTEGER NOT NULL, original_json TEXT NOT NULL, extractor_version TEXT NOT NULL,
 pub_ts REAL, observed_ts REAL, copy_hash TEXT NOT NULL, first_seen REAL NOT NULL, origin TEXT NOT NULL);
CREATE UNIQUE INDEX identity_version ON observations(identity_hash, version_hash);
CREATE INDEX observation_source ON observations(source, sequence);
CREATE TABLE latest (identity_hash TEXT PRIMARY KEY, observation_id TEXT NOT NULL);
CREATE TABLE seen (observation_id TEXT PRIMARY KEY, last_seen REAL NOT NULL, capture_sequence INTEGER NOT NULL);
CREATE INDEX seen_time ON seen(last_seen);
CREATE TABLE captures (sequence INTEGER PRIMARY KEY, at REAL NOT NULL, mode TEXT NOT NULL,
 revision TEXT NOT NULL, activation TEXT, status TEXT NOT NULL, bounds_json TEXT NOT NULL, limitations_json TEXT NOT NULL);
CREATE TABLE capture_items (capture_sequence INTEGER NOT NULL, observation_id TEXT NOT NULL, baseline_id TEXT,
 baseline_reason TEXT NOT NULL, PRIMARY KEY(capture_sequence, observation_id));
CREATE INDEX item_captures ON capture_items(observation_id, capture_sequence);
CREATE TABLE proposals (capture_sequence INTEGER NOT NULL, current_id TEXT NOT NULL, previous_id TEXT NOT NULL,
 relation TEXT NOT NULL, score REAL NOT NULL, reason TEXT NOT NULL, fields_json TEXT NOT NULL, matcher_version TEXT NOT NULL,
 PRIMARY KEY(capture_sequence, current_id, previous_id));
PRAGMA user_version=1;
"""


def _pagination(after, limit):
    if type(after) is not int or not 0 <= after <= 9223372036854775807 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Pagination requires a nonnegative SQLite integer cursor and limit 1..100.")


def _reference(url, role="permalink", *, truncated=False):
    if truncated:
        return {"role": "reference", "url": "[source URL truncated]", "redacted": True, "clickable": False}
    shown = safe_url(url)
    return {"role": role if role in {"permalink", "article", "comment", "repository", "discussion", "release"} else "reference",
            "url": shown, "redacted": shown != url,
            "clickable": bool(shown and shown == url and urlsplit(url).scheme in {"http", "https"})}


def _text(text, limit=20000):
    text = text[:limit]
    text = re.sub(r"https?://[^\s<>\"\\]+", lambda m: safe_url(m[0]), text, flags=re.IGNORECASE)
    return re.sub(r"(?im)\b(authorization|cookie|api[-_]key)[\"']?\s*:\s*[^\n]+", r"\1: [redacted]", text)


def _facts_text(text, *, truncated=False):
    # Never parse a clipped JSON prefix or expose unredacted keys from it.
    if truncated:
        return "[structured facts display omitted: stored original exceeds 20,000 characters]"
    sensitive = {"authorization", "cookie", "setcookie", "apikey", "accesskey", "accesstoken",
                 "refreshtoken", "token", "password", "secret", "clientsecret", "appsecret"}

    def redact(value, depth=0):
        if depth > 16:
            return "[nested facts omitted]"
        if isinstance(value, dict):
            return {_text(key): "[redacted]" if re.sub(r"[-_\s]", "", key).casefold() in sensitive
                    else redact(item, depth + 1) for key, item in value.items()}
        if isinstance(value, list):
            return [redact(item, depth + 1) for item in value]
        return _text(value) if isinstance(value, str) else value

    try:
        shown = json.dumps(redact(json.loads(text)), ensure_ascii=False, separators=(",", ":"))
        return shown if len(shown) <= 20000 else "[structured facts display omitted: safe projection exceeds 20,000 characters]"
    except (ValueError, RecursionError):
        return "[structured facts cannot be safely displayed; original is retained]"


class Ledger:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def _connection(self, *, write=False):
        conn = None
        try:
            existed = self.path.exists()
            if not existed and not write:
                yield None
                return
            if write:
                self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                self.path.parent.chmod(0o700)
                if not existed:
                    descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
                    os.close(descriptor)
                self.path.chmod(0o600)
            conn = sqlite3.connect(str(self.path) if write else self.path.resolve().as_uri() + "?mode=ro",
                                   uri=not write, timeout=0.25, isolation_level=None)
            conn.row_factory = sqlite3.Row
            if write and not existed:
                conn.execute("PRAGMA journal_mode=DELETE")
                conn.executescript(SCHEMA)
            if not write:
                conn.execute("BEGIN")
            if conn.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise StoryStorageError("version")
            yield conn
        except StoryStorageError:
            raise
        except sqlite3.Error as error:
            code = getattr(error, "sqlite_errorcode", 0) & 255
            raise StoryStorageError("locked" if code in {5, 6} else "corrupt" if code in {11, 26, 1} else "unavailable") from None
        except (ValueError, TypeError, KeyError, OSError):
            raise StoryStorageError("corrupt" if not write else "unavailable") from None
        finally:
            if conn:
                conn.close()

    @staticmethod
    def _metadata(conn):
        return dict(conn.execute("SELECT * FROM metadata WHERE id=1").fetchone()) if conn else {
            "mode": None, "revision": None, "activation": None, "frontier": 0, "observations": 0, "captures": 0}

    def status(self):
        with self._connection() as conn:
            m = self._metadata(conn)
            return {"contract_version": 1, "availability": "available" if conn else "not_initialized",
                    "activated_mode": m["mode"], "activated_revision": m["revision"],
                    "activation_id": m["activation"], "frontier_sequence": m["frontier"],
                    "observation_count": m["observations"], "capture_count": m["captures"]}

    @staticmethod
    def _select():
        return ("SELECT o.sequence,o.observation_id,o.identity_hash,o.version_hash,o.source,o.adapter,o.partition,"
                "o.native_json,o.kind,substr(o.title,1,500) AS title,substr(o.body,1,20000) AS body,"
                "length(o.body) AS body_length,substr(o.facts_json,1,20000) AS facts_json,"
                "length(o.facts_json) AS facts_length,o.refs_json,o.reference_count,substr(o.permalink,1,2048) AS permalink,length(o.permalink) AS permalink_length,"
                "o.complete,o.extractor_version,o.pub_ts,o.observed_ts,o.copy_hash,o.first_seen,o.origin FROM observations o ")

    @staticmethod
    def _draft(row):
        evidence = SourceEvidence(row["title"], row["body"], row["facts_json"],
                                  tuple(SourceReference(r["role"], r["url"]) for r in json.loads(row["refs_json"])),
                                  row["permalink"], bool(row["complete"]))
        return ObservationDraft(row["observation_id"], row["identity_hash"], row["version_hash"],
                                row["source"], row["adapter"], row["partition"], json.loads(row["native_json"]),
                                row["kind"], evidence, "", row["extractor_version"], row["pub_ts"],
                                row["observed_ts"], row["copy_hash"])

    @staticmethod
    def _public(row):
        return {"sequence": row["sequence"], "observation_id": row["observation_id"],
                "identity_hash": row["identity_hash"], "version_hash": row["version_hash"],
                "source": row["source"], "adapter": row["adapter"], "kind": row["kind"],
                "extractor_version": row["extractor_version"], "pub_ts": row["pub_ts"],
                "observed_ts": row["observed_ts"], "first_seen": row["first_seen"], "origin": row["origin"],
                "evidence": {"title": _text(row["title"], 500), "text": _text(row["body"]),
                             "facts_text": _facts_text(row["facts_json"], truncated=row["facts_length"] > 20000),
                             "text_complete": bool(row["complete"]),
                             "text_truncated": row["body_length"] > 20000,
                             "facts_truncated": row["facts_length"] > 20000,
                             "references": [_reference(r["url"], r["role"], truncated=r.get("truncated", False)) for r in json.loads(row["refs_json"])],
                             "references_truncated": row["reference_count"] > 20,
                             "permalink": _reference(row["permalink"], truncated=row["permalink_length"] > 2048)}}

    def observations(self, *, after=0, limit=50, source=""):
        _pagination(after, limit)
        with self._connection() as conn:
            rows = conn.execute(self._select() + "WHERE sequence>? AND (?='' OR source=?) ORDER BY sequence LIMIT ?",
                                (after, source, source, limit + 1)).fetchall() if conn else []
            return {"contract_version": 1, "availability": "available" if conn else "not_initialized",
                    "observations": [self._public(r) for r in rows[:limit]], "has_more": len(rows) > limit,
                    "next_after": rows[limit - 1]["sequence"] if len(rows) > limit else None}

    def observation(self, observation_id):
        with self._connection() as conn:
            row = conn.execute(self._select() + "WHERE observation_id=?", (observation_id,)).fetchone() if conn else None
            return self._public(row) if row else None

    def snapshot(self, drafts, *, at, window_days, candidate_limit, scan_limit):
        if (type(window_days) is not int or not 1 <= window_days <= 90 or type(candidate_limit) is not int
                or not 1 <= candidate_limit <= 20 or type(scan_limit) is not int or not 50 <= scan_limit <= 2000):
            raise ValueError("Invalid local retrieval bounds.")
        with self._connection() as conn:
            m = self._metadata(conn)
            latest = {}
            if conn:
                for draft in drafts:
                    row = conn.execute(self._select() + "JOIN latest l ON l.observation_id=o.observation_id WHERE l.identity_hash=?",
                                       (draft.identity_hash,)).fetchone()
                    if row:
                        latest[draft.identity_hash] = self._draft(row)
                rows = conn.execute(self._select() + "JOIN seen s ON s.observation_id=o.observation_id WHERE s.last_seen>=? AND o.origin='source' ORDER BY s.capture_sequence DESC,o.sequence DESC LIMIT ?",
                                    ((at - timedelta(days=window_days)).timestamp(), scan_limit + 1)).fetchall()
            else:
                rows = []
            bounds = {"window_days": window_days, "candidate_limit": candidate_limit, "scan_limit": scan_limit,
                      "scanned_count": min(len(rows), scan_limit), "text_limit": 20000, "term_limit": 256,
                      "as_of_capture_sequence": m["captures"]}
            return CaptureSnapshot(m["activation"], m["captures"], latest,
                                   tuple(self._draft(r) for r in rows[:scan_limit]), bounds,
                                   ("scan_limit_reached",) if len(rows) > scan_limit else ())

    def _insert_observation(self, conn, draft, at, origin):
        e = draft.evidence
        params = (draft.observation_id, draft.identity_hash, draft.version_hash, draft.source, draft.adapter,
                  draft.partition, json.dumps(draft.native_id), draft.kind, e.title, e.text, e.facts_json,
                  json.dumps([{"role": r.role, "url": r.url[:2048], "truncated": len(r.url) > 2048} for r in e.references[:20]]), len(e.references),
                  e.permalink, int(e.text_complete), draft.original_json, draft.extractor_version,
                  draft.pub_ts, draft.observed_ts, draft.copy_hash, at.timestamp(), origin)
        return conn.execute("INSERT OR IGNORE INTO observations(observation_id,identity_hash,version_hash,source,adapter,partition,native_json,kind,title,body,facts_json,refs_json,reference_count,permalink,complete,original_json,extractor_version,pub_ts,observed_ts,copy_hash,first_seen,origin) VALUES (" + ",".join(["?"] * len(params)) + ")", params).rowcount

    def record(self, drafts, *, at, mode, config_revision, current_revision, expected_activation_id, snapshot, proposals, baselines):
        if mode == "off" and not self.path.exists():
            return {"contract_version": 1, "status": "off", "inserted": 0, "repeated": 0, "proposal_count": 0}
        with self._connection(write=True) as conn:
            conn.execute("BEGIN IMMEDIATE")
            m = self._metadata(conn)
            status, activation, frontier = "recorded", m["activation"], m["frontier"]
            if current_revision != config_revision or expected_activation_id != activation:
                status = "stale_settings"
            else:
                for draft in drafts:
                    current = conn.execute("SELECT observation_id FROM latest WHERE identity_hash=?", (draft.identity_hash,)).fetchone()
                    old = snapshot.latest_by_identity.get(draft.identity_hash)
                    if (current[0] if current else None) != (old.observation_id if old else None):
                        status = "stale_history"
                        break
            sequence = m["captures"] + 1
            if status == "recorded" and (m["mode"] != mode or m["revision"] != config_revision):
                activation, frontier = "act_" + uuid.uuid4().hex, sequence
            inserted, baseline_inserted, repeated = 0, 0, 0
            limitations = set(snapshot.limitations)
            blocked, item_rows = set(), []
            # Stale originals still persist, but only uncontested identities may advance.
            expected_latest = {identity: draft.observation_id for identity, draft in snapshot.latest_by_identity.items()}
            for draft in drafts if mode == "observe" else []:
                old = snapshot.latest_by_identity.get(draft.identity_hash)
                baseline = baselines.get(str(draft.native_id), {}) if old is None else {}
                material = baseline.get("draft")
                reason = baseline.get("availability", "")
                if baseline.get("completed") and (material is None or material.identity_hash != draft.identity_hash):
                    limitations.add("migration_baseline_missing_material")
                    blocked.add(draft.observation_id)
                    reason = "migration_baseline"
                    material = None
                if material is not None:
                    baseline_inserted += self._insert_observation(conn, material, at, "legacy_original")
                added = self._insert_observation(conn, draft, at, "source")
                inserted += added
                repeated += not added
                conn.execute("INSERT INTO seen VALUES (?,?,?) ON CONFLICT(observation_id) DO UPDATE SET last_seen=max(last_seen,excluded.last_seen),capture_sequence=excluded.capture_sequence",
                             (draft.observation_id, at.timestamp(), sequence))
                expected = expected_latest.get(draft.identity_hash)
                if expected is None:
                    updated = conn.execute("INSERT OR IGNORE INTO latest VALUES (?,?)", (draft.identity_hash, draft.observation_id))
                else:
                    updated = conn.execute("UPDATE latest SET observation_id=? WHERE identity_hash=? AND observation_id=?",
                                           (draft.observation_id, draft.identity_hash, expected))
                if updated.rowcount:
                    expected_latest[draft.identity_hash] = draft.observation_id
                item_rows.append((sequence, draft.observation_id, old.observation_id if old else material.observation_id if material else None, reason))
            accepted = [p for p in proposals if status == "recorded" and mode == "observe" and p["current_id"] not in blocked]
            conn.execute("INSERT INTO captures VALUES (?,?,?,?,?,?,?,?)", (sequence, at.timestamp(), mode, config_revision, activation, status,
                         json.dumps(snapshot.bounds), json.dumps(sorted(limitations))))
            conn.executemany("INSERT OR IGNORE INTO capture_items VALUES (?,?,?,?)", item_rows)
            for p in accepted:
                conn.execute("INSERT OR IGNORE INTO proposals VALUES (?,?,?,?,?,?,?,?)", (sequence, p["current_id"], p["previous_id"], p["relation"], p["score"], p["reason"], json.dumps(p["evidence_fields"]), p["matcher_version"]))
            conn.execute("UPDATE metadata SET mode=?,revision=?,activation=?,frontier=?,observations=?,captures=? WHERE id=1",
                         (mode if status == "recorded" else m["mode"], config_revision if status == "recorded" else m["revision"],
                          activation, frontier, m["observations"] + inserted + baseline_inserted, sequence))
            conn.commit()
            return {"contract_version": 1, "status": status, "capture_sequence": sequence, "activation_id": activation,
                    "inserted": inserted, "repeated": repeated, "proposal_count": len(accepted)}

    def preview(self, *, observation_id="", after=0, limit=20):
        _pagination(after, limit)
        with self._connection() as conn:
            rows = conn.execute("SELECT c.* FROM captures c WHERE c.sequence>? AND (?='' OR EXISTS(SELECT 1 FROM capture_items i WHERE i.capture_sequence=c.sequence AND i.observation_id=?)) ORDER BY c.sequence LIMIT ?",
                                (after, observation_id, observation_id, limit + 1)).fetchall() if conn else []
            receipts = []
            for row in rows[:limit]:
                items = conn.execute("SELECT observation_id,baseline_id,baseline_reason FROM capture_items WHERE capture_sequence=? LIMIT 101", (row["sequence"],)).fetchall()
                proposals = conn.execute("SELECT current_id,previous_id,relation,score,reason,fields_json,matcher_version FROM proposals WHERE capture_sequence=? AND (?='' OR current_id=?) ORDER BY CASE relation WHEN 'unclassified_change' THEN 0 WHEN 'exact_copy' THEN 1 ELSE 2 END,current_id,score DESC,previous_id LIMIT 201", (row["sequence"], observation_id, observation_id)).fetchall()
                limitations = json.loads(row["limitations_json"])
                if len(items) > 100 or len(proposals) > 200:
                    limitations.append("receipt_display_truncated")
                receipts.append({"capture_sequence": row["sequence"], "at": row["at"], "mode": row["mode"], "revision": row["revision"],
                                 "activation_id": row["activation"], "status": row["status"], "bounds": json.loads(row["bounds_json"]),
                                 "limitations": limitations, "observations": [dict(i) for i in items[:100]],
                                 "proposals": [{**{k: p[k] for k in ("current_id", "previous_id", "relation", "score", "reason", "matcher_version")},
                                                "evidence_fields": json.loads(p["fields_json"])} for p in proposals[:200]]})
            return {"contract_version": 1, "availability": "available" if conn else "not_initialized", "receipts": receipts,
                    "has_more": len(rows) > limit, "next_after": rows[limit - 1]["sequence"] if len(rows) > limit else None}
