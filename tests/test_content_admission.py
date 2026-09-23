"""The C entry point runs the embedded asset/v3 + kilix-license worker, end to end.

A fixture content commit is made from the real content source (its package,
catalogue code and vendored kilix-license), with only the two EnCodec catalogue
entries given small synthetic files and the catalogue pin moved to match. The
real `tools/build_content_bundle.py` embeds it with this repository's worker and
a matching synthetic `graph_population.py`; `src/admission.c` is compiled
against that bundle, and `kenc_installed_assets_open` is called through ctypes.
So what is exercised is the sealed-ZIP launch, the isolated interpreter, the
worker's own origin check, the catalogue, the receipt and the tree checks, and
the descriptor transfer back into C. No model byte and no network is involved.
"""
import ast
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import kilix_content as content
import kilix_license
from kilix_license.agreement import capture_agreement, typed_agreement_line
from kilix_license.receipts import receipt_from_agreement

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(content.__file__).resolve().parents[2]
KENC_OK, KENC_ERR_MODEL = 0, 2


class AssetFd(ctypes.Structure):
    _fields_ = [("name", ctypes.c_char_p), ("descriptor", ctypes.c_int)]


class AssetSet(ctypes.Structure):
    _fields_ = [("files", ctypes.POINTER(AssetFd)), ("count", ctypes.c_size_t)]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def git(repository, *arguments):
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    return subprocess.check_output(["/usr/bin/git", "-C", str(repository), *arguments],
                                   env=environment, stderr=subprocess.DEVNULL).decode().strip()


