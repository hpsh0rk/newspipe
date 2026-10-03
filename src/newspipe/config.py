"""资讯管线 v2 —— 配置层（四轴）。

`sources.yaml`（信源表）与 `models.yaml`（模型路由/预算）是唯一人工编辑入口。
本模块只做「读 + 校验 + 展开默认值」：改配置即改行为，引擎每次运行读一次，无需重启。

四轴（v1 的 `policy: digest|alert` 三合一枚举在这里被拆开）：
  fetch   —— 何时拉（slot / poll / manual）
  filter  —— 留什么（关键词闸 / 条数上下限 / 排序）
  enrich  —— 加工什么（off / title / post / article，可选抓正文、可选译正文）
  deliver —— 怎么发（card / append_card / state_only，优先级、静默窗口、打扰预算）
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml  # noqa: E402

from newspipe import hooks as hooks_lib
from newspipe.errors import ConfigError  # noqa: E402

def data_root() -> Path:
    """数据根目录。

    包化前这里写死 `Path(__file__).resolve().parents[2]`（= 宿主仓库根）。包化后 `__file__`
    指向安装位置，那个式子会指向包自己 —— 所以改成「环境变量优先，其次当前工作目录」：
      - 在宿主仓库里跑（cwd = 仓库根）→ 与包化前一致；
      - 独立部署 → 设 `NEWSPIPE_HOME` 指向数据目录。
    """
    env = os.environ.get("NEWSPIPE_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return Path.cwd().resolve()


def default_news_dir() -> Path:
    """配置与状态的默认位置：`NEWSPIPE_NEWS_DIR` 优先，其次 `<data_root>/news`。

    显式给 `NEWSPIPE_NEWS_DIR` 是推荐做法：数据目录放哪由部署决定，不靠猜。
    """
    env = os.environ.get("NEWSPIPE_NEWS_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return data_root() / "news"


# 兼容常量：导入时求值一次（调用点应优先用 default_news_dir()，它每次都重新解析环境变量）
NEWS_DIR = default_news_dir()
SOURCES_PATH = NEWS_DIR / "sources.yaml"
MODELS_PATH = NEWS_DIR / "models.yaml"
ADAPTERS_DIR = Path(__file__).resolve().parent / "adapters"

# 飞书卡片 JSON 2.0 硬限制：整卡 ≤200 元素（ErrCode 11310）。
# 2026-10-03 实测（scripts/news/render.py 的行结构，见 tests/test_news_pipeline.py）：
#   每行 14 元素 + 固定 5 元素 ⇒ n=13 → 187 元素 / 22.3KB（OK），n=14 → 201 元素（超限）。
#   所以上限是 13；另一条线是报文 30KB（230025）。**只约束发卡形态**：
#   `form: state_only` 的源不受此限（它不发卡）。
# 改行结构（加按钮/多一层 column_set）后必须重算，并同步 tests 里的预算测试。
CARD_MAX_ITEMS = 13

TRIGGERS = ("slot", "poll", "manual")
FORMS = ("card", "append_card", "state_only")
PRIORITIES = ("high", "normal", "low")
SUMMARY_MODES = ("off", "title", "post", "article")
RANKS = ("score_desc", "pub_desc", "none")
TIERS = ("T1", "T2")

MODEL_DEFAULT_BUDGET: dict[str, Any] = {
    "max_calls_per_day": 400,
    "max_calls_per_hour": 80,
    "max_chars_per_call": 8000,
    "on_exceeded": "degrade",
    "timeout_seconds": 90,
}


def available_adapters() -> tuple[str, ...]:
    """适配器 = adapters/ 下的模块名。加一个信源只需丢一个文件 + 加一段配置。"""
    if not ADAPTERS_DIR.is_dir():
        return ()
    return tuple(sorted(p.stem for p in ADAPTERS_DIR.glob("*.py") if p.stem != "__init__"))


def _tuple_of_str(value: Any, *, where: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, (list, tuple)):
        raise ConfigError(f"{where} 需要字符串列表，得到 {type(value).__name__}")
    return tuple(str(v) for v in value)


def _bool(value: Any, *, where: str) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    raise ConfigError(f"{where} 需要 true/false，得到 {value!r}")


def _int(value: Any, *, where: str, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{where} 需要整数，得到 {value!r}")
    return value


def _num(value: Any, *, where: str, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{where} 需要数字，得到 {value!r}")
    return float(value)


def _enum(value: Any, allowed: tuple[str, ...], *, where: str, default: str) -> str:
    if value is None:
        return default
    # YAML 1.1 的坑：`summary: off` 会被解析成布尔 False（on/off/yes/no 同此）。
    # 配置里已引号包裹，这里再兜一层——用户手改配置时不该因为这个陷阱炸掉整条管线。
    if value is False and "off" in allowed:
        return "off"
    if value is True:
        raise ConfigError(f"{where}: 需要字符串（{ '|'.join(allowed) }），YAML 里请加引号")
    if not isinstance(value, str) or value not in allowed:
        raise ConfigError(f"{where} 必须是 {'|'.join(allowed)} 之一，得到 {value!r}")
    return value


@dataclass(frozen=True)
class FetchCfg:
    """轴①：何时拉。"""

    trigger: str = "slot"
    slots: tuple[str, ...] = ()
    interval_min: float = 0.0

    @classmethod
    def parse(cls, raw: Any, *, where: str) -> "FetchCfg":
        raw = raw or {}
        if not isinstance(raw, dict):
            raise ConfigError(f"{where} 需要映射")
        trigger = _enum(raw.get("trigger"), TRIGGERS, where=f"{where}.trigger", default="slot")
        slots = _tuple_of_str(raw.get("slots"), where=f"{where}.slots")
        interval = _num(raw.get("interval_min"), where=f"{where}.interval_min", default=0.0)
        if trigger == "slot" and not slots:
            raise ConfigError(f"{where}: trigger=slot 必须给 slots")
        if trigger == "poll" and interval < 0:
            raise ConfigError(f"{where}: interval_min 不能为负")
        return cls(trigger=trigger, slots=slots, interval_min=interval)


@dataclass(frozen=True)
class FilterCfg:
    """轴②：留什么。"""

    include_keywords: tuple[str, ...] = ()
    exclude_keywords: tuple[str, ...] = ()
    min_items: int = 1
    max_items: int = CARD_MAX_ITEMS
    rank: str = "none"

    @classmethod
    def parse(cls, raw: Any, *, where: str) -> "FilterCfg":
        raw = raw or {}
        if not isinstance(raw, dict):
            raise ConfigError(f"{where} 需要映射")
        cfg = cls(
            include_keywords=_tuple_of_str(raw.get("include_keywords"), where=f"{where}.include_keywords"),
            exclude_keywords=_tuple_of_str(raw.get("exclude_keywords"), where=f"{where}.exclude_keywords"),
            min_items=_int(raw.get("min_items"), where=f"{where}.min_items", default=1),
            max_items=_int(raw.get("max_items"), where=f"{where}.max_items", default=CARD_MAX_ITEMS),
            rank=_enum(raw.get("rank"), RANKS, where=f"{where}.rank", default="none"),
        )
        if cfg.min_items < 0 or cfg.max_items < 1:
            raise ConfigError(f"{where}: min_items ≥ 0、max_items ≥ 1")
        return cfg


@dataclass(frozen=True)
class EnrichCfg:
    """轴③：加工什么。summary=off 时整条链路零模型调用。"""

    summary: str = "off"
    fetch_body: bool = False
    max_body_chars: int = 6000
    translate_body: bool = False

    @classmethod
    def parse(cls, raw: Any, *, where: str) -> "EnrichCfg":
        raw = raw or {}
        if not isinstance(raw, dict):
            raise ConfigError(f"{where} 需要映射")
        mode = _enum(raw.get("summary"), SUMMARY_MODES, where=f"{where}.summary", default="off")
        fetch_body = _bool(raw.get("fetch_body"), where=f"{where}.fetch_body")
        translate_body = _bool(raw.get("translate_body"), where=f"{where}.translate_body")
        if mode == "article" and not fetch_body:
            # 不是错误：正文由适配器直接给出（如 RSS 全文）时无需再抓。
            pass
        if translate_body and mode == "off":
            raise ConfigError(f"{where}: translate_body 需要 summary 不为 off（否则没有正文可译）")
        return cls(
            summary=mode,
            fetch_body=fetch_body,
            max_body_chars=_int(raw.get("max_body_chars"), where=f"{where}.max_body_chars", default=6000),
            translate_body=translate_body,
        )


@dataclass(frozen=True)
class DeliverCfg:
    """轴④：怎么发。priority=high 越过静默窗口与最小间隔（手动指定，后续接 priority_judge）。"""

    form: str = "card"
    priority: str = "normal"
    quiet_hours: str = ""
    max_cards_per_day: int = 12
    min_gap_min: float = 0.0

    @classmethod
    def parse(cls, raw: Any, *, where: str) -> "DeliverCfg":
        raw = raw or {}
        if not isinstance(raw, dict):
            raise ConfigError(f"{where} 需要映射")
        budget = raw.get("budget") or {}
        if not isinstance(budget, dict):
            raise ConfigError(f"{where}.budget 需要映射")
        return cls(
            form=_enum(raw.get("form"), FORMS, where=f"{where}.form", default="card"),
            priority=_enum(raw.get("priority"), PRIORITIES, where=f"{where}.priority", default="normal"),
            quiet_hours=str(raw.get("quiet_hours") or ""),
            max_cards_per_day=_int(budget.get("max_cards_per_day"),
                                   where=f"{where}.budget.max_cards_per_day", default=12),
            min_gap_min=_num(budget.get("min_gap_min"), where=f"{where}.budget.min_gap_min", default=0.0),
        )


@dataclass(frozen=True)
class SourceConfig:
    name: str
    adapter: str
    enabled: bool
    tier: str
    first_party: bool
    fetch: FetchCfg
    filter: FilterCfg
    enrich: EnrichCfg
    deliver: DeliverCfg
    card: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def card_title(self) -> str:
        return str(self.card.get("title") or self.name)

    @property
    def sends_card(self) -> bool:
        return self.deliver.form != "state_only"

    def adapter_config(self) -> dict[str, Any]:
        """透传给适配器的配置：feeds / feed_label / http2 / tier / … 原样给，适配器只认自己要的。"""
        cfg = dict(self.extra)
        cfg.update({"name": self.name, "tier": self.tier, "first_party": self.first_party})
        return cfg


CHANNELS = ("feishu_lark_cli", "feishu_direct")
INBOUND_MODES = ("ws", "http", "none")


@dataclass(frozen=True)
class ServiceCfg:
    """独立运行配置（`service.yaml`）。**文件缺失时全部取默认值 = 与阶段 1 行为完全一致**。

    默认 `channel=feishu_direct`、`inbound.mode=none`，所以只跑单次命令的用法一行配置都不用改。
    """

    channel: str = "feishu_lark_cli"
    feishu: dict[str, Any] = field(default_factory=dict)
    inbound: dict[str, Any] = field(default_factory=dict)
    schedule: dict[str, Any] = field(default_factory=dict)
    log: dict[str, Any] = field(default_factory=dict)

    @property
    def inbound_mode(self) -> str:
        return str(self.inbound.get("mode") or "none")

    @property
    def tick_seconds(self) -> float:
        return float(self.schedule.get("tick_seconds") or 300)

    def http(self) -> dict[str, Any]:
        return dict(self.inbound.get("http") or {})


def load_service(news_dir: Path) -> ServiceCfg:
    """读 `service.yaml`（可选）。任何非法枚举都抛 ConfigError，不静默取默认。"""
    path = Path(news_dir) / "service.yaml"
    if not path.is_file():
        return ServiceCfg()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} 解析失败：{exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: 顶层需要映射")
    channel = str(raw.get("channel") or "feishu_lark_cli")
    if channel not in CHANNELS:
        raise ConfigError(f"{path}: channel={channel!r} 未知，可用：{', '.join(CHANNELS)}")
    inbound = raw.get("inbound") or {}
    if not isinstance(inbound, dict):
        raise ConfigError(f"{path}: inbound 需要映射")
    mode = str(inbound.get("mode") or "none")
    if mode not in INBOUND_MODES:
        raise ConfigError(f"{path}: inbound.mode={mode!r} 未知，可用：{', '.join(INBOUND_MODES)}")
    schedule = raw.get("schedule") or {}
    if not isinstance(schedule, dict):
        raise ConfigError(f"{path}: schedule 需要映射")
    tick = float(schedule.get("tick_seconds") or 300)
    if tick < 30:
        raise ConfigError(f"{path}: schedule.tick_seconds={tick:g} 太小（下限 30）——"
                          "轮询频率由各源的 fetch.interval_min 控制，别靠缩短 tick 提速")
    for name in ("feishu", "log"):
        if raw.get(name) is not None and not isinstance(raw.get(name), dict):
            raise ConfigError(f"{path}: {name} 需要映射")
    return ServiceCfg(channel=channel, feishu=dict(raw.get("feishu") or {}), inbound=dict(inbound),
                      schedule=dict(schedule), log=dict(raw.get("log") or {}))


@dataclass(frozen=True)
class Config:
    chat: str
    slots: dict[str, str]
    sources: dict[str, SourceConfig]
    models: dict[str, Any] = field(default_factory=dict)
    service: ServiceCfg = field(default_factory=ServiceCfg)
    #: 第三方卡片 hook（`hooks.yaml`）。缺失 = 空集合 = 行为与没有 hook 时逐字节一致。
    #: `hooks.problems` 里是被跳过的声明（doctor 会报出来，不静默）。
    hooks: hooks_lib.HookSet = field(default_factory=hooks_lib.HookSet)

    def enabled_sources(self) -> list[SourceConfig]:
        return [s for s in self.sources.values() if s.enabled]

    def slot_sources(self, slot: str) -> list[SourceConfig]:
        return [s for s in self.enabled_sources()
                if s.fetch.trigger == "slot" and slot in s.fetch.slots]

    def poll_sources(self) -> list[SourceConfig]:
        return [s for s in self.enabled_sources() if s.fetch.trigger == "poll"]

    def budget(self) -> dict[str, Any]:
        merged = dict(MODEL_DEFAULT_BUDGET)
        merged.update(self.models.get("budget") or {})
        return merged


def _parse_sources(raw: Any, *, news_dir: Path) -> dict[str, SourceConfig]:
    if not isinstance(raw, dict) or not raw:
        raise ConfigError(f"{news_dir / 'sources.yaml'}: sources 段为空")
    known = available_adapters()
    out: dict[str, SourceConfig] = {}
    for name, body in raw.items():
        where = f"sources.{name}"
        if not isinstance(body, dict):
            raise ConfigError(f"{where} 需要映射")
        adapter = body.get("adapter")
        if not isinstance(adapter, str) or not adapter:
            raise ConfigError(f"{where}: 缺少 adapter")
        if known and adapter not in known:
            raise ConfigError(f"{where}: 未知适配器 {adapter!r}，可用：{', '.join(known)}")
        reserved = {"adapter", "enabled", "tier", "first_party", "fetch", "filter",
                    "enrich", "deliver", "card"}
        out[name] = SourceConfig(
            name=name,
            adapter=adapter,
            enabled=_bool(body.get("enabled", True), where=f"{where}.enabled") if "enabled" in body else True,
            tier=_enum(body.get("tier"), TIERS, where=f"{where}.tier", default="T2"),
            first_party=_bool(body.get("first_party"), where=f"{where}.first_party"),
            fetch=FetchCfg.parse(body.get("fetch"), where=f"{where}.fetch"),
            filter=FilterCfg.parse(body.get("filter"), where=f"{where}.filter"),
            enrich=EnrichCfg.parse(body.get("enrich"), where=f"{where}.enrich"),
            deliver=DeliverCfg.parse(body.get("deliver"), where=f"{where}.deliver"),
            card=dict(body.get("card") or {}),
            extra={k: v for k, v in body.items() if k not in reserved},
        )
    return out


def load(news_dir: Path | None = None, *, sources_text: str | None = None,
         hooks_text: str | None = None) -> Config:
    """读 + 校验配置。任何问题都抛 ConfigError（含可执行的修法提示）。

    `sources_text` / `hooks_text` 是**校验用覆盖**：编辑命令把候选文本喂进来先跑一遍真校验，
    通过了才落盘——「先验证再写」比「写完发现坏了」便宜得多。
    """
    news_dir = Path(news_dir or default_news_dir())
    sources_path = news_dir / "sources.yaml"
    models_path = news_dir / "models.yaml"
    if sources_text is None and not sources_path.is_file():
        raise ConfigError(f"缺少 {sources_path}")
    try:
        raw = (yaml.safe_load(sources_text) if sources_text is not None
               else yaml.safe_load(sources_path.read_text(encoding="utf-8"))) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{sources_path} 解析失败：{exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{sources_path}: 顶层需要映射")

    chat = raw.get("chat")
    if not isinstance(chat, str) or not chat.startswith("oc_"):
        raise ConfigError(f"{sources_path}: chat 必须是 oc_ 开头的群 ID")

    slots_raw = raw.get("slots") or {}
    if not isinstance(slots_raw, dict) or not slots_raw:
        raise ConfigError(f"{sources_path}: slots 段为空")
    slots = {str(k): str(v) for k, v in slots_raw.items()}

    sources = _parse_sources(raw.get("sources"), news_dir=news_dir)
    for src in sources.values():
        for slot in src.fetch.slots:
            if slot not in slots:
                raise ConfigError(f"sources.{src.name}.fetch.slots 含未定义槽位 {slot!r}")

    models: dict[str, Any] = {}
    if models_path.is_file():
        try:
            models = yaml.safe_load(models_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"{models_path} 解析失败：{exc}") from exc
        if not isinstance(models, dict):
            raise ConfigError(f"{models_path}: 顶层需要映射")

    return Config(chat=chat, slots=slots, sources=sources, models=models,
                  service=load_service(news_dir),
                  hooks=hooks_lib.load_from(news_dir / "hooks.yaml", text=hooks_text))


def summarize(cfg: Config) -> list[dict[str, Any]]:
    """给 --verbose / --dry 用的可读展开视图。"""
    rows = []
    for src in cfg.sources.values():
        rows.append({
            "source": src.name,
            "enabled": src.enabled,
            "adapter": src.adapter,
            "fetch": {"trigger": src.fetch.trigger, "slots": list(src.fetch.slots),
                      "interval_min": src.fetch.interval_min},
            "filter": {"min_items": src.filter.min_items, "max_items": src.filter.max_items,
                       "rank": src.filter.rank,
                       "include": len(src.filter.include_keywords),
                       "exclude": len(src.filter.exclude_keywords)},
            "enrich": {"summary": src.enrich.summary, "fetch_body": src.enrich.fetch_body,
                       "translate_body": src.enrich.translate_body},
            "deliver": {"form": src.deliver.form, "priority": src.deliver.priority,
                        "quiet_hours": src.deliver.quiet_hours,
                        "max_cards_per_day": src.deliver.max_cards_per_day,
                        "min_gap_min": src.deliver.min_gap_min},
        })
    return rows
