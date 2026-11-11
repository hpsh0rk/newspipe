"""常驻服务（阶段 3）的单元测试 —— 注入时钟与假 pipeline，不联网、不真发卡。

守四件事：
1. 槽位判定（到点 / 容差内 / 已错过 / 已跑过）与下一次唤醒时间；
2. 运行记录落盘（`state/service.json`）与轮转；
3. `--serve --once` 真的只跑一轮且退出码 0；
4. `service.yaml` 的非法值必须抛 ConfigError（不静默取默认——默认值决定了「是否脱离宿主」）。
"""

from __future__ import annotations

import json
import sys
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
for _p in (str(SRC_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from newspipe import backends, config, credentials, pipeline, runtime, service, state  # noqa: E402
from newspipe.errors import ConfigError, InboundError, NewsError  # noqa: E402

SLOTS = {"am": "08:15", "noon": "12:35", "pm": "18:35"}


def _cfg(**kw) -> config.Config:
    return config.Config(chat=kw.get("chat", "oc_test"), slots=kw.get("slots", dict(SLOTS)),
                         sources=kw.get("sources", {}), models={},
                         service=kw.get("service", config.ServiceCfg()))


class SlotTimeTests(unittest.TestCase):
    def test_parse_valid(self) -> None:
        self.assertEqual(service.parse_slot_time("08:15"), (8, 15))
        self.assertEqual(service.parse_slot_time(" 23:59 "), (23, 59))

    def test_parse_invalid_is_loud(self) -> None:
        for bad in ("8", "8:5:1", "aa:bb", "24:00", "12:60", "", None):
            with self.assertRaises(ConfigError):
                service.parse_slot_time(bad)

    def test_slot_datetime_keeps_date(self) -> None:
        now = datetime(2026, 10, 3, 9, 30)
        self.assertEqual(service.slot_datetime(_cfg(), "am", now), datetime(2026, 10, 3, 8, 15))


class DueSlotTests(unittest.TestCase):
    def test_due_within_tolerance(self) -> None:
        cfg = _cfg()
        self.assertEqual(service.due_slots(cfg, datetime(2026, 10, 3, 8, 15), {}), ["am"])
        self.assertEqual(service.due_slots(cfg, datetime(2026, 10, 3, 8, 25), {}), ["am"])

    def test_not_due_before_or_after(self) -> None:
        cfg = _cfg()
        self.assertEqual(service.due_slots(cfg, datetime(2026, 10, 3, 8, 14), {}), [])
        self.assertEqual(service.due_slots(cfg, datetime(2026, 10, 3, 8, 31), {}), [])

    def test_already_run_today_is_not_due(self) -> None:
        cfg = _cfg()
        last = {"slots": {"2026-10-03": ["am"]}}
        self.assertEqual(service.due_slots(cfg, datetime(2026, 10, 3, 8, 16), last), [])
        self.assertEqual(service.missed_slots(cfg, datetime(2026, 10, 3, 8, 16), last), [])

    def test_missed_reported_but_not_due(self) -> None:
        cfg = _cfg()
        now = datetime(2026, 10, 3, 9, 30)
        self.assertEqual(service.due_slots(cfg, now, {}), [])
        self.assertEqual(service.missed_slots(cfg, now, {}), ["am"])

    def test_multiple_slots_ordered_by_time(self) -> None:
        cfg = _cfg(slots={"pm": "18:35", "am": "08:15"})
        now = datetime(2026, 10, 3, 18, 36)
        last = {"slots": {"2026-10-03": ["am"]}}
        self.assertEqual(service.due_slots(cfg, now, last), ["pm"])

    def test_previous_day_record_does_not_block_today(self) -> None:
        cfg = _cfg()
        last = {"slots": {"2026-10-02": ["am"]}}
        self.assertEqual(service.due_slots(cfg, datetime(2026, 10, 3, 8, 16), last), ["am"])


class WaitTests(unittest.TestCase):
    def test_tick_wins_when_sooner(self) -> None:
        cfg = _cfg()
        self.assertEqual(service.compute_wait(cfg, datetime(2026, 10, 3, 8, 0), 300), 300)

    def test_next_slot_wins_when_sooner(self) -> None:
        cfg = _cfg()
        # 08:00 时，am 槽位 08:15 只剩 900s < tick 3600
        self.assertEqual(service.compute_wait(cfg, datetime(2026, 10, 3, 8, 0), 3600), 900)

    def test_past_slot_rolls_to_tomorrow(self) -> None:
        cfg = _cfg(slots={"am": "08:15"})
        # 08:20 时今天已过，下一个是明天 08:15（23h55m）> tick
        self.assertEqual(service.compute_wait(cfg, datetime(2026, 10, 3, 8, 20), 300), 300)

    def test_never_returns_zero(self) -> None:
        cfg = _cfg(slots={"am": "08:15"})
        self.assertGreaterEqual(service.compute_wait(cfg, datetime(2026, 10, 3, 8, 15), 0.0), 1.0)


class RunRecordTests(unittest.TestCase):
    def test_mark_and_load(self) -> None:
        with TemporaryDirectory() as tmp:
            news_dir = Path(tmp)
            self.assertEqual(service.load_runs(news_dir), {})
            service.mark_slot(news_dir, "am", datetime(2026, 10, 3, 8, 16))
            data = service.load_runs(news_dir)
            self.assertEqual(data["slots"]["2026-10-03"], ["am"])
            self.assertIn("last_run_at", data)
            # 幂等：重复标记不产生重复项
            service.mark_slot(news_dir, "am", datetime(2026, 10, 3, 8, 17))
            self.assertEqual(service.load_runs(news_dir)["slots"]["2026-10-03"], ["am"])

    def test_old_days_are_trimmed(self) -> None:
        with TemporaryDirectory() as tmp:
            news_dir = Path(tmp)
            base = datetime(2026, 9, 1, 8, 16)
            for offset in range(20):
                service.mark_slot(news_dir, "am", base + timedelta(days=offset))
            kept = service.load_runs(news_dir)["slots"]
            self.assertLessEqual(len(kept), 14)
            self.assertIn("2026-09-20", kept)

    def test_corrupt_file_is_tolerated(self) -> None:
        with TemporaryDirectory() as tmp:
            news_dir = Path(tmp)
            service.runs_path(news_dir).parent.mkdir(parents=True, exist_ok=True)
            service.runs_path(news_dir).write_text("{not json", encoding="utf-8")
            self.assertEqual(service.load_runs(news_dir), {})


class RunOnceTests(unittest.TestCase):
    def setUp(self) -> None:
        # Exact injected schedule instants must not acquire real subsecond jitter.
        timer = mock.patch("time.monotonic", return_value=0.0)
        timer.start()
        self.addCleanup(timer.stop)
        self._tmp = TemporaryDirectory()
        self.news_dir = Path(self._tmp.name)
        self.store = state.Store(self.news_dir)
        self.logs: list[str] = []

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _log(self, message: str) -> None:
        self.logs.append(message)

    def test_due_slot_runs_and_is_marked(self) -> None:
        cfg = _cfg()
        with mock.patch.object(pipeline, "run_slot", return_value=[]) as run_slot, \
             mock.patch.object(config.Config, "poll_sources", return_value=[]):
            service.run_once(cfg, self.store, now=datetime(2026, 10, 3, 8, 16),
                             news_dir=self.news_dir, logger=self._log)
        run_slot.assert_called_once()
        self.assertEqual(run_slot.call_args[0][2], "am")
        self.assertEqual(service.load_runs(self.news_dir)["slots"]["2026-10-03"], ["am"])

    def test_dry_run_does_not_mark(self) -> None:
        cfg = _cfg()
        with mock.patch.object(pipeline, "run_slot", return_value=[]), \
             mock.patch.object(config.Config, "poll_sources", return_value=[]):
            service.run_once(cfg, self.store, now=datetime(2026, 10, 3, 8, 16),
                             news_dir=self.news_dir, dry=True, logger=self._log)
        self.assertEqual(service.load_runs(self.news_dir), {})

    def test_second_call_same_slot_does_not_rerun(self) -> None:
        cfg = _cfg()
        with mock.patch.object(pipeline, "run_slot", return_value=[]) as run_slot, \
             mock.patch.object(config.Config, "poll_sources", return_value=[]):
            service.run_once(cfg, self.store, now=datetime(2026, 10, 3, 8, 16),
                             news_dir=self.news_dir, logger=self._log)
            service.run_once(cfg, self.store, now=datetime(2026, 10, 3, 8, 17),
                             news_dir=self.news_dir, logger=self._log)
        self.assertEqual(run_slot.call_count, 1)

    def test_poll_runs_every_tick(self) -> None:
        cfg = _cfg()
        with mock.patch.object(pipeline, "run_slot", return_value=[]), \
             mock.patch.object(pipeline, "run_poll", return_value=[]) as run_poll, \
             mock.patch.object(config.Config, "poll_sources", return_value=[object()]):
            service.run_once(cfg, self.store, now=datetime(2026, 10, 3, 10, 0),
                             news_dir=self.news_dir, logger=self._log)
        run_poll.assert_called_once()

    def test_no_poll_sources_skips_poll(self) -> None:
        cfg = _cfg()
        with mock.patch.object(pipeline, "run_slot", return_value=[]), \
             mock.patch.object(pipeline, "run_poll", return_value=[]) as run_poll, \
             mock.patch.object(config.Config, "poll_sources", return_value=[]):
            service.run_once(cfg, self.store, now=datetime(2026, 10, 3, 10, 0),
                             news_dir=self.news_dir, logger=self._log)
        run_poll.assert_not_called()

    def test_slot_failure_is_logged_not_raised(self) -> None:
        cfg = _cfg()
        with mock.patch.object(pipeline, "run_slot",
                               side_effect=NewsError("boom")), \
             mock.patch.object(config.Config, "poll_sources", return_value=[]):
            service.run_once(cfg, self.store, now=datetime(2026, 10, 3, 8, 16),
                             news_dir=self.news_dir, logger=self._log)
        self.assertTrue(any("失败" in line for line in self.logs))
        self.assertNotIn("am", service.load_runs(self.news_dir).get("slots", {}).get("2026-10-03", []))

    def test_failed_slot_retries_with_a_limit_and_no_success_marker(self) -> None:
        with mock.patch.object(pipeline, "run_slot", side_effect=NewsError("temporary")) as run, \
             mock.patch.object(config.Config, "poll_sources", return_value=[]):
            for minute in (16, 16, 17, 18, 19, 31):
                service.run_once(_cfg(), self.store, now=datetime(2026, 10, 3, 8, minute),
                                 logger=self._log)
        self.assertEqual(run.call_count, 3)
        self.assertNotIn("am", service.load_runs(self.news_dir).get("slots", {}).get("2026-10-03", []))

    def test_partial_slot_retries_only_failed_source_after_restart(self) -> None:
        cfg = _cfg(sources={name: config.SourceConfig(name=name, adapter="aihot", enabled=True,
                   tier="T2", first_party=False, filter=config.FilterCfg(),
                   enrich=config.EnrichCfg(), deliver=config.DeliverCfg(),
                   fetch=config.FetchCfg(trigger="slot", slots=["am"])) for name in ("good", "bad")})
        calls = []

        def run(_cfg, _store, slot, *, only=None, **kw):
            calls.append(only)
            return [pipeline.SourceRun(source=only, status="send_failed" if calls.count(only) == 1
                                       and only == "bad" else "ok")]

        with mock.patch.object(pipeline, "run_slot", side_effect=run):
            service.run_once(cfg, self.store, now=datetime(2026, 10, 3, 8, 16), logger=self._log)
            self.assertNotIn("am", service.load_runs(self.news_dir).get("slots", {}).get("2026-10-03", []))
            service.run_once(cfg, state.Store(self.news_dir), now=datetime(2026, 10, 3, 8, 21),
                             logger=self._log)
        self.assertEqual(calls, ["good", "bad", "bad"])
        self.assertEqual(service.load_runs(self.news_dir)["slots"]["2026-10-03"], ["am"])

    def test_one_source_exception_does_not_skip_the_other_sources(self) -> None:
        cfg = _cfg(sources={name: config.SourceConfig(name=name, adapter="aihot", enabled=True,
                   tier="T2", first_party=False, filter=config.FilterCfg(),
                   enrich=config.EnrichCfg(), deliver=config.DeliverCfg(),
                   fetch=config.FetchCfg(trigger="slot", slots=["am"])) for name in ("bad", "good")})

        def run(_cfg, _store, slot, *, only=None, **kw):
            if only == "bad":
                raise NewsError("temporary")
            return [pipeline.SourceRun(source="good")]

        with mock.patch.object(pipeline, "run_slot", side_effect=run) as run_mock:
            service.run_once(cfg, self.store, now=datetime(2026, 10, 3, 8, 16), logger=self._log)
        self.assertEqual(run_mock.call_count, 2)

    def test_expired_retry_window_does_not_rerun(self) -> None:
        with mock.patch.object(pipeline, "run_slot", side_effect=NewsError("temporary")) as run:
            service.run_once(_cfg(), self.store, now=datetime(2026, 10, 3, 8, 29), logger=self._log)
            service.run_once(_cfg(), self.store, now=datetime(2026, 10, 3, 8, 31), logger=self._log)
        self.assertEqual(run.call_count, 1)

    def test_config_missing_waits_for_a_configuration_change(self) -> None:
        source = config.SourceConfig(name="s", adapter="aihot", enabled=True, tier="T2",
                   first_party=False, filter=config.FilterCfg(), enrich=config.EnrichCfg(),
                   deliver=config.DeliverCfg(), fetch=config.FetchCfg(trigger="slot", slots=["am"]))
        cfg = _cfg(sources={"s": source})
        with mock.patch.object(pipeline, "run_slot", side_effect=[
                [pipeline.SourceRun(source="s", status="config_missing")],
                [pipeline.SourceRun(source="s")]]) as run:
            service.run_once(cfg, self.store, now=datetime(2026, 10, 3, 8, 16), logger=self._log)
            service.run_once(cfg, self.store, now=datetime(2026, 10, 3, 8, 21), logger=self._log)
            cfg = replace(cfg, sources={"s": replace(source, extra={"feeds": ["https://example.test/rss"]})})
            service.run_once(cfg, self.store, now=datetime(2026, 10, 3, 8, 26), logger=self._log)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(service.load_runs(self.news_dir)["slots"]["2026-10-03"], ["am"])


class RunForeverTests(unittest.TestCase):
    def test_ws_ownership_check_uses_the_exact_startup_credential_snapshot(self) -> None:
        with TemporaryDirectory() as tmp:
            news = Path(tmp)
            host_env = news / "host-test.env"
            host_env.write_text("FEISHU_APP_ID=test-host\n")
            cfg = _cfg(service=config.ServiceCfg(inbound={"mode": "ws"}))
            first = credentials.FeishuCreds("test-other", "test-only")
            second = credentials.FeishuCreds("test-host", "test-only")
            with mock.patch.object(credentials, "resolve_feishu", side_effect=[first, second]) as resolve, \
                 mock.patch.object(service, "host_dotenv_path", return_value=host_env), \
                 mock.patch.object(service, "start_inbound_for", return_value=service.inbound_mod.InboundHandle(mode="ws")) as start, \
                 mock.patch.object(service.callback_jobs, "Worker"), \
                 mock.patch.object(service, "run_once", return_value=[]):
                code = service.run_forever(cfg, state.Store(news), once=True, reload_fn=lambda: cfg,
                                           logger=lambda _: None)
            self.assertEqual(code, 0)
            self.assertIs(start.call_args.kwargs["creds"], first)
            resolve.assert_called_once()

    def test_credential_environment_changes_do_not_replace_active_transport(self) -> None:
        with TemporaryDirectory() as tmp:
            news = Path(tmp)
            cfg = _cfg(service=config.ServiceCfg(inbound={"mode": "http"}))
            initial = credentials.FeishuCreds(app_id="test-original", app_secret="test-only",
                                             verification_token="test-verification")
            newer = credentials.FeishuCreds(app_id="test-new", app_secret="test-only-new")
            changed = False

            def reload_cfg():
                nonlocal changed
                changed = True
                (news / ".env").touch()
                return replace(cfg)

            channels = []

            def run(current, store, **kw):
                with runtime.activate(current, news):
                    channels.append(backends.get_channel().creds)
                return []

            with mock.patch.object(credentials, "resolve_feishu", side_effect=lambda *a, **k: newer if changed else initial), \
                 mock.patch.object(service, "start_inbound_for", return_value=service.inbound_mod.InboundHandle(mode="http")) as start, \
                 mock.patch.object(service.callback_jobs, "Worker") as worker, \
                 mock.patch.object(service, "run_once", side_effect=run):
                code = service.run_forever(cfg, state.Store(news), once=True, reload_fn=reload_cfg,
                                           logger=lambda _: None)
            self.assertEqual(code, 0)
            self.assertEqual(channels, [initial])
            self.assertEqual(start.call_args.kwargs.get("creds"), initial)
            self.assertEqual(service.runtime_status(news)["restart_required"], ["service.feishu"])

    def test_last_tick_inbound_failure_is_not_reported_as_success(self) -> None:
        with TemporaryDirectory() as tmp:
            news = Path(tmp)
            handle = service.inbound_mod.InboundHandle(mode="ws")

            def tick(*a, **kw):
                handle.error = "ConnectionError"
                return []

            with mock.patch.object(service, "prepare_channel"), \
                 mock.patch.object(service, "start_inbound_for", return_value=handle), \
                 mock.patch.object(service.callback_jobs, "Worker"), \
                 mock.patch.object(service, "run_once", side_effect=tick):
                code = service.run_forever(_cfg(), state.Store(news), once=True, logger=lambda _: None)
            self.assertEqual(code, 1)
            self.assertEqual(service.runtime_status(news)["error"], "ConnectionError")

    def test_failed_source_results_fail_one_shot_service(self) -> None:
        for status in ("error", "config_missing", "send_failed", "overflow"):
            with self.subTest(status=status), TemporaryDirectory() as tmp, \
                 mock.patch.object(service, "run_once", return_value=[pipeline.SourceRun(source="test", status=status)]):
                code = service.run_forever(_cfg(), state.Store(Path(tmp)), once=True,
                                           with_inbound=False, logger=lambda _: None)
            self.assertEqual(code, 1)

    def test_effective_http_address_uses_actual_listener_binding(self) -> None:
        with TemporaryDirectory() as tmp:
            news = Path(tmp)
            cfg = _cfg(service=config.ServiceCfg(inbound={"mode": "http", "http": {"port": 0}},
                                               view={"enabled": True}))
            handle = service.inbound_mod.InboundHandle(mode="http", server=mock.Mock(server_address=("0.0.0.0", 43210)))
            service._record_runtime(news, cfg, cfg, handle, [], running=True)
            info = service.runtime_status(news)
            self.assertEqual(info["effective"]["http_host"], "0.0.0.0")
            self.assertEqual(info["effective"]["http_port"], 43210)

    def test_resource_changes_require_restart_but_business_changes_reload(self) -> None:
        with TemporaryDirectory() as tmp:
            news_dir = Path(tmp)
            first = _cfg(service=config.ServiceCfg(inbound={"mode": "http", "http": {"port": 1001}},
                view={"enabled": True, "actions": False}, feishu={"app_secret": "test-original"}))
            desired = replace(first, chat="oc_new", service=replace(first.service,
                inbound={"mode": "ws", "http": {"port": 1002}},
                view={"enabled": True, "actions": True}, feishu={"app_secret": "test-replacement"},
                schedule={"tick_seconds": 120}))
            logs = []
            with mock.patch.object(service, "prepare_channel"), \
                 mock.patch.object(credentials, "resolve_feishu", return_value=credentials.FeishuCreds("test-app", "test-only")), \
                 mock.patch.object(service, "shared_app_ws_conflict", return_value=None), \
                 mock.patch.object(service, "start_inbound_for", return_value=service.inbound_mod.InboundHandle(mode="http")) as start, \
                 mock.patch.object(service.callback_jobs, "Worker"), \
                 mock.patch.object(service, "run_once", return_value=[]) as run:
                code = service.run_forever(first, state.Store(news_dir), news_dir=news_dir,
                    once=True, logger=logs.append, reload_fn=lambda: desired)
            self.assertEqual(code, 0)
            start.assert_called_once()
            effective = run.call_args.args[0]
            self.assertEqual(effective.chat, "oc_new")
            self.assertEqual(effective.service.tick_seconds, 120)
            self.assertEqual(effective.service.inbound, first.service.inbound)
            self.assertEqual(effective.service.view, first.service.view)
            self.assertEqual(effective.service.feishu, first.service.feishu)
            self.assertTrue(any("需重启" in line for line in logs))
            info = service.describe_service(desired, news_dir=news_dir)["runtime"]
            self.assertEqual(set(info["restart_required"]), {"service.inbound", "service.view", "service.feishu"})
            self.assertEqual(info["effective"]["inbound_mode"], "http")
            self.assertEqual(info["desired"]["inbound_mode"], "ws")
            self.assertNotIn("test-original", json.dumps(info))
            self.assertNotIn("test-replacement", json.dumps(info))
            self.assertFalse(info["running"])

    def test_inbound_startup_failure_returns_nonzero_and_stops_worker(self) -> None:
        with TemporaryDirectory() as tmp:
            news_dir = Path(tmp)
            with mock.patch.object(service, "prepare_channel"), \
                 mock.patch.object(service, "shared_app_ws_conflict", return_value=None), \
                 mock.patch.object(service, "start_inbound_for", side_effect=InboundError("test unavailable")), \
                 mock.patch.object(service.callback_jobs, "Worker") as worker, \
                 mock.patch.object(service, "run_once", return_value=[]):
                code = service.run_forever(_cfg(), state.Store(news_dir), once=True, logger=lambda _: None)
            self.assertNotEqual(code, 0)
            worker.return_value.stop.assert_called_once()

    def test_known_shared_application_conflict_is_rejected_before_opening_ws(self) -> None:
        with TemporaryDirectory() as tmp:
            news_dir = Path(tmp)
            cfg = _cfg(service=config.ServiceCfg(inbound={"mode": "ws"}))
            with mock.patch.object(service, "shared_app_ws_conflict", return_value="shared application conflict"), \
                 mock.patch.object(credentials, "resolve_feishu", return_value=credentials.FeishuCreds("test-host", "test-only")), \
                 mock.patch.object(service, "prepare_channel"), \
                 mock.patch.object(service, "start_inbound_for") as start, \
                 mock.patch.object(service, "run_once", return_value=[]):
                code = service.run_forever(cfg, state.Store(news_dir), news_dir=news_dir,
                                           once=True, logger=lambda _m: None)
            self.assertEqual(code, 2)
            start.assert_not_called()

    def test_unexpected_failure_stops_inbound_and_worker(self) -> None:
        with TemporaryDirectory() as tmp:
            news_dir = Path(tmp)
            with mock.patch.object(service, "prepare_channel"), \
                 mock.patch.object(service, "start_inbound_for") as start, \
                 mock.patch.object(service.callback_jobs, "Worker") as worker, \
                 mock.patch.object(service, "shared_app_ws_conflict", return_value=None):
                start.return_value.describe.return_value = {"mode": "none"}
                start.return_value.error = ""
                start.return_value.server = None
                with self.assertRaisesRegex(RuntimeError, "clock failed"):
                    service.run_forever(_cfg(), state.Store(news_dir), news_dir=news_dir,
                                        logger=lambda _m: None,
                                        now_fn=mock.Mock(side_effect=RuntimeError("clock failed")))
                start.return_value.stop.assert_called_once()
                worker.return_value.stop.assert_called_once()

    def test_once_returns_zero_and_stops(self) -> None:
        with TemporaryDirectory() as tmp:
            news_dir = Path(tmp)
            cfg = _cfg()
            store = state.Store(news_dir)
            with mock.patch.object(pipeline, "run_slot", return_value=[]), \
                 mock.patch.object(config.Config, "poll_sources", return_value=[]):
                code = service.run_forever(cfg, store, news_dir=news_dir, dry=True, once=True,
                                           with_inbound=False, logger=lambda _m: None,
                                           now_fn=lambda: datetime(2026, 10, 3, 8, 16))
            self.assertEqual(code, 0)

    def test_stop_event_breaks_loop(self) -> None:
        with TemporaryDirectory() as tmp:
            news_dir = Path(tmp)
            waits: list[float] = []

            def fake_wait(seconds: float) -> bool:
                waits.append(seconds)
                return True                      # 要求停止

            with mock.patch.object(pipeline, "run_slot", return_value=[]), \
                 mock.patch.object(config.Config, "poll_sources", return_value=[]):
                code = service.run_forever(_cfg(), state.Store(news_dir), news_dir=news_dir,
                                           dry=True, with_inbound=False,
                                           logger=lambda _m: None, wait_fn=fake_wait,
                                           now_fn=lambda: datetime(2026, 10, 3, 10, 0))
            self.assertEqual(code, 0)
            self.assertEqual(len(waits), 1)
            self.assertEqual(waits[0], 300.0)


    def test_config_is_reloaded_every_round(self) -> None:
        """改了配置不必重启进程（原缺口：只在启动读一次，改了不生效且**不报错**）。

        用「下一轮等待时间」当观测量：tick 从 300 变 120 只能来自重载后的新配置。
        """
        with TemporaryDirectory() as tmp:
            news_dir = Path(tmp)
            first = _cfg()
            second = _cfg(service=config.ServiceCfg(schedule={"tick_seconds": 120}))
            waits: list[float] = []
            calls = {"n": 0}

            def reloader() -> config.Config:
                calls["n"] += 1
                return second if calls["n"] > 1 else first

            def fake_wait(seconds: float) -> bool:
                waits.append(seconds)
                return len(waits) >= 2

            with mock.patch.object(pipeline, "run_slot", return_value=[]), \
                 mock.patch.object(config.Config, "poll_sources", return_value=[]):
                code = service.run_forever(first, state.Store(news_dir), news_dir=news_dir,
                                           dry=True, with_inbound=False, logger=lambda _m: None,
                                           wait_fn=fake_wait, reload_fn=reloader,
                                           now_fn=lambda: datetime(2026, 10, 3, 10, 0))
            self.assertEqual(code, 0)
            self.assertEqual(waits, [300.0, 120.0])

    def test_broken_config_keeps_the_previous_one_and_complains(self) -> None:
        with TemporaryDirectory() as tmp:
            news_dir = Path(tmp)
            logs: list[str] = []
            calls = {"n": 0}

            def reloader() -> config.Config:
                calls["n"] += 1
                if calls["n"] == 1:
                    return None
                raise ConfigError("sources.yaml 坏了")

            def fake_wait(_seconds: float) -> bool:
                return calls["n"] >= 2

            with mock.patch.object(pipeline, "run_slot", return_value=[]), \
                 mock.patch.object(config.Config, "poll_sources", return_value=[]):
                code = service.run_forever(_cfg(), state.Store(news_dir), news_dir=news_dir,
                                           dry=True, with_inbound=False, logger=logs.append,
                                           wait_fn=fake_wait, reload_fn=reloader,
                                           now_fn=lambda: datetime(2026, 10, 3, 10, 0))
            self.assertEqual(code, 0)                       # 沿用上一份配置继续跑
            self.assertTrue(any("重载失败" in line for line in logs), logs)


class SharedAppWsConflictTests(unittest.TestCase):
    """「一个机器人」= 「一个应用只能有一个 client 持有长连接」。

    飞书长连接是集群模式、不支持广播：同一应用多个 client 时事件只随机落到一个。
    这条约束必须有代码守着（doctor 警告 + 服务启动告警），不能只写在文档里。
    """

    def _env_file(self, tmp: str, app_id: str) -> Path:
        path = Path(tmp) / ".env"
        path.write_text(f"FEISHU_APP_ID={app_id}\nFEISHU_APP_SECRET=fake-secret-value\n",
                        encoding="utf-8")
        return path

    def _cfg(self, mode: str, app_id: str) -> config.Config:
        return _cfg(service=config.ServiceCfg(inbound={"mode": mode},
                                              feishu={"app_id": app_id}))

    def test_same_app_ws_is_reported(self) -> None:
        with TemporaryDirectory() as tmp:
            env = self._env_file(tmp, "cli_same")
            msg = service.shared_app_ws_conflict(self._cfg("ws", "cli_same"),
                                                 dotenv_paths=[env], host_env=env, env={})
            self.assertIsNotNone(msg)
            self.assertIn("集群", msg)
            self.assertIn("静默丢弃", msg)
            self.assertIn("cli_same", msg)              # app_id 不是密钥，可以出现

    def test_different_app_is_fine(self) -> None:
        with TemporaryDirectory() as tmp:
            env = self._env_file(tmp, "cli_host")
            self.assertIsNone(service.shared_app_ws_conflict(self._cfg("ws", "cli_other"),
                                                             dotenv_paths=[env], host_env=env,
                                                             env={}))

    def test_http_and_none_modes_never_conflict(self) -> None:
        """HTTP 入站不占长连接 ⇒ 天然没有这个问题（这正是推荐它的原因）。"""
        with TemporaryDirectory() as tmp:
            env = self._env_file(tmp, "cli_same")
            for mode in ("http", "none"):
                self.assertIsNone(service.shared_app_ws_conflict(self._cfg(mode, "cli_same"),
                                                                 dotenv_paths=[env], host_env=env,
                                                                 env={}))

    def test_missing_host_env_or_creds_is_silent(self) -> None:
        with TemporaryDirectory() as tmp:
            missing = Path(tmp) / "absent.env"
            self.assertIsNone(service.shared_app_ws_conflict(self._cfg("ws", "cli_same"),
                                                             dotenv_paths=[missing],
                                                             host_env=missing, env={}))


class ServiceConfigTests(unittest.TestCase):
    def _write(self, tmp: str, body: str) -> Path:
        path = Path(tmp) / "service.yaml"
        path.write_text(body, encoding="utf-8")
        return path

    def test_missing_file_means_defaults(self) -> None:
        with TemporaryDirectory() as tmp:
            cfg = config.load_service(Path(tmp))
            self.assertEqual(cfg.channel, "feishu_direct")
            self.assertEqual(cfg.inbound_mode, "none")
            self.assertEqual(cfg.tick_seconds, 300)

    def test_valid_file_parsed(self) -> None:
        with TemporaryDirectory() as tmp:
            self._write(tmp, "channel: feishu_direct\n"
                             "feishu: {app_id: cli_x}\n"
                             "inbound: {mode: ws}\n"
                             "schedule: {tick_seconds: 120}\n")
            cfg = config.load_service(Path(tmp))
            self.assertEqual(cfg.channel, "feishu_direct")
            self.assertEqual(cfg.inbound_mode, "ws")
            self.assertEqual(cfg.tick_seconds, 120)
            self.assertEqual(cfg.feishu["app_id"], "cli_x")

    def test_unknown_channel_is_loud(self) -> None:
        with TemporaryDirectory() as tmp:
            self._write(tmp, "channel: telegram\n")
            with self.assertRaises(ConfigError):
                config.load_service(Path(tmp))

    def test_unknown_inbound_mode_is_loud(self) -> None:
        with TemporaryDirectory() as tmp:
            self._write(tmp, "inbound: {mode: pigeon}\n")
            with self.assertRaises(ConfigError):
                config.load_service(Path(tmp))

    def test_tiny_tick_is_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            self._write(tmp, "schedule: {tick_seconds: 5}\n")
            with self.assertRaises(ConfigError):
                config.load_service(Path(tmp))

    def test_load_uses_service_yaml_when_present(self) -> None:
        """config.load() 会把 service.yaml 一起吃进来（没有此文件时 → 行为不变）。"""
        import yaml
        with TemporaryDirectory() as tmp:
            news = Path(tmp) / "info" / "news"
            news.mkdir(parents=True)
            (news / "sources.yaml").write_text(yaml.safe_dump(
                {"chat": "oc_x", "slots": {"am": "08:15"},
                 "sources": {"s": {"adapter": "aihot", "fetch": {"trigger": "slot", "slots": ["am"]}}}},
                allow_unicode=True), encoding="utf-8")
            self.assertEqual(config.load(news).service.channel, "feishu_direct")
            (news / "service.yaml").write_text("channel: feishu_direct\n", encoding="utf-8")
            self.assertEqual(config.load(news).service.channel, "feishu_direct")


class ChannelWiringTests(unittest.TestCase):
    def tearDown(self) -> None:
        backends.set_channel(None)
        backends.set_default_channel_name("")

    def test_default_channel_name_installed(self) -> None:
        with TemporaryDirectory() as tmp:
            service.prepare_channel(_cfg(), news_dir=Path(tmp))
            self.assertIsInstance(backends.get_channel(), backends.FeishuDirectChannel)

    def test_direct_channel_installed_when_configured(self) -> None:
        with TemporaryDirectory() as tmp:
            cfg = _cfg(service=config.ServiceCfg(channel="feishu_direct", feishu={"app_id": "cli_x"}))
            # 凭据解析第一优先是进程环境：注入哑值，测试不读开发机的 env / 钥匙串。
            with mock.patch.dict("os.environ", {"NEWSPIPE_FEISHU_APP_SECRET": "test-secret"}):
                service.prepare_channel(cfg, news_dir=Path(tmp))
                channel = backends.get_channel()
                self.assertIsInstance(channel, backends.FeishuDirectChannel)
                self.assertEqual(channel.creds.app_id, "cli_x")

    def test_available_channels_lists_both(self) -> None:
        self.assertEqual(backends.available_channels(), ["feishu_direct", "feishu_lark_cli"])


class DescribeServiceTests(unittest.TestCase):
    def tearDown(self) -> None:
        backends.set_channel(None)
        backends.set_default_channel_name("")

    def test_shape_for_default_config(self) -> None:
        with TemporaryDirectory() as tmp:
            info = service.describe_service(_cfg(), news_dir=Path(tmp))
            self.assertEqual(info["channel"], "feishu_direct")
            self.assertEqual(info["inbound_mode"], "none")
            self.assertEqual(info["slots"], SLOTS)
            self.assertIn("channel_detail", info)

    def test_direct_channel_reports_creds_without_secrets(self) -> None:
        secret = "SUPER-SECRET-VALUE-42"
        with TemporaryDirectory() as tmp:
            cfg = _cfg(service=config.ServiceCfg(channel="feishu_direct",
                                                 feishu={"app_id": "cli_x", "app_secret": secret}))
            info = service.describe_service(cfg, news_dir=Path(tmp))
            rendered = json.dumps(info, ensure_ascii=False)
            self.assertIn("feishu_direct", rendered)
            self.assertNotIn(secret, rendered, "密钥值绝不能出现在状态输出里")
            self.assertIn("已设置", rendered)


if __name__ == "__main__":
    unittest.main()
