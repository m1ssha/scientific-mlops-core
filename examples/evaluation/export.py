"""Export only structured summaries and byte-identical outputs from a completed series."""
import argparse
import json
import shutil
import re
from pathlib import Path
from common import digest,new_root,read_json,require,write_json_new

NAMES=('result.json','predictions.csv','metrics.json','model.json','environment.json')

def safe_label(value):
    require(isinstance(value,str) and len(value)<=512 and not any(x in value for x in ('/','\\','\n','\r')),'unsafe evidence label')
    return value

def project_checks(items,native=False):
    out=[]
    for item in items:
        key=safe_label(item['key']);status=safe_label(item['check'] if native else item['status'])
        require(type(item['required']) is bool,'invalid required flag')
        out.append({'key':key,('check' if native else 'status'):status,'required':item['required']})
    return out

def project_blockers(items):
    return [{'material_key':safe_label(item['material_key']),'code':safe_label(item['code'])} for item in items]

def safe_source_audit(audit):
    for name in audit['files']:
        path=Path(name);require(not path.is_absolute() and '..' not in path.parts,'unsafe source audit path')
    return audit

def export(work,destination):
    work=Path(work).resolve(strict=True);series=read_json(work/'series.json')
    order=series['protocol']['case_order'];events=[json.loads(x) for x in (work/'attempts.jsonl').read_text().splitlines()]
    starts=[e for e in events if e['event']=='started'];ends=[e for e in events if e['event']=='finished']
    require([(e['case'],e['system']) for e in starts]==[tuple(x) for x in order] and len(ends)==len(starts),'series incomplete')
    require(any(e.get('event')=='tool_setup_finished' and e.get('status')=='complete' for e in events),'tool setup incomplete')
    require(all(a['attempt_id']==b['attempt_id'] and a['case']==b['case'] and a['system']==b['system'] and b['status']=='observed' for a,b in zip(starts,ends)),'series blocked or attempt mismatch')
    tool=read_json(work/'tool-setup/manifest.json');require('source_audit' in tool and 'cli' in tool,'tool provenance missing')
    target=new_root(destination,[work]);summaries=[]
    for number,started in enumerate(starts,1):
        case,system=started['case'],started['system']
        result=read_json(work/(case+'-'+system)/'result.json');require(result['status']=='observed' and result['attempt_id']==started['attempt_id'],'case not observed or identity mismatch')
        audit=read_json(work/(case+'-'+system+'-audit.json'));require(audit['functional_status']=='passed' and audit['network_status'] in ('observed_no_forbidden_events','unchecked'),'case functional audit not complete')
        stage=result['stages'];report=stage.get('native_report',{})
        summary={'case':case,'system':system,'attempt_id':result['attempt_id'],'history_unchanged':stage['history_unchanged'],
                 'functional_status':audit['functional_status'],'network_status':audit['network_status'],'attributed_unknowns':audit['attributed_unknowns'],'functional_assumption':audit['functional_assumption'],'native_exit':stage.get('native_pull_exit',stage.get('exit')),'registration':stage.get('registration',report.get('registration')),
                 'materials':stage.get('materials',report.get('materials')),'execution':stage.get('execution',report.get('execution')),
                 'runtime':stage.get('runtime',report.get('runtime')),'numerical':stage.get('numerical',report.get('numerical')),
                 'common_checks':project_checks(stage['common_checks']),'mutation_proof':stage.get('mutation_proof'),
                 'training_process_count':len(audit['training_execs_all']),'training_processes':[{'pid':x['pid'],'started_unix':x.get('time'),'ended_unix':(x.get('exit') or {}).get('time')} for x in audit['training_execs_all']],'oracle':stage.get('oracle')}
        for key in ('wine_uuids','source_snapshot_id','source_run_id','current_snapshot_id','mlflow_run_id','c1_revision','c2_revision','dvc_experiment'):
            if key in stage:summary[key]=stage[key]
        if report.get('run_id'):summary['core_run_id']=report['run_id']
        if report:
            summary['native_material_checks']=project_checks(report.get('material_checks',[]),native=True)
            summary['native_blockers']=project_blockers(report.get('blockers',[]))
            summary['native_diagnostic']=report.get('error') if report.get('error') in (None,'required materials unavailable') else '[redacted diagnostic]'
        if 'target_status' in stage:summary.update(target_status=stage['target_status'],corruption_detection=stage['corruption_detection'],diagnosis=stage['diagnosis'])
        commandlog=work/(case+'-'+system)/'commands/commands.jsonl';rawtrace=work/'case-traces'/f'{number:03}.trace'
        summary['private_log_hashes']={'commands':digest(commandlog),'trace':digest(rawtrace)}
        summaries.append(summary)
        if case=='S0':
            for role,key in [('source','source_output_dir'),('repeat','repeat_output_dir')]:
                origin=Path(stage[key]).resolve(strict=True);require(work in origin.parents,'output outside series')
                directory=target/(case+'-'+system)/role;directory.mkdir(parents=True)
                for name in NAMES:
                    src=origin/name;before=digest(src);shutil.copyfile(src,directory/name);require(digest(directory/name)==before,'output bytes changed')
    write_json_new(target/'results.json',{'schema_version':1,'harness_commit':series['harness_commit'],'tool_provenance':{'core_commit':tool['core_commit'],'cli':tool['cli'],'source_audit':safe_source_audit(tool['source_audit'])},'cases':summaries,'diagnostics':'Schema-projected keys/codes/statuses; diagnostic text omitted or redacted. Paths and full raw command logs remain private; hashes refer to unchanged raw files.'})
    hashes={p.relative_to(target).as_posix():digest(p) for p in sorted(target.rglob('*')) if p.is_file()}
    write_json_new(target/'sha256.json',hashes)

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--work',required=True);parser.add_argument('--destination',required=True);args=parser.parse_args()
    try:export(args.work,args.destination)
    except Exception as exc:print(json.dumps({'error':type(exc).__name__,'message':str(exc)}));return 1
    return 0
if __name__=='__main__':raise SystemExit(main())
