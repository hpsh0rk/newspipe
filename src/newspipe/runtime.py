"""Runtime assembly shared by CLI, scheduler and callback workers."""
from __future__ import annotations

import hashlib
import json
import os
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict
from functools import wraps
from pathlib import Path

from newspipe import backends, config, hostenv
from newspipe.errors import ConfigError

_cache: dict[str, object] = {}
_guard = threading.Lock()
_bound_channels: ContextVar[dict[str, object]] = ContextVar("newspipe_bound_transports", default={})


def channel_for(cfg, news_dir: Path, *, creds=None):
    bound = _bound_channels.get().get(str(Path(news_dir).resolve()))
    if bound is not None:
        return bound
    service = cfg.service
    files = [Path(news_dir) / ".env", hostenv.host_env_path()]
    stamps = []
    for path in files:
        try:
            stamps.append((str(path), path.stat().st_mtime_ns))
        except (OSError, AttributeError):
            stamps.append((str(path), None))
    signature = json.dumps([str(Path(news_dir).resolve()), service.channel, service.feishu,
                            {k: v for k, v in os.environ.items() if k.startswith("NEWSPIPE_")
                             or k.startswith("FEISHU_") or k == "HERMES_HOME"}, stamps,
                            asdict(creds) if creds is not None else None], sort_keys=True)
    key = hashlib.sha256(signature.encode()).hexdigest()
    with _guard:
        if key not in _cache:
            try:
                kwargs = {"service_cfg": service.feishu, "news_dir": news_dir} if service.channel == "feishu_direct" else {}
                if service.channel == "feishu_direct" and creds is not None:
                    kwargs["creds"] = creds
                channel = backends.make_channel(service.channel, **kwargs)
            except KeyError as exc:
                raise ConfigError(str(exc)) from None
            if len(_cache) >= 64:
                _cache.pop(next(iter(_cache)))
            _cache[key] = channel
        return _cache[key]


@contextmanager
def bind_channel(news_dir: Path, channel):
    """Pin a service-owned transport through nested scopes, without affecting other directories."""
    if channel is None:
        yield
        return
    token = _bound_channels.set({**_bound_channels.get(), str(Path(news_dir).resolve()): channel})
    try:
        yield
    finally:
        _bound_channels.reset(token)


@contextmanager
def activate(cfg, news_dir: Path):
    if not hasattr(cfg, "service"):
        yield
        return
    with backends.use_channel(channel_for(cfg, news_dir)):
        yield


def scoped(fn):
    @wraps(fn)
    def run(cfg, store, *args, **kwargs):
        with activate(cfg, store.news_dir):
            return fn(cfg, store, *args, **kwargs)
    return run
