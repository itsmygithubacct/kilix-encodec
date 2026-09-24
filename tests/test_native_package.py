"""Offline package-source and no-clobber boundaries; no model execution."""
import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import build_native_package as package


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.repo=self.root/'repo'
        self.repo.mkdir()
        self.git('init','-q')
        self.git('config','user.name','Kilix Test')
        self.git('config','user.email','test@example.invalid')
        for name in ('Makefile','LICENSE','THIRD-PARTY-NOTICES.md','tools/debian-dependencies.json','tools/build_native_package.py'):
            path=self.repo/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('fixed '+name+'\n')
        self.commit=self.commit_all()

    def git(self,*args):
        return subprocess.check_output(['git','-C',str(self.repo),*args],stderr=subprocess.DEVNULL).decode().strip()

    def commit_all(self):
        self.git('add','.')
        self.git('commit','-qm','fixture')
        return self.git('rev-parse','HEAD')

    def test_raw_object_snapshot_ignores_dirty_worktree_and_archive_attributes(self):
        tree,baseline=package.source_files(self.repo,self.commit,lambda:None)
        (self.repo/'LICENSE').write_text('replaced')
        (self.repo/'.gitattributes').write_text('LICENSE export-ignore\n')
        self.assertEqual(package.source_files(self.repo,self.commit,lambda:None),(tree,baseline))
        blob=self.git('hash-object','-w','LICENSE')
        old=self.git('rev-parse',self.commit+':LICENSE')
        self.git('replace',old,blob)
        self.assertEqual(package.source_files(self.repo,self.commit,lambda:None),(tree,baseline))

    def test_model_payload_and_symlink_are_refused(self):
        (self.repo/'model.onnx').write_bytes(b'not a model')
        with self.assertRaisesRegex(ValueError,'payload'):
            package.source_files(self.repo,self.commit_all(),lambda:None)
        (self.repo/'model.onnx').unlink()
        (self.repo/'linked').symlink_to('/etc/passwd')
        with self.assertRaisesRegex(ValueError,'unsupported'):
            package.source_files(self.repo,self.commit_all(),lambda:None)

    def test_source_archive_is_exact_and_repeatable(self):
        long_name = 'tests/data/receipts-' + 'a' * 24 + '/' + 'b' * 130 + '.json'
        path = self.repo/long_name
        path.parent.mkdir(parents=True)
        path.write_text('{"receipt": true}\n')
        commit = self.commit_all()
        _,files=package.source_files(self.repo,commit,lambda:None)
        first,second=self.root/'first.gz',self.root/'second.gz'
        package.write_source_archive(files,first)
        package.write_source_archive(files,second)
        self.assertEqual(first.read_bytes(),second.read_bytes())
        with tarfile.open(first) as archive:
            self.assertEqual(set(archive.getnames()),set(files))
            for item in archive:
                self.assertEqual(archive.extractfile(item).read(),files[item.name][1])
                self.assertEqual((item.uid,item.gid,item.mtime),(0,0,0))

    def test_inventory_binds_bytes_modes_and_only_linker_symlink(self):
        root=self.root/'installed';lib=root/'usr/lib';lib.mkdir(parents=True)
        target=lib/'libkilix-encodec.so.0';target.write_bytes(b'library')
        (lib/'libkilix-encodec.so').symlink_to(target.name)
        rows=package.inventory(root,lambda:None)
        self.assertEqual(rows['usr/lib/libkilix-encodec.so.0']['sha256'],hashlib.sha256(b'library').hexdigest())
        (lib/'escape').symlink_to('/etc/passwd')
        with self.assertRaises(ValueError):package.inventory(root,lambda:None)

    def test_existing_output_entries_refuse_before_build(self):
        output=self.root/'output'
        for kind in ('directory','file','symlink'):
            with self.subTest(kind=kind):
                if kind=='directory':output.mkdir()
                elif kind=='file':output.write_bytes(b'preserve')
                else:output.symlink_to(self.root/'missing')
                args=argparse.Namespace(output=output,source=self.repo,content_source=self.repo,timeout=1)
                before=len(os.listdir('/proc/self/fd'))
                with self.assertRaisesRegex(ValueError,'new entry'):package.build(args)
                self.assertEqual(len(os.listdir('/proc/self/fd')),before)
                if kind=='directory':output.rmdir()
                else:output.unlink()

    def test_deadline_checkpoint_interrupts_source_walk(self):
        def expired():package.process_io.checkpoint(time.monotonic()-1)
        with self.assertRaises(TimeoutError):package.source_files(self.repo,self.commit,expired)


    def test_direct_dependencies_are_minimums_not_exact_pins(self):
        lock={'packages':{'libonnxruntime1.21':{'version':'1.21.0+dfsg-1'},
                          'libssl3t64':{'version':'3.5.6-1~deb13u2'},
                          'libc6':{'version':'2.41-12+deb13u3'}}}
        depends=package.debian_depends(lock)
        self.assertEqual(depends,'libonnxruntime1.21 (>= 1.21.0+dfsg-1), '
                         'libssl3t64 (>= 3.5.6-1~deb13u2), libc6 (>= 2.41-12+deb13u3)')
        self.assertNotIn('(= ',depends)

    def test_runtime_libraries_name_their_owner_and_refuse_unowned(self):
        owners={'libc.so.6':('libc6','2.41-12+deb13u3'),'libcrypto.so.3':('libssl3t64','3.5.6-1~deb13u2')}
        paths=[Path('/usr/lib/x86_64-linux-gnu/'+name) for name in owners]
        rows=package.runtime_libraries(paths,lambda path:owners[path.name],
                                       lambda path:{'bytes':1,'sha256':'0'*64})
        self.assertEqual(rows['libc.so.6'],{'package':'libc6','version':'2.41-12+deb13u3',
                                            'built':{'bytes':1,'sha256':'0'*64}})
        with self.assertRaisesRegex(ValueError,'no Debian owner'):
            package.runtime_libraries([Path('/usr/lib/x86_64-linux-gnu/libstray.so.1')],
                                      lambda path:('',''),lambda path:{})
        with self.assertRaisesRegex(ValueError,'duplicate'):
            package.runtime_libraries([paths[0],Path('/lib/x86_64-linux-gnu/libc.so.6')],
                                      lambda path:owners[path.name],lambda path:{})

if __name__=='__main__':unittest.main()
