"""Importable offline acquisition fixture; never contacts a provider or model."""
import os
import sys
import time

from newspipe.errors import ConfigError
from newspipe.source_contract import FetchResult, SubscriptionReport


def fetch(cfg=None, since=None):
    item = {"ext_id": 0, "title": "Fixture", "pub_ts": since, "metadata": {"pid": os.getpid()}}
    return iter([item]) if (cfg or {}).get("generator") else [item]


def fetch_partitions(cfg=None, since=None):
    cfg = cfg or {}
    mode = cfg.get("mode", "legacy")
    if mode == "legacy":
        yield fetch(cfg, since)
        return
    if mode == "configuration":
        raise ConfigError("Authorization: PRIVATE_FIXTURE_VALUE")
    if mode == "empty":
        yield FetchResult([], (SubscriptionReport("feed-a", "empty", watermark=100),))
        return
    if mode == "noise":
        print("PRIVATE_FIXTURE_VALUE")
        print("PRIVATE_FIXTURE_VALUE", file=sys.stderr)
        os.write(1, b"PRIVATE_FIXTURE_VALUE\n")
        os.write(2, b"PRIVATE_FIXTURE_VALUE\n")
        raise OSError("PRIVATE_FIXTURE_VALUE")
    if mode == "cpu":
        while True:
            pass
    if mode == "descendant":
        child = os.fork()
        if child == 0:
            time.sleep(60)
            os._exit(0)
        yield FetchResult([{"ext_id": "child", "metadata": {"pid": child}}],
                          (SubscriptionReport("feed-a", "success", 1),))
        os._exit(0)
    yield FetchResult([{"ext_id": "first", "title": "Completed", "pub_ts": 100}],
                      (SubscriptionReport("feed-a", "success", 1, watermark=100),))
    if mode == "slow":
        time.sleep(60)
    if mode == "crash":
        os._exit(9)
    if mode == "overflow":
        yield [{"ext_id": "large", "source_text": "x" * (17 * 1024 * 1024)}]
        return
    yield FetchResult([{"ext_id": "second", "title": "Completed", "pub_ts": 200}],
                      (SubscriptionReport("feed-b", "success", 1, watermark=200),))
