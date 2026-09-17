"""consumer_grep.sh must fail loudly on a git error (F1 verifier finding F4)."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/consumer_grep.sh"


class ConsumerGrepTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="kenc-consumer-grep-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.root.chmod(0o700)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "itsmygithubacct")
        self.git("config", "user.email", "itsmygithubacct@users.noreply.github.com")
        (self.repo / "clean.c").write_text("int main(void) { return 0; }\n")
        self.git("add", "clean.c")
        self.git("commit", "-qm", "clean")
        self.fds = len(os.listdir("/proc/self/fd"))

    def tearDown(self):
        self.assertEqual(len(os.listdir("/proc/self/fd")), self.fds)

    def git(self, *args):
        subprocess.check_call(
            ["/usr/bin/git", "-c", "maintenance.auto=false", "-C", str(self.repo), *args],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def run_script(self, repo, ref):
        return subprocess.run(
            [str(SCRIPT), str(repo), ref],
            text=True,
            capture_output=True,
            timeout=10,
        )

    def test_usage_without_arguments_exits_2(self):
        result = subprocess.run([str(SCRIPT)], text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 2)
        self.assertIn("consumer_grep: usage:", result.stderr)

    def test_clean_ref_exits_1_with_no_hits(self):
        result = self.run_script(self.repo, "HEAD")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_planted_hit_exits_0(self):
        (self.repo / "planted.c").write_text("void kenc_decoder_pull_s16(void);\n")
        self.git("add", "planted.c")
        self.git("commit", "-qm", "planted")
        result = self.run_script(self.repo, "HEAD")
        self.assertEqual(result.returncode, 0)
        self.assertIn("kenc_decoder_pull_s16", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_planted_bad_ref_exits_128_and_prints_failed(self):
        result = self.run_script(self.repo, "deadbeefdeadbeef")
        self.assertEqual(result.returncode, 128)
        self.assertEqual(result.stdout, "")
        self.assertIn("consumer_grep: FAILED", result.stderr)
        self.assertIn("git grep exited 128", result.stderr)
        self.assertIn("deadbeefdeadbeef", result.stderr)


if __name__ == "__main__":
    unittest.main()
