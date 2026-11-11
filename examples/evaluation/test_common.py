import tempfile
import unittest
from pathlib import Path
from common import append_event,digest,mutation,new_root,read_json,safe_copy_tree
from trace import classify

def complete(call):
 return '1 execve("/bin/test", ["/bin/test"], 0) = 0\n'+call+'\n1 +++ exited with 0 +++\n'

class TestEvaluation(unittest.TestCase):
 def test_mutation_and_copy(self):
  with tempfile.TemporaryDirectory() as tmp:
   r=Path(tmp);source=r/'source';source.mkdir();p=source/'data';p.write_bytes(b'ab')
   target={'sha256':digest(p)['sha256'],'size':2,'mutated_sha256':__import__('hashlib').sha256(b'`b').hexdigest()}
   safe_copy_tree(source,r/'copy');mutation(r/'copy/data','corrupt',target);self.assertEqual(p.read_bytes(),b'ab')
   mutation(p,'remove',target);self.assertFalse(p.exists())
 def test_safety(self):
  with tempfile.TemporaryDirectory() as tmp:
   r=Path(tmp);(r/'exists').mkdir();(r/'link').symlink_to(r/'exists',target_is_directory=True)
   with self.assertRaises(ValueError):new_root(r/'exists')
   with self.assertRaises(ValueError):new_root(r/'link'/'new')
   with self.assertRaises(ValueError):new_root(r/'exists'/'new',[r/'exists'])
 def test_evidence_append(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'index';append_event(path,{'event':'started'});append_event(path,{'event':'failed'})
   self.assertEqual(len(path.read_text().splitlines()),2)
 def test_trace_cases(self):
  self.assertEqual(classify(complete('1 socket(AF_INET6, SOCK_STREAM, IPPROTO_IP) = 3<TCPv6:[1]>\n1 bind(3<TCPv6:[1]>, {sa_family=AF_INET6}, 28) = 0'))['status'],'pass')
  self.assertEqual(classify('1 connect(3<TCP:[1]>, {sa_family=AF_INET}, 16) = 0')['status'],'traffic')
  self.assertEqual(classify(complete('1 connect(3<UNIX-STREAM:[1]>, {sa_family=AF_UNIX}, 16) = 0'))['status'],'pass')
  self.assertEqual(classify('1 sendto(3, "x", 1, 0, NULL, 0) = 1')['status'],'unchecked')
  self.assertEqual(classify('1 execve("/bin/false", ["..."], 0x0) = 0')['status'],'unchecked')
  self.assertEqual(classify('',False)['status'],'unchecked')
if __name__=='__main__':unittest.main()

class TestTraceReassembly(unittest.TestCase):
 def test_matching_and_orphans(self):
  trace='1 execve("/bin/test", ["/bin/test"], 0) = 0\n1 wait4(2,  <unfinished ...>\n2 +++ exited with 0 +++\n1 <... wait4 resumed>NULL, 0, NULL) = 2\n1 +++ exited with 0 +++\n'
  self.assertEqual(classify(trace)['status'],'pass')
  self.assertEqual(classify('1 wait4(2, <unfinished ...>')['status'],'unchecked')
  self.assertEqual(classify('1 <... wait4 resumed>NULL) = 1')['status'],'unchecked')

class TestRealProcess(unittest.TestCase):
 def test_real_fork_trace(self):
  import subprocess,sys,shutil
  if not shutil.which('strace'):self.skipTest('strace unavailable')
  with tempfile.TemporaryDirectory() as tmp:
   trace=Path(tmp)/'trace'
   command='import subprocess,sys; subprocess.run([sys.executable,"-c","print(42)"],check=True)'
   proc=subprocess.run(['strace','-f','-yy','-s','4096','-e','trace=network,process,write,writev,sendfile,splice','-o',str(trace),sys.executable,'-c',command],capture_output=True)
   self.assertEqual(proc.returncode,0);result=classify(trace.read_text());self.assertEqual(result['status'],'pass',result['unknown'])
 def test_detached_session_child_is_stopped(self):
  import subprocess,sys,time,os
  from process import terminate_tree,identity
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'pid'
   command='import subprocess,sys,time; subprocess.Popen([sys.executable,"-c","import os,time;os.setsid();time.sleep(60)"]);time.sleep(60)'
   parent=subprocess.Popen([sys.executable,'-c',command])
   try:
    from process import descendants
    deadline=time.monotonic()+3;children={}
    while time.monotonic()<deadline:
     children=descendants(parent.pid)
     if len(children)>1:break
     time.sleep(.03)
    self.assertGreater(len(children),1);terminate_tree(parent.pid,0.2);parent.wait(timeout=2)
    for pid,start in children.items():
     state=identity(pid)
     if state and state[1]==start:
      raw=Path(f'/proc/{pid}/stat').read_text();self.assertEqual(raw[raw.rfind(')')+2:][0],'Z')
   finally:
    if parent.poll() is None:parent.kill();parent.wait()

class TestAdapters(unittest.TestCase):
 def test_core_negative_not_confused_with_success(self):
  from core_adapter import check_objects
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);p=root/'objects/sha256';p.mkdir(parents=True);(p/'good').write_bytes(b'a');expected=digest(p/'good')
   plan={'materials':[{'key':'dataset','required':True,'source':{'object':{'digest':'good','size':1}}}]}
   # The on-disk address is protocol data; hashing, not mere existence, determines status.
   self.assertEqual(check_objects(root,plan)[0]['status'],'corrupt')
   (p/'good').unlink();self.assertEqual(check_objects(root,plan)[0]['status'],'missing')
 def test_baseline_not_reusing_missing_material(self):
  from baseline_adapter import checks_for_repo
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);plan={'materials':[{'purpose':'input','path':'wine/data/wine.data','key':'dataset','required':True,'source':{'object':{'digest':'0'*64,'size':1}}}]}
   self.assertEqual(checks_for_repo(root,plan)[0]['status'],'missing')
 def test_tracker_separate_runs(self):
  import os,time
  from tracking import Tracker
  from process import Executor
  python=os.environ.get('EVAL_TEST_MLFLOW_PYTHON')
  if not python:self.skipTest('optional locked MLflow environment not supplied')
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);logs=root/'logs';logs.mkdir();ex=Executor(logs,time.monotonic()+120)
   ex.env={'PATH':'/usr/bin:/bin','HOME':str(root),'MLFLOW_DISABLE_TELEMETRY':'true'}
   tracker=Tracker(ex,root,python,Path(__file__).parent/'tracker.py');source=tracker.start({'origin':'synthetic'})
   tracker.end(source,'FINISHED');before=tracker.get(source);attempt=tracker.start({'source_run_id':source});tracker.end(attempt,'FAILED')
   self.assertNotEqual(source,attempt);self.assertEqual(before,tracker.get(source));self.assertEqual(tracker.get(attempt)['info']['status'],'FAILED')

