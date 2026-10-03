"""历史哨兵兼容：`hermes` 等价于 `host`（老配置不用改）。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import yaml

from newspipe import llm

NL = chr(10)


class LegacySentinelTests(unittest.TestCase):
    def test_hermes_sentinel_still_follows_host(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "config.yaml").write_text(yaml.safe_dump({
                "model": {"provider": "local", "base_url": "http://127.0.0.1:9/v1",
                          "default": "some-model"},
                "providers": {"local": {"base_url": "http://127.0.0.1:9/v1", "key_env": "K"}},
            }, allow_unicode=True), encoding="utf-8")
            (home / ".env").write_text("K=sk-x" + NL, encoding="utf-8")
            old = os.environ.get("NEWSPIPE_HOST_HOME")
            os.environ["NEWSPIPE_HOST_HOME"] = str(home)
            try:
                ref = llm.resolve_model("summarize", {"default": "hermes"},
                                        host_config_path=home / "config.yaml",
                                        env_path=home / ".env")
            finally:
                if old is None:
                    os.environ.pop("NEWSPIPE_HOST_HOME", None)
                else:
                    os.environ["NEWSPIPE_HOST_HOME"] = old
            self.assertEqual(ref.model, "some-model")
            self.assertEqual(ref.provider, "local")
            self.assertEqual(ref.api_key, "sk-x")

    def test_missing_host_home_is_actionable(self) -> None:
        """没配宿主目录时报错必须给可执行的修法，而不是猜一个目录。"""
        from newspipe import hostenv
        from newspipe.errors import ConfigError

        keys = list(hostenv.HOST_HOME_ENV_KEYS)
        saved = {k: os.environ.pop(k, None) for k in keys}
        try:
            with self.assertRaises(ConfigError) as ctx:
                hostenv.host_home(required=True)
            msg = str(ctx.exception)
            self.assertIn("NEWSPIPE_HOST_HOME", msg)
            self.assertIn("explicit", msg)
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
