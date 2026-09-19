"""R4-198: no model weight is tracked in kilix-encodec.

Tracked-tree scan for weight suffixes, magic numbers and size, plus every blob
whose sha256 is in the generated catalog digest list. Each planted control
must fail the scan; the regeneration check must refuse a hand-edited list.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "tests" / "support"))

from weight_scan import (  # noqa: E402
    ALWAYS_SUFFIXES,
    SIZE_GATED_SUFFIXES,
    catalog_matches_generator,
    load_catalog_digests,
    scan_tree,
    sha256_file,
)

CATALOG = ROOT / "tests" / "data" / "catalog_digests.txt"
GENERATOR = ROOT / "tools" / "regenerate_catalog_digests.py"
IDENTITY = {
    "GIT_AUTHOR_NAME": "itsmygithubacct",
    "GIT_AUTHOR_EMAIL": "itsmygithubacct@users.noreply.github.com",
    "GIT_COMMITTER_NAME": "itsmygithubacct",
    "GIT_COMMITTER_EMAIL": "itsmygithubacct@users.noreply.github.com",
}
# The guard's floor, typed here and not read from weight_scan: a suffix dropped
# from the guard, or a bound moved, fails these tests instead of shrinking them.
# Suffixes the guard adds are planted as well (the union below).
REQUIRED_ALWAYS_SUFFIXES = (
    ".gguf", ".ggml", ".safetensors", ".onnx", ".pt", ".pth",
    ".ckpt", ".mdl", ".tflite", ".th", ".fst", ".f32le",
)
REQUIRED_SIZE_GATED_SUFFIXES = (
    ".bin", ".npy", ".npz", ".pkl", ".pb", ".h5",
    ".engine", ".plan", ".ot", ".f32", ".raw",
)
REQUIRED_SIZE_GATE_BYTES = 1024 * 1024
REQUIRED_TRACKED_MAXIMUM_BYTES = 2 * 1024 * 1024


def _git_init(tree: Path) -> None:
    env = {**os.environ, **IDENTITY}
    subprocess.run(["git", "init", "-q", str(tree)], check=True, env=env)
    subprocess.run(["git", "-C", str(tree), "add", "-A"], check=True, env=env)
    subprocess.run(
        ["git", "-C", str(tree), "-c", "user.name=itsmygithubacct",
         "-c", "user.email=itsmygithubacct@users.noreply.github.com",
         "commit", "-q", "--no-gpg-sign", "-m", "fixture"],
        check=True,
        env=env,
    )


class WeightGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._scratch = tempfile.TemporaryDirectory(prefix="kilix-encodec-weight-")
        self.scratch = Path(self._scratch.name)

    def tearDown(self) -> None:
        self._scratch.cleanup()

    def planted_tree(self, name: str, files: dict[str, bytes], extra_digests: tuple[str, ...] = ()) -> tuple[Path, Path]:
        """A committed git tree holding only the planted files, and a list copy."""
        tree = self.scratch / name
        tree.mkdir()
        for relative, data in files.items():
            (tree / relative).parent.mkdir(parents=True, exist_ok=True)
            (tree / relative).write_bytes(data)
        _git_init(tree)
        catalog = self.scratch / (name + "-catalog_digests.txt")
        catalog.write_text(
            CATALOG.read_text(encoding="utf-8") + "".join(item + "\n" for item in extra_digests),
            encoding="utf-8",
        )
        return tree, catalog

    def reasons(self, name: str, files: dict[str, bytes], extra: tuple[str, ...] = ()) -> set[str]:
        tree, catalog = self.planted_tree(name, files, extra)
        return {item.reason for item in scan_tree(tree, catalog)}

    def reasons_by_path(self, name: str, files: dict[str, bytes]) -> dict[str, set[str]]:
        tree, catalog = self.planted_tree(name, files)
        found: dict[str, set[str]] = {relative: set() for relative in files}
        for item in scan_tree(tree, catalog):
            found.setdefault(item.path, set()).add(item.reason)
        return found

    def test_repository_tracked_tree_has_no_weights(self) -> None:
        self.assertEqual([], scan_tree(ROOT, CATALOG))

    def test_planted_weight_suffix_fails(self) -> None:
        # Every always-refused suffix, each named exactly: ".pth" must not pass
        # as ".th", nor ".f32le" as anything else.
        suffixes = sorted(set(REQUIRED_ALWAYS_SUFFIXES) | set(ALWAYS_SUFFIXES))
        found = self.reasons_by_path("suffixes", {"planted" + suffix: b"not a real model" for suffix in suffixes})
        for suffix in suffixes:
            with self.subTest(suffix=suffix):
                self.assertIn(f"weight-suffix:{suffix}", found["planted" + suffix])

    def test_size_gated_suffix_at_gate_fails(self) -> None:
        # Every size-gated suffix fails at 1 MiB and passes one byte below it.
        suffixes = sorted(set(REQUIRED_SIZE_GATED_SUFFIXES) | set(SIZE_GATED_SUFFIXES))
        files = {"planted" + suffix: b"\0" * REQUIRED_SIZE_GATE_BYTES for suffix in suffixes}
        files.update({"small" + suffix: b"\0" * (REQUIRED_SIZE_GATE_BYTES - 1) for suffix in suffixes})
        found = self.reasons_by_path("size-gated", files)
        for suffix in suffixes:
            with self.subTest(suffix=suffix):
                self.assertIn(f"weight-suffix-size:{suffix}", found["planted" + suffix])
                self.assertEqual(set(), found["small" + suffix])

    def test_upper_and_mixed_case_suffixes_fail(self) -> None:
        # The suffix layer folds case: MODEL.ONNX and final.Mdl are refused
        # under the lower-case suffix, always-refused and size-gated alike.
        # The files carry no magic and sit below the 2 MiB bound, so only
        # the suffix layer can refuse them.
        def spellings(suffix: str) -> tuple[str, str]:
            return suffix.upper(), suffix[:2].upper() + suffix[2:]

        always = sorted(set(REQUIRED_ALWAYS_SUFFIXES) | set(ALWAYS_SUFFIXES))
        gated = sorted(set(REQUIRED_SIZE_GATED_SUFFIXES) | set(SIZE_GATED_SUFFIXES))
        files: dict[str, bytes] = {}
        expected: dict[str, str] = {}
        for suffix in always:
            for index, spelled in enumerate(spellings(suffix)):
                self.assertNotEqual(spelled, suffix)
                name = f"case{index}/planted{spelled}"
                files[name] = b"not a real model"
                expected[name] = f"weight-suffix:{suffix}"
        for suffix in gated:
            for index, spelled in enumerate(spellings(suffix)):
                self.assertNotEqual(spelled, suffix)
                name = f"case{index}/gated{spelled}"
                files[name] = b"\0" * REQUIRED_SIZE_GATE_BYTES
                expected[name] = f"weight-suffix-size:{suffix}"
        found = self.reasons_by_path("case-folding", files)
        for name, reason in expected.items():
            with self.subTest(name=name):
                self.assertEqual({reason}, found[name])

    def test_any_tracked_file_at_the_size_bound_fails(self) -> None:
        # The bound is 2 MiB exactly: a neutral text file of that size fails,
        # one byte smaller passes. A raised or lowered bound fails one side.
        found = self.reasons_by_path("size-bound", {
            "docs/huge.txt": b"a" * REQUIRED_TRACKED_MAXIMUM_BYTES,
            "docs/large.txt": b"a" * (REQUIRED_TRACKED_MAXIMUM_BYTES - 1),
        })
        self.assertEqual({f"size:{REQUIRED_TRACKED_MAXIMUM_BYTES}"}, found["docs/huge.txt"])
        self.assertEqual(set(), found["docs/large.txt"])

    def test_planted_magic_on_neutral_names_fails(self) -> None:
        meta = b'{"__metadata__":{"format":"pt"},"x":{"dtype":"F32","shape":[1],"data_offsets":[0,4]}}'
        cases = {
            "magic:GGUF": b"GGUF" + b"\0" * 16,
            "magic:safetensors": len(meta).to_bytes(8, "little") + meta,
            # The official 24 kHz checkpoint is a torch.save zip whose first member is archive/data.pkl.
            "magic:torch-zip": b"PK\x03\x04" + b"\0" * 26 + b"archive/data.pkl" + b"\0" * 8,
            "magic:torch-legacy": b"\x80\x02\x8a\x0a\x6c\xfc\x9c\x46\xf9\x20\x6a\xa8\x50\x19" + b"\0" * 8,
            # torch.onnx.export graphs start ir_version 8, producer_name "pytorch".
            "magic:onnx": b"\x08\x08\x12\x07pytorch\x1a\x052.6.0" + b"\0" * 8,
            "magic:fst": struct.pack("<i", 2125659606) + b"\x06\x00\x00\x00vector" + b"\0" * 32,
        }
        for reason, data in cases.items():
            with self.subTest(reason=reason):
                self.assertIn(reason, self.reasons(reason.replace(":", "-"), {"assets/renamed.dat": data}))

    def test_planted_catalog_digest_blob_fails(self) -> None:
        blob = b"catalog-digest-planted-blob"
        path = self.scratch / "blob"
        path.write_bytes(blob)
        digest = sha256_file(path)
        self.assertEqual(set(), self.reasons("digest-absent", {"fixtures/planted.dat": blob}))
        reasons = self.reasons("digest-listed", {"fixtures/planted.dat": blob}, (digest,))
        self.assertEqual({f"catalog-digest:{digest}"}, reasons)

    def test_list_names_every_bound_checkpoint_and_converted_payload(self) -> None:
        _text, listed = load_catalog_digests(CATALOG)
        binding = json.loads((ROOT / "tools" / "converter-inputs.json").read_text(encoding="utf-8"))
        for name, profile in binding["profiles"].items():
            for kind in ("inputs", "outputs"):
                for file, pinned in profile[kind].items():
                    with self.subTest(profile=name, file=file):
                        self.assertIn(pinned["sha256"], listed)
        population: dict = {}
        exec(compile((ROOT / "python" / "graph_population.py").read_text(), "graph_population", "exec"), population)
        for _asset, _version, _limit, files in population["PROFILES"].values():
            for file, _size, digest in files:
                self.assertIn(digest, listed, file)
        # Code licence and notice texts are not weights.
        for notice in ("tools/converter-notices/encodec-LICENSE-MIT", "third_party/kilix-license/LICENSE"):
            self.assertNotIn(sha256_file(ROOT / notice), listed)

    def test_regeneration_matches_committed_list(self) -> None:
        self.assertTrue(catalog_matches_generator(ROOT, CATALOG))
        result = subprocess.run([sys.executable, "-B", str(GENERATOR), "--check"],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_hand_edited_digest_list_is_refused(self) -> None:
        planted = self.scratch / "catalog_digests.txt"
        planted.write_text(CATALOG.read_text(encoding="utf-8") + ("a" * 64) + "\n", encoding="utf-8")
        self.assertFalse(catalog_matches_generator(ROOT, planted))
        dropped = self.scratch / "dropped.txt"
        text = CATALOG.read_text(encoding="utf-8")
        first = next(line for line in text.splitlines() if line and not line.startswith("#"))
        dropped.write_text(text.replace(first + "\n", "", 1), encoding="utf-8")
        self.assertFalse(catalog_matches_generator(ROOT, dropped))

    def generator_copy(self, name: str) -> Path:
        """Only the generator and its inputs, so --check runs on edited copies."""
        root = self.scratch / name
        for relative in ("tools/regenerate_catalog_digests.py", "tools/converter-inputs.json",
                         "python/graph_population.py", "tests/data/catalog_source.json",
                         "tests/data/catalog_digests.txt"):
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, root / relative)
        return root

    def check(self, root: Path) -> int:
        return subprocess.run([sys.executable, "-B", str(root / "tools/regenerate_catalog_digests.py"), "--check"],
                              capture_output=True, check=False).returncode

    def test_regeneration_check_refuses_hand_edits_and_stale_pins(self) -> None:
        self.assertEqual(self.check(self.generator_copy("unchanged")), 0)
        edited = self.generator_copy("list")
        path = edited / "tests/data/catalog_digests.txt"
        path.write_text(path.read_text(encoding="utf-8").replace("# count: ", "# count: 1"), encoding="utf-8")
        self.assertEqual(self.check(edited), 1)
        source = self.generator_copy("source")
        path = source / "tests/data/catalog_source.json"
        parsed = json.loads(path.read_text(encoding="utf-8"))
        parsed["members"].pop()
        path.write_text(json.dumps(parsed, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self.assertEqual(self.check(source), 1)
        stale = self.generator_copy("stale")
        path = stale / "tools/converter-inputs.json"
        parsed = json.loads(path.read_text(encoding="utf-8"))
        parsed["profiles"]["24khz"]["outputs"]["manifest.json"]["sha256"] = "b" * 64
        path.write_text(json.dumps(parsed, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self.assertEqual(self.check(stale), 1)

    def test_catalog_option_folds_in_encodec_members_without_notices(self) -> None:
        root = self.generator_copy("catalog")
        catalog = {"assets": [
            {"id": "encodec-24khz-stateful", "provider": "kilix-encodec",
             "source": {"input": {"sha256": "c" * 64}, "mode": "upstream-convert"},
             "files": [{"path": "manifest.json", "sha256": "d" * 64},
                       {"path": "notices/CC-BY-NC-4.0.txt", "sha256": "e" * 64}]},
            {"id": "vosk-model-small-en-us-0.15", "provider": "kilix-voice",
             "source": {"archive_sha256": "f" * 64}, "files": []},
        ]}
        path = self.scratch / "plebian.json"
        path.write_text(json.dumps(catalog), encoding="utf-8")
        script = root / "tools/regenerate_catalog_digests.py"
        subprocess.run([sys.executable, "-B", str(script), "--catalog", str(path)], check=True, capture_output=True)
        self.assertEqual(self.check(root), 0)
        _text, listed = load_catalog_digests(root / "tests/data/catalog_digests.txt")
        self.assertIn("c" * 64, listed)
        self.assertIn("d" * 64, listed)
        self.assertNotIn("e" * 64, listed)
        self.assertNotIn("f" * 64, listed)
        _text, committed = load_catalog_digests(CATALOG)
        self.assertTrue(committed < listed)


if __name__ == "__main__":
    unittest.main()