class TestExport(unittest.TestCase):
 def test_protocol_identity_completeness_and_raw_bytes(self):
  import json
  from export import export,NAMES,PROTOCOL_PATH
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);work=root/'work';work.mkdir();protocol=read_json(PROTOCOL_PATH);sha=digest(PROTOCOL_PATH)['sha256']
   series={'harness_commit':'test-head','protocol':protocol,'protocol_sha256':sha};(work/'series.json').write_text(json.dumps(series))
   index=work/'attempts.jsonl';index.write_text(json.dumps({'event':'tool_setup_finished','status':'complete'})+'\n')
   with self.assertRaises(ValueError):export(work,root/'incomplete')
   tool=work/'tool-setup';tool.mkdir();(tool/'manifest.json').write_text(json.dumps({'core_commit':'sha','cli':{'sha256':'dummy'},'source_audit':{'files':{}}}))
   for role in ('source','repeat'):
    d=work/role;d.mkdir()
    for name in NAMES:(d/name).write_bytes((role+':'+name+'\r\n').encode())
   (work/'case-traces').mkdir()
   for number,(case_id,system) in enumerate(protocol['case_order'],1):
    attempt=f'id-{number}';case=work/(case_id+'-'+system);case.mkdir();(case/'commands').mkdir();(case/'commands/commands.jsonl').write_text('private log')
    (work/'case-traces'/f'{number:03}.trace').write_text('private trace')
    stage={'history_unchanged':True,'native_report':{'registration':'confirmed','execution':'succeeded','runtime':'passed','numerical':'pass','material_checks':[{'key':'input:sample','check':'missing','required':True,'detail':'/private/diagnostic'}],'blockers':[{'material_key':'input:sample','code':'missing','message':'/private/diagnostic'}]},'common_checks':[{'key':'input:sample','status':'missing','required':True,'detail':'/private/diagnostic'}],'source_output_dir':str(work/'source'),'repeat_output_dir':str(work/'repeat')}
    result={'status':'observed','attempt_id':attempt,'stages':stage,'protocol_sha256':sha,'harness_commit':'test-head'};(case/'result.json').write_text(json.dumps(result))
    with index.open('a') as f:
     for event in ('started','finished'):f.write(json.dumps({'event':event,'case':case_id,'system':system,'attempt_id':attempt,'status':'observed'})+'\n')
    (work/(case_id+'-'+system+'-audit.json')).write_text(json.dumps({'status':'observed','functional_status':'passed','network_status':'unchecked','attributed_unknowns':[],'functional_assumption':'test','network':{'status':'unchecked'},'training_execs_all':[{'pid':1}]}))
   export(work,root/'out');self.assertEqual((root/'out/S0-core/repeat/predictions.csv').read_bytes(),(work/'repeat/predictions.csv').read_bytes())
   public=(root/'out/results.json').read_text();out=json.loads(public);self.assertNotIn(str(work),public);self.assertNotIn('/private/diagnostic',public);self.assertIn('input:sample',public)
   self.assertEqual((out['protocol_schema_version'],out['series_label'],out['protocol_sha256']),(3,'post-amendment-v3',sha));self.assertEqual(len(out['cases']),8)
   path=work/'S2-core/result.json';bad=json.loads(path.read_text());bad['protocol_sha256']='0'*64;path.write_text(json.dumps(bad))
   with self.assertRaises(ValueError):export(work,root/'bad-case')
   self.assertFalse((root/'bad-case').exists())
   bad['protocol_sha256']=sha;bad['harness_commit']='wrong';path.write_text(json.dumps(bad))
   with self.assertRaises(ValueError):export(work,root/'bad-head')
   bad['harness_commit']='test-head';path.write_text(json.dumps(bad));series['protocol_sha256']='0'*64;(work/'series.json').write_text(json.dumps(series))
   with self.assertRaises(ValueError):export(work,root/'bad-series-sha')
   series['protocol_sha256']=sha;series['protocol']['series_label']='wrong';(work/'series.json').write_text(json.dumps(series))
   with self.assertRaises(ValueError):export(work,root/'bad-series')

