#!/usr/bin/env python3
"""Pre-registered Wine evaluation runner. Trials require a reviewed clean commit."""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from common import append_event,digest,new_root,read_json,require,start_attempt,write_json_new
from process import Executor,StageFailure
from prepare import prepare_core,prepare_baseline
from core_adapter import run_case as core_case
from baseline_adapter import run_case as baseline_case
from tracking import Tracker
from trace import attribute_unknowns

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[1]
CORE='5c37e250896fbb67670e7a1897853f78baec144d'
PLAN='107cf252e799b5f0f8e187abec431dab80f4ab84'
NAMES=('result.json','predictions.csv','metrics.json','model.json','environment.json')

def gate(reviewed):
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip()
    require(reviewed==head,'reviewed SHA must match checked-out harness HEAD')
    require(subprocess.run(['git','merge-base','--is-ancestor',PLAN,head],cwd=REPO).returncode==0,'approved protocol not in history')
    require(not subprocess.check_output(['git','status','--porcelain','--untracked-files=all'],cwd=REPO,text=True).strip(),'harness checkout not clean')
    for path in ('docs/evaluation/protocol-v1.json','docs/evaluation/protocol-v2.json','docs/evaluation-plan.md','docs/evaluation/dvc-tools.txt','docs/evaluation/mlflow-tools.txt'):
        pinned=subprocess.check_output(['git','show',PLAN+':'+path],cwd=REPO)
        require((REPO/path).read_bytes()==pinned,'approved plan or lock changed: '+path)
    return head

