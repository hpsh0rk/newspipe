"""Code-owned adapter metadata. Describing a source never executes its module."""
from copy import deepcopy
from dataclasses import dataclass
import math
import re


def validate_value(value, field: dict) -> None:
    kind = field["type"]
    valid = {"str": lambda: isinstance(value, str),
             "bool": lambda: type(value) is bool,
             "int": lambda: type(value) is int,
             "float": lambda: type(value) in (int, float) and math.isfinite(value),
             "list": lambda: isinstance(value, list) and all(isinstance(v, str) for v in value),
             "enum": lambda: isinstance(value, str) and value in field.get("choices", [])}
    if kind not in valid or not valid[kind]():
        raise ValueError("Adapter setting must match its declared type and choices.")
    if kind in {"int", "float"} and ("min" in field and value < field["min"] or "max" in field and value > field["max"]):
        raise ValueError("Adapter number is outside its declared range.")
    if kind == "list" and ("max_items" in field and len(value) > field["max_items"]
                           or field.get("item_pattern") and any(not re.fullmatch(field["item_pattern"], v) for v in value)):
        raise ValueError("Adapter subscriptions do not match their declared syntax or limit.")
    if kind == "str" and ("max_length" in field and len(value) > field["max_length"]
                          or field.get("pattern") and not re.fullmatch(field["pattern"], value)):
        raise ValueError("Adapter text does not match its declared syntax or limit.")
    if kind == "list" and field.get("disallowed_operators"):
        operators = "|".join(re.escape(v) for v in field["disallowed_operators"])
        for query in value:
            unquoted = re.sub(r'"(?:\\.|[^"\\])*"', " ", query)
            if re.search(r"(?<![\w:])(?:" + operators + r")\s*:", unquoted, re.IGNORECASE):
                raise ValueError("Query time operators conflict with the configured search window.")
    if field.get("credential") and value and not re.fullmatch(r"[A-Z_][A-Z0-9_]*", value):
        raise ValueError("Credential setting must name an environment variable, never a literal secret.")


def validate_relations(values: dict, fields) -> None:
    """Cross-field constraints stay code-owned and work without importing providers."""
    def get(path):
        current = values
        for part in path.split("."):
            current = current.get(part) if isinstance(current, dict) else None
        return current
    for field in fields:
        if field.get("prefix_from"):
            value = get(field["path"])
            if value and value.split(":", 1)[0] != get(field["prefix_from"]):
                raise ValueError(field["path"] + " must match " + field["prefix_from"])


