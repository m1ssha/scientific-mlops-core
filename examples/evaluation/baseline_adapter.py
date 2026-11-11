"""DVC native materialization/reproduction with an isolated MLflow worker."""
import importlib.util
import shutil
from pathlib import Path
from urllib.parse import unquote,urlparse
from common import digest,file_inventory,mutation,read_json,require
from tracking import Tracker


def raw_target(remote,expected):
    matches=[]
    for p in Path(remote).rglob('*'):
        if p.is_file() and not p.is_symlink() and digest(p)=={'sha256':expected['sha256'],'size':expected['size']}:matches.append(p)
    require(len(matches)==1,'target not unique in DVC remote')
    return matches[0]

def checks_for_repo(repo,plan):
    checks=[]
    for m in plan['materials']:
        if m['purpose']=='original_output':continue
        ref=m['source'].get('object');require(ref is not None,'unexpected external material')
        path=m['path'];require(path.startswith('wine/'),'unexpected material path')
        p=repo/'examples/wine'/path[5:]
        if not p.is_file():status='missing'
        else:status='verified' if digest(p)=={'sha256':ref['digest'],'size':ref['size']} else 'corrupt'
        checks.append({'key':m['key'],'status':status,'required':m['required']})
    return checks

def run_case(executor,core,base,info,work,case,protocol,tracker):
    gitremote=Path(info['git_remote']);remote=Path(info['dvc_remote']);source=Path(info['source_repo'])
    shutil.rmtree(source);require(not source.exists(),'source workspace remains')
    repo=work/'recovery';executor.run(['git','clone','--no-local',gitremote,repo],work,executor.env)
    def refs():
        values={}
        for ref in ('history-c1','current-c2'):
            _,out,_=executor.run(['git','rev-parse','refs/remotes/origin/'+ref],repo,executor.env);values[ref]=out.read_text().strip()
        return values
    original_refs=refs();require(original_refs=={'history-c1':info['c1'],'current-c2':info['c2']},'historical or current ref mismatch')
    executor.run(['git','checkout','history-c1'],repo,executor.env)
    _,head,_=executor.run(['git','rev-parse','HEAD'],repo,executor.env);require(head.read_text().strip()==info['c1'],'not historical C1')
    executor.run(['git','config','user.name','Evaluation fixture'],repo,executor.env);executor.run(['git','config','user.email','fixture@example.invalid'],repo,executor.env)
    checks_target=protocol['failure_targets'];target=checks_target['wheel'] if case=='S3' else checks_target['dataset']
    injected=None
    if case!='S0':
        entry=dict(target)
        if case=='S2':entry['mutated_sha256']=checks_target['corruption']['mutated_sha256']
        injected=mutation(raw_target(remote,target),'corrupt' if case=='S2' else 'remove',entry)
    # Start MLflow before pull so even a missing material has its own failed attempt.
    source_id=info['mlflow_source_id'];before=tracker.get(source_id);artifacts=Path(info['source_artifacts']);hashes=file_inventory(artifacts)
    run_id=tracker.start({'source_run_id':source_id,'source_git_revision':info['c1'],'case':case,'attempt_uuid':executor.attempt_id})
    try:
        code,out,_=executor.run([executor.dvc,'pull','examples/wine/data/wine.data.dvc','examples/wine/wheels.dvc'],repo,executor.env,accept_failure=True)
        checks=checks_for_repo(repo,base['source_plan']);issues=[x for x in checks if x['required'] and x['status']!='verified']
        if case!='S0':
            require(issues,'negative unexpectedly recovered all required bytes')
            target_path='wine/wheels/'+target['name'] if case=='S3' else target['path']
            target_key=next(m['key'] for m in base['source_plan']['materials'] if m['path']==target_path)
            observed=next(x['status'] for x in checks if x['key']==target_key)
            allowed=('corrupt','missing') if case=='S2' else ('missing',)
            require(observed in allowed,'unexpected target status')
            tracker.end(run_id,'FAILED');require(tracker.get(run_id)['info']['status']=='FAILED','failure registration not confirmed');return {'native_pull_exit':code,'mutation_proof':injected,'target_status':observed,'corruption_detection':('common_corrupt' if observed=='corrupt' else 'unchecked_native_cause') if case=='S2' else None,'diagnosis':'native pull failed; exact cause unclassified' if code else 'common checker classified restored material','common_checks':checks,'execution':'unchecked','numerical':'unchecked','registration':'confirmed','mlflow_run_id':run_id,'source_run_id':source_id,'c1_revision':info['c1'],'c2_revision':info['c2'],'dvc_experiment':None,'history_unchanged':tracker.get(source_id)==before and file_inventory(artifacts)==hashes and refs()==original_refs}
        require(code==0 and not issues,'required materials not restored')
        root=repo/'examples/wine';envfile=root/'reference/environment.json';venv=work/'wine-venv'
        executor.run([executor.wine_python,'-I','-m','venv',venv],work,executor.env)
        py=venv/'bin/python'
        executor.run([py,'-I','-m','pip','install','--no-index','--no-cache-dir','--require-hashes','--no-deps','--only-binary=:all:','--find-links',root/'wheels','-r',root/'requirements.lock'],repo,executor.env)
        helper=core/'examples/wine_catalog/repeat_helper.py'
        executor.run([py,'-I',helper,'preflight',root,envfile],repo,executor.env)
        # Verify byte hashes again after installing and immediately before execution.
        require(all(x['status']=='verified' for x in checks_for_repo(repo,base['source_plan'])),'materials changed before execution')
        _,out,obs=executor.run([executor.dvc,'repro','--force','--no-run-cache'],repo,executor.env)
        resultdir=repo/'attempt-output';names=('result.json','predictions.csv','metrics.json','model.json','environment.json')
        require(all((resultdir/n).is_file() for n in names),'missing fresh outputs')
        executor.run([py,'-I',helper,'validate',root,envfile,resultdir,artifacts],repo,executor.env)
        oraclefile=core/'examples/wine_catalog/validate_cycle.py';require(digest(oraclefile)['sha256']==protocol['oracle']['sha256'],'oracle hash mismatch')
        spec=importlib.util.spec_from_file_location('pinned_oracle',oraclefile);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        oracle=module.oracle(root,artifacts,resultdir)
        saved=read_json(artifacts/'result.json');new=read_json(resultdir/'result.json');require(saved['run_id']!=new['run_id'],'Wine UUID reused')
        executor.run(['git','add','dvc.lock'],repo,executor.env)
        executor.run([executor.dvc,'exp','save','--name',executor.attempt_id],repo,executor.env)
        _,exprefs,_=executor.run(['git','for-each-ref','--format=%(refname) %(objectname)','refs/exps'],repo,executor.env)
        match=[line.split() for line in exprefs.read_text().splitlines() if line.split()[0].endswith('/'+executor.attempt_id)]
        require(len(match)==1,'experiment ref missing');experiment_ref={'ref':match[0][0],'commit':match[0][1]}
        metrics=read_json(resultdir/'metrics.json');flat={a+'.'+b:metrics[a][b]['value'] for a in ('model','dummy') for b in ('accuracy','balanced_accuracy')}
        tracker.end(run_id,'FINISHED',flat,[resultdir/n for n in names]);require(tracker.get(run_id)['info']['status']=='FINISHED','attempt registration not confirmed')
        return {'native_pull_exit':code,'common_checks':checks,'execution':'succeeded','numerical':'pass','registration':'confirmed','mlflow_run_id':run_id,'source_run_id':source_id,'c1_revision':info['c1'],'c2_revision':info['c2'],'dvc_experiment':experiment_ref,'wine_uuids':{'source':saved['run_id'],'repeat':new['run_id']},'oracle':oracle,'source_output_dir':str(artifacts),'repeat_output_dir':str(resultdir),'history_unchanged':tracker.get(source_id)==before and file_inventory(artifacts)==hashes and refs()==original_refs}
    except BaseException:
        # A separate run exists even if materialization or execution fails.
        try:tracker.end(run_id,'FAILED')
        except Exception:pass
        raise
