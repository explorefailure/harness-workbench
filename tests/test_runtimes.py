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
        self.assertFalse(rows["claude"]["installable_here"])  # npm installer is Pi-only so far
        self.assertTrue(rows["hermes"]["installable_here"])
        self.assertTrue(rows["claude"]["sign_in"])


class TestShippedNpmAndPrerequisitePins(unittest.TestCase):
    def test_prerequisite_pins_resolve_for_every_supported_platform(self):
        catalog = runtimes.load_catalog()
        for name in ("node", "uv"):
            entry = catalog["prerequisites"][name]
            for version, spec in entry["versions"].items():
                with self.subTest(prerequisite=name, version=version):
                    pin = runtimes._pin(spec)
                    self.assertEqual(version, pin["version"])
                    self.assertEqual({"darwin-arm64", "darwin-x86_64", "linux-x86_64", "linux-aarch64"},
                                     set(pin["platforms"]))
                    for asset in pin["platforms"].values():
                        self.assertRegex(asset["archive_sha256"], r"^[0-9a-f]{64}$")
                        self.assertTrue(asset["url"].endswith(".tar.gz"))

    def test_each_pi_version_names_a_known_node_and_ships_its_lockfile(self):
        catalog = runtimes.load_catalog()
        nodes = catalog["prerequisites"]["node"]["versions"]
        for version, spec in catalog["harnesses"]["pi"]["versions"].items():
            with self.subTest(version=version):
                self.assertIn(spec["node"], nodes)
                pin = runtimes._pin(spec)
                self.assertEqual(spec["node"], pin["node_version"])
                lock = json.loads((runtimes.PACKAGE / spec["lock_dir"] / "package-lock.json").read_text())
                key = "node_modules/" + pin["npm_package"]
                self.assertEqual(version, lock["packages"][key]["version"])
                self.assertEqual(pin["npm_integrity"], lock["packages"][key]["integrity"])

    def test_the_bundled_pi_lockfile_is_the_one_its_pin_binds(self):
        spec = runtimes.load_catalog()["harnesses"]["pi"]["versions"]["1.4.0"]
        lock = (runtimes.PACKAGE / spec["lock_dir"] / "package-lock.json").read_bytes()
        self.assertEqual(runtimes._pin(spec)["installation_lock_sha256"], hashlib.sha256(lock).hexdigest())


def _tree(members, mode=0o755):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data, kind in members:
            info = tarfile.TarInfo(name)
            if kind == "file":
                info.size = len(data)
                info.mode = mode
                archive.addfile(info, io.BytesIO(data))
            elif kind == "dir":
                info.type = tarfile.DIRTYPE
                archive.addfile(info)
            elif kind == "link":
                info.type = tarfile.SYMTYPE
                info.linkname = data
                archive.addfile(info)
            elif kind == "hard":
                info.type = tarfile.LNKTYPE
                info.linkname = data
                archive.addfile(info)
    return buffer.getvalue()


class TestExtractTree(unittest.TestCase):
    def setUp(self):
        self.target = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.target, ignore_errors=True))

    def test_files_dirs_and_inside_links_with_normalized_modes(self):
        raw = _tree([("top", None, "dir"), ("top/bin/node", BINARY, "file"),
                     ("top/lib/npm/cli.js", b"js", "file"),
                     ("top/bin/npm", "../lib/npm/cli.js", "link")])
        self.assertEqual(2, runtimes._extract_tree(raw, "top", self.target))
        self.assertEqual(BINARY, (self.target / "bin/node").read_bytes())
        self.assertEqual(0o755, (self.target / "bin/node").stat().st_mode & 0o777)
        self.assertEqual("../lib/npm/cli.js", os.readlink(self.target / "bin/npm"))

    def test_refusals(self):
        cases = {
            "escaping link": [("top/bin/x", "../../outside", "link")],
            "absolute link": [("top/bin/x", "/etc/passwd", "link")],
            "hard link": [("top/a", b"a", "file"), ("top/b", "top/a", "hard")],
            "dot-dot name": [("top/../evil", b"x", "file")],
            "absolute name": [("/top/evil", b"x", "file")],
            "outside strip": [("other/evil", b"x", "file")],
        }
        for label, members in cases.items():
            with self.subTest(label), tempfile.TemporaryDirectory() as target:
                with self.assertRaises(runtimes.RuntimeStoreError):
                    runtimes._extract_tree(_tree(members), "top", Path(target))


