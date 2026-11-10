import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import repeat

class RepeatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.binary = cls.dir / 'smlops'
        subprocess.run(['go', 'build', '-o', str(cls.binary), './cmd/smlops'], cwd=PROJECT, check=True)
        cls.wheels = os.environ.get('WINE_WHEELHOUSE')
        cls.python = Path(os.environ.get('WINE_PYTHON', sys.executable))
        cls.template = cls.dir / 'template'; cls.template.mkdir()
        cmd = [str(cls.python), str(HERE/'import_reference.py'), '--smlops', str(cls.binary), '--store', str(cls.template), '--repository-id', '0123456789abcdef0123456789abcdef']
        if cls.wheels: cmd += ['--wheelhouse', cls.wheels]
        p = subprocess.run(cmd, capture_output=True, text=True, check=True)
        cls.source = json.loads(p.stdout)['run']['record_id']
    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.store = self.root/'store'; shutil.copytree(self.template, self.store)
        self.work = self.root/'work'
    def call(self, *args):
        return json.loads(subprocess.run([str(self.binary), *map(str,args)], capture_output=True, text=True, check=True).stdout)
    def plan(self): return self.call('recovery','plan','--store',self.store,'--run',self.source)
    def invoke(self, python=None): return repeat.repeat(self.binary,self.store,self.source,python or self.python,self.work)
    def get_run(self, report): return self.call('run','show','--store',self.store,'--id',report['run_id'])['record']['body']
    def test_paths_and_no_clobber(self):
        root = self.root/'inputs'; root.mkdir()
        for path in ('../bad','/bad','a//b','a/../b','a/./b'):
            with self.assertRaises(ValueError): repeat.safe_path(root,path)
        (root/'link').symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError): repeat.safe_path(root,'link/escape')
        (root/'existing').write_text('kept')
        with self.assertRaises(ValueError): repeat.safe_path(root,'existing')
        self.work.mkdir()
        with self.assertRaises(FileExistsError): self.invoke()
        (self.root/'alias').symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError): repeat.repeat(self.binary,self.store,self.source,self.python,self.root/'alias'/'new')
    def test_no_source_does_not_register(self):
        report=repeat.repeat(self.binary,self.store,'f'*32,self.python,self.work)
        self.assertIsNone(report['run_id']); self.assertEqual(report['registration'],'unchecked')
        self.assertFalse((self.work/'register-request.json').exists())
    def test_missing_required_registers_failed(self):
        plan=self.plan(); m=next(x for x in plan['materials'] if x['required'] and x['source']['kind']=='object')
        (self.store/'objects'/'sha256'/m['source']['object']['digest']).unlink()
        report=self.invoke(); self.assertEqual(report['materials'],'failed'); self.assertEqual(report['registration'],'confirmed')
        run=self.get_run(report); self.assertEqual(run['outcome'],'failed'); self.assertIsNone(run['started_at']); self.assertIsNone(run['finished_at']); self.assertEqual(run['source_run_id'],self.source)
    def test_corrupt_required_registers_failed(self):
        plan=self.plan(); m=next(x for x in plan['materials'] if x['required'] and x['source']['kind']=='object')
        (self.store/'objects'/'sha256'/m['source']['object']['digest']).write_bytes(b'bad')
        r=self.invoke(); self.assertEqual(r['materials'],'failed'); self.assertEqual(self.get_run(r)['outcome'],'failed')
    def test_loss_after_plan_stops_at_use(self):
        plan=self.plan(); m=next(x for x in plan['materials'] if x['required'] and x['source']['kind']=='object')
        original=repeat.cli
        def intercept(binary,*args):
            result=original(binary,*args)
            if args[:2]==('recovery','plan'): (self.store/'objects'/'sha256'/m['source']['object']['digest']).unlink()
            return result
        with mock.patch.object(repeat,'cli',side_effect=intercept): r=self.invoke()
        self.assertEqual(r['materials'],'failed'); self.assertEqual(self.get_run(r)['outcome'],'failed')
    def test_uncertain_registration_preserves_exact_request(self):
        # Force an early, well-defined failed attempt and hide its first receipt.
        plan=self.plan(); m=next(x for x in plan['materials'] if x['required'] and x['source']['kind']=='object')
        (self.store/'objects'/'sha256'/m['source']['object']['digest']).unlink()
        original=repeat.cli
        def uncertain(binary,*args):
            if args[:2]==('run','register'):
                original(binary,*args)
                raise ValueError('uncertain registration')
            return original(binary,*args)
        with mock.patch.object(repeat,'cli',side_effect=uncertain): r=self.invoke()
        self.assertEqual(r['registration'],'unconfirmed'); self.assertIsNone(r['run_id'])
        request=self.work/'register-request.json'; before=request.read_bytes()
        first=self.call('run','register','--store',self.store,'--request',request)
        second=self.call('run','register','--store',self.store,'--request',request)
        self.assertEqual(first,second); self.assertEqual(request.read_bytes(),before)

    def test_callback_failure_cleans_up_child(self):
        spawned = []
        popen = subprocess.Popen
        def launch(*args, **kwargs):
            child = popen(*args, **kwargs); spawned.append(child); return child
        def fail():
            raise repeat.Interrupted()
        with mock.patch.object(repeat.subprocess, 'Popen', side_effect=launch):
            with self.assertRaises(repeat.Interrupted):
                repeat.execute([sys.executable, '-c', 'import time; time.sleep(30)'], on_start=fail)
        self.assertIsNotNone(spawned[0].poll())

    def test_sigterm_stops_child_group(self):
        code = """
import os, signal, subprocess, sys
sys.path.insert(0, sys.argv[1])
import repeat
children=[]
original=subprocess.Popen
def launch(*args, **kwargs):
    proc=original(*args, **kwargs); children.append(proc); return proc
repeat.subprocess.Popen=launch
def handler(signum, frame): raise repeat.Interrupted()
signal.signal(signal.SIGTERM, handler)
def interrupt(): os.kill(os.getpid(), signal.SIGTERM)
try:
    repeat.execute([sys.executable, '-c', 'import time; time.sleep(30)'], on_start=interrupt)
except repeat.Interrupted:
    pass
else:
    raise AssertionError('signal not observed')
assert children and children[0].poll() is not None
"""
        subprocess.run([sys.executable, '-c', code, str(HERE)], check=True, timeout=10)

    @unittest.skipUnless(os.environ.get('WINE_WHEELHOUSE'), 'locked wheels not supplied')
    def test_optional_source_output_does_not_block_execution(self):
        plan=self.plan(); m=next(x for x in plan['materials'] if x['purpose']=='original_output' and x['path']=='reference/predictions.csv')
        (self.store/'objects'/'sha256'/m['source']['object']['digest']).unlink()
        r=self.invoke()
        if r['runtime']=='failed' and 'fingerprint differs' in (r['error'] or ''):
            self.skipTest('host differs from historical environment')
        self.assertEqual((r['execution'],r['registration'],r['numerical']),('succeeded','confirmed','unchecked'),r)
        self.assertEqual(self.get_run(r)['outcome'],'succeeded'); self.assertEqual(repeat.exit_status(r),1)

    @unittest.skipUnless(os.environ.get('WINE_WHEELHOUSE'), 'locked wheels not supplied')
    def test_full_fingerprint_mismatch_preflight(self):
        reference=PROJECT/'examples/wine/evidence/main-v1/reference/result/environment.json'
        changed=json.loads(reference.read_text()); changed['libc'][1]='different'
        expected=self.root/'different-environment.json';expected.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, 'runtime fingerprint differs'):
            repeat.execute([self.python,'-I',repeat.HELPER,'preflight',PROJECT/'examples/wine',expected],repeat.clean_env())

    @unittest.skipUnless(os.environ.get('WINE_WHEELHOUSE'), 'locked wheels not supplied')
    def test_wrong_environment_before_train(self):
        r=self.invoke('/usr/bin/false'); self.assertEqual(r['runtime'],'failed');self.assertEqual(r['execution'],'unchecked');self.assertEqual(self.get_run(r)['outcome'],'failed');self.assertFalse((self.work/'result').exists())
    @unittest.skipUnless(os.environ.get('WINE_WHEELHOUSE'), 'locked wheels not supplied')
    def test_real_repeat_and_history(self):
        before=self.call('run','show','--store',self.store,'--id',self.source)['record']
        source_bytes={o['object']['digest']:(self.store/'objects'/'sha256'/o['object']['digest']).read_bytes() for o in before['body']['outputs']}
        r=self.invoke()
        if r['runtime']=='failed' and 'fingerprint differs' in (r['error'] or ''):
            self.skipTest('host differs from historical environment; negative result was retained')
        self.assertEqual((r['materials'],r['runtime'],r['execution'],r['registration'],r['numerical']),('verified','passed','succeeded','confirmed','pass'),r)
        self.assertEqual(repeat.exit_status(r),0)
        run=self.get_run(r);self.assertEqual(run['source_run_id'],self.source);self.assertEqual(run['snapshot_id'],before['body']['snapshot_id']);self.assertNotEqual(r['run_id'],self.source)
        self.assertEqual(before,self.call('run','show','--store',self.store,'--id',self.source)['record'])
        self.assertEqual(source_bytes,{digest:(self.store/'objects'/'sha256'/digest).read_bytes() for digest in source_bytes})
        request=json.loads((self.work/'register-request.json').read_text()); self.assertEqual(request['body']['source_run_id'],run['source_run_id']); self.assertEqual(request['body']['snapshot_id'],run['snapshot_id']); self.assertEqual(self.call('run','register','--store',self.store,'--request',self.work/'register-request.json')['record_id'],r['run_id'])
    @unittest.skipUnless(os.environ.get('WINE_WHEELHOUSE'), 'locked wheels not supplied')
    def test_material_changed_after_preflight_stops_before_train(self):
        original=repeat.execute
        def drift(argv,env=None,on_start=None):
            result=original(argv,env,on_start)
            if 'preflight' in argv:
                (self.work/'inputs/wine/data/wine.data').write_bytes(b'changed')
            return result
        with mock.patch.object(repeat,'execute',side_effect=drift): r=self.invoke()
        if r['runtime']=='failed' and 'fingerprint differs' in (r['error'] or ''):
            self.skipTest('host differs from historical environment')
        self.assertEqual(r['materials'],'failed',r);self.assertEqual(r['execution'],'unchecked')
        self.assertEqual(self.get_run(r)['outcome'],'failed');self.assertFalse((self.work/'result').exists())

    @unittest.skipUnless(os.environ.get('WINE_WHEELHOUSE'), 'locked wheels not supplied')
    def test_process_failure_and_interrupt(self):
        original=repeat.execute
        for exc,expected in ((ValueError('test process failure'),'failed'),(repeat.Interrupted(),'interrupted')):
            with self.subTest(expected=expected):
                self.work=self.root/('work-'+expected)
                def stop(argv,env=None,on_start=None):
                    if 'run' in argv and '--phase' in argv:
                        on_start(); raise exc
                    return original(argv,env,on_start)
                with mock.patch.object(repeat,'execute',side_effect=stop): r=self.invoke()
                if r['runtime']=='failed' and 'fingerprint differs' in (r['error'] or ''):
                    self.skipTest('host differs from historical environment')
                self.assertEqual(r['execution'],expected,r);run=self.get_run(r);self.assertEqual(run['outcome'],expected);self.assertIsNotNone(run['started_at']);self.assertEqual(run['metrics'],{});self.assertEqual(run['outputs'],[])
