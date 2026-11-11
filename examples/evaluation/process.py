"""Record each child command and observe its whole process tree."""
import json
import os
import signal
import subprocess
import time
from pathlib import Path
from common import append_event
from trace import classify

def identity(pid):
    try:
        text=Path(f"/proc/{pid}/stat").read_text();fields=text[text.rfind(")")+2:].split()
        return int(fields[1]),int(fields[19])
    except (OSError,ValueError,IndexError):return None

def descendants(root):
    """Return only current descendants and their start times, protecting against PID reuse."""
    entries={}
    for p in Path("/proc").iterdir():
        if p.name.isdigit():
            value=identity(int(p.name))
            if value:entries[int(p.name)]=value
    found={root};changed=True
    while changed:
        changed=False
        for pid,(parent,_) in entries.items():
            if parent in found and pid not in found:found.add(pid);changed=True
    return {pid:entries[pid][1] for pid in found if pid in entries}

def terminate_tree(root,grace=5):
    known=descendants(root)
    for sig in (signal.SIGTERM,signal.SIGKILL):
        for pid,start in sorted(known.items(),reverse=True):
            current=identity(pid)
            if current and current[1]==start:
                try:os.kill(pid,sig)
                except ProcessLookupError:pass
        if sig==signal.SIGTERM:
            deadline=time.monotonic()+grace
            while time.monotonic()<deadline:
                known.update(descendants(root))
                if all(not identity(pid) or identity(pid)[1]!=start for pid,start in known.items()):break
                time.sleep(0.05)

class StageFailure(Exception):
    pass

class Executor:
    def __init__(self,root,deadline,trace_children=True):
        self.root=Path(root);self.deadline=deadline;self.count=0;self.trace_children=trace_children
    def run(self,argv,cwd,env,*,trace=None,accept_failure=False,allow_unchecked=False,stage_limit=300):
        if trace is None:trace=self.trace_children
        self.count+=1;name=f'{self.count:03d}';base=self.root/name
        budget=min(stage_limit,self.deadline-time.monotonic())
        if budget<=0:raise StageFailure('case deadline expired')
        command=[str(x) for x in argv]
        actual=(['strace','-f','-ttt','-yy','-s','4096','-e','trace=network,process,write,writev,sendfile,splice','-o',str(base)+'.trace','--'] if trace else [])+command
        rec={'argv':command,'cwd':str(cwd),'env':dict(env),'started_ns':time.time_ns(),'timeout_seconds':budget}
        append_event(self.root/'commands.jsonl',{'event':'started','stage':name,**rec})
        with open(str(base)+'.stdout','xb') as out,open(str(base)+'.stderr','xb') as err:
            try:
                child=subprocess.Popen(actual,cwd=cwd,env=env,stdin=subprocess.DEVNULL,stdout=out,stderr=err,close_fds=True,start_new_session=True)
                try: code=child.wait(timeout=budget);timed_out=False
                except subprocess.TimeoutExpired:
                    timed_out=True;terminate_tree(child.pid,5);child.wait()
                    code=child.returncode
            except OSError as exc:
                append_event(self.root/'commands.jsonl',{'event':'spawn_failed','stage':name,'error':type(exc).__name__});raise StageFailure('spawn failed') from exc
        observation=classify(Path(str(base)+'.trace').read_text(errors='replace'),not timed_out) if trace else None
        append_event(self.root/'commands.jsonl',{'event':'finished','stage':name,'pid':child.pid,'exit':code,'timed_out':timed_out,'ended_ns':time.time_ns(),'network':observation})
        if timed_out:raise StageFailure('stage timed out')
        if observation and observation['status']!='pass' and not (allow_unchecked and observation['status']=='unchecked' and not observation['inet_activity']):raise StageFailure('network trace '+observation['status'])
        if code and not accept_failure:raise StageFailure(f'command failed: {name}, exit {code}')
        return code,Path(str(base)+'.stdout'),observation
