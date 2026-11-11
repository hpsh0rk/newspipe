"""An injected datetime anchor that advances through an entire pipeline invocation."""
from __future__ import annotations

import time
from contextvars import ContextVar
from datetime import datetime, timedelta
from functools import wraps

_anchor: ContextVar[tuple[datetime, float] | None] = ContextVar("newspipe_clock", default=None)


def now() -> datetime:
    anchor = _anchor.get()
    if anchor is None:
        return datetime.now()
    when, started = anchor
    return when + timedelta(seconds=max(0, time.monotonic() - started))


def scoped(fn):
    @wraps(fn)
    def run(*args, **kwargs):
        if _anchor.get() is not None:
            return fn(*args, **{**kwargs, "now": now()})
        when = kwargs.get("now") or datetime.now()
        token = _anchor.set((when, time.monotonic()))
        try:
            return fn(*args, **{**kwargs, "now": when})
        finally:
            _anchor.reset(token)
    return run
