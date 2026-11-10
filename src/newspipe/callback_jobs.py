"""Durable callback inbox: fast acceptance, one worker, observable completion."""
from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from newspipe import _atomic, config, events, interaction, runtime, state
from newspipe.errors import InboundError


def folder(news_dir: Path) -> Path:
    return Path(news_dir) / "state" / "callbacks"


def receipt_path(news_dir: Path, job_id: str, status: str) -> Path:
    return folder(news_dir) / "receipts" / job_id[:2] / f"{job_id}.{status}.json"


def _fingerprint(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def _read_evidence(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("id") and data.get("state") in ("queued", "running", "done", "failed"):
            return data
    except (OSError, UnicodeError, json.JSONDecodeError):
        pass
    raise InboundError("回调去重记录损坏，拒绝覆盖或重放；请核对存储与第三方回执")


def _receipt(news_dir: Path, job_id: str) -> dict | None:
    for status in ("done", "failed"):
        receipt = _read_evidence(receipt_path(news_dir, job_id, status))
        if receipt is not None:
            if receipt.get("state") != status or receipt.get("id") != job_id:
                raise InboundError("回调去重回执身份或状态不一致，拒绝重放")
            return receipt
    return None


def _retire(news_dir: Path, path: Path, job: dict) -> None:
    # Receipt first, removal last: interruptions can leave duplicate evidence, never lose it.
    with _atomic.file_lock(folder(news_dir) / ".inbox.lock"):
        receipt = _receipt(news_dir, job["id"])
        if receipt is None:
            receipt = {key: value for key, value in job.items()
                       if key not in ("payload", "retry_at", "accepted_ns")}
            receipt["payload_hash"] = _fingerprint(job["payload"])
            if job["state"] == "failed":
                # Unknown third-party effects need their original input for reconciliation.
                receipt["payload"] = job["payload"]
            _atomic.write_json_atomic(receipt_path(news_dir, job["id"], job["state"]), receipt)
        elif receipt.get("payload_hash") != _fingerprint(job["payload"]):
            raise InboundError("回调任务与去重回执不一致，拒绝丢弃原始证据")
        path.unlink(missing_ok=True)


def enqueue(news_dir: Path, payload: dict, *, event_id: str = "") -> dict:
    identity = event_id or uuid.uuid4().hex
    job_id = hashlib.sha256(identity.encode()).hexdigest()
    path = folder(news_dir) / f"{job_id}.json"
    with _atomic.file_lock(folder(news_dir) / ".inbox.lock"):
        existing = _receipt(news_dir, job_id) or _read_evidence(path)
        if existing:
            same = (existing.get("payload_hash") == _fingerprint(payload) if "payload_hash" in existing
                    else existing.get("payload") == payload)
            if not same:
                raise InboundError("同一平台事件 ID 对应不同动作，拒绝覆盖已接收任务")
            return existing
        job = {"id": job_id, "event_id": event_id, "payload": payload, "state": "queued",
               "attempts": 0, "created_at": state.now_iso(), "accepted_ns": time.time_ns(), "retry_at": 0}
        _atomic.write_json_atomic(path, job)
        return job


def stats(news_dir: Path) -> dict[str, int]:
    result = {"queued": 0, "running": 0, "done": 0, "failed": 0}
    # Count compact receipt filenames; explicit status reads never parse historical payloads.
    for status in ("done", "failed"):
        result[status] = sum(1 for _ in (folder(news_dir) / "receipts").glob(f"*/*.{status}.json"))
    for path in folder(news_dir).glob("*.json"):
        job = state.read_json(path)
        if isinstance(job, dict) and job.get("state") in result:
            if job["state"] in ("done", "failed") and receipt_path(news_dir, job["id"], job["state"]).exists():
                continue
            result[job["state"]] += 1
    return result


def drain(news_dir: Path, *, hook_set: Any = None, config_provider=None, logger=print,
          now: float | None = None, limit: int = 20) -> int:
    now = time.time() if now is None else now
    completed = 0
    # Separate from the acceptance lock: slow jobs must never block HTTP acknowledgement.
    with _atomic.file_lock(folder(news_dir) / ".worker.lock"):
        jobs = []
        for path in folder(news_dir).glob("*.json"):
            job = state.read_json(path)
            if not isinstance(job, dict):
                continue
            if job.get("state") in ("done", "failed"):
                _retire(news_dir, path, job)  # Legacy terminal files migrate without replay.
            else:
                jobs.append((path, job))
        jobs.sort(key=lambda entry: (entry[1].get("created_at", ""), entry[1].get("accepted_ns", 0)))
        for path, job in jobs:
            if completed >= limit:
                break
            if not isinstance(job, dict) or job.get("state") not in ("queued", "running"):
                continue
            if float(job.get("retry_at") or 0) > now:
                continue
            payload = dict(job["payload"], _callback_id=job["id"])
            is_hook = payload.get("news_action") not in interaction.CORE_ACTIONS
            error = ""
            if job["state"] == "running" and is_hook:
                error = "hook 执行结果不确定；禁止自动重放，请核对第三方回执"
            else:
                job.update(state="running", attempts=int(job.get("attempts") or 0) + 1)
                _atomic.write_json_atomic(path, job)
                try:
                    hooks = hook_set() if callable(hook_set) else hook_set
                    cfg = config_provider() if config_provider is not None else None
                    if cfg is None and (Path(news_dir) / "sources.yaml").is_file():
                        cfg = config.load(news_dir)
                    if cfg is not None:
                        with runtime.activate(cfg, news_dir):
                            message = interaction.handle_json(json.dumps(payload), news_dir=news_dir,
                                                              hook_set=hooks if hooks is not None else cfg.hooks)
                    else:
                        message = interaction.handle_json(json.dumps(payload), news_dir=news_dir,
                                                          hook_set=hooks)
                    if message.startswith("⚠️"):
                        error = message
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"[:300]
            if error:
                job["state"] = "failed" if is_hook or job["attempts"] >= 3 else "queued"
                job["error"] = error
                job["retry_at"] = now + 5
                logger(f"卡片后台任务 {job['id'][:12]}：{error}")
                events.append(news_dir, "failed", payload={"job_id": job["id"], "status": job["state"],
                                                           "error": error, "action": payload.get("news_action")})
            else:
                job["state"] = "done"
                job.pop("error", None)
            job["updated_at"] = state.now_iso()
            _atomic.write_json_atomic(path, job)
            if job["state"] in ("done", "failed"):
                _retire(news_dir, path, job)
            completed += 1
    return completed


class Worker:
    def __init__(self, news_dir: Path, *, hook_set=None, config_provider=None, logger=print):
        self.news_dir, self.hook_set, self.logger = news_dir, hook_set, logger
        self.config_provider = config_provider
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, name="newspipe-callback-worker", daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=2)

    def run(self):
        while not self.stop_event.is_set():
            try:
                drain(self.news_dir, hook_set=self.hook_set, config_provider=self.config_provider,
                      logger=self.logger)
            except Exception as exc:
                self.logger(f"卡片后台消费者失败：{type(exc).__name__}: {exc}")
            self.stop_event.wait(0.2)
