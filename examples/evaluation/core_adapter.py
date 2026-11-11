"""Core scenario: keep native recovery and failure recording visible."""
import importlib.util
import shutil
from pathlib import Path
from common import digest,mutation,read_json,require,safe_copy_tree
from prepare import cli


def check_objects(store,plan):
    checks=[]
    for m in plan['materials']:
        source=m['source'];ref=source.get('object')
        if not ref:
            checks.append({'key':m['key'],'status':'unchecked'});continue
        p=Path(store)/'objects'/'sha256'/ref['digest']
        if not p.is_file():status='missing'
        else:
            actual=digest(p);status='verified' if actual=={'sha256':ref['digest'],'size':ref['size']} else 'corrupt'
        checks.append({'key':m['key'],'required':m['required'],'status':status})
    return checks

def run_case(executor,core,binary,base,work,case,protocol):
    store=work/'recovery-store';safe_copy_tree(Path(base['store']),store)
    shutil.rmtree(base['store']);require(not Path(base['store']).exists(),'source store copy remains')
    # The disposable copy is the only mutable recovery store.
    targets=protocol['failure_targets'];target=targets['wheel'] if case=='S3' else targets['dataset']
    injected=None
    if case!='S0':
        entry=dict(target)
        if case=='S2':entry['mutated_sha256']=targets['corruption']['mutated_sha256']
        injected=mutation(store/'objects'/'sha256'/target['sha256'],'corrupt' if case=='S2' else 'remove',entry)
    ids=base['ids'];plan=base['source_plan'];repeat_work=work/'repeat-work'
    code,out,obs=executor.run([executor.python,core/'examples/wine_catalog/repeat.py','--smlops',binary,'--store',store,'--run',ids['run'],'--python',executor.wine_python,'--work',repeat_work],work,executor.env,accept_failure=True)
    result=read_json(out);checks=check_objects(store,plan)
    old={k:cli(executor,binary,store,k,'show','--id',v)['record'] for k,v in ids.items()}
    receipts={k:cli(executor,binary,store,'operation','show','--id',base['import'][k]['operation_id']) for k in ids}
    latest=cli(executor,binary,store,'ref','show','--name','latest')['reference']
    require(old==base['original_records'] and receipts==base['receipts'] and latest==base['latest'],'source history changed')
    require(all(x['status']=='verified' for x,m in zip(checks,plan['materials']) if m['purpose']=='original_output'),'source output missing or changed')
    if result['registration']=='confirmed':
        record=cli(executor,binary,store,'run','show','--id',result['run_id'])['record']['body']
        require(record['source_run_id']==ids['run'] and record['snapshot_id']==ids['snapshot'],'run lineage mismatch')
    else:record=None
    oracle=None;wine_uuids=None
    if case=='S0':
        require(code==0 and result['execution']=='succeeded' and result['runtime']=='passed' and result['registration']=='confirmed','source replay not confirmed')
        path=core/'examples/wine_catalog/validate_cycle.py';require(digest(path)['sha256']==protocol['oracle']['sha256'],'oracle hash mismatch')
        spec=importlib.util.spec_from_file_location('pinned_oracle',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        for m in plan['materials']:
            if m['required']:
                ref=m['source']['object'];require(digest(repeat_work/'inputs'/m['path'])=={'sha256':ref['digest'],'size':ref['size']},'materialized input mismatch')
        oracle=module.oracle(repeat_work/'inputs/wine',repeat_work/'source-results',repeat_work/'result')
        saved=read_json(repeat_work/'source-results/result.json');new=read_json(repeat_work/'result/result.json')
        require(saved['run_id']!=new['run_id'],'Wine UUID reused');wine_uuids={'source':saved['run_id'],'repeat':new['run_id']}
        require(all((repeat_work/'result'/n).is_file() for n in ('result.json','predictions.csv','metrics.json','model.json','environment.json')),'missing raw output')
    else:
        require(code!=0 and result['execution']=='unchecked' and result['numerical']=='unchecked','negative did not block execution')
        expected_status='corrupt' if case=='S2' else 'missing'
        require(any(x['status']==expected_status and x.get('required') for x in checks),'common checker did not see target')
    return {'native_report':result,'wine_uuids':wine_uuids,'source_snapshot_id':ids['snapshot'],'source_run_id':ids['run'],'current_snapshot_id':base['c2_id'],'mutation_proof':injected,'common_checks':checks,'history_unchanged':True,'registered_record':record,'oracle':oracle,'exit':code,'source_output_dir':str(repeat_work/'source-results') if case=='S0' else None,'repeat_output_dir':str(repeat_work/'result') if case=='S0' else None}
