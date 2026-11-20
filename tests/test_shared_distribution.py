"""Disk layout, converter bindings and interrupted upgrades of the system package."""
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
INSTALL = runpy.run_path(str(ROOT / "packaging/install-converter"))
MONO = INSTALL["MONO_COMMAND"]
STEREO = "bin/kilix-encodec-convert-48khz"
RUNTIME = INSTALL["SHARED_RUNTIME"]
OLD_RUNTIME = INSTALL["MONO_RUNTIME"]


def digest(data):
    return hashlib.sha256(data).hexdigest()


class SharedDistribution(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.installed = self.root / "installed"
        self.installed.mkdir()
        self.selected = {"size": 1, "sha256": "1" * 64, "files": {}}
        template = (ROOT / "tools/converter_runtime.py").read_text()
        common = {"python": {"bytes": 6, "mode": 0o700, "sha256": digest(b"python")}}
        for profile, command, runtime in (("24khz", MONO, OLD_RUNTIME), ("48khz", STEREO, RUNTIME)):
            directory = Path(runtime).parent
            members = dict(common)
            if profile == "48khz":
                members["stereo.py"] = {"bytes": 4, "mode": 0o600, "sha256": digest(b"code")}
            archive = ("archive for " + profile).encode()
            facts = {"directory": str(directory), "entry": [profile], "record": {"id": profile},
                     "inputs": {"expected": profile}, "profile": profile}
            receipt = {"runtime_files": members, "runtime_bytes": sum(v["bytes"] for v in members.values()),
                       "runtime_tar_bytes": len(archive), "runtime_tar_sha256": digest(archive)}
            script = template
            for key, value in {"@BUNDLE_SHA256@": digest(archive), "@BUNDLE_BYTES@": str(len(archive)),
                               "@RUNTIME_BYTES@": str(receipt["runtime_bytes"]), "@OUTPUTS_JSON@": "{}",
                               "@PROFILE_HEX@": json.dumps(facts, sort_keys=True, separators=(",", ":")).encode().hex(),
                               "@AUTHORITY_HEX@": b'{"fixture":true}'.hex()}.items():
                script = script.replace(key, value)
            self.put(command, script.encode(), 0o755)
            self.put(runtime, archive)
            self.put(str(directory / "receipt.json"), json.dumps(receipt).encode())
        self.put("BUILD.json", b'{"architecture":"amd64","source_commit":"fixture"}')
        self.put("source.tar.gz", b"fixture source")

        reference = self.root / "reference"
        shutil.copytree(self.installed, reference)
        (reference / OLD_RUNTIME).unlink()
        INSTALL["share_runtime"](reference, self.selected)
        self.selected["shared"] = {name: digest((reference / name).read_bytes()) for name in
                                   (MONO, INSTALL["MONO_RECEIPT"], "BUILD.json")}
        self.selected["installed_bytes"] = sum(p.stat().st_size for p in reference.rglob("*") if p.is_file())
        # Only root's trust in the parent directory and file ownership vary in
        # an unprivileged test. Publication, inventory hashes and modes are real.
        globals_ = INSTALL["install_locked"].__globals__
        self.scope = patch.dict(globals_, {"DESTINATION": self.installed, "secure_directory": lambda _: None,
                                           "inventory": lambda p: INSTALL["inventory"](p, owner=os.geteuid())})
        self.scope.start()
        self.addCleanup(self.scope.stop)

    def put(self, name, data, mode=0o644):
        path = self.installed / name
        path.parent.mkdir(mode=0o755, exist_ok=True)
        path.parent.chmod(0o755)
        path.write_bytes(data)
        path.chmod(mode)
        self.selected["files"][name] = digest(data)

    def test_existing_install_reuses_inode_and_preserves_both_profile_gates(self):
        old = runpy.run_path(str(self.installed / MONO))
        inode = (self.installed / RUNTIME).stat().st_ino
        missing_archive = self.root / "never-downloaded"
        INSTALL["install_locked"](self.selected, missing_archive)
        new = runpy.run_path(str(self.installed / MONO))
        stereo = runpy.run_path(str(self.installed / STEREO))
        self.assertEqual(new["PROFILE"], {**old["PROFILE"], "directory": stereo["PROFILE"]["directory"]})
        for key in ("AUTHORITY", "OUTPUTS"):
            self.assertEqual(new[key], old[key])
        for key in ("BUNDLE_SHA256", "BUNDLE_BYTES", "RUNTIME_BYTES"):
            self.assertEqual(new[key], stereo[key])
        self.assertEqual((self.installed / RUNTIME).stat().st_ino, inode)
        self.assertEqual(list(self.installed.rglob("runtime.tar")), [self.installed / RUNTIME])
        stale = self.root / ".encodec-converter-stage"
        stale.mkdir(mode=0o700)
        (stale / "interrupted-cleanup").touch()
        INSTALL["install_locked"](self.selected, missing_archive)
        self.assertFalse(stale.exists())
        self.assertEqual((self.installed / RUNTIME).stat().st_ino, inode)

    def test_failed_publication_leaves_the_original_retryable(self):
        with patch.dict(INSTALL["install_locked"].__globals__, {"publish_upgrade": self.fail_exchange}):
            with self.assertRaisesRegex(OSError, "exchange"):
                INSTALL["install_locked"](self.selected, None)
        self.assertEqual(INSTALL["inventory"](self.installed, owner=os.geteuid()),
                         INSTALL["expected_inventory"](self.selected))
        INSTALL["install_locked"](self.selected, None)
        self.assertFalse((self.installed / OLD_RUNTIME).exists())

    @staticmethod
    def fail_exchange(_payload):
        raise OSError("exchange failed")

    def test_changed_file_or_incompatible_stereo_runtime_is_refused(self):
        receipt = self.installed / INSTALL["STEREO_RECEIPT"]
        data = json.loads(receipt.read_bytes())
        data["runtime_files"]["python"]["sha256"] = digest(b"other!")
        receipt.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
            INSTALL["install_locked"](self.selected, None)
        self.selected["files"][INSTALL["STEREO_RECEIPT"]] = digest(receipt.read_bytes())
        with self.assertRaisesRegex(ValueError, "exact mono runtime"):
            INSTALL["install_locked"](self.selected, None)
        self.assertTrue((self.installed / OLD_RUNTIME).is_file())


if __name__ == "__main__":
    unittest.main()
