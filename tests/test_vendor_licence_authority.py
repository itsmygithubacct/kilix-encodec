"""The licence-authority re-pin is generated and guarded; nothing is retyped.

Each test copies the tool into a private tree whose vendored kilix-license is
this repository's own, and uses a local Git fixture of that tree as the
upstream. No network, no model payload.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TOOL = "tools/vendor_licence_authority.py"


class VendorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="kenc-vendor-")
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        self.tree = base / "tree"
        for path in (TOOL, "tools/build_content_bundle.py", "tools/converter-inputs.json",
                     "third_party/kilix-license.pin"):
            (self.tree / path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / path, self.tree / path)
        shutil.copytree(ROOT / "third_party/kilix-license", self.tree / "third_party/kilix-license")
        self.upstream = base / "upstream"
        shutil.copytree(ROOT / "third_party/kilix-license", self.upstream)
        (self.upstream / "tests").mkdir()
        (self.upstream / "tests/excluded.py").write_text("# not vendored\n")
        self.environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        self.git("init", "-q")
        self.git("config", "user.name", "itsmygithubacct")
        self.git("config", "user.email", "itsmygithubacct@users.noreply.github.com")
        self.first = self.commit()
        # Rewrite the fixture so it pins its own first commit.
        (self.tree / "third_party/kilix-license.pin").write_text(self.first + "\n")
        binding = json.loads((self.tree / "tools/converter-inputs.json").read_text())
        binding["licence_authority"]["ref"] = self.first
        binding["licence_authority"]["files"]["third_party/kilix-license.pin"] = hashlib.sha256(
            (self.first + "\n").encode()).hexdigest()
        (self.tree / "tools/converter-inputs.json").write_text(json.dumps(binding, indent=2, sort_keys=True) + "\n")
        coverage = self.upstream / "src/kilix_license/coverage.py"
        coverage.write_bytes(coverage.read_bytes() + b"# second fixture commit\n")
        self.second = self.commit()

    def git(self, *arguments):
        return subprocess.check_output(["/usr/bin/git", "-C", str(self.upstream), *arguments],
                                       env=self.environment, stderr=subprocess.DEVNULL).decode().strip()

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-qm", "Licence authority fixture")
        return self.git("rev-parse", "HEAD")

    def run_tool(self, *arguments):
        return subprocess.run(["/usr/bin/python3", "-I", "-B", str(self.tree / TOOL), *arguments],
                              capture_output=True, timeout=60)

    def snapshot(self):
        return {str(path.relative_to(self.tree)): path.read_bytes()
                for path in sorted(self.tree.rglob("*")) if path.is_file() and "__pycache__" not in path.parts}

    def test_the_committed_pin_is_exactly_what_the_generator_produces(self):
        process = subprocess.run(["/usr/bin/python3", "-I", "-B", str(ROOT / TOOL)], capture_output=True, timeout=60)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)

    def test_repin_regenerates_every_bound_digest_and_the_tree(self):
        self.assertEqual(self.run_tool("--repo", str(self.upstream), "--ref", self.first).returncode, 0)
        process = self.run_tool("--repo", str(self.upstream), "--ref", self.second, "--old", self.first, "--write")
        self.assertEqual(process.returncode, 0, process.stderr)
        binding = json.loads((self.tree / "tools/converter-inputs.json").read_text())["licence_authority"]
        self.assertEqual(binding["ref"], self.second)
        self.assertEqual((self.tree / "third_party/kilix-license.pin").read_text(), self.second + "\n")
        for path, digest in binding["files"].items():
            self.assertEqual(hashlib.sha256((self.tree / path).read_bytes()).hexdigest(), digest, path)
        self.assertFalse((self.tree / "third_party/kilix-license/tests").exists())
        self.assertEqual(self.run_tool("--repo", str(self.upstream), "--ref", self.second).returncode, 0)

    def test_a_wrong_old_value_or_a_changed_tree_replaces_nothing(self):
        before = self.snapshot()
        wrong = self.run_tool("--repo", str(self.upstream), "--ref", self.second, "--old", self.second, "--write")
        self.assertNotEqual(wrong.returncode, 0)
        self.assertIn(b"does not read --old", wrong.stderr)
        self.assertEqual(self.snapshot(), before)
        record = self.tree / "third_party/kilix-license/src/kilix_license/records.py"
        record.write_bytes(record.read_bytes() + b"# local edit\n")
        before = self.snapshot()
        changed = self.run_tool("--repo", str(self.upstream), "--ref", self.second, "--old", self.first, "--write")
        self.assertNotEqual(changed.returncode, 0)
        self.assertIn(b"not exactly --old", changed.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_a_hand_edited_digest_or_pin_is_refused(self):
        path = self.tree / "tools/converter-inputs.json"
        binding = json.loads(path.read_text())
        name = "third_party/kilix-license/src/kilix_license/store.py"
        binding["licence_authority"]["files"][name] = "0" * 64
        path.write_text(json.dumps(binding, indent=2, sort_keys=True) + "\n")
        process = self.run_tool()
        self.assertNotEqual(process.returncode, 0)
        self.assertIn(b"does not match the vendored bytes", process.stderr)
        (self.tree / "third_party/kilix-license.pin").write_text(self.second + "\n")
        self.assertIn(b"disagree", self.run_tool().stderr)


if __name__ == "__main__":
    unittest.main()