@dataclass(frozen=True)
class AdapterDescriptor:
    name: str
    label: str
    capabilities: tuple[str, ...]
    fields: tuple[dict, ...] = ()
    optional_dependencies: tuple[str, ...] = ()
    hidden_core_fields: tuple[str, ...] = ()

    def __post_init__(self):
        if (not isinstance(self.name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.name)
                or not isinstance(self.label, str) or not self.label
                or any(not isinstance(group, tuple) or any(not isinstance(v, str) or not v for v in group)
                       for group in (self.capabilities, self.optional_dependencies, self.hidden_core_fields))
                or not isinstance(self.fields, tuple)):
            raise ValueError("Invalid code-owned adapter descriptor.")
        paths = []
        for field in self.fields:
            if (not isinstance(field, dict) or not isinstance(field.get("path"), str)
                    or not re.fullmatch(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*", field["path"])
                    or "default" not in field or field.get("required")
                    or field.get("type") not in {"str", "bool", "int", "float", "list", "enum"}):
                raise ValueError("Adapter fields need a path, supported type and non-required default.")
            path = field["path"]
            if any(path == other or path.startswith(other + ".") or other.startswith(path + ".")
                   for other in paths):
                raise ValueError("Adapter fields cannot have duplicate or overlapping paths.")
            if field.get("credential") and (field["type"] != "str" or field["default"] != ""
                                            or field.get("reference") != "environment"):
                raise ValueError("Credential fields declare empty environment references only.")
            if field["type"] == "enum" and (not isinstance(field.get("choices"), (list, tuple))
                    or not field["choices"] or any(not isinstance(v, str) for v in field["choices"])):
                raise ValueError("Enum fields must declare string choices.")
            validate_value(field["default"], field)
            paths.append(path)

    def as_dict(self) -> dict:
        return {"name": self.name, "label": self.label, "capabilities": list(self.capabilities),
                "fields": deepcopy(list(self.fields)),
                "optional_dependencies": list(self.optional_dependencies),
                "hidden_core_fields": list(self.hidden_core_fields), "editable": False}


REGISTRY = {
    "google_news": AdapterDescriptor("google_news", "Google News", ("rss_search", "original_evidence", "subscription_results", "bounded_search_window"), fields=(
        {"path": "settings.queries", "type": "list", "default": [], "label": "新闻查询词", "max_items": 25, "separator": "lines",
         "item_pattern": r"[^\x00-\x1f\x7f-\x9f\ud800-\udfff]{1,1024}", "disallowed_operators": ("when", "after", "before"),
         "help": "每行一条查询，支持关键词、引号短语及 Google 搜索语法。时间范围由下方回看天数管理，不要同时填写 when:/after:/before: 时间条件。只读取搜索 RSS，不抓取文章全文。"},
        {"path": "settings.language", "type": "str", "default": "zh-CN", "label": "搜索界面语言",
         "pattern": r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", "max_length": 35,
         "help": "语言代码，例如 zh-CN、zh-TW、en-US。不是严格的文章原文语言过滤；不同地区/语言的在线可用性需要单独验证。"},
        {"path": "settings.region", "type": "str", "default": "CN", "label": "新闻地区",
         "pattern": r"[A-Z]{2}", "max_length": 2, "help": "两个大写字母的地区代码，例如 CN、TW、US、BR。影响 Google News 版面，不保证所有代码均有可用内容。"},
        {"path": "settings.edition", "type": "str", "default": "", "label": "版面代码（可选）", "advanced": True,
         "pattern": r"(?:[A-Z]{2}:[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*)?", "max_length": 38,
         "prefix_from": "settings.region", "help": "通常留空，根据地区和语言生成。特殊版面可填 BR:pt-419 等代码，冒号前必须与新闻地区一致；修改地区时需同步修改或清空。"},
        {"path": "settings.lookback_days", "type": "int", "default": 7, "min": 1, "max": 90,
         "label": "回看天数", "advanced": True, "help": "每次重查最近 1..90 天，默认 7 天。搜索结果是有限窗口，不是完整历史；长时间停机后不能保证补齐。无日期条目保留未知日期。"},
        {"path": "settings.max_results", "type": "int", "default": 50, "min": 1, "max": 100,
         "label": "每条查询最多保留", "advanced": True, "help": "每条查询保留 1..100 条，默认 50 条，优先最新已知发布时间。受窗口/条数上限影响，不推进完整历史游标；重复采集保持原生身份幂等。"},
    ), hidden_core_fields=("feeds", "http2")),
    "github": AdapterDescriptor("github", "GitHub", ("public_user_events", "repository_releases", "original_evidence", "subscription_results", "optional_token", "rate_limit_status"), fields=(
        {"path": "settings.users", "type": "list", "default": [], "label": "公开用户动态", "max_items": 100,
         "item_pattern": r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", "help": "填写 GitHub 用户名，用逗号或换行分隔。只读取公开动态，不包含私有仓库事件；仓库发布可在下一项单独订阅。"},
        {"path": "settings.repositories", "type": "list", "default": [], "label": "仓库发布", "max_items": 100,
         "item_pattern": r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?/(?!(?:\.|\.\.)$)[A-Za-z0-9_.-]{1,100}", "help": "填写 owner/repo，用逗号或换行分隔。订阅公开 Release（含预发布、不含草稿），不是提交列表。"},
        {"path": "settings.token_env", "type": "str", "default": "", "label": "Token 环境变量名（可选）",
         "credential": True, "reference": "environment", "help": "留空使用匿名公开 API；需要更高额度时填环境变量名，例如 GITHUB_TOKEN，不能填写 token 本身。已填写但环境变量缺失会报错，不会切换账号或降级匿名。"},
        {"path": "settings.max_pages", "type": "int", "default": 1, "min": 1, "max": 3, "advanced": True,
         "label": "每个订阅最多拉取页数", "help": "每页最多 100 条，1..3 页，共享此源采集超时。动态 API 只提供有限历史；满页时不推进游标，不保证长时间停机后的历史补齐。"},
    ), hidden_core_fields=("feeds", "http2")),
    "aihot": AdapterDescriptor("aihot", "AI 热榜", ("legacy_items", "hot_list")),
    "hn": AdapterDescriptor("hn", "Hacker News", ("legacy_items", "front_page", "article_links", "original_evidence", "subscription_results")),
    "reddit": AdapterDescriptor("reddit", "Reddit", ("legacy_items", "communities", "original_evidence", "subscription_results")),
    "rsshub": AdapterDescriptor("rsshub", "RSS / RSSHub", ("legacy_items", "rss", "atom", "original_evidence", "subscription_results", "configured_local_endpoint"),
                               optional_dependencies=("httpx[http2]",)),
}


def describe_adapter(name: str) -> AdapterDescriptor:
    descriptor = REGISTRY.get(name)
    return deepcopy(descriptor) if descriptor else AdapterDescriptor(name, name + " (兼容适配器)", ("legacy_items",))
