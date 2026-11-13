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
    if field.get("credential") and value and not re.fullmatch(r"[A-Z_][A-Z0-9_]*", value):
        raise ValueError("Credential setting must name an environment variable, never a literal secret.")


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
