"""Invoke MLflow in a separate control environment; no tracking dependencies in Wine."""
import json
from pathlib import Path
from common import read_json,write_json_new

class Tracker:
    def __init__(self,executor,root,python,worker):
        self.executor=executor;self.root=Path(root);self.python=python;self.worker=worker;self.i=0
        self.uri='sqlite:///'+str(self.root/'tracking.sqlite');self.artifact_uri=(self.root/'artifacts').as_uri()
    def call(self,op,**args):
        self.i+=1;request=self.root/f'tracker-{self.i:03}.json'
        write_json_new(request,{'op':op,'uri':self.uri,'artifact_uri':self.artifact_uri,**args})
        _,out,_=self.executor.run([self.python,self.worker,request],self.root,self.executor.env)
        return read_json(out)
    def start(self,tags):return self.call('start',tags=tags)['run_id']
    def end(self,id,status,metrics=None,artifacts=None):
        return self.call('end',run_id=id,status=status,metrics=metrics or {},artifacts=[str(p) for p in artifacts or []])
    def get(self,id):return self.call('get',run_id=id)
