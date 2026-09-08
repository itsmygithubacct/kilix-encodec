"""Real raw-object stalls and owned cleanup, without downloads or model input."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import build_native_package as package
import build_content_bundle as objects


class GitDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='native-git-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.root.chmod(0o700)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.git('init', '-q')
        for name in ('Makefile', 'LICENSE', 'THIRD-PARTY-NOTICES.md',
                     'tools/debian-dependencies.json', 'tools/build_native_package.py'):
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixed ' + name + '\n')
        self.git('add', '.')
        self.git('-c', 'user.name=itsmygithubacct', '-c',
                 'user.email=itsmygithubacct@users.noreply.github.com', 'commit', '-qm', 'fixture')
        self.commit = self.git('rev-parse', 'HEAD')
        self.fds = len(os.listdir('/proc/self/fd'))

    def tearDown(self):
        self.assertEqual(len(os.listdir('/proc/self/fd')), self.fds)

    def git(self, *args):
        return subprocess.check_output(['/usr/bin/git', '-c', 'maintenance.auto=false',
                                        '-C', str(self.repo), *args],
                                       stderr=subprocess.DEVNULL).decode().strip()

    def fifo_case(self, kind, ending):
        identity = {'commit': self.commit, 'tree': self.git('rev-parse', self.commit + '^{tree}'),
                    'blob': self.git('rev-parse', self.commit + ':LICENSE')}[kind]
        path = self.repo / '.git/objects' / identity[:2] / identity[2:]
        original = path.read_bytes()
        path.unlink()
        os.mkfifo(path)
        output = self.root / (kind + '-' + ending)
        command = [sys.executable, '-B', str(ROOT / 'tools/build_native_package.py'),
                   '--source', str(self.repo), '--commit', self.commit,
                   '--content-source', str(self.repo), '--content-commit', self.commit,
                   '--output', str(output), '--timeout', '1' if ending == 'deadline' else '5']
        child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 start_new_session=True)
        started = time.monotonic()
        git_pid = None
        try:
            while time.monotonic() - started < .8:
                children = Path(f'/proc/{child.pid}/task/{child.pid}/children')
                for pid in children.read_text().split() if children.exists() else []:
                    proc = Path('/proc') / pid
                    try:
                        argv = (proc / 'cmdline').read_bytes().split(b'\0')
                        blocked = (proc / 'wchan').read_text() == 'wait_for_partner'
                    except FileNotFoundError:
                        continue
                    if blocked and kind.encode() in argv and identity.encode() in argv:
                        git_pid = int(pid)
                        break
                if git_pid is not None:
                    break
                time.sleep(.005)
            self.assertIsNotNone(git_pid, 'actual selected raw Git subprocess did not reach its FIFO')
            signaled = time.monotonic()
            if ending != 'deadline':
                child.send_signal(getattr(signal, ending))
            stdout, stderr = child.communicate(timeout=2)
            self.assertEqual(child.returncode, 1, (stdout, stderr))
            self.assertIn(b'deadline exceeded' if ending == 'deadline' else b'interrupted', stderr)
            self.assertLess(time.monotonic() - (started if ending == 'deadline' else signaled), 1.7)
            self.assertFalse(Path('/proc', str(git_pid)).exists())
            self.assertFalse(os.path.lexists(output))
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
            child.communicate(timeout=3)
            path.unlink()
            path.write_bytes(original)

    def test_initial_commit_tree_and_blob_fifo_deadlines(self):
        for kind in ('commit', 'tree', 'blob'):
            with self.subTest(kind=kind):
                self.fifo_case(kind, 'deadline')

    def test_initial_commit_tree_and_blob_fifo_sigint(self):
        for kind in ('commit', 'tree', 'blob'):
            with self.subTest(kind=kind):
                self.fifo_case(kind, 'SIGINT')

    def test_initial_commit_tree_and_blob_fifo_sigterm(self):
        for kind in ('commit', 'tree', 'blob'):
            with self.subTest(kind=kind):
                self.fifo_case(kind, 'SIGTERM')

    def test_whole_capture_budget_is_not_reset_between_objects(self):
        real = subprocess.Popen
        pids = []
        def delayed(command, **kwargs):
            child = real([sys.executable, '-c',
                          'import os,sys,time;time.sleep(.12);os.execv(sys.argv[1],sys.argv[1:])',
                          *command], **kwargs)
            pids.append(child.pid)
            return child
        deadline = time.monotonic() + .3
        def check():
            if time.monotonic() >= deadline:
                raise TimeoutError('one unchanged operation budget')
        with mock.patch.object(objects.subprocess, 'Popen', side_effect=delayed):
            with self.assertRaisesRegex(TimeoutError, 'unchanged operation'):
                package.source_files(self.repo, self.commit, check)
        self.assertGreaterEqual(len(pids), 2)
        self.assertLess(time.monotonic(), deadline + .3)
        for pid in pids:
            self.assertFalse(Path('/proc', str(pid)).exists())

    def test_expired_first_read_starts_no_process(self):
        def expired():
            raise TimeoutError('already expired')
        with mock.patch.object(objects.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(TimeoutError, 'already expired'):
                package.source_files(self.repo, self.commit, expired)
        spawn.assert_not_called()

    def test_partial_pipe_and_eof_wait_observe_same_deadline(self):
        real = subprocess.Popen
        unrelated = real(['/usr/bin/sleep', '15'])
        try:
            for source in ('import os,time;os.write(1,b"x");time.sleep(10)',
                           'import os,time;os.close(1);time.sleep(10)'):
                pids = []
                def spawn(_command, **kwargs):
                    child = real([sys.executable, '-c', source], **kwargs)
                    pids.append(child.pid)
                    return child
                deadline = time.monotonic() + .2
                def check():
                    if time.monotonic() >= deadline:
                        raise TimeoutError('pipe budget')
                with mock.patch.object(objects.subprocess, 'Popen', side_effect=spawn):
                    with self.assertRaisesRegex(TimeoutError, 'pipe budget'):
                        objects.git_bytes(self.repo, [], 64, check=check)
                self.assertFalse(Path('/proc', str(pids[0])).exists())
                self.assertIsNone(unrelated.poll())
        finally:
            unrelated.terminate()
            unrelated.wait()

    def test_byte_bound_preserves_binary_output_and_reaps_overflow(self):
        data = bytes(range(256)) * 300
        path = self.repo / 'binary'
        path.write_bytes(data)
        oid = self.git('hash-object', '-w', 'binary')
        self.assertEqual(objects.git_object(self.repo, 'blob', oid, len(data)), data)
        with self.assertRaisesRegex(ValueError, 'exceeds bound'):
            objects.git_object(self.repo, 'blob', oid, len(data) - 1)
        self.assertEqual(objects.git_object(self.repo, 'blob', oid, len(data)), data)

    def test_dedicated_raw_capture_reaps_escaped_children_on_all_endings(self):
        child_script = self.root / 'child.py'
        child_script.write_text('''import os,signal,sys,time
pid=os.fork()
if pid==0:
 os.setsid();signal.signal(signal.SIGTERM,signal.SIG_IGN)
 with open(sys.argv[1],"w") as stream:stream.write(str(os.getpid()))
 while True:time.sleep(.01)
while not os.path.exists(sys.argv[1]):time.sleep(.005)
if sys.argv[2]=="success":os.execv(sys.argv[3],sys.argv[3:])
while True:time.sleep(.01)
''')
        helper = self.root / 'helper.py'
        helper.write_text('''import ctypes,hashlib,json,os,pathlib,signal,subprocess,sys,time
sys.path.insert(0,sys.argv[1]);import build_content_bundle as objects;import converter_build_io as io
assert ctypes.CDLL(None).prctl(36,1,0,0,0)==0
for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,io.stop)
root=pathlib.Path(sys.argv[2]);mode=sys.argv[3];repo=pathlib.Path(sys.argv[4]);oid=sys.argv[5]
real=subprocess.Popen;leader=None
def spawn(command,**kwargs):
 global leader
 child=real([sys.executable,str(root/"child.py"),str(root/(mode+".pid")),mode,*command],**kwargs)
 leader=child.pid
 return child
objects.subprocess.Popen=spawn
deadline=time.monotonic()+(2 if mode!="deadline" else .3)
before=len(os.listdir("/proc/self/fd"));error=None;data=None
try:data=objects.git_object(repo,"commit",oid,65536,check=lambda:io.checkpoint(deadline),cleanup=io.reap_owned)
except BaseException as caught:error=type(caught).__name__
finally:io.reap_owned()
print(json.dumps(dict(error=error,leader=leader,identity=None if data is None else hashlib.sha1(b"commit "+str(len(data)).encode()+b"\\0"+data).hexdigest(),fd_delta=len(os.listdir("/proc/self/fd"))-before,children=pathlib.Path(f"/proc/self/task/{os.getpid()}/children").read_text().split())))
''')
        unrelated = subprocess.Popen(['/usr/bin/sleep', '15'])
        try:
            for mode in ('success', 'deadline', 'SIGINT', 'SIGTERM'):
                with self.subTest(mode=mode):
                    child = subprocess.Popen([sys.executable, '-B', str(helper), str(ROOT/'tools'),
                                              str(self.root), mode, str(self.repo), self.commit],
                                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    try:
                        marker = self.root / (mode + '.pid')
                        until = time.monotonic() + 1
                        while not marker.exists() and time.monotonic() < until:
                            time.sleep(.005)
                        self.assertTrue(marker.exists())
                        if mode.startswith('SIG'):
                            child.send_signal(getattr(signal, mode))
                        out, err = child.communicate(timeout=3)
                        self.assertEqual(child.returncode, 0, err)
                        result = json.loads(out)
                        self.assertEqual(result['error'], None if mode == 'success' else
                                         'TimeoutError' if mode == 'deadline' else 'InterruptedError')
                        self.assertEqual(result['children'], [])
                        self.assertEqual(result['fd_delta'], 0)
                        if mode == 'success':
                            self.assertEqual(result['identity'], self.commit)
                        self.assertFalse(Path('/proc', str(result['leader'])).exists())
                        self.assertFalse(Path('/proc', marker.read_text()).exists())
                        self.assertIsNone(unrelated.poll())
                    finally:
                        if child.poll() is None:
                            child.kill()
                        child.communicate(timeout=3)
        finally:
            unrelated.terminate()
            unrelated.wait()


if __name__ == '__main__':
    unittest.main()
