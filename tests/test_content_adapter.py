"""Small synthetic models with real packaged authority, receipts and FD snapshots.

Only catalog bytes/hash and compiled graph identities are fixture replacements.
These tests do not authorize or qualify any actual model population.
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
from kilix_content import receipt


def digest(data):
    return hashlib.sha256(data).hexdigest()


class Fixture:
    def __init__(self, profile=1, *, receipt_present=True, changes=None):
        self.profile = profile
        self.receipt_present = receipt_present
        self.changes = changes or {}

    def __enter__(self):
        self.stack = ExitStack()
        try:
            self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix="kenc-admit-")))
            asset_id, version, _budget, population = adapter.PROFILES[self.profile]
            self.graphs = {name: ("synthetic adapter fixture: " + name).encode() for name, _size, _hash in population}
            self.payloads = {**self.graphs, "notices/terms.txt": b"Synthetic test population only.\n"}
            population = tuple((name,len(data),digest(data)) for name,data in self.graphs.items())
            self.stack.enter_context(patch.dict(adapter.PROFILES, {self.profile:(asset_id,version,4096,population)}))
            total = sum(map(len,self.payloads.values()))
            raw = dict(schema="kilix.content.asset/v1", id=asset_id, label="Synthetic graph adapter fixture",
                version=version, provider="kilix-encodec", stream="F101",
                files=[dict(path=name,bytes=len(data),sha256=digest(data)) for name,data in self.payloads.items()],
                licenses=[dict(id="fixture-notice",text_sha256=digest(self.payloads["notices/terms.txt"]),decision="informational")],
                compatibility=dict(consumer_schema="kilix.encodec.graphs/v1",minimum=1,maximum=1),
                sizes=dict(download_bytes=10240,installed_bytes=total,temporary_bytes=total+10240),
                source=dict(mode="mirrored",mirrors=["https://example.invalid/fixture.tar"],archive_sha256="7"*64,
                    provenance=dict(project="Synthetic test",revision=version,original_url="https://example.invalid/fixture")))
            raw.update(self.changes)
            self.spec = content.AssetSpec.from_mapping(raw)
            original = receipt._packaged_bytes
            document = json.loads(original(receipt._CATALOG_RESOURCE,"fixture"))
            document["assets"] = [raw]
            catalog = json.dumps(document,sort_keys=True,separators=(",",":")).encode()
            self.stack.enter_context(patch.object(receipt,"_packaged_bytes",lambda name,label:
                catalog if name==receipt._CATALOG_RESOURCE else original(name,label)))
            self.stack.enter_context(patch.object(receipt,"_CATALOG_SHA256",digest(catalog)))
            self.stack.enter_context(patch.dict(os.environ,{"XDG_STATE_HOME":str(self.root/'state')}))
            self.release = content.ReleaseContext.packaged()
            self.store = self.stack.enter_context(content.ReceiptStore.open_default())
            if self.receipt_present:
                decision=content.LicenseDecision.from_mapping(dict(schema="kilix.install.license/v1",kind="decision",
                    artifact_ids=[self.spec.asset_id],decision_class="informational",license_id="fixture-notice",
                    license_text_sha256=digest(self.payloads["notices/terms.txt"]),outcome="record",
                    presenter="kilix-installer",release=self.release.release_id))
                self.store.record(decision,self.payloads["notices/terms.txt"],self.release,[self.spec])
            self.installer=content.Installer(str(self.root/'data'))
            self.selected=Path(self.installer.asset_destination(self.spec))
            self.selected.mkdir(mode=0o700,parents=True)
            for name,data in self.payloads.items():
                path=self.selected/name
                path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
                path.write_bytes(data);path.chmod(0o600)
            return self
        except BaseException:
            self.stack.close()
            raise

    def __exit__(self,*args):
        return self.stack.__exit__(*args)

    def open(self,**kwargs):
        return adapter.admitted_assets(self.profile,str(self.root/'data'),**kwargs)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.descriptors=len(os.listdir('/proc/self/fd'))

    def tearDown(self):
        self.assertEqual(len(os.listdir('/proc/self/fd')),self.descriptors)

    def test_real_receipts_and_exact_borrowed_snapshot_bytes_both_profiles(self):
        for profile in (1,2):
            with self.subTest(profile=profile),Fixture(profile) as fixture:
                with fixture.open() as files:
                    self.assertEqual(tuple(name for name,_fd in files),tuple(fixture.graphs))
                    for name,fd in files:
                        self.assertEqual(os.pread(fd,4096,0),fixture.graphs[name])
                        self.assertEqual(os.lseek(fd,0,os.SEEK_CUR),0)
                        self.assertFalse(os.get_inheritable(fd))
                        self.assertEqual(fcntl.fcntl(fd,1034)&15,15)
                        with self.assertRaises(OSError):os.write(fd,b'x')
                for _name,fd in files:
                    with self.assertRaises(OSError):os.fstat(fd)

    def test_missing_receipt_and_changed_file_never_yield(self):
        for missing in (False,True):
            with self.subTest(missing=missing),Fixture(receipt_present=not missing) as fixture:
                if not missing:(fixture.selected/'manifest.json').write_bytes(b'changed')
                with self.assertRaises(adapter.AdmissionError),fixture.open():
                    self.fail('unadmitted graph yielded')

    def test_catalog_metadata_and_population_cannot_replace_native_identity(self):
        changes=[dict(provider='another-provider'),dict(stream='F104'),dict(version='other-version'),
            dict(compatibility=dict(consumer_schema='wrong-schema',minimum=1,maximum=1)),
            dict(compatibility=dict(consumer_schema='kilix.encodec.graphs/v1',minimum=2,maximum=2))]
        for change in changes:
            with self.subTest(change=change),Fixture(changes=change) as fixture:
                with self.assertRaises(adapter.AdmissionError),fixture.open():
                    self.fail('catalog mismatch yielded')
        with Fixture() as fixture:
            asset_id,version,budget,population=adapter.PROFILES[1]
            for altered in (population[:-1],population+(("extra.onnx",1,'0'*64),),
                    ((population[0][0],population[0][1],'0'*64),)+population[1:]):
                with patch.dict(adapter.PROFILES,{1:(asset_id,version,budget,altered)}):
                    with self.assertRaises(adapter.AdmissionError),fixture.open():
                        self.fail('native population mismatch yielded')

    def test_every_load_rechecks_installed_paths_and_receipts(self):
        with Fixture() as fixture:
            with fixture.open() as files:
                saved=dict(files)
                (fixture.selected/'manifest.json').write_bytes(b'changed after snapshot')
                self.assertEqual(os.pread(saved['manifest.json'],4096,0),fixture.graphs['manifest.json'])
            with self.assertRaises(adapter.AdmissionError),fixture.open():self.fail('changed next load admitted')
        with Fixture() as fixture:
            with fixture.open():pass
            for name in Path(fixture.store.root).glob('*.json'):name.unlink()
            with self.assertRaises(adapter.AdmissionError),fixture.open():self.fail('removed receipt admitted')

    def test_unbound_snapshot_and_wrong_sealed_bytes_refuse(self):
        with Fixture() as fixture:
            actual_duplicate=content.InstalledAsset.duplicate
            def wrong(asset,name):
                if name!='manifest.json':return actual_duplicate(asset,name)
                writer=os.memfd_create('wrong-fixture',os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING)
                os.fchmod(writer,0o600)
                os.write(writer,b'x'*len(fixture.graphs[name]))
                fcntl.fcntl(writer,1033,15)
                fd=os.open(f'/proc/self/fd/{writer}',os.O_RDONLY|os.O_CLOEXEC)
                os.close(writer)
                return fd
            with patch.object(content.InstalledAsset,'duplicate',wrong):
                with self.assertRaises(adapter.AdmissionError),fixture.open():self.fail('wrong sealed bytes admitted')
            with patch.object(content.InstalledAsset,'files',property(lambda _self:())):
                with self.assertRaises(adapter.AdmissionError),fixture.open():self.fail('unbound files admitted')

    def test_cancel_and_deadline_during_snapshot_release_partial_descriptors(self):
        with Fixture() as fixture:
            calls=[]
            original=content.InstalledAsset.duplicate
            def duplicate(asset,name):
                calls.append(name)
                return original(asset,name)
            with patch.object(content.InstalledAsset,'duplicate',duplicate):
                with self.assertRaises(adapter.AdmissionError),fixture.open(cancelled=lambda:len(calls)>=2):
                    self.fail('canceled read yielded')
            self.assertEqual(len(calls),2)
            def slow(asset,name):
                fd=original(asset,name)
                time.sleep(.02)
                return fd
            with patch.object(content.InstalledAsset,'duplicate',slow):
                with self.assertRaises(adapter.AdmissionError),fixture.open(timeout_ms=10):
                    self.fail('expired read yielded')

    def test_invalid_request_refuses_before_authority(self):
        with patch.object(content,'verified_packaged_catalog',side_effect=AssertionError('invalid request opened authority')):
            for profile,root,timeout in [(True,'/',1000),(0,'/',1000),(3,'/',1000),(1,'relative',1000),
                    (1,'/bad/../root',1000),(1,'/x\0y',1000),(1,'/'+'x'*4095,1000),
                    (1,'/',True),(1,'/',0),(1,'/',120001)]:
                with self.subTest(profile=profile,root=root[:20],timeout=timeout):
                    with self.assertRaises(adapter.AdmissionError),adapter.admitted_assets(profile,root,timeout_ms=timeout):
                        self.fail('invalid request yielded')


if __name__=='__main__':unittest.main()
