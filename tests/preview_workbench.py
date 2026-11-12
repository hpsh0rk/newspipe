"""Temporary offline UI fixture. No scheduler, credentials, fetches or remote sends."""
from __future__ import annotations

import sys
from contextlib import ExitStack, contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import threading
from unittest.mock import patch

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from newspipe import backends, channel, credentials, inbound, llm, net, pipeline, rssconnect, runtime, state
from newspipe.backends.feishu_direct import FeishuDirectChannel
from newspipe.backends.feishu_lark_cli import LarkCliChannel


@dataclass
class WorkbenchFixture:
    news_dir: Path
    url: str
    store: state.Store
    server: object
    thread: threading.Thread
    blocked_effects: list[str]
    group_calls: list[str]


@contextmanager
def offline_workbench(*, view_actions=True):
    with TemporaryDirectory(prefix="newspipe-workbench-") as tmp, ExitStack() as stack:
        news = Path(tmp)
        raw = {"chat": "oc_offline_preview", "slots": {"am": "08:15", "pm": "18:35"},
               "notifications": {"max_cards_per_day": 24, "paused": False},
               "policies": {"digest": {"label": "早晚读报", "mode": "digest", "times": {"am": "08:15", "pm": "18:35"}},
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
        for source in raw["sources"].values():
            source["enrich"] = {"summary": "off"}
        (news / "sources.yaml").write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False))
        (news / "service.yaml").write_text(f"inbound: {{mode: none}}\nview: {{enabled: true, actions: {str(view_actions).lower()}}}\n")
        store = state.Store(news)
        store.append_pending("research", [{"ext_id": "preview-1", "title": "A small, verifiable research update",
                                           "url": "https://example.invalid/article", "source": "Preview"}])
        store.heartbeat("research", status="collected", note="离线预览数据", queued=1)
        item = {"id": "a01", "ext_id": "preview-old", "title": "A browsable offline article",
                "url": "https://example.invalid/article", "source": "Preview", "status": "unread"}
        store.write_batch({"source": "research", "digest": datetime.now().date().isoformat(),
                           "slot": "sample", "form": "state_only", "title": "Offline reading",
                           "items": [item], "view": "list", "archived": [], "overflow": 0})
        blocked = []
        def deny(effect):
            def fail(*args, **kwargs):
                blocked.append(effect)
                raise RuntimeError(f"offline fixture blocks {effect}")
            return fail
        stack.enter_context(patch.object(pipeline, "_adapter", return_value=SimpleNamespace(fetch=lambda *a, **k: [
            {"ext_id": "preview-collected", "title": "A deterministic collected item",
             "url": "https://example.invalid/collected", "source": "Preview"}])))
        stack.enter_context(patch.object(credentials, "resolve_feishu", side_effect=deny("credentials")))
        group_calls = []
        def groups(*, page_size=100, page_token=""):
            group_calls.append(page_token)
            if page_token not in ("", "offline-next"):
                raise RuntimeError("unknown synthetic page")
            entries = ([{"chat_id": "oc_offline_preview", "name": "我的阅读群", "external": False},
                        {"chat_id": "oc_engineering", "name": "工程讨论", "external": False}] if not page_token else
                       [{"chat_id": "oc_reading", "name": "周末读书", "external": False}])
            return {"items": entries, "has_more": not bool(page_token), "page_token": "offline-next" if not page_token else ""}
        stack.enter_context(patch.object(channel, "list_chats", side_effect=groups))
        stack.enter_context(patch.object(FeishuDirectChannel, "list_chats", side_effect=deny("direct group directory")))
        stack.enter_context(patch.object(LarkCliChannel, "list_chats", side_effect=deny("CLI group directory")))
        stack.enter_context(patch.object(runtime, "channel_for", return_value=SimpleNamespace(list_chats=groups)))
        stack.enter_context(patch.object(runtime, "activate", side_effect=lambda *_: backends.use_channel(SimpleNamespace(list_chats=groups))))
        stack.enter_context(patch.object(net, "request", side_effect=deny("network")))
        stack.enter_context(patch.object(rssconnect, "_probe_request", side_effect=deny("RSSHub probe network")))
        stack.enter_context(patch.object(llm, "chat_json", side_effect=deny("model")))
        stack.enter_context(patch.object(pipeline, "_enrich_new", side_effect=deny("enrichment")))
        for method in ("create_entity", "send_card", "update_entity"):
            stack.enter_context(patch.object(channel, method, side_effect=deny(method)))
        stack.enter_context(patch.dict("os.environ", {"NEWSPIPE_BIND_HOST": "127.0.0.1", "RSSHub_BASE": ""}))
        server, _ = inbound.start_http(service_cfg={"http": {"host": "127.0.0.1", "port": 0}}, creds=None,
                                       news_dir=news, accept_events=False, view_actions=view_actions, logger=lambda _: None)
        # Drain handlers before ExitStack restores guards; partial HTTP reads
        # time out rather than keeping a test/preview process alive indefinitely.
        server.daemon_threads = False
        server.RequestHandlerClass.timeout = 5
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield WorkbenchFixture(news, f"http://127.0.0.1:{server.server_address[1]}", store, server, thread, blocked, group_calls)
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()


def main():
    with offline_workbench() as fixture:
        print(fixture.url, flush=True)
        try:
            fixture.thread.join()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
