"""Temporary offline UI fixture. No scheduler, credentials, fetches or remote sends."""
from __future__ import annotations

import sys
from contextlib import ExitStack, contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import threading
from unittest.mock import patch

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from newspipe import backends, channel, config, credentials, inbound, llm, net, pipeline, rssconnect, runtime, state
from newspipe.observations import prepare_observation
from newspipe.story_matching import rank_candidates
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
    story_ids: dict[str, str]


def _story_history(store):
    """Seed immutable synthetic evidence, never acquire from a real provider."""
    now = datetime.now()
    _, revision = config.story_settings_snapshot(store.news_dir)
    text = "A verifiable toolkit launch includes a reproducible build, public documentation, measured compatibility and explicit supported versions. " * 3
    original = {"ext_id": "launch", "title": "A verifiable toolkit launch", "content_kind": "article",
                "source_text": text, "source_text_complete": True, "url": "https://example.invalid/launch",
                "pub_ts": now.timestamp()}
    store.record_pushed("research", [{"ext_id": "legacy-only"}], now.date().isoformat())
    cases = [("original", "research", original),
             ("copy", "products", {**original, "ext_id": "copy", "url": "https://example.invalid/syndication"}),
             ("commentary", "products", {**original, "ext_id": "commentary", "content_kind": "post",
                "source_text": text + " My different commentary questions the compatibility measurement.", "source_text_complete": False}),
             ("change", "research", {**original, "source_text": text + " Later correction: supported versions include 2.1."}),
             ("late", "research", {**original, "ext_id": "late", "pub_ts": (now - timedelta(days=2)).timestamp(),
                "title": "An older launch report arrived today", "source_text_complete": False}),
             ("legacy", "research", {**original, "ext_id": "legacy-only"}),
             ("unrelated", "reading", {**original, "ext_id": "unrelated", "title": "A quiet weekend reading list",
                "source_text": "An unrelated reading list of local history and cooking books.", "source_text_complete": False})]
    ids = {}
    for index, (key, source, item) in enumerate(cases):
        draft = prepare_observation(source, "rsshub" if source != "products" else "hn", item)
        at = now + timedelta(seconds=index)
        snapshot = store.observation_capture_snapshot([draft], at=at, window_days=14, candidate_limit=5, scan_limit=500)
        baselines = store.original_observation_baselines(source, {str(draft.native_id)}) if key == "legacy" else {}
        store.record_observations([draft], at=at, mode="observe", config_revision=revision, current_revision=revision,
            expected_activation_id=snapshot.activation_id, snapshot=snapshot,
            proposals=rank_candidates(draft, list(snapshot.candidates), limit=5), baselines=baselines)
        ids[key] = draft.observation_id
    return ids


@contextmanager
def offline_workbench(*, view_actions=True, story_history=False):
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
        if story_history:
            raw["stories"] = {"mode": "observe"}
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
        story_ids = _story_history(store) if story_history else {}
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
            yield WorkbenchFixture(news, f"http://127.0.0.1:{server.server_address[1]}", store, server, thread, blocked, group_calls, story_ids)
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
