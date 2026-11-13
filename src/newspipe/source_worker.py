"""Bounded acquisition-only subprocesses; state and delivery stay in the caller."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict
import importlib
import json
import math
import os
import re
import signal
import subprocess
import sys
import time

from newspipe.source_contract import FetchResult, ISSUE_CODES, SourceContractError, SubscriptionReport, normalize_fetch

MAX_INPUT = 1024 * 1024
MAX_STREAM = 16 * 1024 * 1024
CLEANUP_SECONDS = 2
_budget = ContextVar("newspipe_source_budget", default=90)


@contextmanager
def limit(seconds):
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("Acquisition budget must be finite and positive.")
    token = _budget.set(seconds)
    try:
        yield
    finally:
        _budget.reset(token)


class _OutputLimit(ValueError):
    pass


def _encode(value, limit):
    parts, size = [], 0
    for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(",", ":")).iterencode(value):
        data = chunk.encode("utf-8")
        size += len(data)
        if size > limit:
            raise _OutputLimit("Acquisition protocol limit exceeded.")
        parts.append(data)
    return b"".join(parts) + b"\n"


def _failed(items, reports, code):
    keys = {report.key for report in reports}
    key = "acquisition-worker"
    while key in keys:
        key += "-failure"
    return FetchResult(items, tuple([*reports, SubscriptionReport(key, "failure", code=code)]))


def _decode(raw, failure=""):
    items, reports, reported, terminal = [], [], None, False
    order = ""
    code = failure
    if len(raw) > MAX_STREAM:
        raw, code = raw[:MAX_STREAM], "incomplete"
    for line in raw.splitlines(keepends=True):
        try:
            if terminal or not line.endswith(b"\n"):
                raise SourceContractError("Interrupted acquisition protocol.")
            frame = json.loads(line)
            if not isinstance(frame, dict):
                raise SourceContractError("Invalid acquisition frame.")
            if set(frame) == {"order"} and frame["order"] == "pub_desc" and reported is None and not order:
                order = frame["order"]
                continue
            if set(frame) == {"done"} and frame["done"] is True:
                terminal = True
                continue
            if set(frame) == {"error"} and frame["error"] in ISSUE_CODES - {""}:
                code, terminal = frame["error"], True
                continue
            if set(frame) != {"items", "reports", "reported"}:
                raise SourceContractError("Invalid acquisition result.")
            result = normalize_fetch(FetchResult(frame["items"], tuple(SubscriptionReport(**r) for r in frame["reports"]),
                                                reported=frame["reported"]), source="worker", adapter="worker")
            if (reported is not None and reported != result.reported
                    or {report.key for report in reports}.intersection(report.key for report in result.reports)):
                raise SourceContractError("Acquisition partitions contradict each other.")
            items.extend(result.items)
            reports.extend(result.reports)
            reported = result.reported
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            code = "invalid_response"
            break
    if order == "pub_desc":
        items.sort(key=lambda item: -(item.get("pub_ts") or 0))
    if code or not terminal or not reports:
        return _failed(items, reports, code or "network")
    return FetchResult(items, tuple(reports), reported=reported)


def _kill_group(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


class Adapter:
    def __init__(self, module):
        if not isinstance(module, str) or not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", module):
            raise ValueError("Acquisition adapter must be an importable module.")
        self.module = module

    def fetch(self, cfg=None, since=None):
        started = time.monotonic()
        try:
            request = _encode({"module": self.module, "settings": cfg or {}, "since": since}, MAX_INPUT - 1)
        except (ValueError, TypeError, UnicodeError, RecursionError):
            return _failed([], [], "configuration")
        env = dict(os.environ, PYTHONPATH=os.pathsep.join(os.path.abspath(path or os.getcwd()) for path in sys.path))
        try:
            process = subprocess.Popen([sys.executable, "-m", "newspipe.source_worker"], stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
                                       start_new_session=True)
        except OSError:
            return _failed([], [], "network")
        output, failure, cleanup_until, terminated = b"", "", None, False
        try:
            remaining = _budget.get() - (time.monotonic() - started)
            output, _ = process.communicate(request, timeout=max(0, remaining))
            if process.returncode:
                failure = "network"
        except subprocess.TimeoutExpired as error:
            output, failure = error.output or b"", "timeout"
            cleanup_until = time.monotonic() + CLEANUP_SECONDS
            terminated = True
            _kill_group(process)
            try:
                output, _ = process.communicate(timeout=CLEANUP_SECONDS)
            except subprocess.TimeoutExpired as cleanup:
                output = cleanup.output or output
        finally:
            if cleanup_until is None:
                cleanup_until = time.monotonic() + CLEANUP_SECONDS
            if not terminated:
                _kill_group(process)
            for pipe in (process.stdin, process.stdout):
                pipe.close()
            try:
                process.wait(timeout=max(0, cleanup_until - time.monotonic()))
            except subprocess.TimeoutExpired:
                # Kernel scheduling is outside the deadline contract; never wait forever.
                pass
        return _decode(output, failure)


def _main():
    # Reserve a protocol fd, then discard adapter prints at the OS level (including children).
    protocol = os.dup(1)
    with open(os.devnull, "wb") as sink:
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)
    with os.fdopen(protocol, "wb") as writer:
        used = 0

        def emit(value):
            nonlocal used
            payload = _encode(value, MAX_STREAM - used - 1024)
            writer.write(payload)
            writer.flush()
            used += len(payload)

        try:
            raw = sys.stdin.buffer.read(MAX_INPUT + 1)
            if len(raw) > MAX_INPUT:
                raise _OutputLimit("Acquisition input too large.")
            request = json.loads(raw)
            module = Adapter(request["module"]).module
            adapter = importlib.import_module(module)
            order = getattr(adapter, "FETCH_RESULT_ORDER", "")
            if order:
                if order != "pub_desc":
                    raise SourceContractError("Unsupported acquisition ordering.")
                emit({"order": order})
            parts = getattr(adapter, "fetch_partitions", None)
            results = (parts(request["settings"], since=request["since"]) if callable(parts)
                       else [adapter.fetch(request["settings"], since=request["since"])])
            for index, part in enumerate(results):
                if index >= 100:
                    raise _OutputLimit("Acquisition partition count exceeded.")
                result = normalize_fetch(part, source=module, adapter=module.rsplit(".", 1)[-1])
                emit({"items": result.items, "reports": [asdict(report) for report in result.reports],
                      "reported": result.reported})
            emit({"done": True})
        except Exception as error:
            from newspipe.adapters._common import issue_code
            code = ("incomplete" if isinstance(error, _OutputLimit) else
                    "invalid_response" if isinstance(error, SourceContractError) else issue_code(error))
            writer.write(_encode({"error": code}, 1024))
            writer.flush()


if __name__ == "__main__":
    _main()
