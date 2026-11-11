"""MLflow worker, run only in the separately locked MLflow environment."""
import json
import sys
from pathlib import Path
import mlflow
from mlflow.tracking import MlflowClient

def main():
    request=json.loads(Path(sys.argv[1]).read_text());mlflow.set_tracking_uri(request['uri']);client=MlflowClient()
    op=request['op']
    if op=='start':
        experiment=client.get_experiment_by_name('wine-evaluation')
        if experiment is None:
            eid=client.create_experiment('wine-evaluation',artifact_location=request['artifact_uri'])
        else:eid=experiment.experiment_id
        run=client.create_run(eid,tags=request['tags']);print(json.dumps({'run_id':run.info.run_id}))
    elif op=='end':
        for key,value in request.get('metrics',{}).items():client.log_metric(request['run_id'],key,float(value))
        for artifact in request.get('artifacts',[]):client.log_artifact(request['run_id'],artifact)
        client.set_terminated(request['run_id'],status=request['status']);print('{}')
    elif op=='get':
        r=client.get_run(request['run_id']);i=r.info;d=r.data
        print(json.dumps({'info':{'run_id':i.run_id,'experiment_id':i.experiment_id,'status':i.status,'artifact_uri':i.artifact_uri,'start_time':i.start_time,'end_time':i.end_time},'data':{'metrics':d.metrics,'params':d.params,'tags':d.tags}},sort_keys=True))
    else:raise ValueError('unknown operation')
if __name__=='__main__':main()