class TestObservedTraffic(unittest.TestCase):
 def test_unknown_is_never_pass(self):
  self.assertEqual(classify('1 ???( <unfinished ...>\n1 +++ exited with 0 +++\n')['status'],'unchecked')
  self.assertEqual(classify('1 write(7<TCP:[123]>, "x", 1) = 1\n')['status'],'traffic')
 def test_timeout_executor_cleans_setsid(self):
  import sys,time,os
  from process import Executor,StageFailure
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);logs=root/'logs';logs.mkdir();pidfile=root/'child'
   script='import subprocess,sys,time;subprocess.Popen([sys.executable,"-c",'+repr('import os,time;os.setsid();open('+repr(str(pidfile))+',"w").write(str(os.getpid()));time.sleep(60)')+']);time.sleep(60)'
   ex=Executor(logs,time.monotonic()+1);env={'PATH':'/usr/bin:/bin','HOME':str(root)}
   with self.assertRaises(StageFailure):ex.run([sys.executable,'-c',script],root,env)
   pid=int(pidfile.read_text());stat=Path(f'/proc/{pid}/stat')
   if stat.exists():
    raw=stat.read_text();self.assertEqual(raw[raw.rfind(')')+2:][0],'Z')

class TestOrchestration(unittest.TestCase):
 def test_negative_case_rejects_unexpected_training(self):
  from harness import assess_trace
  p={'cases':{'S1':{'training_calls_expected':0}}}
  trace={'status':'pass','execs':[{'pid':3,'call':'execve("pipeline.py", ["run", "validation"], 0) = 0'}]}
  self.assertEqual(assess_trace(trace,0,{'status':'observed'},'S1',p,'/test/smlops')['status'],'blocked')
  trace['execs']=[];self.assertEqual(assess_trace(trace,0,{'status':'observed'},'S1',p,'/test/smlops')['status'],'observed')
  trace['status']='unchecked';self.assertEqual(assess_trace(trace,0,{'status':'observed'},'S1',p,'/test/smlops')['status'],'blocked')

