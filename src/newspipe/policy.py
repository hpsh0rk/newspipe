"""Notification configuration and pure, single-level policy resolution.

No state, adapters or transports are consulted here. config.py remains the
public validation/schema facade; delivery receives an effective SourceConfig.
"""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field, replace
from typing import Any, TYPE_CHECKING

from newspipe.errors import ConfigError

if TYPE_CHECKING:
    from newspipe.config import Config, SourceConfig

MODES = ("immediate", "digest", "collect")
FORMS = ("card", "append_card")
OVERRIDE_FIELDS = ("mode", "slots", "form", "quiet_hours", "max_cards_per_day",
                   "min_gap_min", "bypass_source_limits")


def identifier(value: Any, *, where: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_-]*", value):
        raise ConfigError(f"{where}: ID 必须是非空 ASCII 字母、数字、下划线或短横线")
    return value


def clock(value: Any, *, where: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value):
        raise ConfigError(f"{where}: 时间必须是 HH:MM（00:00 至 23:59）")
    return value


def quiet_window(value: Any, *, where: str) -> str:
    if value == "":
        return ""
    if not isinstance(value, str) or len(value.split("-")) != 2:
        raise ConfigError(f"{where}: 静默窗口必须是 HH:MM-HH:MM，或留空")
    start, end = value.split("-")
    clock(start, where=where)
    clock(end, where=where)
    return value


def _mapping(raw: Any, allowed: tuple[str, ...], where: str) -> dict:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: 需要映射")
    unknown = set(raw) - set(allowed)
    if unknown:
        raise ConfigError(f"{where}: 未知字段 {', '.join(sorted(map(str, unknown)))}")
    return raw


def _fields(raw: dict, *, where: str) -> dict:
    out = dict(raw)
    for key, value in raw.items():
        path = f"{where}.{key}"
        if key in ("mode", "form"):
            allowed = MODES if key == "mode" else FORMS
            if value not in allowed:
                raise ConfigError(f"{path}: 必须是 {'|'.join(allowed)}")
        elif key == "slots":
            if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
                raise ConfigError(f"{path}: 需要槽位 ID 列表")
            if len(set(value)) != len(value):
                raise ConfigError(f"{path}: 槽位不能重复")
            out[key] = tuple(value)
        elif key == "times":
            if not isinstance(value, dict):
                raise ConfigError(f"{path}: 需要发送时间映射")
            out[key] = {identifier(k, where=path): clock(v, where=f"{path}.{k}")
                        for k, v in value.items()}
            if len(set(value.values())) != len(value):
                raise ConfigError(f"{path}: 发送时间不能重复")
        elif key == "quiet_hours":
            quiet_window(value, where=path)
        elif key == "max_cards_per_day":
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ConfigError(f"{path}: 需要非负整数，0 表示不发送")
        elif key == "min_gap_min":
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value < 0):
                raise ConfigError(f"{path}: 需要有限的非负数字")
        elif key in ("bypass_source_limits", "paused"):
            if not isinstance(value, bool):
                raise ConfigError(f"{path}: 需要 true/false")
        elif key == "label" and not isinstance(value, str):
            raise ConfigError(f"{path}: 需要显示名称字符串")
    return out


@dataclass(frozen=True)
class NotificationPolicy:
    label: str = ""
    mode: str = "immediate"
    slots: tuple[str, ...] = ()
    times: dict[str, str] = field(default_factory=dict)
    form: str = "card"
    quiet_hours: str = ""
    max_cards_per_day: int = 12
    min_gap_min: float = 0.0
    bypass_source_limits: bool = False

    @classmethod
    def parse(cls, raw: Any, *, where: str) -> "NotificationPolicy":
        body = _fields(_mapping(raw, ("label", "times", *OVERRIDE_FIELDS), where), where=where)
        result = cls(**body)
        result.validate(where=where)
        return result

    def validate(self, *, where: str, slots: dict | None = None) -> None:
        if self.times and self.slots:
            raise ConfigError(f"{where}.times: 不能同时配置发送时间与旧 slots 引用")
        if self.mode == "digest" and not (self.slots or self.times):
            raise ConfigError(f"{where}.times: 定时汇总必须填写至少一个发送时间")
        if slots is not None:
            for slot in self.slots:
                if slot not in slots:
                    raise ConfigError(f"{where}.slots: 未定义槽位 {slot!r}，先配置 slots")

    def values(self) -> dict[str, Any]:
        out = asdict(self)
        out["slots"] = list(self.slots)
        return out


@dataclass(frozen=True)
class NotificationGlobals:
    paused: bool = False
    quiet_hours: str = ""
    max_cards_per_day: int | None = None
    min_gap_min: float = 0.0

    @classmethod
    def parse(cls, raw: Any) -> "NotificationGlobals":
        allowed = ("paused", "quiet_hours", "max_cards_per_day", "min_gap_min")
        return cls(**_fields(_mapping(raw, allowed, "notifications"), where="notifications"))


