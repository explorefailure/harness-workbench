"""The shared runtime store behind `hwb harness` (no network: fetch is injected)."""
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from harness_workbench import runtimes  # noqa: E402

BINARY = b"#!/bin/sh\necho 9.9.9\n"


def _tar(members):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data, kind in members:
            info = tarfile.TarInfo(name)
            if kind == "file":
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
            else:
                info.type = tarfile.SYMTYPE
                info.linkname = data
                archive.addfile(info)
    return buffer.getvalue()


def _zip(name, data):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, data)
    return buffer.getvalue()


class StoreFixture(unittest.TestCase):
    """A package dir with one fake binary harness, and a separate empty store."""

    url = "https://example.invalid/tool-linux-x64.tar.gz"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.package = self.tmp / "pkg"
        self.package.mkdir()
        self.store = self.tmp / "store"
        self.archive = _tar([("tool", BINARY, "file")])
        self.write_pin(self.url, self.archive, BINARY)
        self.catalog = {"schema": runtimes.CATALOG_SCHEMA, "harnesses": {"tool": {
            "display": "Tool", "kind": "binary", "executable": "tool", "installer": True,
            "default": "9.9.9", "versions": {"9.9.9": {"pin": "pin.json", "member": "tool"}},
            "sign_in": None, "modes": {"your_task": True}}}}
        patcher = mock.patch.object(runtimes, "PACKAGE", self.package)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.fetched = []

    def write_pin(self, url, archive, binary, key="linux-x86_64"):
        (self.package / "pin.json").write_text(json.dumps({"platforms": {key: {
            "url": url, "archive_sha256": hashlib.sha256(archive).hexdigest(),
            "binary_sha256": hashlib.sha256(binary).hexdigest()}}}))

    def fetch(self, payload=None):
        def inner(url):
            self.fetched.append(url)
            return self.archive if payload is None else payload
        return inner

    def add(self, **kwargs):
        kwargs.setdefault("fetch", self.fetch())
        return runtimes.add("tool", root=self.store, catalog=self.catalog,
                            key="linux-x86_64", **kwargs)

    def assert_nothing_installed(self):
        self.assertFalse((self.store / "tool").exists())
        partial = self.store / ".partial"
        self.assertEqual([], list(partial.iterdir()) if partial.exists() else [])


class TestStoreRoot(unittest.TestCase):
    def test_override_then_xdg_then_home(self):
        self.assertEqual(Path("/x/store"), runtimes.store_root({"HWB_RUNTIME_STORE": "/x/store",
                                                                "XDG_DATA_HOME": "/d"}))
        self.assertEqual(Path("/d/hwb/runtimes"), runtimes.store_root({"XDG_DATA_HOME": "/d"}))
        self.assertEqual(Path.home() / ".local/share/hwb/runtimes", runtimes.store_root({}))


class TestShippedCatalog(unittest.TestCase):
    def test_every_entry_points_at_a_real_pin_and_none_copies_a_hash(self):
        catalog = runtimes.load_catalog()
        self.assertEqual({"claude", "codex", "deepseek", "hermes", "opencode", "pi"},
                         set(catalog["harnesses"]))
        raw = runtimes.CATALOG_PATH.read_text()
        self.assertNotRegex(raw, r"[0-9a-f]{64}", "the catalog must reference pins, not copy hashes")
        for name, entry in catalog["harnesses"].items():
            self.assertIn(entry["default"], entry["versions"], name)
            for version, spec in entry["versions"].items():
                with self.subTest(harness=name, version=version):
                    pin = runtimes._pin(spec)
                    self.assertEqual(version, pin["version"])

    def test_binary_pins_cover_the_platform_names_this_module_produces(self):
        pin = runtimes._pin(runtimes.load_catalog()["harnesses"]["opencode"]["versions"]["1.19.5"])
        self.assertTrue({"darwin-arm64", "linux-x86_64"} <= set(pin["platforms"]))

    def test_describe_reports_path_copies_and_installability(self):
        with tempfile.TemporaryDirectory() as store:
            rows = {r["harness"]: r for r in runtimes.describe(
                Path(store), which=lambda name: "/bin/" + name if name == "pi" else None)}
        self.assertEqual("/bin/pi", rows["pi"]["on_path"])
        self.assertIsNone(rows["opencode"]["on_path"])
        self.assertFalse(rows["pi"]["installable_here"])  # npm installer not built yet
        self.assertTrue(rows["claude"]["sign_in"])