class TestWholeCaseTrace(unittest.TestCase):
 def test_trace_includes_nested_synthetic_training_process(self):
  import os,sys,time
  from process import Executor
  from harness import assess_trace
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);logs=root/'logs';logs.mkdir();pipeline=root/'pipeline.py';pipeline.write_text('print("synthetic only")\n')
   worker=root/'worker.py';worker.write_text('import subprocess,sys\nsubprocess.run([sys.executable,sys.argv[1],"run","--phase","validation"],check=True)\n')
   ex=Executor(logs,time.monotonic()+30);env={'PATH':'/usr/bin:/bin','HOME':str(root)}
   code,_,observation=ex.run([sys.executable,worker,pipeline],root,env)
   result=assess_trace(observation,code,{'status':'observed'},'S0',{'cases':{'S0':{'training_calls_expected':1}}},'/test/smlops')
   self.assertEqual(result['status'],'observed');self.assertEqual(len(result['training_execs_all']),1)

class TestV2Attribution(unittest.TestCase):
 def test_known_cli_leaf_only_and_network_unchecked(self):
  from trace import attribute_unknowns
  from harness import assess_trace
  raw='''100 1.000 execve("/p/smlops", ["/p/smlops", "object"], 0) = 0
100 1.010 clone(child_stack=NULL, flags=CLONE_THREAD) = 101
100 1.020 exit_group(0 <unfinished ...>
101 1.021 ???( <unfinished ...>
100 1.022 <... exit_group resumed>) = ?
101 1.023 +++ exited with 0 +++
100 1.024 +++ exited with 0 +++
'''
  obs=classify(raw);self.assertEqual(obs['status'],'unchecked');self.assertEqual(attribute_unknowns(obs,'/p/smlops')[0]['pid'],101)
  protocol={'cases':{'S1':{'training_calls_expected':0}}}
  audit=assess_trace(obs,0,{'status':'observed'},'S1',protocol,'/p/smlops');self.assertEqual(audit['functional_status'],'passed');self.assertEqual(audit['network_status'],'unchecked')
  self.assertIsNone(attribute_unknowns(obs,'/other/smlops'))
  self.assertEqual(assess_trace(classify(raw.replace('CLONE_THREAD','SIGCHLD')),0,{'status':'observed'},'S1',protocol,'/p/smlops')['status'],'blocked')
  self.assertEqual(assess_trace(classify(raw.replace('101 1.023 +++ exited with 0 +++\n','')),0,{'status':'observed'},'S1',protocol,'/p/smlops')['status'],'blocked')
 def test_traffic_wins_over_unknown_and_unattributed_stops(self):
  raw='1 socket(AF_INET, SOCK_STREAM, 0) = 3<TCP:[1]>\n1 connect(3<TCP:[1]>, {sa_family=AF_INET}, 16) = 0\n2 ???( <unfinished ...>\n'
  self.assertEqual(classify(raw)['status'],'traffic')
  from trace import attribute_unknowns
  self.assertIsNone(attribute_unknowns(classify('1 ???( <unfinished ...>\n'),'/p/smlops'))