@dataclass(frozen=True)
class NotifyBinding:
    policy: str
    overrides: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def parse(cls, raw: Any, *, where: str) -> "NotifyBinding":
        body = _mapping(raw, ("policy", "overrides"), where)
        name = identifier(body.get("policy"), where=f"{where}.policy")
        overrides = _fields(_mapping(body.get("overrides", {}), OVERRIDE_FIELDS,
                                    f"{where}.overrides"), where=f"{where}.overrides")
        return cls(name, overrides)


@dataclass(frozen=True)
class EffectiveNotification:
    policy_id: str
    values: dict[str, Any]
    origins: dict[str, str]


def resolve(cfg: "Config", source: "SourceConfig") -> EffectiveNotification | None:
    binding = source.notify
    if binding is None:
        return None
    if binding.policy not in cfg.policies:
        raise ConfigError(f"sources.{source.name}.notify.policy: 未定义策略 {binding.policy!r}")
    template = cfg.policies[binding.policy]
    effective = replace(template, **binding.overrides)
    effective.validate(where=f"sources.{source.name}.notify", slots=cfg.slots)
    values = {key: value for key, value in effective.values().items() if key != "label"}
    origins = {key: (f"source:{source.name}" if key in binding.overrides
                     else f"policy:{binding.policy}") for key in values}
    return EffectiveNotification(binding.policy, values, origins)


def effective_source(cfg: "Config", source: "SourceConfig") -> "SourceConfig":
    rule = resolve(cfg, source)
    if rule is None:
        return source
    from newspipe.config import DeliverCfg
    values = rule.values
    deliver = DeliverCfg(form="state_only" if values["mode"] == "collect" else values["form"],
                         priority="high" if values["bypass_source_limits"] else "normal",
                         quiet_hours="" if values["mode"] == "collect" else values["quiet_hours"],
                         max_cards_per_day=values["max_cards_per_day"],
                         min_gap_min=values["min_gap_min"])
    return replace(source, deliver=deliver)


def sending_times(cfg: "Config", values: dict) -> dict[str, str]:
    """Stable keys are local to each source's completion/receipt ledger."""
    return dict(values.get("times") or {s: cfg.slots[s] for s in values.get("slots", [])})


def notification_datetime(cfg: "Config", source: "SourceConfig", key: str, now):
    rule = resolve(cfg, source)
    time = sending_times(cfg, rule.values)[key]
    hour, minute = map(int, time.split(":"))
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0)


def describe_fields() -> tuple[list[dict], list[dict]]:
    """Metadata surfaced only through config.describe_schema()."""
    policy_fields = [
        {"path": "label", "label": "策略名称", "type": "str", "default": ""},
        {"path": "mode", "label": "通知方式", "type": "enum", "choices": list(MODES), "default": "immediate"},
        {"path": "times", "label": "发送时间", "type": "times", "default": {},
         "help": "此策略独立管理，可添加多个时间；修改后所有绑定此策略的源同步生效，其他策略不变。"},
        {"path": "form", "label": "卡片形式", "type": "enum", "choices": list(FORMS), "default": "card"},
        {"path": "quiet_hours", "label": "静默窗口", "type": "str", "default": "",
         "help": "HH:MM-HH:MM，支持跨午夜；空值关闭静默"},
        {"path": "max_cards_per_day", "label": "每日通知操作上限（每源）", "type": "int", "default": 12,
         "help": "更新已有卡片也计一次；0 表示不发送"},
        {"path": "min_gap_min", "label": "最小间隔（分钟，每源）", "type": "float", "default": 0.0},
        {"path": "bypass_source_limits", "label": "源级限制例外", "type": "bool", "default": False,
         "help": "只越过该源静默、上限、间隔；不能越过全局限制或通知时刻"},
    ]
    global_fields = [{"path": "paused", "label": "暂停所有通知", "type": "bool", "default": False,
                      "help": "继续采集和修复已确认的本地回执"}]
    for item in policy_fields:
        if item["path"] in ("quiet_hours", "max_cards_per_day", "min_gap_min"):
            entry = dict(item)
            entry["label"] = entry["label"].replace("（每源）", "（全局）").replace("，每源", "，全局")
            if item["path"] == "max_cards_per_day":
                entry["default"] = None
            global_fields.append(entry)
    return policy_fields, global_fields


def human_summary(rule: dict | None, slots: dict) -> str:
    if not rule or rule.get("policy_id") is None:
        return "旧式内联策略 · 使用旧全局计数，不会自动迁移"
    values = rule["values"]
    mode = values["mode"]
    timing = {"immediate": "有新内容即通知", "collect": "只采集，本地消费待处理内容"}.get(mode)
    if timing is None:
        times = values.get("times") or {s: slots.get(s, "") for s in values.get("slots", [])}
        timing = "定时汇总 · " + " / ".join(sorted(times.values()))
    if mode == "collect":
        return timing
    return (f"{timing} · {'追加同一张卡' if values['form'] == 'append_card' else '每批新卡'} · "
            f"每源 {values['max_cards_per_day']} 次/日 · 间隔 {values['min_gap_min']:g} 分钟"
            + (f" · 静默 {values['quiet_hours']}" if values["quiet_hours"] else "")
            + (" · 源级限制例外（全局限制仍生效）" if values["bypass_source_limits"] else ""))
