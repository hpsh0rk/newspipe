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
    if field.get("credential") and value and not re.fullmatch(r"[A-Z_][A-Z0-9_]*", value):
        raise ValueError("Credential setting must name an environment variable, never a literal secret.")


@dataclass(frozen=True)
class AdapterDescriptor:
    name: str
    label: str
    capabilities: tuple[str, ...]
    fields: tuple[dict, ...] = ()
    optional_dependencies: tuple[str, ...] = ()

    def __post_init__(self):
        if (not isinstance(self.name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.name)
                or not isinstance(self.label, str) or not self.label
                or any(not isinstance(group, tuple) or any(not isinstance(v, str) or not v for v in group)
                       for group in (self.capabilities, self.optional_dependencies))
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
                "optional_dependencies": list(self.optional_dependencies), "editable": False}


REGISTRY = {
    "aihot": AdapterDescriptor("aihot", "AI 热榜", ("legacy_items", "hot_list")),
    "hn": AdapterDescriptor("hn", "Hacker News", ("legacy_items", "front_page", "article_links")),
    "reddit": AdapterDescriptor("reddit", "Reddit", ("legacy_items", "communities")),
    "rsshub": AdapterDescriptor("rsshub", "RSS / RSSHub", ("legacy_items", "rss", "configured_local_endpoint"),
                               optional_dependencies=("httpx[http2]",)),
}


def describe_adapter(name: str) -> AdapterDescriptor:
    descriptor = REGISTRY.get(name)
    return deepcopy(descriptor) if descriptor else AdapterDescriptor(name, name + " (兼容适配器)", ("legacy_items",))