class TestNormalExit(unittest.TestCase):
 def test_nonreturning_exit_group_is_not_unknown(self):
  cases=['1 1.0 exit_group(0) = ?\n1 1.1 +++ exited with 0 +++\n',
         '1 1.0 exit_group(0 <unfinished ...>\n1 1.1 <... exit_group resumed>) = ?\n1 1.2 +++ exited with 0 +++\n']
  for case in cases:self.assertEqual(classify('1 0.9 execve("/bin/test", ["/bin/test"], 0) = 0\n'+case)['status'],'pass')

class TestUnknownSocketDescriptors(unittest.TestCase):
 def test_all_transfer_calls_and_each_descriptor(self):
  from trace import attribute_unknowns
  calls=['write(7<socket:[123]>, "x", 1)',
         'writev(7<socket:[123]>, [{iov_base="x", iov_len=1}], 1)',
         'sendfile(8</tmp/file>, 7<socket:[123]>, NULL, 1)',
         'sendfile(7<socket:[123]>, 8</tmp/file>, NULL, 1)',
         'splice(8</tmp/file>, NULL, 7<socket:[123]>, NULL, 1, 0)',
         'splice(7<socket:[123]>, NULL, 8<UNIX-STREAM:[44]>, NULL, 1, 0)',
         'write(7<anon_inode:[unknown]>, "x", 1)']
  for call in calls:
   with self.subTest(call=call):
    obs=classify('1 '+call+' = 1\n');self.assertEqual(obs['status'],'unchecked');self.assertIsNone(attribute_unknowns(obs,'/p/smlops'))
  for call in ('write(7<TCP:[123]>, "x", 1)', 'sendfile(8</tmp/file>, 7<TCPv6:[123]>, NULL, 1)', 'splice(7<socket:[123]>, NULL, 8<TCP:[44]>, NULL, 1, 0)'):
   with self.subTest(call=call):self.assertEqual(classify('1 '+call+' = 1\n')['status'],'traffic')

class TestTraceCompletenessAndPayload(unittest.TestCase):
 def test_payload_is_not_network_metadata(self):
  for call in ('write(1</tmp/log>, "AF_INET", 7)',
               'write(1</tmp/log>, "<TCP:[4]> AF_INET6", 18)',
               'write(2<UNIX-STREAM:[7]>, "AF_INET <TCP:[4]>", 17)',
               'write(7<anon_inode:[eventfd]>, "AF_INET", 7)',
               'write(1</tmp/socket(AF_INET>, "ordinary", 8)',
               'write(1</tmp/sa_family=AF_INET>, "ordinary", 8)'):
   with self.subTest(call=call):self.assertEqual(classify(complete('1 '+call+' = 7'))['status'],'pass')
 def test_empty_truncated_and_missing_child_are_unchecked(self):
  from trace import attribute_unknowns
  traces=['', '1 execve("/bin/test", ["/bin/test"], 0) = 0\n',
          '1 execve("/bin/test", ["/bin/test"], 0) = 0\n1 clone(child_stack=NULL, flags=SIGCHLD) = 2\n1 +++ exited with 0 +++\n',
          '1 write(1</tmp/log>, "x", 1) = 1\n1 execve("/bin/test", ["/bin/test"], 0) = 0\n1 +++ exited with 0 +++\n']
  for text in traces:
   with self.subTest(text=text):
    result=classify(text);self.assertEqual(result['status'],'unchecked');self.assertIsNone(attribute_unknowns(result,'/bin/test'))
    from harness import assess_trace
    audit=assess_trace(result,0,{'status':'observed'},'S1',{'cases':{'S1':{'training_calls_expected':0}}},'/bin/test');self.assertEqual(audit['functional_status'],'blocked')

