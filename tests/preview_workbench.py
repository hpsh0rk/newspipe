"""Temporary offline UI fixture. No scheduler, credentials, fetches or remote sends."""
from __future__ import annotations

import sys
from contextlib import ExitStack, nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from newspipe import channel, inbound, pipeline, runtime, state


def main():
    with TemporaryDirectory(prefix="newspipe-workbench-") as tmp, ExitStack() as stack:
        news = Path(tmp)
        raw = {"chat": "oc_offline_preview", "slots": {"am": "08:15", "pm": "18:35"},
               "notifications": {"max_cards_per_day": 24, "paused": False},
               "policies": {"digest": {"label": "早晚读报", "mode": "digest", "slots": ["am", "pm"]},
                            "live": {"label": "值得及时知道", "mode": "immediate", "form": "append_card",
                                     "min_gap_min": 10, "quiet_hours": "23:00-07:30"},
                            "archive": {"label": "先收着，慢慢看", "mode": "collect"}},
               "sources": {"research": {"adapter": "rsshub", "feeds": ["https://example.invalid/research.xml"],
                                        "feed_label": "研究与论文", "card": {"title": "研究与论文"},
                                        "fetch": {"trigger": "poll", "interval_min": 10}, "notify": {"policy": "digest"}},
                           "products": {"adapter": "hn", "card": {"title": "产品与工程动态"},
                                        "fetch": {"trigger": "slot", "slots": ["am"]}, "notify": {"policy": "live"}},
                           "reading": {"adapter": "rsshub", "feeds": ["https://example.invalid/feed.xml"],
                                       "enabled": False, "card": {"title": "个人阅读清单"}, "fetch": {"trigger": "manual"},
                                       "notify": {"policy": "archive"}}}}
        (news / "sources.yaml").write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False))
        (news / "service.yaml").write_text("inbound: {mode: none}\nview: {enabled: true, actions: true}\n")
        store = state.Store(news)
        store.append_pending("research", [{"ext_id": "preview-1", "title": "A small, verifiable research update",
                                           "url": "https://example.invalid/article", "source": "Preview"}])
        store.heartbeat("research", status="collected", note="离线预览数据", queued=1)
        stack.enter_context(patch.object(runtime, "activate", side_effect=lambda *_: nullcontext()))
        stack.enter_context(patch.object(pipeline, "_adapter", return_value=SimpleNamespace(fetch=lambda *a, **k: [])))
        for method in ("create_entity", "send_card", "update_entity"):
            stack.enter_context(patch.object(channel, method, side_effect=RuntimeError("offline preview blocks remote effects")))
        server, _ = inbound.start_http(service_cfg={"http": {"host": "127.0.0.1", "port": 0}}, creds=None,
                                       news_dir=news, accept_events=False, view_actions=True)
        print(f"http://127.0.0.1:{server.server_address[1]}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
