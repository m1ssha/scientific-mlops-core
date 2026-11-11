"""Create per-case C1/C2 stores without executing training."""
import copy
import json
import os
import shutil
import uuid
from pathlib import Path
from common import digest,read_json,require,write_json_new


def output(executor,args,cwd,env):
    _,path,_=executor.run(args,cwd,env)
    return read_json(path)

def cli(executor,binary,store,*args):
    return output(executor,[binary,*args,'--store',store],store,executor.env)

def prepare_core(executor,core,work,wheels,binary):
    store=work/'source-store';store.mkdir();repo=uuid.uuid4().hex
    imported=output(executor,[executor.python,core/'examples/wine_catalog/import_reference.py','--smlops',binary,'--store',store,'--repository-id',repo,'--wheelhouse',wheels],core,executor.env)
    ids={k:imported[k]['record_id'] for k in ('dataset','snapshot','run')}
    original={k:cli(executor,binary,store,k,'show','--id',v)['record'] for k,v in ids.items()}
    receipts={k:cli(executor,binary,store,'operation','show','--id',imported[k]['operation_id']) for k in ids}
    body=copy.deepcopy(original['snapshot']['body']);require(body['parameters']['config.C']['value']==1,'source C differs')
    def put(p):
        ref=digest(p);return cli(executor,binary,store,'object','put','--file',p,'--sha256',ref['sha256'],'--size',str(ref['size']))['object']
    def cmd(body,kind,action,update=None):
        request={'schema_version':1,'repository_id':repo,'operation_id':uuid.uuid4().hex,'type':kind,'body':body,'ref_update':update}
        p=work/(request['operation_id']+'.json');write_json_new(p,request)
        return cli(executor,binary,store,*action,'--request',p)
    cmd({'name':'latest','target_kind':'snapshot','target_id':ids['snapshot'],'expected_revision':0},'set_reference',['ref','set'])
    config=read_json(core/'examples/wine/config.json');config['C']=2.0
    config_path=work/'c2-config.json';write_json_new(config_path,config);config_ref=put(config_path)
    manifest=read_json(core/'examples/wine/manifest.json');manifest['files']['config.json']=config_ref['digest']
    manifest_path=work/'c2-manifest.json';write_json_new(manifest_path,manifest);manifest_ref=put(manifest_path)
    body['predecessor_id']=ids['snapshot'];body['parameters']['config.C']={'type':'number','value':2.0}
    for m in body['materials']:
        if m['name']=='config.json':m['source']['object']=config_ref
        if m['name']=='manifest.json':m['source']['object']=manifest_ref
    control=cmd(body,'save_snapshot',['snapshot','create'],{'name':'latest','expected_revision':1})
    latest=cli(executor,binary,store,'ref','show','--name','latest')['reference']
    require(latest['target_id']==control['record_id'],'C2 not current')
    plan=cli(executor,binary,store,'recovery','plan','--run',ids['run'])
    require(plan['snapshot_id']==ids['snapshot'] and plan['stored_materials_ready'],'source not ready')
    return {'repository_id':repo,'ids':ids,'import':imported,'original_records':original,'receipts':receipts,'latest':latest,'c2_id':control['record_id'],'source_plan':plan,'store':str(store)}

def prepare_baseline(executor,core,work,wheels,core_data):
    repo=work/'source-repo';remote=work/'dvc-remote';remote.mkdir()
    executor.run(['git','clone','--no-local',core,repo],work,executor.env)
    def git(*args):return executor.run(['git',*args],repo,executor.env)
    def dvc(*args):return executor.run([executor.dvc,*args],repo,executor.env)
    git('config','user.name','Evaluation fixture');git('config','user.email','fixture@example.invalid')
    git('checkout','--detach','5c37e250896fbb67670e7a1897853f78baec144d')
    dvc('init','--quiet');dvc('config','core.analytics','false');dvc('config','core.check_update','false');dvc('config','cache.type','copy');dvc('remote','add','-d','materials',remote)
    dataset='examples/wine/data/wine.data';git('rm','--cached',dataset);dvc('add',dataset)
    refdir=repo/'examples/wine/reference';refdir.mkdir()
    shutil.copyfile(repo/'examples/wine/evidence/main-v1/reference/result/environment.json',refdir/'environment.json')
    # Source results are recovered only through the separately declared MLflow artifacts.
    git('rm','-r','examples/wine/evidence','examples/wine_catalog/evidence')
    wheel_dir=repo/'examples/wine/wheels';shutil.copytree(wheels,wheel_dir,copy_function=shutil.copy2)
    wheel_files=sorted(wheel_dir.glob('*.whl'));require(len(wheel_files)==7,'not seven wheels')
    dvc('add','examples/wine/wheels')
    # DVC removes only this disposable stage output, never saved source artifacts.
    script='../wine-venv/bin/python -I examples/wine/pipeline.py run --phase validation --root examples/wine --out attempt-output'
    args=['stage','add','-n','wine']
    for dep in ('examples/wine/data/wine.data','examples/wine/wheels','examples/wine/pipeline.py','examples/wine/prepare_split.py','examples/wine/config.json','examples/wine/split.json','examples/wine/manifest.json','examples/wine/protocol.json','examples/wine/requirements.lock'):
        args.extend(['-d',dep])
    dvc(*args,'-o','attempt-output',script)
    git('add','-A');git('commit','-m','Save C1 materials')
    _,sha,_=git('rev-parse','HEAD');c1=sha.read_text().strip();git('branch','history-c1',c1)
    # C2 reuses exactly the same control bytes generated for core.
    shutil.copyfile(work/'c2-config.json',repo/'examples/wine/config.json')
    shutil.copyfile(work/'c2-manifest.json',repo/'examples/wine/manifest.json')
    git('add','examples/wine/config.json','examples/wine/manifest.json');git('commit','-m','Set current C2 without execution')
    _,sha,_=git('rev-parse','HEAD');c2=sha.read_text().strip();git('branch','current-c2',c2)
    dvc('push')
    git('checkout','current-c2')
    bare=work/'git-remote';executor.run(['git','clone','--no-local','--bare',repo,bare],work,executor.env)
    return {'git_remote':str(bare),'dvc_remote':str(remote),'c1':c1,'c2':c2,'source_repo':str(repo),'dataset_dvc':str(repo/(dataset+'.dvc')),'wheels_dvc':str(repo/'examples/wine/wheels.dvc')}