def clean_environment(root,dvc_bin):
    home=root/'home';home.mkdir();cache=home/'cache';cache.mkdir()
    telemetry=home/'.config/go/telemetry';telemetry.mkdir(parents=True);(telemetry/'mode').write_text('off\n')
    return {'PATH':str(Path(dvc_bin).parent)+':/usr/bin:/bin','HOME':str(home),'XDG_CACHE_HOME':str(cache),
            'TMPDIR':str(root),'LC_ALL':'C','GOTOOLCHAIN':'local','GOPROXY':'off','GOSUMDB':'off','PIP_CONFIG_FILE':'/dev/null','PIP_NO_INDEX':'1','PIP_NO_CACHE_DIR':'1',
            'PIP_DISABLE_PIP_VERSION_CHECK':'1','PIP_NO_INPUT':'1','PYTHONNOUSERSITE':'1',
            'DVC_NO_ANALYTICS':'true','DVC_STUDIO_OFFLINE':'true','DVC_SYSTEM_CONFIG_DIR':str(home/'dvc-system'),'DVC_GLOBAL_CONFIG_DIR':str(home/'dvc-global'),'DVC_SITE_CACHE_DIR':str(home/'dvc-site'),'MLFLOW_DISABLE_TELEMETRY':'true','GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null','GIT_TERMINAL_PROMPT':'0',
            **{x:'1' for x in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','BLIS_NUM_THREADS')}}

def validate_tools(executor,args,protocol):
    versions=protocol['versions']
    for name in ('dvc-tools.txt','mlflow-tools.txt'):
        require(digest(REPO/'docs/evaluation'/name)['sha256']==protocol['tool_locks'][name],'tool lock hash mismatch')
    for python,name in ((str(Path(args.dvc).parent/'python'),'dvc-tools.txt'),(args.mlflow_python,'mlflow-tools.txt')):
        _,out,_=executor.run([python,'-m','pip','freeze','--all'],REPO,executor.env)
        require(sorted(out.read_text().splitlines())==sorted((REPO/'docs/evaluation'/name).read_text().splitlines()),'tool environment differs from lock: '+name)
    for command,expected in (([args.wine_python,'-I','-c','import platform;print(platform.python_version())'],versions['python']),([args.control_python,'-I','-c','import platform;print(platform.python_version())'],versions['python']),([args.dvc,'--version'],versions['dvc']),(['git','--version'],versions['git']),(['strace','--version'],versions['strace']),([args.go,'version'],versions['go'])):
        _,out,_=executor.run(command,REPO,executor.env)
        require(re.search(r'(?<![0-9.])'+re.escape(expected)+r'(?![0-9.])',out.read_text().splitlines()[0]) is not None,'tool version mismatch: '+command[0])
    _,head,_=executor.run(['git','rev-parse','HEAD'],args.platform_source,executor.env)
    require(head.read_text().strip()==versions['platform'],'platform revision mismatch')

def verified_core(executor,source,root):
    core=root/'core';executor.run(['git','clone','--no-local','--no-checkout',source,core],root,executor.env)
    executor.run(['git','checkout','--detach',CORE],core,executor.env)
    _,head,_=executor.run(['git','rev-parse','HEAD'],core,executor.env);require(head.read_text().strip()==CORE,'wrong core revision')
    _,archive,_=executor.run(['git','archive','--format=tar',CORE],core,executor.env)
    with tarfile.open(archive) as tf:
        for member in tf:
            if member.isdir():continue
            require(member.isfile(),'unexpected Git entry')
            p=core/member.name;require(not p.is_symlink() and p.is_file(),'unsafe checkout')
            sourcefile=tf.extractfile(member);require(sourcefile is not None and p.read_bytes()==sourcefile.read(),'checkout differs from Git')
    return core

def audit_cli_sources(core):
    forbidden=re.compile(r'"(?:os/exec|plugin|unsafe|C)"|//go:linkname|\b(?:StartProcess|ForkExec|RawSyscall|Syscall|Exec)\s*\(')
    inventory={}
    for p in sorted(core.rglob('*.go')):
        if '.git' in p.parts or p.name.endswith('_test.go'):continue
        data=p.read_text(encoding='utf-8');require(not forbidden.search(data),'CLI source contains an unsupported process/spawn primitive')
        inventory[p.relative_to(core).as_posix()]=digest(p)
    require(inventory,'no CLI source files found')
    for name in ('go.mod','go.sum'):
        p=core/name
        if p.is_file():inventory[name]=digest(p)
    return {'source_commit':CORE,'files':inventory,'rule':'non-test project source has no searched process-spawn/exec primitives; not a proof about arbitrary hidden processes'}

def run_one(root,case,system,args,protocol,head,attempt,binary,binary_digest):
    work=root/(case+'-'+system);work.mkdir(mode=0o700)
    logs=work/'commands';logs.mkdir();executor=Executor(logs,time.monotonic()+protocol['timeouts_seconds']['case'],trace_children=False)
    executor.env=clean_environment(work,args.dvc);executor.python=args.control_python;executor.wine_python=args.wine_python;executor.dvc=args.dvc;executor.attempt_id=attempt['attempt_id']
    outcome={'case':case,'system':system,'attempt_id':executor.attempt_id,'harness_commit':head,'protocol_sha256':digest(REPO/'docs/evaluation/protocol-v2.json')['sha256'],'stages':{},'status':'blocked'}
    try:
        require(digest(binary)==binary_digest,'pinned CLI changed before case')
        core=verified_core(executor,args.core_source,work)
        require(digest(core/'examples/wine/data/wine.data')=={'sha256':protocol['failure_targets']['dataset']['sha256'],'size':protocol['failure_targets']['dataset']['size']},'source dataset mismatch')
        original=core/'examples/wine/evidence/main-v1/reference/result'
        source_raw=work/'source-raw';shutil.copytree(original,source_raw)
        base=prepare_core(executor,core,work,args.wheelhouse,binary)
        setup={'core':CORE,'source_run':base['ids']['run'],'source_snapshot':base['ids']['snapshot'],'current_snapshot':base['c2_id'],'source_outputs':{n:digest(source_raw/n) for n in NAMES},'inventory':[{'key':m['key'],'path':m['path'],'purpose':m['purpose'],'required':m['required'],'object':m['source'].get('object')} for m in base['source_plan']['materials']]}
        if system=='alternative':
            info=prepare_baseline(executor,core,work,args.wheelhouse,base)
            tracking=work/'mlflow';tracking.mkdir();tracker=Tracker(executor,tracking,args.mlflow_python,HERE/'tracker.py')
            source_id=tracker.start({'origin':'historical caller-reported reference','git_revision':info['c1']})
            metrics=base['original_records']['run']['body']['metrics'];tracker.end(source_id,'FINISHED',{k:v['value'] for k,v in metrics.items()},[source_raw/n for n in NAMES])
            source_record=tracker.get(source_id);uri=urlparse(source_record['info']['artifact_uri']);require(uri.scheme=='file','source artifact URI not local')
            artifact_path=Path(unquote(uri.path));require(artifact_path.is_dir() and tracking in artifact_path.parents,'artifact root escapes')
            require({n:digest(artifact_path/n) for n in NAMES}==setup['source_outputs'],'source artifacts differ from reference')
            info.update({'mlflow_source_id':source_id,'source_artifacts':str(artifact_path)})
            setup['allowed_recovery_assets']={'git_remote':info['git_remote'],'dvc_remote':info['dvc_remote'],'mlflow':str(tracking)};setup['c1_revision']=info['c1'];setup['c2_revision']=info['c2'];setup['mlflow_source_run']=source_id
            write_json_new(work/'setup.json',setup)
            # Only declared Git/DVC/MLflow assets survive source disposal.
            shutil.rmtree(base['store']);shutil.rmtree(source_raw)
            shutil.rmtree(core/'examples/wine')
            outcome['stages']=baseline_case(executor,core,base,info,work,case,protocol,tracker)
        else:
            setup['allowed_recovery_assets']={'store':str(work/'recovery-store')}
            write_json_new(work/'setup.json',setup)
            shutil.rmtree(source_raw);shutil.rmtree(core/'examples/wine')
            outcome['stages']=core_case(executor,core,binary,base,work,case,protocol)
        require(digest(binary)==binary_digest,'pinned CLI changed during case')
        require(outcome['stages']['history_unchanged'],'source history changed')
        outcome['status']='observed'
    except Exception as exc:
        outcome['error']={'type':type(exc).__name__,'message':str(exc)}
    write_json_new(work/'result.json',outcome)
    return outcome

# URI parsing stays in the controller, not the MLflow worker.
from urllib.parse import urlparse,unquote

def assess_trace(observation,code,result,case,protocol,binary,error=None):
    training=[dict(entry) for entry in (observation or {}).get('execs',[]) if 'pipeline.py' in entry['call'] and '"run"' in entry['call']]
    for entry in training:
        entry['exit']=(observation or {}).get('exits',{}).get(str(entry['pid']))
    attributed=attribute_unknowns(observation,binary) if observation else None
    functional=(error is None and code==0 and result['status']=='observed' and observation and observation['status']!='traffic' and attributed is not None and len(training)==protocol['cases'][case]['training_calls_expected'])
    if functional and training:
        functional=all(x.get('time') and x.get('exit') and x['exit'].get('time') and '"validation"' in x['call'] and x['call'].rstrip().endswith('= 0') for x in training)
    auxiliary='observed_no_forbidden_events' if observation and observation['status']=='pass' else (observation or {}).get('status','unchecked')
    return {'status':'observed' if functional else 'blocked','functional_status':'passed' if functional else 'blocked',
            'network_status':auxiliary,'network':observation,'training_execs_all':training,'attributed_unknowns':attributed,
            'functional_assumption':'only pinned byte-checked CLI leaf thread after exit_group may be attributed; network remains unchecked',
            'error':error,'exit':code}

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work',required=True);p.add_argument('--core-source',required=True);p.add_argument('--platform-source',required=True);p.add_argument('--wheelhouse',required=True)
    p.add_argument('--go',required=True);p.add_argument('--dvc',required=True);p.add_argument('--control-python',required=True);p.add_argument('--wine-python',required=True);p.add_argument('--mlflow-python',required=True)
    p.add_argument('--reviewed-commit',required=True,help='exact reviewed clean harness commit; required only after review')
    a=p.parse_args(argv)
    try:
        head=gate(a.reviewed_commit);protocol=read_json(REPO/'docs/evaluation/protocol-v2.json')
        protected=[REPO,a.core_source,a.platform_source,a.wheelhouse]
        root=new_root(a.work,protected);index=root/'attempts.jsonl'
        write_json_new(root/'series.json',{'harness_commit':head,'protocol':protocol,'started_ns':time.time_ns()})
        setup=root/'tool-setup';setup.mkdir();logs=setup/'commands';logs.mkdir()
        append_event(index,{'event':'tool_setup_started','unix_ns':time.time_ns()})
        ex=Executor(logs,time.monotonic()+protocol['timeouts_seconds']['case']);ex.env=clean_environment(setup,a.dvc)
        try:
            validate_tools(ex,a,protocol);checkout=verified_core(ex,a.core_source,setup)
            source_audit=audit_cli_sources(checkout)
            binary=setup/'smlops';_,_,observation=ex.run([a.go,'build','-o',binary,'./cmd/smlops'],checkout,ex.env,allow_unchecked=True)
            os.chmod(binary,0o555);cli_digest=digest(binary);write_json_new(setup/'manifest.json',{'core_commit':CORE,'cli':cli_digest,'source_audit':source_audit,'build_network_observation':observation,'boundary':'tool setup before cases; no offline claim for build'})
            shutil.rmtree(checkout);append_event(index,{'event':'tool_setup_finished','unix_ns':time.time_ns(),'status':'complete'})
        except Exception as error:
            append_event(index,{'event':'tool_setup_finished','unix_ns':time.time_ns(),'status':'blocked','error':type(error).__name__});raise
        traces=root/'case-traces';traces.mkdir();tracehome=root/'trace-control';tracehome.mkdir()
        tracer=Executor(traces,time.monotonic()+protocol['timeouts_seconds']['case']*len(protocol['case_order']))
        traceenv=clean_environment(tracehome,a.dvc)
        for case,system in protocol['case_order']:
            attempt=start_attempt(index,case,system)
            request=root/(case+'-'+system+'-worker.json')
            write_json_new(request,{'root':str(root),'case':case,'system':system,'args':vars(a),'protocol':protocol,'head':head,'attempt':attempt,'binary':str(binary),'binary_digest':cli_digest})
            error=None;observation=None;code=None;stage_id=f'{tracer.count+1:03d}'
            try:
                code,_,observation=tracer.run([a.control_python,HERE/'harness.py','--worker',request],REPO,traceenv,accept_failure=True,allow_unchecked=True,stage_limit=protocol['timeouts_seconds']['case'])
            except StageFailure as exc:
                error=str(exc)
                events=[json.loads(line) for line in (traces/'commands.jsonl').read_text().splitlines()]
                completed=[x for x in events if x.get('event')=='finished' and x.get('stage')==stage_id]
                if completed:observation=completed[-1].get('network')
            result_file=root/(case+'-'+system)/'result.json'
            result=read_json(result_file) if result_file.is_file() else {'status':'blocked'}
            audit=assess_trace(observation,code,result,case,protocol,binary,error)
            passed=audit['status']=='observed'
            audit_path=root/(case+'-'+system+'-audit.json');write_json_new(audit_path,audit)
            append_event(index,{'event':'finished','attempt_id':attempt['attempt_id'],'case':case,'system':system,'status':audit['status'],'unix_ns':time.time_ns()})
            if not passed:return 1
        return 0
    except Exception as exc:
        print(json.dumps({'error':type(exc).__name__,'message':str(exc)}),file=sys.stderr);return 1
if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--worker':
        from types import SimpleNamespace
        request=read_json(sys.argv[2]);gate(request['head'])
        result=run_one(Path(request['root']),request['case'],request['system'],SimpleNamespace(**request['args']),request['protocol'],request['head'],request['attempt'],Path(request['binary']),request['binary_digest'])
        raise SystemExit(0 if result['status']=='observed' else 1)
    raise SystemExit(main())