class TestEventFDAnnotation(unittest.TestCase):
 def test_exact_eventfd_only_and_socket_remains_unknown(self):
  from trace import attribute_unknowns
  label='{eventfd-count=0, eventfd-id=137, eventfd-semaphore=0}'
  valid=classify(complete('1 write(5<'+label+'>, "x", 8) = 8'));self.assertEqual(valid['status'],'pass')
  self.assertEqual(classify(complete('1 write(5<{eventfd-count=0x1, eventfd-id=137, eventfd-semaphore=1}>, "x", 8) = 8'))['status'],'pass')
  for bad in ('{eventfd-count=0, eventfd-id=137}',
              '{eventfd-count=0, eventfd-id=137, eventfd-semaphore=2}',
              '{eventfd-count=0, eventfd-id=137, eventfd-semaphore=0, socket=TCP}',
              '{eventfd-count=0, eventfd-id=137, eventfd-semaphore=0}extra'):
   with self.subTest(bad=bad):self.assertEqual(classify(complete('1 write(5<'+bad+'>, "x", 8) = 8'))['status'],'unchecked')
  self.assertEqual(classify(complete('1 sendfile(4</tmp/out>, 3</tmp/in>, [0] => [8], 8) = 8'))['status'],'pass')
  self.assertEqual(classify(complete('1 sendfile(5<'+label+'>, 3</tmp/in>, [0] => [8], 8) = 8'))['status'],'pass')
  mixed=classify(complete('1 sendfile(5<'+label+'>, 7<socket:[123]>, NULL, 1) = 1'))
  self.assertEqual(mixed['status'],'unchecked');self.assertIsNone(attribute_unknowns(mixed,'/bin/test'))
 def test_real_os_eventfd_under_strace(self):
  import re,shutil,subprocess,sys
  from trace import known_eventfd
  if not shutil.which('strace') or not hasattr(__import__('os'),'eventfd'):self.skipTest('Linux eventfd/strace unavailable')
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'trace';script='import os,struct;f=os.eventfd(0,0);os.write(f,struct.pack("Q",1));os.write(f,struct.pack("Q",1));os.close(f)'
   result=subprocess.run(['strace','-f','-ttt','-yy','-s','4096','-e','trace=network,process,write,writev,sendfile,splice','-o',str(path),sys.executable,'-c',script],capture_output=True)
   self.assertEqual(result.returncode,0);text=path.read_text()
   labels=re.findall(r'\bwrite\(\d+<([^>]+)>',text)
   eventfd_writes=[label for label in labels if label=='anon_inode:[eventfd]' or known_eventfd(label)]
   self.assertGreaterEqual(len(eventfd_writes),2,'both actual eventfd writes must have a recognized annotation')
   observation=classify(text);self.assertEqual(observation['status'],'pass',observation['unknown'])

