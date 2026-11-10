"""Runtime assembly shared by CLI, scheduler and callback workers."""
from __future__ import annotations

import hashlib
import json
import os
import threading
from contextlib import contextmanager
from functools import wraps
from pathlib import Path

from newspipe import backends, config, hostenv
from newspipe.errors import ConfigError

_cache: dict[str, object] = {}
_guard = threading.Lock()


def channel_for(cfg, news_dir: Path):
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
                             or k.startswith("FEISHU_") or k == "HERMES_HOME"}, stamps], sort_keys=True)
    key = hashlib.sha256(signature.encode()).hexdigest()
    with _guard:
        if key not in _cache:
            try:
                kwargs = {"service_cfg": service.feishu, "news_dir": news_dir} if service.channel == "feishu_direct" else {}
                channel = backends.make_channel(service.channel, **kwargs)
            except KeyError as exc:
                raise ConfigError(str(exc)) from None
            if len(_cache) >= 64:
                _cache.pop(next(iter(_cache)))
            _cache[key] = channel
        return _cache[key]


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