class TestAdd(StoreFixture):
    def test_installs_verified_binary_with_record(self):
        result = self.add()
        target = self.store / "tool" / "9.9.9" / "bin" / "tool"
        self.assertTrue(result["installed"])
        self.assertEqual(BINARY, target.read_bytes())
        self.assertTrue(os.access(target, os.X_OK))
        record = runtimes.read_install_record(self.store / "tool" / "9.9.9")
        self.assertEqual("9.9.9", record["version"])
        self.assertEqual(hashlib.sha256(BINARY).hexdigest(), record["files"]["bin/tool"])
        self.assertEqual([self.url], [d["url"] for d in record["downloads"]])
        self.assertEqual([str(target.parent)], runtimes.bin_dirs(self.store, self.catalog))

    def test_zip_archives_work_too(self):
        url = "https://example.invalid/tool.zip"
        self.archive = _zip("tool", BINARY)
        self.write_pin(url, self.archive, BINARY)
        self.add()
        self.assertEqual(BINARY, (self.store / "tool/9.9.9/bin/tool").read_bytes())

    def test_second_add_is_a_no_op_without_network(self):
        self.add()
        self.fetched.clear()
        self.assertFalse(self.add()["installed"])
        self.assertEqual([], self.fetched)

    def test_archive_hash_mismatch_installs_nothing(self):
        with self.assertRaisesRegex(runtimes.RuntimeStoreError, "archive"):
            self.add(fetch=self.fetch(self.archive + b"x"))
        self.assert_nothing_installed()

    def test_binary_hash_mismatch_installs_nothing(self):
        self.write_pin(self.url, self.archive, BINARY + b"other")
        with self.assertRaisesRegex(runtimes.RuntimeStoreError, "binary"):
            self.add()
        self.assert_nothing_installed()

    def test_symlink_member_is_refused(self):
        self.archive = _tar([("tool", "/etc/passwd", "link")])
        self.write_pin(self.url, self.archive, BINARY)
        with self.assertRaisesRegex(runtimes.RuntimeStoreError, "regular file"):
            self.add()
        self.assert_nothing_installed()

    def test_unpinned_platform_is_refused_before_any_download(self):
        with self.assertRaisesRegex(runtimes.RuntimeStoreError, "no pinned build"):
            runtimes.add("tool", root=self.store, catalog=self.catalog, key="sunos-sparc",
                         fetch=self.fetch())
        self.assertEqual([], self.fetched)

    def test_unknown_version_and_harness_are_refused(self):
        with self.assertRaisesRegex(runtimes.RuntimeStoreError, "not in the catalog"):
            self.add(version="1.0.0")
        with self.assertRaisesRegex(runtimes.RuntimeStoreError, "unknown harness"):
            runtimes.add("nope", root=self.store, catalog=self.catalog)

    def test_directory_without_record_is_not_overwritten(self):
        leftover = self.store / "tool" / "9.9.9"
        leftover.mkdir(parents=True)
        (leftover / "keep").write_text("mine")
        with self.assertRaisesRegex(runtimes.RuntimeStoreError, "without a valid install record"):
            self.add()
        self.assertEqual("mine", (leftover / "keep").read_text())

    def test_failure_while_writing_cleans_the_staging_dir(self):
        with mock.patch.object(runtimes.os, "rename", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.add()
        self.assert_nothing_installed()

    def test_unsafe_names_cannot_escape_the_store(self):
        for bad in ("..", ".hidden", "a/b", ""):
            with self.subTest(bad=bad), self.assertRaises(runtimes.RuntimeStoreError):
                runtimes.version_dir(self.store, "tool", bad)


class TestRemove(StoreFixture):
    def test_removes_one_version(self):
        self.add()
        removed = runtimes.remove("tool", "9.9.9", root=self.store, catalog=self.catalog)
        self.assertEqual([str(self.store / "tool" / "9.9.9")], removed)
        self.assertFalse((self.store / "tool" / "9.9.9").exists())

    def test_missing_version_is_an_error(self):
        with self.assertRaisesRegex(runtimes.RuntimeStoreError, "not installed"):
            runtimes.remove("tool", "9.9.9", root=self.store, catalog=self.catalog)

    def test_symlinked_version_pointing_outside_is_not_followed(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "precious").write_text("x")
        (self.store / "tool").mkdir(parents=True)
        os.symlink(outside, self.store / "tool" / "9.9.9")
        with self.assertRaisesRegex(runtimes.RuntimeStoreError, "inside the store"):
            runtimes.remove("tool", "9.9.9", root=self.store, catalog=self.catalog)
        self.assertTrue((outside / "precious").exists())


class TestCli(unittest.TestCase):
    def run_hwb(self, *args, store):
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), HWB_RUNTIME_STORE=store)
        return subprocess.run([sys.executable, "-m", "harness_workbench", *args], env=env,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)

    def test_add_without_a_terminal_or_yes_downloads_nothing(self):
        with tempfile.TemporaryDirectory() as store:
            done = self.run_hwb("harness", "add", "opencode", store=store)
            self.assertNotEqual(0, done.returncode)
            self.assertIn("--yes", done.stderr)
            self.assertFalse(Path(store, "opencode").exists())

    def test_list_json_names_every_catalog_harness(self):
        with tempfile.TemporaryDirectory() as store:
            done = self.run_hwb("harness", "list", "--json", store=store)
        self.assertEqual(0, done.returncode, done.stderr)
        doc = json.loads(done.stdout)
        self.assertEqual(store, doc["store"])
        self.assertEqual(sorted(runtimes.load_catalog()["harnesses"]),
                         [row["harness"] for row in doc["harnesses"]])


if __name__ == "__main__":
    unittest.main()