class TestV3LeafProcessAttribution(unittest.TestCase):
 def trace(self):
  return '''100 1.000 execve("/p/smlops", ["/p/smlops", "object"], 0) = 0
100 1.010 clone(child_stack=NULL, flags=CLONE_THREAD) = 101
101 1.020 clone(child_stack=NULL, flags=CLONE_THREAD) = 102
102 1.030 clone(child_stack=NULL, flags=CLONE_THREAD) = 103
100 1.040 exit_group(0 <unfinished ...>
101 1.050 ???( <unfinished ...>
100 1.060 <... exit_group resumed>) = ?
103 1.070 +++ exited with 0 +++
102 1.080 +++ exited with 0 +++
101 1.090 +++ exited with 0 +++
100 1.100 +++ exited with 0 +++
'''
 def test_multilevel_threads_nonleaf_unknown_is_attributed_but_network_unchecked(self):
  from trace import attribute_unknowns
  observation=classify(self.trace());self.assertEqual(observation['status'],'unchecked')
  attributed=attribute_unknowns(observation,'/p/smlops',rule='v3');self.assertEqual(attributed[0]['pid'],101)
  self.assertIsNone(attribute_unknowns(observation,'/p/smlops')) # v2 remains fail-closed
  from harness import assess_trace
  audit=assess_trace(observation,0,{'status':'observed'},'S1',{'cases':{'S1':{'training_calls_expected':0}}},'/p/smlops')
  self.assertEqual((audit['functional_status'],audit['network_status']),('passed','unchecked'))
 def test_nonthread_spawn_from_nonroot_rejects_whole_group(self):
  from trace import attribute_unknowns
  raw=self.trace().replace('100 1.040 exit_group', '101 1.035 fork() = 200\n200 1.036 +++ exited with 0 +++\n100 1.040 exit_group')
  self.assertIsNone(attribute_unknowns(classify(raw),'/p/smlops',rule='v3'))
  raw=self.trace().replace('100 1.040 exit_group', '102 1.035 clone(child_stack=NULL, flags=SIGCHLD) = 200\n200 1.036 +++ exited with 0 +++\n100 1.040 exit_group')
  self.assertIsNone(attribute_unknowns(classify(raw),'/p/smlops',rule='v3'))
 def test_unknown_before_exit_rejected(self):
  from trace import attribute_unknowns
  raw=self.trace().replace('100 1.040 exit_group(0 <unfinished ...>\n101 1.050 ???( <unfinished ...>', '101 1.035 ???( <unfinished ...>\n100 1.040 exit_group(0 <unfinished ...>')
  self.assertIsNone(attribute_unknowns(classify(raw),'/p/smlops',rule='v3'))
 def test_reexec_and_pid_reuse_rejected(self):
  from trace import attribute_unknowns
  raw=self.trace().replace('100 1.040 exit_group', '101 1.035 execve("/other", ["/other"], 0) = 0\n100 1.040 exit_group')
  self.assertIsNone(attribute_unknowns(classify(raw),'/p/smlops',rule='v3'))
  raw=self.trace()+'101 1.110 execve("/p/smlops", ["/p/smlops"], 0) = 0\n101 1.120 +++ exited with 0 +++\n'
  obs=classify(raw);self.assertTrue(any(x['kind']=='pid_reuse' for x in obs['unknown_events']));self.assertIsNone(attribute_unknowns(obs,'/p/smlops',rule='v3'))
 def test_missing_lineage_missing_terminal_and_temporal_inconsistency_rejected(self):
  from trace import attribute_unknowns
  raw=self.trace().replace('100 1.010 clone(child_stack=NULL, flags=CLONE_THREAD) = 101\n','')
  self.assertIsNone(attribute_unknowns(classify(raw),'/p/smlops',rule='v3'))
  self.assertIsNone(attribute_unknowns(classify(self.trace().replace('103 1.070 +++ exited with 0 +++\n','')),'/p/smlops',rule='v3'))
  raw=self.trace().replace('100 1.010 clone(child_stack=NULL, flags=CLONE_THREAD) = 101\n','').replace('101 1.050 ???( <unfinished ...>','101 1.050 ???( <unfinished ...>\n100 1.055 clone(child_stack=NULL, flags=CLONE_THREAD) = 101')
  self.assertIsNone(attribute_unknowns(classify(raw),'/p/smlops',rule='v3'))
 def test_syscall_after_unfinished_unknown_rejected(self):
  from trace import attribute_unknowns
  raw=self.trace().replace('101 1.050 ???( <unfinished ...>', '101 1.050 ???( <unfinished ...>\n101 1.055 write(1</tmp/log>, \"x\", 1) = 1')
  self.assertIsNone(attribute_unknowns(classify(raw),'/p/smlops',rule='v3'))
 def test_unresolved_socket_descriptor_still_blocks(self):
  from trace import attribute_unknowns
  raw=self.trace().replace('100 1.040 exit_group','102 1.035 write(7<socket:[123]>, "x", 1) = 1\n100 1.040 exit_group')
  obs=classify(raw);self.assertEqual(obs['status'],'unchecked');self.assertIsNone(attribute_unknowns(obs,'/p/smlops',rule='v3'))

