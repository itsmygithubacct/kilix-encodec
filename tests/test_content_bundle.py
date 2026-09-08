"""Real Git fixtures for source identity, independent of archive interpretation."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
import zipfile
import zlib


BUILDER = Path(__file__).resolve().parents[1] / "tools/build_content_bundle.py"


class BundleIdentity(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="kenc-bundle-")
        self.root = Path(self.temporary.name)
        self.repository = self.root / "source"
        self.repository.mkdir()
        self.environment = {key: value for key, value in os.environ.items()
                            if not key.startswith("GIT_")}
        self.git("init", "-q")
        self.git("config", "user.name", "itsmygithubacct")
        self.git("config", "user.email", "itsmygithubacct@users.noreply.github.com")
        self.package = self.repository / "src/kilix_content"
        self.package.mkdir(parents=True)
        for name in ("__init__.py", "installed.py", "receipt.py", "catalog.py"):
            (self.package / name).write_text("# Original exact source: $Format:%H$\n")
        (self.repository / "LICENSE").write_text("Original license\n")
        (self.repository / ".gitattributes").write_text("src/kilix_content/installed.py export-ignore\nsrc/kilix_content/receipt.py export-subst\n")
        self.commit = self.save()

    def tearDown(self):
        self.temporary.cleanup()

    def git(self, *arguments):
        return subprocess.check_output(["/usr/bin/git", "-C", str(self.repository), *arguments],
                                       env=self.environment, stderr=subprocess.DEVNULL).decode().strip()

    def save(self):
        self.git("add", ".")
        self.git("commit", "-qm", "Bundle identity fixture")
        return self.git("rev-parse", "HEAD")

    def build(self, label, commit=None, environment=None, success=True):
        output = self.root / label
        process = subprocess.run(["/usr/bin/python3", "-I", "-B", str(BUILDER),
            "--source", str(self.repository), "--commit", commit or self.commit,
            "--output", str(output)], env=environment or self.environment,
            capture_output=True, timeout=15)
        if not success:
            self.assertNotEqual(process.returncode, 0)
            self.assertFalse(output.exists())
            return
        self.assertEqual(process.returncode, 0, process.stderr.decode())
        return output

    def assert_same(self, first, second):
        for path in first.iterdir():
            self.assertEqual(path.read_bytes(), (second / path.name).read_bytes(), path.name)

    def test_exact_population_ignores_tracked_and_local_archive_attributes(self):
        first = self.build("first")
        (self.repository / ".git/info/attributes").write_text("src/kilix_content/* export-ignore\nLICENSE export-ignore\n")
        second = self.build("second")
        self.assert_same(first, second)
        with zipfile.ZipFile(first / "content_bundle.zip") as archive:
            for path in self.package.iterdir():
                self.assertEqual(archive.read("kilix_content/" + path.name), path.read_bytes())
        receipt = json.loads((first / "content_bundle.receipt.json").read_text())
        self.assertEqual(receipt["content_tree"], self.git("rev-parse", self.commit + "^{tree}"))
        self.assertEqual(len(receipt["content_objects"]), 5)

    def test_commit_tree_and_blob_replacements_cannot_change_named_source(self):
        first = self.build("first")
        blob = self.git("rev-parse", self.commit + ":LICENSE")
        tree = self.git("rev-parse", self.commit + "^{tree}")
        (self.repository / "LICENSE").write_text("Replacement license\n")
        replacement = self.save()
        replaced_blob = self.git("rev-parse", replacement + ":LICENSE")
        replaced_tree = self.git("rev-parse", replacement + "^{tree}")
        for index, (old, new) in enumerate(((self.commit, replacement), (tree, replaced_tree), (blob, replaced_blob))):
            self.git("replace", old, new)
            self.assert_same(first, self.build("replacement-" + str(index)))
            self.git("replace", "-d", old)

    def test_ambient_git_routing_config_and_path_are_ignored(self):
        first = self.build("first")
        fake_bin = self.root / "bin"
        fake_bin.mkdir()
        (fake_bin / "git").write_text("#!/bin/sh\nexit 91\n")
        (fake_bin / "git").chmod(0o700)
        hostile = dict(self.environment, PATH=str(fake_bin), GIT_DIR=str(self.root / "absent"),
                       GIT_COMMON_DIR=str(self.root / "absent"), GIT_WORK_TREE=str(self.root),
                       GIT_OBJECT_DIRECTORY=str(self.root / "absent"),
                       GIT_ALTERNATE_OBJECT_DIRECTORIES=str(self.root / "absent"),
                       GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="core.repositoryFormatVersion",
                       GIT_CONFIG_VALUE_0="999", GIT_CONFIG_GLOBAL=str(self.root / "absent"))
        self.assert_same(first, self.build("hostile", environment=hostile))

    def test_noncommit_short_and_missing_identity_refuse_without_output(self):
        for index, identity in enumerate((self.commit[:12], "0" * 40,
                                         self.git("rev-parse", self.commit + ":LICENSE"))):
            self.build("invalid-" + str(index), identity, success=False)

    def test_symlink_and_unrecognized_members_refuse_without_output(self):
        link = self.package / "unsafe.py"
        link.symlink_to("installed.py")
        self.build("symlink", self.save(), success=False)
        link.unlink()
        (self.package / "unrecognized.dat").write_bytes(b"unsafe")
        self.build("unknown", self.save(), success=False)

    def test_oversized_blob_refuses_before_output(self):
        (self.package / "oversized.py").write_bytes(b"#" * (2 * 1024**2 + 1))
        self.build("large", self.save(), success=False)

    def test_raw_object_identity_is_recomputed(self):
        oid = self.git("rev-parse", self.commit + ":LICENSE")
        value = b"Wrong bytes under the original object name\n"
        raw = b"blob " + str(len(value)).encode() + b"\0" + value
        self.assertNotEqual(hashlib.sha1(raw).hexdigest(), oid)
        path = self.repository / ".git/objects" / oid[:2] / oid[2:]
        path.chmod(0o600)
        path.write_bytes(zlib.compress(raw))
        self.build("corrupt", success=False)


if __name__ == "__main__":
    unittest.main()
