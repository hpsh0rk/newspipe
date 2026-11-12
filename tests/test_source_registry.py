"""Code-owned descriptions compose the CLI schema without executing sources."""
from copy import deepcopy
from dataclasses import replace
import importlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import yaml
from newspipe import config, edit
from newspipe.errors import ConfigError
from test_news_pipeline import _src, _write_registry


class SourceRegistryTests(unittest.TestCase):
    def setUp(self):
        try:
            self.registry = importlib.import_module("newspipe.source_registry")
        except ModuleNotFoundError:
            self.fail("the code-owned source registry is not implemented")

    def descriptor(self, **kw):
        fields = (
            {"path": "settings.comments", "type": "bool", "default": True, "label": "Comments"},
            {"path": "settings.limit", "type": "int", "default": 10, "label": "Limit"},
            {"path": "settings.tags", "type": "list", "default": ["news"], "label": "Tags"},
            {"path": "settings.token_env", "type": "str", "default": "", "label": "Token reference",
             "credential": True, "reference": "environment"},
        )
        return self.registry.AdapterDescriptor(**{"name": "aihot", "label": "Synthetic source",
                                                 "capabilities": ("legacy_items",), "fields": fields, **kw})

    def test_builtin_and_custom_descriptions_need_no_adapter_or_sdk_imports(self):
        with patch("newspipe.net.request", side_effect=AssertionError("network")), patch(
                "importlib.import_module", side_effect=AssertionError("provider import")):
            schema = config.describe_schema()
            self.assertEqual(set(schema["adapters"]), set(schema["adapter_descriptors"]))
            for name in ("aihot", "hn", "reddit", "rsshub"):
                descriptor = self.registry.describe_adapter(name)
                self.assertTrue(descriptor.label)
                self.assertIn("legacy_items", descriptor.capabilities)
            custom = self.registry.describe_adapter("my_custom")
            self.assertEqual(custom.name, "my_custom")
            self.assertEqual(custom.capabilities, ("legacy_items",))
            self.assertEqual(custom.optional_dependencies, ())

    def test_invalid_types_duplicate_paths_hidden_requirements_and_literals_rejected(self):
        bad_fields = (
            ({"path": "settings.x", "type": "widget", "default": ""},),
            ({"path": "settings.x", "type": "int", "default": False},),
            ({"path": "settings.x", "type": "str", "default": "", "required": True},),
            ({"path": "settings.token", "type": "str", "default": "sensitive-input", "credential": True},),
            ({"path": "settings.x", "type": "str", "default": ""},) * 2,
            ({"path": "settings.x", "type": "str", "default": ""},
             {"path": "settings.x.child", "type": "str", "default": ""}),
        )
        for fields in bad_fields:
            with self.subTest(fields=fields):
                with self.assertRaises(ValueError):
                    self.descriptor(fields=fields)

    def test_metadata_conflicts_with_core_schema_are_rejected(self):
        for path in ("enabled", "fetch.custom", "card.title", "notify.policy", "feeds"):
            with self.subTest(path=path):
                descriptor = self.descriptor(fields=({"path": path, "type": "str", "default": ""},))
                with patch.dict(self.registry.REGISTRY, {"aihot": descriptor}):
                    with self.assertRaises(ConfigError):
                        config.describe_schema()

    def test_current_values_keep_false_zero_empty_lists_and_unknown_extras(self):
        descriptor = self.descriptor()
        extra = {"settings": {"comments": False, "limit": 0, "tags": [], "token_env": "NEWS_API_TOKEN",
                              "unknown": {"keep": 1}}, "custom_legacy": [1]}
        source = replace(_src(), extra=deepcopy(extra))
        with patch.dict(self.registry.REGISTRY, {"aihot": descriptor}):
            values = config.source_values(source)
            self.assertIs(values["settings.comments"], False)
            self.assertEqual(values["settings.limit"], 0)
            self.assertEqual(values["settings.tags"], [])
            self.assertEqual(values["settings.token_env"], "NEWS_API_TOKEN")
            missing = config.source_values(_src())
            self.assertEqual(missing["settings.limit"], 10)
            missing["settings.tags"].append("mutated")
            self.assertEqual(config.source_values(_src())["settings.tags"], ["news"])
            schema = config.describe_schema()
            self.assertFalse(schema["adapter_descriptors"]["aihot"]["editable"])
            self.assertNotIn("settings.limit", {f["path"] for f in schema["fields"]})
        self.assertEqual(source.extra, extra)

    def test_cli_check_edit_and_copy_roundtrip_and_hash_conflict(self):
        body = {"adapter": "aihot", "fetch": {"trigger": "manual"},
                "settings": {"comments": False, "limit": 0, "tags": [], "token_env": "NEWS_API_TOKEN"},
                "unknown": {"keep": "legacy"}}
        with TemporaryDirectory() as tmp, patch.dict(self.registry.REGISTRY, {"aihot": self.descriptor()}):
            news = _write_registry(Path(tmp), {"s": body})
            original = (news / "sources.yaml").read_bytes()
            base = edit.file_hash(news / "sources.yaml")
            changed = {**body, "settings": {**body["settings"], "limit": 2}}
            checked = edit.set_source(news, "s", changed, dry_run=True, base_hash=base)
            self.assertTrue(checked["ok"])
            self.assertEqual(original, (news / "sources.yaml").read_bytes())
            self.assertTrue(edit.set_source(news, "s", changed, base_hash=base)["ok"])
            self.assertEqual(edit.set_source(news, "s", body, base_hash=base)["code"], "E_CONFLICT")
            raw = yaml.safe_load((news / "sources.yaml").read_text())["sources"]["s"]
            self.assertTrue(edit.set_source(news, "copy", raw, create_only=True)["ok"])
            sources = config.load(news).sources
            self.assertEqual(sources["s"].extra, sources["copy"].extra)
            self.assertEqual(sources["s"].extra["unknown"], {"keep": "legacy"})
            for invalid in ("two", False, float("inf")):
                bad = {**body, "settings": {**body["settings"], "limit": invalid}}
                before = (news / "sources.yaml").read_bytes()
                self.assertEqual(edit.set_source(news, "s", bad)["code"], "E_VALIDATION")
                self.assertEqual(before, (news / "sources.yaml").read_bytes())
            literal = {**body, "settings": {**body["settings"], "token_env": "Bearer sensitive-input"}}
            refused = edit.set_source(news, "s", literal)
            self.assertEqual(refused["code"], "E_VALIDATION")
            self.assertNotIn("sensitive-input", str(refused))


if __name__ == "__main__":
    unittest.main()
