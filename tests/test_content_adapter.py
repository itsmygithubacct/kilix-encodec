"""Installed admission against the real asset/v3 catalogue code and kilix-license receipts.

Only the graph bytes are synthetic: each profile's catalogue entry is the
packaged one with its files replaced by small stand-ins, the compiled population
is patched to match, and the catalogue pin is re-pinned to those bytes -- except
in the arms that plant a catalogue the pin does not match. Receipts are real
kilix-license receipts for the real EnCodec licence records, written to the one
store kilix_license.receipt_store_root() names under a private stack home.
These tests authorize and qualify no actual model population.
"""
from contextlib import ExitStack
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import installed_assets as adapter
import kilix_content as content
from kilix_content import receipt as packaged
import kilix_license
from kilix_license.agreement import capture_agreement, typed_agreement_line
from kilix_license.receipts import receipt_from_agreement


def digest(data):
    return hashlib.sha256(data).hexdigest()


def record_for(asset_id):
    from importlib.resources import files
    return kilix_license.LicenseRecord.from_bytes(
        files("kilix_license").joinpath("data", "records", asset_id + ".json").read_bytes())


class Fixture:
    """One installed asset, one covering receipt, one pinned catalogue."""

    def __init__(self, profile=1, *, receipt_present=True, changes=None, pin_catalog=True,
                 catalog_edit=None):
        self.profile = profile
        self.receipt_present = receipt_present
        self.changes = changes or {}
        self.pin_catalog = pin_catalog
        self.catalog_edit = catalog_edit

    def __enter__(self):
        self.stack = ExitStack()
        try:
            self.base = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix="kenc-admit-")))
            asset_id, version, _budget, population = adapter.PROFILES[self.profile]
            self.asset_id = asset_id
            self.graphs = {name: ("synthetic adapter fixture: " + name).encode() for name, _size, _hash in population}
            self.notice = b"Synthetic stand-in for the licence notice.\n"
            self.payloads = {**self.graphs, "notices/LICENSE-cc-by-nc-4.0.txt": self.notice}
            synthetic = tuple((name, len(data), digest(data)) for name, data in self.graphs.items())
            self.stack.enter_context(patch.dict(adapter.PROFILES, {self.profile: (asset_id, version, 4096, synthetic)}))
            document = json.loads(packaged.catalog_bytes())
            entry = next(item for item in document["assets"] if item["id"] == asset_id)
            entry["files"] = [dict(path=name, bytes=len(data), sha256=digest(data))
                              for name, data in sorted(self.payloads.items())]
            entry["sizes"]["installed_bytes"] = sum(map(len, self.payloads.values()))
            entry.update(self.changes)
            if self.catalog_edit is not None:
                self.catalog_edit(entry)
            self.catalog = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
            self.stack.enter_context(patch.object(packaged, "catalog_bytes", lambda: self.catalog))
            if self.pin_catalog:
                self.stack.enter_context(patch.object(packaged, "_CATALOG_SHA256", digest(self.catalog)))
            self.spec = content.Catalog.loads(self.catalog.decode(), label="fixture").require_asset(asset_id)
            self.stack.enter_context(patch.dict(os.environ, {"GPU_TERMINAL_HOME": str(self.base / "stack")}))
            os.environ.pop("KILIX_LICENSE_RECEIPTS", None)
            self.store = kilix_license.ReceiptStore.shared()
            self.record = record_for(asset_id)
            if self.receipt_present:
                self.write_receipt(self.spec.manifest_digest)
            self.root = self.base / "data"
            installer = content.Installer(str(self.root))
            self.selected = Path(installer.asset_destination(self.spec))
            self.selected.mkdir(mode=0o700, parents=True)
            for name, data in self.payloads.items():
                path = self.selected / name
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                path.write_bytes(data)
                path.chmod(0o600)
            return self
        except BaseException:
            self.stack.close()
            raise

    def write_receipt(self, manifest_digest, record=None):
        record = record or self.record
        agreement = capture_agreement(record, typed_agreement_line(record))
        receipt = receipt_from_agreement(record, agreement, manifest_digest=manifest_digest,
                                         release_digest=packaged.release_digest(),
                                         catalogue_digest=digest(self.catalog))
        return self.store.write(receipt)

    def receipts(self):
        return sorted(self.store.root.glob("*.json"))

    def __exit__(self, *args):
        return self.stack.__exit__(*args)

    def open(self, **kwargs):
        return adapter.admitted_assets(self.profile, str(self.root), **kwargs)

    def refused(self, test, message, reason, **kwargs):
        """Refused, and for this reason: the refusal or the authority failure it wraps."""
        with test.assertRaises(adapter.AdmissionError) as caught:
            with self.open(**kwargs):
                test.fail(message)
        error = caught.exception
        seen = f"{error} | {type(error.__cause__).__name__}: {error.__cause__}"
        if os.environ.get("KENC_ADMISSION_REASONS"):
            print("REASON", message, "=>", seen)
        test.assertIn(reason, seen, message)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.descriptors = len(os.listdir('/proc/self/fd'))

    def tearDown(self):
        self.assertEqual(len(os.listdir('/proc/self/fd')), self.descriptors)

    def test_real_receipts_and_exact_sealed_snapshot_bytes_both_profiles(self):
        for profile in (1, 2):
            with self.subTest(profile=profile), Fixture(profile) as fixture:
                receipts = {path.name: path.read_bytes() for path in fixture.receipts()}
                with fixture.open() as files:
                    self.assertEqual(tuple(name for name, _fd in files), tuple(fixture.graphs))
                    for name, fd in files:
                        self.assertEqual(os.pread(fd, 4096, 0), fixture.graphs[name])
                        self.assertEqual(os.lseek(fd, 0, os.SEEK_CUR), 0)
                        self.assertFalse(os.get_inheritable(fd))
                        self.assertEqual(fcntl.fcntl(fd, 1034) & 15, 15)
                        with self.assertRaises(OSError):
                            os.write(fd, b'x')
                for _name, fd in files:
                    with self.assertRaises(OSError):
                        os.fstat(fd)
                # Admission read the store; it wrote nothing to it.
                self.assertEqual({path.name: path.read_bytes() for path in fixture.receipts()}, receipts)

    def test_no_receipt_absent_store_or_shared_store_is_refused(self):
        with Fixture(receipt_present=False) as fixture:
            self.assertEqual(fixture.receipts(), [])
            fixture.refused(self, 'unreceipted graph yielded', 'field receipt')
        with Fixture() as fixture:
            for path in fixture.receipts():
                path.unlink()
            fixture.refused(self, 'removed receipt admitted', 'field receipt')
        with Fixture() as fixture:
            receipts = fixture.store.root
            moved = receipts.with_name('moved-away')
            receipts.rename(moved)
            fixture.refused(self, 'absent receipt store admitted', 'FileNotFoundError')
            self.assertFalse(receipts.exists(), 'admission created the receipt store')
            moved.rename(receipts)
            receipts.chmod(0o750)
            fixture.refused(self, 'shared receipt store admitted', 'not private to this user')
            receipts.chmod(0o700)
            with fixture.open():
                pass

    def test_a_wrong_receipt_is_refused(self):
        other = adapter.PROFILES[2]
        with Fixture() as fixture:
            # A real receipt for this record, but for another manifest.
            for path in fixture.receipts():
                path.unlink()
            wrong = fixture.write_receipt("e" * 64)
            fixture.refused(self, 'receipt for another manifest admitted', 'field manifest_digest')
            # The same receipt bytes under this binding's file name.
            right = fixture.store.path_for(fixture.record.digest, fixture.spec.manifest_digest)
            right.write_bytes(wrong.read_bytes())
            wrong.unlink()
            fixture.refused(self, 'misfiled receipt admitted', 'field manifest_digest')
            right.unlink()
            # A receipt for this binding whose licence text digest was edited.
            genuine = fixture.write_receipt(fixture.spec.manifest_digest)
            body = json.loads(genuine.read_text())
            body["licence_text_digest"] = "0" * 64
            genuine.write_text(json.dumps(body, sort_keys=True))
            fixture.refused(self, 'edited receipt admitted', 'field licence_text_digest')
            genuine.unlink()
            # The other EnCodec record's receipt, filed under this binding's name.
            foreign = fixture.write_receipt(fixture.spec.manifest_digest, record_for(other[0]))
            right.write_bytes(foreign.read_bytes())
            fixture.refused(self, "another record's receipt admitted", 'field licence_id')
            right.unlink()
            # A FIFO where the receipt should be is read by nobody and admits nothing.
            os.mkfifo(right, 0o600)
            fixture.refused(self, 'FIFO receipt admitted', 'field receipt', timeout_ms=5000)
            right.unlink()
            fixture.write_receipt(fixture.spec.manifest_digest)
            with fixture.open():
                pass

    def test_a_tampered_installed_tree_is_refused(self):
        def flip(fixture):
            path = fixture.selected / 'manifest.json'
            data = bytearray(path.read_bytes()); data[0] ^= 1; path.write_bytes(bytes(data))

        def notice(fixture):
            (fixture.selected / 'notices/LICENSE-cc-by-nc-4.0.txt').write_bytes(b'x' * len(fixture.notice))

        def extra(fixture):
            (fixture.selected / 'extra.onnx').write_bytes(b'undeclared')

        def missing(fixture):
            (fixture.selected / 'notices/LICENSE-cc-by-nc-4.0.txt').unlink()

        def symlink(fixture):
            path = fixture.selected / 'manifest.json'
            target = fixture.base / 'elsewhere.json'
            target.write_bytes(path.read_bytes()); path.unlink(); path.symlink_to(target)

        def hardlink(fixture):
            os.link(fixture.selected / 'manifest.json', fixture.base / 'second-name')

        def shared_member(fixture):
            (fixture.selected / 'manifest.json').chmod(0o620)

        def shared_directory(fixture):
            fixture.selected.chmod(0o770)

        def swapped_directory(fixture):
            moved = fixture.selected.with_name('real-asset')
            fixture.selected.rename(moved); fixture.selected.symlink_to(moved)

        for plant, reason in ((flip, 'member digest differs'), (notice, 'member digest differs'),
                              (extra, 'undeclared member'), (missing, 'missing a declared member'),
                              (symlink, 'wrong type'), (hardlink, 'member metadata differs'),
                              (shared_member, 'member metadata differs'),
                              (shared_directory, 'unsafe directory'),
                              (swapped_directory, 'NotADirectoryError')):
            with self.subTest(plant=plant.__name__), Fixture() as fixture:
                plant(fixture)
                fixture.refused(self, 'tampered tree admitted', reason)

    def test_a_tampered_catalogue_is_refused(self):
        with Fixture(pin_catalog=False) as fixture:
            fixture.refused(self, 'catalogue that does not match its pin admitted', 'do not match _CATALOG_SHA256')
        changes = [dict(provider='another-provider'), dict(stream='F104'), dict(version='other-version'),
                   dict(compatibility=dict(consumer_schema='wrong-schema', minimum=1, maximum=1)),
                   dict(compatibility=dict(consumer_schema='kilix.encodec.graphs/v1', minimum=2, maximum=2))]
        for change in changes:
            with self.subTest(change=change), Fixture(changes=change) as fixture:
                fixture.refused(self, 'catalogue metadata mismatch admitted', 'identity differs from native consumer')
        other = record_for(adapter.PROFILES[2][0])

        def other_record(entry):
            entry["licenses"][0]["record_digest"] = other.digest

        def second_licence(entry):
            entry["licenses"].append(dict(entry["licenses"][0], id="another-licence"))

        for edit in (other_record, second_licence):
            with self.subTest(edit=edit.__name__), Fixture(catalog_edit=edit) as fixture:
                fixture.refused(self, 'catalogue licence mismatch admitted', 'licence record differs from native consumer')
        with Fixture() as fixture:
            asset_id, version, budget, population = adapter.PROFILES[1]
            for altered in (population[:-1], population + (("extra.onnx", 1, '0' * 64),),
                            ((population[0][0], population[0][1], '0' * 64),) + population[1:]):
                with patch.dict(adapter.PROFILES, {1: (asset_id, version, budget, altered)}):
                    fixture.refused(self, 'native population mismatch admitted', 'identity differs from native consumer')

    def test_every_load_rechecks_and_changes_during_the_snapshot_refuse(self):
        with Fixture() as fixture:
            with fixture.open() as files:
                saved = dict(files)
                (fixture.selected / 'manifest.json').write_bytes(b'changed after snapshot')
                self.assertEqual(os.pread(saved['manifest.json'], 4096, 0), fixture.graphs['manifest.json'])
            fixture.refused(self, 'changed next load admitted', 'member metadata differs')
        original = adapter._snapshot
        with Fixture() as fixture:
            def revoke(*arguments):
                result = original(*arguments)
                for path in fixture.receipts():
                    path.unlink()
                return result
            with patch.object(adapter, '_snapshot', revoke):
                fixture.refused(self, 'receipt withdrawn during the snapshot admitted', 'field receipt')
        with Fixture() as fixture:
            def rewrite(parent, item, check, keep):
                result = original(parent, item, check, keep)
                if item.path == 'manifest.json':
                    path = fixture.selected / 'manifest.json'
                    path.write_bytes(path.read_bytes())
                return result
            with patch.object(adapter, '_snapshot', rewrite):
                fixture.refused(self, 'member rewritten during the snapshot admitted', 'changed during admission')

    def test_cancel_and_deadline_during_snapshot_release_partial_descriptors(self):
        original = adapter._snapshot
        with Fixture() as fixture:
            calls = []

            def counted(*arguments):
                calls.append(arguments[1].path)
                return original(*arguments)
            with patch.object(adapter, '_snapshot', counted):
                fixture.refused(self, 'canceled read yielded', 'admission canceled', cancelled=lambda: len(calls) >= 2)
            self.assertEqual(len(calls), 2)

            def slow(*arguments):
                result = original(*arguments)
                time.sleep(.02)
                return result
            with patch.object(adapter, '_snapshot', slow):
                fixture.refused(self, 'expired read yielded', 'deadline exceeded', timeout_ms=10)

    def test_admission_creates_nothing(self):
        with Fixture() as fixture:
            absent = fixture.base / 'absent-root'
            with self.assertRaises(adapter.AdmissionError):
                with adapter.admitted_assets(1, str(absent)):
                    self.fail('absent root admitted')
            self.assertFalse(absent.exists())
            before = sorted(str(path) for path in fixture.base.rglob('*'))
            with fixture.open():
                pass
            self.assertEqual(sorted(str(path) for path in fixture.base.rglob('*')), before)

    def test_invalid_request_refuses_before_authority(self):
        with patch.object(content, 'verified_packaged_catalog', side_effect=AssertionError('invalid request opened authority')):
            for profile, root, timeout in [(True, '/', 1000), (0, '/', 1000), (3, '/', 1000), (1, 'relative', 1000),
                                           (1, '/bad/../root', 1000), (1, '/x\0y', 1000), (1, '/' + 'x' * 4095, 1000),
                                           (1, '/', True), (1, '/', 0), (1, '/', 120001)]:
                with self.subTest(profile=profile, root=root[:20], timeout=timeout):
                    with self.assertRaises(adapter.AdmissionError), adapter.admitted_assets(profile, root, timeout_ms=timeout):
                        self.fail('invalid request yielded')


if __name__ == '__main__':
    unittest.main()
