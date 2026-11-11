"""Preserve uncertainty while separating network and execution evidence."""
import re

PID=re.compile(r'^\s*(?:\[pid\s+)?(\d+)\]?\s+(?:(\d+\.\d+)\s+)?(.*)$')
CALL=re.compile(r'^([a-zA-Z_][a-zA-Z_0-9]*)\(')
SEND={'connect','send','sendto','sendmsg','sendmmsg','write','writev','sendfile','splice'}
INCOMING={'listen','accept','accept4'}


def classify(text, completed=True):
    result={'status':'pass','inet_activity':[],'probes':[],'unix':[],'unknown':[],'unknown_events':[],
            'execs':[],'clones':[],'exits':{},'exit_groups':[]}
    def unknown(pid,kind,detail,seq=None):
        result['unknown'].append(detail);result['unknown_events'].append({'pid':int(pid) if pid else None,'kind':kind,'detail':detail,'sequence':seq})
    if not completed:unknown(None,'incomplete','tracer did not finish')
    pending={};normalized=[]
    for seq,line in enumerate(text.splitlines()):
        m=PID.match(line)
        if not m:
            if line.strip():unknown(None,'unparsed','unparsed trace line',seq)
            continue
        pid,ts,body=m.groups()
        if body.startswith('exit_group('):result['exit_groups'].append({'pid':int(pid),'sequence':seq,'time':ts})
        if body.endswith('<unfinished ...>'):
            call=CALL.match(body)
            if body.startswith('???('):unknown(pid,'unknown_syscall',body,seq)
            elif not call or pid in pending:unknown(pid,'unfinished','ambiguous unfinished syscall',seq)
            else:pending[pid]=(call.group(1),body[:-len('<unfinished ...>')],ts,seq)
            continue
        resumed=re.match(r'^<\.\.\. (\w+) resumed>(.*)$',body)
        if resumed:
            prior=pending.pop(pid,None)
            if not prior or prior[0]!=resumed.group(1):unknown(pid,'orphan','orphan or mismatched resumed syscall',seq)
            else:normalized.append((pid,prior[2],prior[1]+resumed.group(2),prior[3]))
            continue
        normalized.append((pid,ts,body,seq))
    for pid in pending:unknown(pid,'unfinished','unfinished syscall at EOF')
    for pid,ts,body,seq in normalized:
        if body.startswith('+++'):
            result['exits'][pid]={'time':ts,'detail':body,'sequence':seq};continue
        if body.startswith('---'):continue
        match=CALL.match(body)
        if not match:unknown(pid,'unparsed','unparsed syscall',seq);continue
        call=match.group(1)
        if call in ('clone','clone3','fork','vfork'):
            child=re.search(r'=\s*(\d+)\s*$',body)
            if child:result['clones'].append({'parent':int(pid),'child':int(child.group(1)),'thread':'CLONE_THREAD' in body,'sequence':seq})
        if call in ('execve','execveat'):
            if '...' in body:unknown(pid,'truncated_exec','truncated exec arguments',seq)
            else:
                path=re.match(r'^execve\("([^"]+)"',body)
                result['execs'].append({'pid':int(pid),'time':ts,'call':body,'path':path.group(1) if path else None,'sequence':seq})
        family='inet' if ('AF_INET' in body or re.search(r'<(?:TCP|UDP)v?\d*[:\[]',body)) else 'unix' if 'AF_UNIX' in body or '<UNIX' in body else None
        if call in ('socket','bind') and family=='inet':result['probes'].append(body)
        if call in SEND|INCOMING:
            if family=='inet':result['inet_activity'].append(body)
            elif family=='unix':result['unix'].append(body)
            elif call in ('connect','send','sendto','sendmsg','sendmmsg','listen','accept','accept4'):unknown(pid,'descriptor',body,seq)
            elif call in ('write','writev','sendfile','splice'):
                arguments=body.split('(',1)[1].split(',');indexes={'write':(0,),'writev':(0,),'sendfile':(0,1),'splice':(0,2)}[call]
                if any(i>=len(arguments) or not re.fullmatch(r'\s*\d+<[^>]+>\s*',arguments[i]) for i in indexes):unknown(pid,'descriptor',body,seq)
    if result['inet_activity']:result['status']='traffic'
    elif result['unknown']:result['status']='unchecked'
    return result


def attribute_unknowns(observation,binary):
    """Conservative attribution: pinned CLI leaf thread exiting after exit_group only."""
    if observation['status']=='traffic':return None
    if observation['status']=='pass':return []
    roots={x['pid'] for x in observation['execs'] if x['path']==str(binary)}
    if not roots:return None
    children=observation['clones'];exits=observation['exits'];attributed=[]
    for unknown in observation['unknown_events']:
        pid=unknown['pid']
        if unknown['kind']!='unknown_syscall' or pid is None or unknown['sequence'] is None:return None
        matched=False
        for root in roots:
            threads={root};changed=True
            while changed:
                changed=False
                for edge in children:
                    if edge['thread'] and edge['parent'] in threads and edge['child'] not in threads:
                        threads.add(edge['child']);changed=True
            if pid==root or pid not in threads:continue
            if any(edge['parent']==pid for edge in children):continue
            if not all(str(t) in exits and exits[str(t)]['detail'].startswith('+++ exited with ') for t in threads):continue
            group_execs=[x for x in observation['execs'] if x['pid'] in threads]
            if len(group_execs)!=1 or group_execs[0]['pid']!=root or group_execs[0]['path']!=str(binary):continue
            if not any(x['pid'] in threads and x['sequence']<unknown['sequence'] for x in observation['exit_groups']):continue
            if any(x['pid']==pid for x in observation['execs']):continue
            attributed.append({'pid':pid,'root_pid':root,'kind':unknown['kind'],'sequence':unknown['sequence']});matched=True;break
        if not matched:return None
    return attributed