class TestV3CompleteActivityTimeline(unittest.TestCase):
 def group(self):
  return '''100 1.000 execve("/p/smlops", ["/p/smlops", "object"], 0) = 0
100 1.010 clone(child_stack=NULL, flags=CLONE_THREAD) = 101
100 1.020 clone(child_stack=NULL, flags=CLONE_THREAD) = 102
100 1.030 exit_group(0 <unfinished ...>
101 1.040 ???( <unfinished ...>
100 1.050 <... exit_group resumed>) = ?
101 1.060 +++ exited with 0 +++
102 1.070 +++ exited with 0 +++
100 1.080 +++ exited with 0 +++
'''
 def test_unknown_thread_activity_before_clone_blocks(self):
  from trace import attribute_unknowns
  raw=self.group().replace('100 1.010 clone', '101 1.005 write(1</tmp/log>, "x", 1) = 1\n100 1.010 clone')
  obs=classify(raw);self.assertEqual(obs['status'],'unchecked');self.assertIsNone(attribute_unknowns(obs,'/p/smlops',rule='v3'))
 def test_sibling_activity_before_clone_blocks(self):
  from trace import attribute_unknowns
  raw=self.group().replace('100 1.020 clone', '102 1.015 --- SIGURG {si_signo=SIGURG} ---\n100 1.020 clone')
  self.assertIsNone(attribute_unknowns(classify(raw),'/p/smlops',rule='v3'))
 def test_activity_after_normal_terminal_blocks(self):
  from trace import attribute_unknowns
  raw=self.group().replace('102 1.070 +++ exited with 0 +++\n','102 1.070 +++ exited with 0 +++\n102 1.075 --- SIGURG {si_signo=SIGURG} ---\n')
  self.assertIsNone(attribute_unknowns(classify(raw),'/p/smlops',rule='v3'))
 def test_root_may_have_recorded_activity_before_its_exec(self):
  from trace import attribute_unknowns
  raw='''1 0.500 execve("/launcher", ["/launcher"], 0) = 0
1 0.600 clone(child_stack=NULL, flags=SIGCHLD) = 100
100 0.700 write(1</tmp/log>, "preexec", 7) = 7
'''+self.group()+'''1 1.100 +++ exited with 0 +++
'''
  obs=classify(raw);self.assertEqual(attribute_unknowns(obs,'/p/smlops',rule='v3')[0]['pid'],101)

class TestFrozenGate(unittest.TestCase):
 def test_all_protocol_archives_and_locks_are_byte_pinned(self):
  import subprocess
  from unittest.mock import patch
  import harness
  files=('docs/evaluation/protocol-v1.json','docs/evaluation/protocol-v2.json','docs/evaluation/protocol-v3.json','docs/evaluation-plan-v1.md','docs/evaluation-plan-v2.md','docs/evaluation-plan.md','docs/evaluation/dvc-tools.txt','docs/evaluation/mlflow-tools.txt','docs/evaluation/evidence-instrumentation/attempts-v2.json','docs/evaluation/evidence-instrumentation/observation.json','docs/evaluation/evidence-instrumentation/synthetic-object-get.trace.txt')
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp)
   def git(*args):return subprocess.check_output(['git',*args],cwd=root,stderr=subprocess.DEVNULL,text=True).strip()
   git('init','-q');git('config','user.name','Synthetic Test');git('config','user.email','test@example.invalid')
   for name in files:
    path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('original '+name)
   git('add','.');git('commit','-qm','frozen');plan=git('rev-parse','HEAD')
   with patch.object(harness,'REPO',root),patch.object(harness,'PLAN',plan):
    self.assertEqual(harness.gate(plan),plan)
    for name in files:
     with self.subTest(path=name):
      git('reset','--hard',plan);(root/name).write_text('changed');git('add',name);git('commit','-qm','change frozen file');head=git('rev-parse','HEAD')
      with self.assertRaisesRegex(ValueError,'approved plan or lock changed'):harness.gate(head)