class TestTreeInstallAndNodeChoice(StoreFixture):
    def setUp(self):
        super().setUp()
        self.archive = _tree([("node-v1", None, "dir"), ("node-v1/bin/node", BINARY, "file")])
        (self.package / "node.json").write_text(json.dumps({"versions": {"1.0.0": {
            "version": "1.0.0", "platforms": {"linux-x86_64": {
                "url": "https://example.invalid/node.tar.gz", "strip": "node-v1",
                "archive_sha256": hashlib.sha256(self.archive).hexdigest()}}}}}))
        (self.package / "lock").mkdir()
        (self.package / "lock/package-lock.json").write_text(json.dumps({"packages": {"": {}, "node_modules/x": {}}}))
        self.catalog["prerequisites"] = {"node": {
            "kind": "tree", "executable": "node", "installer": True, "default": "1.0.0",
            "versions": {"1.0.0": {"pin": "node.json#1.0.0"}}}}
        self.catalog["harnesses"]["npmtool"] = {
            "kind": "npm", "executable": "npmtool", "installer": True, "default": "2.0.0",
            "versions": {"2.0.0": {"pin": "pin.json", "lock_dir": "lock", "node": "1.0.0"}}}

    def test_tree_install_records_a_tree_digest(self):
        result = runtimes.add("node", root=self.store, catalog=self.catalog, key="linux-x86_64",
                              fetch=self.fetch())
        self.assertEqual(BINARY, (self.store / "node/1.0.0/bin/node").read_bytes())
        self.assertRegex(result["install_record"]["tree_sha256"], r"^[0-9a-f]{64}$")
        self.assertIn(str(self.store / "node/1.0.0/bin"), runtimes.bin_dirs(self.store, self.catalog))

    def plan_npm(self, path_node):
        return runtimes.plan("npmtool", root=self.store, catalog=self.catalog,
                             key="linux-x86_64", path_node=path_node)

    def test_exact_node_on_path_is_used_and_nothing_extra_downloaded(self):
        steps = self.plan_npm({"path": "/usr/bin/node", "version": "1.0.0"})
        self.assertEqual("path", steps["node"]["source"])
        self.assertEqual([], steps["prerequisites"])

    def test_other_node_on_path_means_the_pinned_node_is_planned_into_the_store(self):
        for path_node in (None, {"path": "/usr/bin/node", "version": "0.9.0"}):
            with self.subTest(path_node=path_node):
                steps = self.plan_npm(path_node)
                self.assertEqual("store", steps["node"]["source"])
                self.assertEqual(["https://example.invalid/node.tar.gz"],
                                 [d["url"] for d in steps["downloads"] if d["url"].startswith("https")])

    def test_store_node_already_installed_is_reused(self):
        runtimes.add("node", root=self.store, catalog=self.catalog, key="linux-x86_64", fetch=self.fetch())
        steps = self.plan_npm(None)
        self.assertEqual("store", steps["node"]["source"])
        self.assertEqual([], steps["prerequisites"])


def _git(cwd, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                          text=True, env=env).stdout.strip()


