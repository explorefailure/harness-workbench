"""Named model endpoints (`hwb model`) and the builders' `init --model`."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from harness_workbench import models  # noqa: E402


class ModelStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = {"XDG_CONFIG_HOME": self.tmp.name}

    def test_add_list_remove_round_trip_with_private_file(self):
        saved = models.add("local", "http://127.0.0.1:11434/v1", "gpt-oss:20b", environ=self.env)
        self.assertFalse(saved["replaced"])
        file = Path(self.tmp.name) / "hwb" / "models.json"
        self.assertEqual(str(file), saved["path"])
        self.assertEqual(0o600, stat.S_IMODE(file.stat().st_mode))
        self.assertEqual({"local": {"base_url": "http://127.0.0.1:11434/v1", "model": "gpt-oss:20b"}},
                         models.load(self.env))
        again = models.add("local", "http://127.0.0.1:8081/v1", "qwen", "MY_KEY", environ=self.env)
        self.assertTrue(again["replaced"])
        rows = models.describe(dict(self.env, MY_KEY="x" * 12))
        self.assertEqual([("local", "MY_KEY", True)], [(r["name"], r["api_key_env"], r["api_key_set"]) for r in rows])
        self.assertFalse(models.describe(self.env)[0]["api_key_set"])
        models.remove("local", environ=self.env)
        self.assertEqual({}, models.load(self.env))
        with self.assertRaisesRegex(models.ModelError, "no saved model"):
            models.remove("local", environ=self.env)

    def test_key_values_and_bad_endpoints_are_refused(self):
        for kwargs in ({"api_key_env": "sk-live-abcdef0123456789"}, {"api_key_env": "has space"},
                       {"base_url": "http://user:pw@host/v1"}, {"base_url": "ftp://host/v1"},
                       {"base_url": "http://host/v1?key=1"}, {"model": "two words"}, {"model": "-flag"}):
            args = dict({"base_url": "http://127.0.0.1:11434/v1", "model": "m"}, **kwargs)
            with self.subTest(kwargs=kwargs), self.assertRaises(models.ModelError):
                models.add("local", args["base_url"], args["model"], args.get("api_key_env"), environ=self.env)
        for name in ("", "Upper", "-x", "a/b", "x" * 65):
            with self.subTest(name=name), self.assertRaises(models.ModelError):
                models.add(name, "http://h/v1", "m", environ=self.env)
        self.assertFalse((Path(self.tmp.name) / "hwb" / "models.json").exists())

    def test_a_broken_file_is_refused_not_overwritten(self):
        file = Path(self.tmp.name) / "hwb" / "models.json"
        file.parent.mkdir(parents=True)
        file.write_text("{not json")
        with self.assertRaisesRegex(models.ModelError, "not valid JSON"):
            models.add("local", "http://h/v1", "m", environ=self.env)
        self.assertEqual("{not json", file.read_text())

    def test_each_builder_gets_the_provider_its_config_validates(self):
        models.add("open", "http://127.0.0.1:11434/v1", "m1", environ=self.env)
        models.add("keyed", "https://example.invalid/v1", "m2", "MY_KEY", environ=self.env)
        self.assertEqual({"mode": "local", "base_url": "http://127.0.0.1:11434/v1", "model": "m1"},
                         models.provider_for("open", "hermes", self.env))
        self.assertEqual("local", models.provider_for("open", "opencode", self.env)["mode"])
        self.assertEqual({"mode": "compatible", "base_url": "https://example.invalid/v1", "model": "m2",
                          "api_key_env": "MY_KEY"}, models.provider_for("keyed", "opencode", self.env))
        self.assertEqual("compatible", models.provider_for("open", "pi", self.env)["mode"])
        with self.assertRaisesRegex(models.ModelError, "hwb model add missing"):
            models.provider_for("missing", "pi", self.env)


class BuilderInitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = dict(os.environ, XDG_CONFIG_HOME=self.tmp.name, PYTHONPATH=str(ROOT / "src"))
        self.env.pop("HWB_RUNTIME_STORE", None)

    def hwb(self, module, *args):
        return subprocess.run([sys.executable, "-m", module, *map(str, args)], env=self.env,
                              capture_output=True, text=True, timeout=120)

    def test_model_cli_and_each_builder_init(self):
        added = self.hwb("harness_workbench", "model", "add", "local", "--url", "http://127.0.0.1:11434/v1",
                         "--model", "gpt-oss:20b")
        self.assertEqual(0, added.returncode, added.stderr)
        listed = json.loads(self.hwb("harness_workbench", "model", "list", "--json").stdout)
        self.assertEqual(["local"], [m["name"] for m in listed["models"]])
        for builder in ("hermes", "opencode"):
            dest = Path(self.tmp.name) / builder
            result = self.hwb(f"harness_workbench._{builder}_cli", "init", dest, "--model", "local")
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            provider = json.loads((dest / "adapter_config.json").read_text())["provider"]
            self.assertEqual({"mode": "local", "base_url": "http://127.0.0.1:11434/v1", "model": "gpt-oss:20b"},
                             provider)
        dest = Path(self.tmp.name) / "pi"
        result = self.hwb("harness_workbench._pi_cli", "init", dest, "--model", "local")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        config = json.loads((dest / "model_adapter_config.json").read_text())
        self.assertEqual("gpt-oss:20b", config["provider"]["model"])
        self.assertEqual("compatible", config["provider"]["mode"])
        self.assertIn("model_adapter_config.json", config["inputs"])
        # configure: --model is a saved name when no endpoint is given
        result = self.hwb("harness_workbench._pi_cli", "configure", dest, "--model", "local",
                          "--config-name", "again.json")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual("http://127.0.0.1:11434/v1",
                         json.loads((dest / "again.json").read_text())["provider"]["base_url"])

    def test_unknown_model_or_old_pi_creates_nothing(self):
        for module in ("_hermes_cli", "_opencode_cli", "_pi_cli"):
            dest = Path(self.tmp.name) / module
            result = self.hwb("harness_workbench." + module, "init", dest, "--model", "nope")
            self.assertEqual(2, result.returncode, result.stdout + result.stderr)
            self.assertIn("no saved model named 'nope'", result.stderr)
            self.assertFalse(dest.exists())
        dest = Path(self.tmp.name) / "old-pi"
        result = self.hwb("harness_workbench._pi_cli", "init", dest, "--profile", "baseline", "--model", "x")
        self.assertEqual(2, result.returncode)
        self.assertIn("Pi 1.x profile", result.stderr)
        self.assertFalse(dest.exists())

    def test_cli_refuses_a_key_value(self):
        result = self.hwb("harness_workbench", "model", "add", "x", "--url", "http://h/v1", "--model", "m",
                          "--api-key-env", "sk-abc123-secret")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("never the key itself", result.stderr)
        self.assertFalse((Path(self.tmp.name) / "hwb" / "models.json").exists())


if __name__ == "__main__":
    unittest.main()