class NativeAdmission(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="kenc-native-admit-")
        base = cls.base = Path(cls.temporary.name)
        population = {}
        exec(compile((ROOT / "python/graph_population.py").read_text(), "graph_population", "exec"), population)
        cls.graphs, synthetic = {}, {}
        for profile, (asset_id, version, _budget, members) in population["PROFILES"].items():
            graphs = {name: f"synthetic native admission {profile}: {name}".encode() for name, _s, _d in members}
            cls.graphs[profile] = (asset_id, graphs)
            synthetic[profile] = (asset_id, version, 4096, tuple((n, len(d), digest(d)) for n, d in graphs.items()))
        # This repository's builder and worker, with the synthetic population.
        kenc = base / "kenc"
        for path in ("tools/build_content_bundle.py", "tools/converter-inputs.json", "third_party/kilix-license.pin",
                     "python/installed_assets.py", "python/content_worker.py"):
            (kenc / path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / path, kenc / path)
        (kenc / "python/graph_population.py").write_text("PROFILES = " + repr(synthetic) + "\n")
        # A content commit made from the real content source.
        repository = base / "content"
        licence = json.loads((ROOT / "tools/converter-inputs.json").read_bytes())["licence_authority"]
        wanted = ["LICENSE", "third_party/kilix-license.pin", *licence["files"]]
        wanted += [str(path.relative_to(SOURCE)) for path in (SOURCE / "src/kilix_content").rglob("*")
                   if path.is_file() and "__pycache__" not in path.parts]
        for path in wanted:
            (repository / path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(SOURCE / path, repository / path)
        catalog_path = repository / "src/kilix_content/catalog/plebian.json"
        pinned = digest(catalog_path.read_bytes())
        document = json.loads(catalog_path.read_bytes())
        cls.notice = b"Synthetic stand-in for the licence notice.\n"
        for asset_id, graphs in cls.graphs.values():
            entry = next(item for item in document["assets"] if item["id"] == asset_id)
            payloads = {**graphs, "notices/LICENSE-cc-by-nc-4.0.txt": cls.notice}
            entry["files"] = [dict(path=n, bytes=len(d), sha256=digest(d)) for n, d in sorted(payloads.items())]
            entry["sizes"]["installed_bytes"] = sum(map(len, payloads.values()))
        catalog = json.dumps(document, indent=2).encode() + b"\n"
        catalog_path.write_bytes(catalog)
        receipt_py = repository / "src/kilix_content/receipt.py"
        text = receipt_py.read_text()
        if text.count(pinned) != 1:
            raise AssertionError("the content source does not pin its own catalogue exactly once")
        receipt_py.write_text(text.replace(pinned, digest(catalog)))
        git(repository, "init", "-q")
        git(repository, "config", "user.name", "itsmygithubacct")
        git(repository, "config", "user.email", "itsmygithubacct@users.noreply.github.com")
        git(repository, "add", ".")
        git(repository, "commit", "-qm", "Native admission fixture")
        cls.commit = git(repository, "rev-parse", "HEAD")
        cls.library, cls.receipt = cls.build(kenc, repository, cls.commit, "pinned")
        # A second commit whose catalogue no longer matches its own pin: one
        # label changed after pinning, nothing else.
        document["assets"][0]["label"] += " (changed after pinning)"
        catalog_path.write_bytes(json.dumps(document, indent=2).encode() + b"\n")
        git(repository, "commit", "-qam", "Catalogue changed after its pin")
        cls.tampered, _receipt = cls.build(kenc, repository, git(repository, "rev-parse", "HEAD"), "tampered")
        cls.catalog = content.Catalog.loads(catalog.decode(), label="fixture")
        cls.catalog_digest = digest(catalog)

    @classmethod
    def build(cls, kenc, repository, commit, label):
        """Bundle one content commit the repository's way and compile admission.c against it."""
        out = cls.base / ("out-" + label)
        subprocess.run(["/usr/bin/python3", "-I", "-B", str(kenc / "tools/build_content_bundle.py"),
                        "--source", str(repository), "--commit", commit, "--output", str(out)],
                       check=True, capture_output=True, timeout=120)
        library = cls.base / ("libkenc-admission-" + label + ".so")
        subprocess.run([os.environ.get("CC", "cc"), "-std=c11", "-shared", "-fPIC", "-Wall", "-Werror",
                        "-DKENC_WITH_CONTENT", "-I", str(ROOT / "include"), "-I", str(ROOT / "src"), "-I", str(out),
                        str(ROOT / "src/admission.c"), "-o", str(library)], check=True, capture_output=True, timeout=120)
        loaded = ctypes.CDLL(str(library))
        loaded.kenc_installed_assets_open.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint8,
                                                      ctypes.c_char_p, ctypes.c_uint32, ctypes.c_void_p,
                                                      ctypes.c_void_p]
        loaded.kenc_installed_assets_files.restype = ctypes.POINTER(AssetSet)
        loaded.kenc_installed_assets_files.argtypes = [ctypes.c_void_p]
        loaded.kenc_installed_assets_free.argtypes = [ctypes.c_void_p]
        loaded.kenc_installed_content_commit.restype = ctypes.c_char_p
        loaded.kenc_installed_bundle_sha256.restype = ctypes.c_char_p
        return loaded, json.loads((out / "content_bundle.receipt.json").read_bytes())

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp(prefix="kenc-na-", dir=self.base))
        self.saved = {name: os.environ.get(name) for name in ("KILIX_LICENSE_RECEIPTS", "GPU_TERMINAL_HOME", "HOME")}
        os.environ["KILIX_LICENSE_RECEIPTS"] = str(self.scratch / "receipts")
        self.store = kilix_license.ReceiptStore.shared()
        self.root = self.scratch / "data"
        self.selected = {}
        for profile, (asset_id, graphs) in self.graphs.items():
            spec = self.catalog.require_asset(asset_id)
            selected = Path(content.Installer(str(self.root)).asset_destination(spec))
            for name, data in {**graphs, "notices/LICENSE-cc-by-nc-4.0.txt": self.notice}.items():
                (selected / name).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                (selected / name).write_bytes(data)
                (selected / name).chmod(0o600)
            self.selected[profile] = selected
            from importlib.resources import files
            record = kilix_license.LicenseRecord.from_bytes(
                files("kilix_license").joinpath("data", "records", asset_id + ".json").read_bytes())
            agreement = capture_agreement(record, typed_agreement_line(record))
            self.store.write(receipt_from_agreement(record, agreement, manifest_digest=spec.manifest_digest,
                                                    release_digest="a" * 64, catalogue_digest=self.catalog_digest))

    def tearDown(self):
        for name, value in self.saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        shutil.rmtree(self.scratch)

    def admit(self, profile, library=None):
        assets = ctypes.c_void_p()
        result = (library or self.library).kenc_installed_assets_open(
            ctypes.byref(assets), profile, str(self.root).encode(), 20000, None, None)
        return result, assets

    def test_a_catalogue_that_does_not_match_its_pin_is_refused_through_c(self):
        for profile in self.graphs:
            self.assertEqual(self.admit(profile)[0], KENC_OK)
            result, assets = self.admit(profile, self.tampered)
            self.assertEqual(result, KENC_ERR_MODEL)
            self.assertFalse(assets.value)

    def test_the_worker_refuses_an_authority_imported_from_outside_its_bundle(self):
        # Here kilix_content and kilix_license come from the content checkout,
        # not from beside the worker, which is what a shadowing path would do.
        import content_worker
        with self.assertRaisesRegex(RuntimeError, "not the sealed bundle"):
            content_worker.sealed_authority()

    def test_the_worker_refuses_a_module_imported_from_outside_its_isolated_path(self):
        # The worker's second origin check. Its authority comes from the real
        # bundle ZIP here, so the first check passes; one ordinary module is
        # then imported from a directory that is no longer on the path, which
        # is what code loaded through a path that was later dropped looks like.
        # The control arm, without that module, must return the authority.
        foreign = self.scratch / "foreign"
        foreign.mkdir()
        (foreign / "kenc_foreign_probe.py").write_text("VALUE = 1\n")
        script = "\n".join((
            "import runpy, sys",
            "bundle, foreign, plant = sys.argv[1], sys.argv[2], sys.argv[3] == 'plant'",
            "worker = runpy.run_path(bundle, run_name='kenc_worker_probe')",
            "sys.path.insert(0, bundle)",
            "if plant:",
            "    sys.path.insert(0, foreign)",
            "    import kenc_foreign_probe",
            "    sys.path.remove(foreign)",
            "try:",
            "    worker['sealed_authority']()",
            "except RuntimeError as error:",
            "    print('REFUSED', error)",
            "else:",
            "    print('ADMITTED')",
        ))
        bundle = str(self.base / "out-pinned" / "content_bundle.zip")
        outcomes = {}
        for arm in ("control", "plant"):
            completed = subprocess.run(["/usr/bin/python3", "-I", "-B", "-c", script, bundle, str(foreign), arm],
                                       env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
                                       capture_output=True, timeout=60)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            outcomes[arm] = completed.stdout.decode().strip()
        self.assertEqual(outcomes["control"], "ADMITTED")
        self.assertEqual(outcomes["plant"], "REFUSED a module outside the isolated path was imported")

    def test_an_empty_home_finds_the_receipt_where_kilix_license_files_it(self):
        # With HOME="" and neither override set, kilix-license files receipts
        # under "/" (expanduser gives "/"). The helper must look there too, not
        # in the passwd home. Filing there needs a root this user owns, which
        # the namespace harness provides; tests/test_admission_ipc.c checks the
        # empty HOME crossing on every host.
        top = Path("/.local")
        if os.geteuid() == 0 or os.stat("/").st_uid != os.geteuid() or os.path.lexists(top):
            self.skipTest("needs a private root directory owned by this non-root user, without /.local")
        os.environ.pop("KILIX_LICENSE_RECEIPTS", None)
        os.environ.pop("GPU_TERMINAL_HOME", None)
        os.environ["HOME"] = ""
        try:
            self.assertEqual(kilix_license.receipt_store_root(), Path("/.local/gpu_terminal/license-receipts"))
            for profile in self.graphs:
                self.assertEqual(self.admit(profile)[0], KENC_ERR_MODEL)
            store = kilix_license.ReceiptStore.shared()
            for path in self.store.root.glob("*.json"):
                shutil.copy2(path, store.root / path.name)
            for profile in self.graphs:
                result, assets = self.admit(profile)
                self.assertEqual(result, KENC_OK)
                self.library.kenc_installed_assets_free(assets)
        finally:
            shutil.rmtree(top, ignore_errors=True)

    def test_a_caller_that_is_pid_1_gets_a_runtime_outcome_not_a_model_refusal(self):
        # The worker refuses a parent that is PID 1. A caller that is PID 1 of
        # its PID namespace must therefore learn that admission could not run
        # (KENC_ERR_RUNTIME), not that its model was refused (KENC_ERR_MODEL).
        # The same library, fixture and receipts admit from PID 2 of that
        # namespace, so nothing but the caller's PID differs.
        script = "\n".join((
            "import ctypes, os, sys",
            "library, root = sys.argv[1], sys.argv[2]",
            "uid, gid = os.geteuid(), os.getegid()",
            "try:",
            "    os.unshare(os.CLONE_NEWUSER | os.CLONE_NEWPID)",
            "except OSError as error:",
            "    print('UNSHARE', error.errno)",
            "    raise SystemExit(0)",
            "for name, text in (('setgroups', 'deny'), ('uid_map', f'{uid} {uid} 1'), ('gid_map', f'{gid} {gid} 1')):",
            "    with open('/proc/self/' + name, 'w') as handle:",
            "        handle.write(text)",
            "def admit():",
            "    loaded = ctypes.CDLL(library)",
            "    loaded.kenc_installed_assets_open.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint8,",
            "        ctypes.c_char_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p]",
            "    loaded.kenc_installed_assets_free.argtypes = [ctypes.c_void_p]",
            "    results = []",
            "    for profile in (1, 2):",
            "        assets = ctypes.c_void_p()",
            "        results.append(loaded.kenc_installed_assets_open(ctypes.byref(assets), profile, root.encode(), 20000, None, None))",
            "        loaded.kenc_installed_assets_free(assets)",
            "    return results",
            "reader, writer = os.pipe()",
            "first = os.fork()",
            "if first == 0:",
            "    os.close(reader)",
            "    mine = (os.getpid(), admit())",
            "    second = os.fork()",
            "    if second == 0:",
            "        os.write(writer, repr((os.getpid(), admit())).encode() + b'\\n')",
            "        os._exit(0)",
            "    os.waitpid(second, 0)",
            "    os.write(writer, repr(mine).encode() + b'\\n')",
            "    os._exit(0)",
            "os.close(writer)",
            "os.waitpid(first, 0)",
            "output = b''",
            "while chunk := os.read(reader, 4096):",
            "    output += chunk",
            "sys.stdout.write(output.decode())",
        ))
        library = str(self.base / "libkenc-admission-pinned.so")
        completed = subprocess.run(["/usr/bin/python3", "-I", "-B", "-c", script, library, str(self.root)],
                                   env=dict(os.environ), capture_output=True, timeout=120)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        output = completed.stdout.decode()
        if output.startswith("UNSHARE"):
            self.skipTest("this host refuses an unprivileged user and PID namespace: " + output.strip())
        KENC_ERR_RUNTIME = 3
        # PIDs in a new namespace are allocated in order, so the control being
        # PID 2 also shows that PID 1 started no helper before it answered.
        self.assertEqual(sorted(ast.literal_eval(line) for line in output.splitlines()),
                         [(1, [KENC_ERR_RUNTIME, KENC_ERR_RUNTIME]), (2, [KENC_OK, KENC_OK])])

    def test_a_symlinked_ancestor_of_the_content_root_is_followed_through_c(self):
        linked = self.base / (self.scratch.name + "-linked")
        linked.symlink_to(self.scratch)
        try:
            for profile in self.graphs:
                assets = ctypes.c_void_p()
                result = self.library.kenc_installed_assets_open(
                    ctypes.byref(assets), profile, str(linked / self.root.name).encode(), 20000, None, None)
                self.assertEqual(result, KENC_OK)
                self.library.kenc_installed_assets_free(assets)
        finally:
            linked.unlink()

    def test_the_library_carries_the_bundle_it_was_built_from(self):
        self.assertEqual(self.library.kenc_installed_content_commit().decode(), self.commit)
        self.assertEqual(self.library.kenc_installed_bundle_sha256().decode(), self.receipt["bundle_sha256"])
        self.assertEqual(self.receipt["files"]["installed_assets.py"]["sha256"],
                         digest((ROOT / "python/installed_assets.py").read_bytes()))

    def test_admitted_descriptors_cross_into_c_for_both_profiles(self):
        for profile, (_asset_id, graphs) in self.graphs.items():
            with self.subTest(profile=profile):
                result, assets = self.admit(profile)
                self.assertEqual(result, KENC_OK)
                try:
                    files = self.library.kenc_installed_assets_files(assets).contents
                    self.assertEqual(files.count, len(graphs))
                    for index, (name, data) in enumerate(graphs.items()):
                        descriptor = files.files[index].descriptor
                        self.assertEqual(os.pread(descriptor, 4096, 0), data, name)
                        self.assertEqual(os.fstat(descriptor).st_size, len(data))
                finally:
                    self.library.kenc_installed_assets_free(assets)

    def test_each_planted_failure_is_refused_through_c(self):
        def no_receipt():
            for path in self.store.root.glob("*.json"):
                path.unlink()

        def wrong_receipt():
            for path in self.store.root.glob("*.json"):
                body = json.loads(path.read_text())
                body["manifest_digest"] = "e" * 64
                path.write_text(json.dumps(body, sort_keys=True))

        def tampered_tree():
            for selected in self.selected.values():
                path = selected / "manifest.json"
                data = bytearray(path.read_bytes()); data[0] ^= 1; path.write_bytes(bytes(data))

        def undeclared_member():
            for selected in self.selected.values():
                (selected / "extra.onnx").write_bytes(b"undeclared")

        for plant in (no_receipt, wrong_receipt, tampered_tree, undeclared_member):
            with self.subTest(plant=plant.__name__):
                self.tearDown()
                self.setUp()
                for profile in self.graphs:
                    self.assertEqual(self.admit(profile)[0], KENC_OK)
                plant()
                for profile in self.graphs:
                    result, assets = self.admit(profile)
                    self.assertEqual(result, KENC_ERR_MODEL)
                    self.assertFalse(assets.value)


if __name__ == "__main__":
    unittest.main()