class TestHermesSource(unittest.TestCase):
    """The uv-source checks against a real local repository with an annotated tag."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q")
        (self.repo / "uv.lock").write_text("lock\n")
        (self.repo / "app.py").write_text("print(1)\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "release")
        _git(self.repo, "tag", "-a", "v1.0.0", "-m", "v1.0.0")
        self.pin = {"release_tag": "v1.0.0",
                    "source_commit": _git(self.repo, "rev-parse", "HEAD"),
                    "tag_object": _git(self.repo, "rev-parse", "v1.0.0^{tag}"),
                    "uv_lock_sha256": hashlib.sha256(b"lock\n").hexdigest()}

    def test_matching_checkout_passes_and_tree_digest_is_checked_when_pinned(self):
        identity = runtimes._verify_hermes_source(self.repo, self.pin)
        self.assertEqual(self.pin["source_commit"], identity["source_commit"])
        tree = runtimes._source_tree_digest(self.repo)["source_tree_sha256"]
        self.assertEqual(tree, runtimes._verify_hermes_source(
            self.repo, dict(self.pin, source_tree_sha256=tree))["source_tree_sha256"])
        with self.assertRaisesRegex(runtimes.RuntimeStoreError, "tracked source"):
            runtimes._verify_hermes_source(self.repo, dict(self.pin, source_tree_sha256="0" * 64))

    def test_each_mismatch_is_refused(self):
        cases = {"commit": dict(self.pin, source_commit="0" * 40),
                 "tag object": dict(self.pin, tag_object="0" * 40),
                 "uv.lock": dict(self.pin, uv_lock_sha256="0" * 64)}
        for label, pin in cases.items():
            with self.subTest(label), self.assertRaises(runtimes.RuntimeStoreError):
                runtimes._verify_hermes_source(self.repo, pin)
        (self.repo / "app.py").write_text("changed\n")
        with self.assertRaisesRegex(runtimes.RuntimeStoreError, "dirty"):
            runtimes._verify_hermes_source(self.repo, self.pin)

    def test_failed_uv_sync_leaves_no_version_dir(self):
        fake = self.tmp / "fakeuv"
        fake.mkdir()
        (fake / "uv").write_text("#!/bin/sh\necho boom >&2\nexit 7\n")
        (fake / "uv").chmod(0o755)
        store = self.tmp / "store"
        steps = {"version": "1.0.0", "platform": "linux-x86_64",
                 "uv": {"version": "9", "source": "path", "bin": str(fake)}}
        entry = {"executable": "hermes"}
        spec = {"repository": self.repo.as_uri(), "pin": "unused"}
        with mock.patch.object(runtimes, "_pin", return_value=self.pin):
            with self.assertRaisesRegex(runtimes.RuntimeStoreError, "uv sync failed"):
                runtimes._install_uv_source("hermes", entry, spec, steps, store, {}, None, "linux-x86_64")
        self.assertFalse((store / "hermes" / "1.0.0").exists())

    def test_shipped_hermes_entries_plan_a_pinned_clone(self):
        with tempfile.TemporaryDirectory() as store:
            for version in ("0.22.5", "0.20.5"):
                with self.subTest(version=version):
                    steps = runtimes.plan("hermes", version, root=Path(store), key="linux-x86_64")
                    pin = runtimes._pin(runtimes.load_catalog()["harnesses"]["hermes"]["versions"][version])
                    self.assertIn("--branch " + pin["release_tag"], steps["downloads"][-2]["url"])
                    self.assertEqual("0.12.21", steps["uv"]["version"])


class TestSubjectEnvironment(unittest.TestCase):
    """Store harnesses are found only when PATH has none, and run on their own Node."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.store = self.tmp / "store"
        self.catalog = runtimes.load_catalog()

    def fake_install(self, name, version, executable, record):
        directory = self.store / name / version
        (directory / "bin").mkdir(parents=True)
        launcher = directory / "bin" / executable
        launcher.write_text("#!/bin/sh\n")
        launcher.chmod(0o755)
        record = dict(record, schema=runtimes.RECORD_SCHEMA, harness=name, version=version)
        (directory / "install.json").write_text(json.dumps(record))
        return directory

    def path_with(self, *names):
        directory = self.tmp / "userbin"
        directory.mkdir(exist_ok=True)
        for name in names:
            (directory / name).write_text("#!/bin/sh\n")
            (directory / name).chmod(0o755)
        return str(directory) + os.pathsep + "/usr/bin:/bin"

    def env(self, path, **extra):
        return runtimes.subject_environment(dict({"PATH": path}, **extra), root=self.store,
                                            catalog=self.catalog)

    def test_store_pi_goes_in_front_with_its_own_node_when_path_has_none(self):
        pi = self.fake_install("pi", "1.4.0", "pi", {"kind": "npm",
                               "node": {"version": "26.13.0", "source": "store", "bin": "/store/node/bin"}})
        result = self.env(self.path_with("node"))
        parts = result["environment"]["PATH"].split(os.pathsep)
        self.assertEqual([str(pi / "bin"), "/store/node/bin"], parts[:2])
        self.assertEqual([{"harness": "pi", "version": "1.4.0", "path": str(pi / "bin"), "node": "26.13.0"}],
                         result["used"])

    def test_a_pi_already_on_path_wins_and_nothing_changes(self):
        self.fake_install("pi", "1.4.0", "pi", {"kind": "npm", "node": {"bin": "/store/node/bin"}})
        path = self.path_with("pi", "node")
        result = self.env(path)
        self.assertEqual(path, result["environment"]["PATH"])
        self.assertEqual([], result["used"])

    def test_store_hermes_sets_its_source_root_unless_one_is_set(self):
        self.fake_install("hermes", "0.22.5", "hermes", {"kind": "uv-source", "source_root": "/store/src"})
        self.assertEqual("/store/src", self.env(self.path_with())["environment"]["HERMES_AGENT_ROOT"])
        kept = self.env(self.path_with(), HERMES_AGENT_ROOT="/mine")["environment"]
        self.assertEqual("/mine", kept["HERMES_AGENT_ROOT"])

    def test_reality_check_hermes_takes_the_certified_store_copy_over_another_path_version(self):
        self.fake_install("hermes", "0.22.5", "hermes", {"kind": "uv-source", "source_root": "/store/0.22.5"})
        certified = self.fake_install("hermes", "0.20.5", "hermes", {"kind": "uv-source", "source_root": "/store/0.20.5"})
        path = self.path_with("hermes")
        mine = self.tmp / "mine"; mine.mkdir()  # not a checkout at the certified commit
        result = runtimes.subject_environment({"PATH": path, "HERMES_AGENT_ROOT": str(mine)}, root=self.store,
                                              catalog=self.catalog, versions={"hermes": "0.20.5"})
        self.assertEqual(str(certified / "bin"), result["environment"]["PATH"].split(os.pathsep)[0])
        self.assertEqual("/store/0.20.5", result["environment"]["HERMES_AGENT_ROOT"])
        self.assertEqual("0.20.5", result["used"][0]["version"])
        # Without a wanted version the PATH copy still wins.
        self.assertEqual(path, self.env(path)["environment"]["PATH"])

    def test_a_path_hermes_at_the_certified_commit_still_wins(self):
        self.fake_install("hermes", "0.20.5", "hermes", {"kind": "uv-source", "source_root": "/store/0.20.5"})
        path = self.path_with("hermes")
        with mock.patch.object(runtimes, "_hermes_path_matches", return_value=True) as matches:
            result = runtimes.subject_environment({"PATH": path}, root=self.store, catalog=self.catalog,
                                                  versions={"hermes": "0.20.5"})
        self.assertEqual(path, result["environment"]["PATH"])
        self.assertEqual([], result["used"])
        pinned = runtimes._pin(self.catalog["harnesses"]["hermes"]["versions"]["0.20.5"])["source_commit"]
        self.assertEqual(pinned, matches.call_args[0][1])

    def test_empty_or_incomplete_store_changes_nothing(self):
        path = self.path_with()
        self.assertEqual(path, self.env(path)["environment"]["PATH"])
        (self.store / "pi" / "1.4.0" / "bin").mkdir(parents=True)  # no install.json
        self.assertEqual(path, self.env(path)["environment"]["PATH"])


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
