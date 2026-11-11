"""Preserve uncertainty while separating network and execution evidence."""
import re

PID=re.compile(r'^\s*(?:\[pid\s+)?(\d+)\]?\s+(?:(\d+\.\d+)\s+)?(.*)$')
CALL=re.compile(r'^([a-zA-Z_][a-zA-Z_0-9]*)\(')
SEND={'connect','send','sendto','sendmsg','sendmmsg','write','writev','sendfile','splice'}
INCOMING={'listen','accept','accept4'}


def split_arguments(body):
    """Split syscall arguments without splitting quoted data or FD annotations."""
    text=body.split('(',1)[1];parts=[];start=0;depth=0;quoted=False;escaped=False
    for i,char in enumerate(text):
        if quoted:
            if escaped:escaped=False
            elif char=='\\':escaped=True
            elif char=='"':quoted=False
        elif char=='"':quoted=True
        elif char=='<':depth+=1
        elif char=='>' and depth:depth-=1
        elif char==',' and depth==0:parts.append(text[start:i]);start=i+1
    if quoted or depth:return None
    parts.append(text[start:]);return parts

def known_eventfd(label):
    # strace 6.19 decodes only anon_inode:[eventfd] via /proc/PID/fdinfo.
    # https://github.com/strace/strace/blob/v6.19/src/util.c
    return re.fullmatch(r'\{eventfd-count=(?:0|0x[1-9a-f][0-9a-f]*), eventfd-id=[0-9]+, eventfd-semaphore=[01]\}',label) is not None

def classify(text, completed=True):
    result={'status':'pass','inet_activity':[],'probes':[],'unix':[],'unknown':[],'unknown_events':[],
            'execs':[],'clones':[],'syscalls':[],'exits':{},'exit_groups':[],'activity':{}}
    def unknown(pid,kind,detail,seq=None):
        result['unknown'].append(detail);result['unknown_events'].append({'pid':int(pid) if pid else None,'kind':kind,'detail':detail,'sequence':seq})
    if not completed:unknown(None,'incomplete','tracer did not finish')
    pending={};normalized=[];observed_pids=set()
    for seq,line in enumerate(text.splitlines()):
        m=PID.match(line)
        if not m:
            if line.strip():unknown(None,'unparsed','unparsed trace line',seq)
            continue
        pid,ts,body=m.groups();observed_pids.add(pid)
        activity=result['activity'].setdefault(pid,{'first':seq,'last':seq});activity['last']=seq
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
    unknown_pids={x['pid'] for x in result['unknown_events'] if x['kind']=='unknown_syscall'}
    for pid,ts,body,seq in sorted(normalized,key=lambda item:item[3]):
        if body.startswith('+++'):
            if pid in result['exits']:unknown(pid,'pid_reuse','duplicate PID terminal',seq)
            result['exits'][pid]={'time':ts,'detail':body,'sequence':seq};continue
        if pid in result['exits'] and not body.startswith('---'):
            unknown(pid,'pid_reuse','syscall after PID terminal',seq)
        if body.startswith('---'):continue
        match=CALL.match(body)
        if not match:unknown(pid,'unparsed','unparsed syscall',seq);continue
        call=match.group(1)
        if int(pid) in unknown_pids:result['syscalls'].append({'pid':int(pid),'call':call,'sequence':seq})
        if call in ('clone','clone3','fork','vfork'):
            child=re.search(r'=\s*(\d+)\s*$',body)
            if child and int(child.group(1))>0:result['clones'].append({'parent':int(pid),'child':int(child.group(1)),'thread':'CLONE_THREAD' in body,'sequence':seq})
        if call in ('execve','execveat'):
            if '...' in body:unknown(pid,'truncated_exec','truncated exec arguments',seq)
            else:
                path=re.match(r'^execve\("([^"]+)"',body)
                result['execs'].append({'pid':int(pid),'time':ts,'call':body,'path':path.group(1) if path else None,'sequence':seq})
        # Quoted payloads are data, not sockaddr or descriptor annotations.
        structural=re.sub(r'"(?:\\.|[^"\\])*"', '""', body)
        # Address families come only from socket's domain, sockaddr fields or
        # descriptor annotations; file names and quoted payloads are not metadata.
        without_fds=re.sub(r'\d+<[^>]*>', 'FD', structural)
        inet=(call=='socket' and re.match(r'^socket\(\s*AF_INET6?\b',structural)) or re.search(r'\bsa_family=AF_INET6?\b',without_fds) or re.search(r'(?:\(|,)\s*\d+<(?:TCP|UDP)(?:v6)?[:\[]',structural)
        unix=(call=='socket' and re.match(r'^socket\(\s*AF_UNIX\b',structural)) or re.search(r'\bsa_family=AF_UNIX\b',without_fds) or re.search(r'(?:\(|,)\s*\d+<UNIX',structural)
        family='inet' if inet else 'unix' if unix else None
        if call in ('socket','bind') and family=='inet':result['probes'].append(body)
        if call in SEND|INCOMING:
            if family=='inet':result['inet_activity'].append(body)
            elif family=='unix':result['unix'].append(body)
            if call in ('write','writev','sendfile','splice'):
                arguments=split_arguments(body);indexes={'write':(0,),'writev':(0,),'sendfile':(0,1),'splice':(0,2)}[call]
                for i in indexes:
                    descriptor=re.fullmatch(r'\s*\d+<(.+)>\s*',arguments[i]) if arguments is not None and i<len(arguments) else None
                    label=descriptor.group(1) if descriptor else ''
                    # -yy may print socket:[inode] without its family. Such a
                    # label is not evidence of a regular file or a Unix socket.
                    known_anon=('anon_inode:[eventfd]','anon_inode:[eventpoll]','anon_inode:[timerfd]','anon_inode:[signalfd]','anon_inode:inotify')
                    if not (label.startswith(('/', 'pipe:', 'UNIX', 'TCP:', 'TCPv6:', 'UDP:', 'UDPv6:')) or label in known_anon or known_eventfd(label)):
                        unknown(pid,'descriptor',body,seq)
            elif family is None:
                unknown(pid,'descriptor',body,seq)
    successful_execs=[x for x in result['execs'] if x['call'].rstrip().endswith('= 0')]
    if not successful_execs:unknown(None,'incomplete_trace','missing initial successful exec')
    else:
        initial=min(successful_execs,key=lambda x:x['sequence']);root=str(initial['pid'])
        calls=[(seq,pid,body) for pid,ts,body,seq in normalized if CALL.match(body)]
        if not calls or min(calls)[0]!=initial['sequence']:unknown(None,'incomplete_trace','trace does not begin with root exec')
        if root not in result['exits']:unknown(int(root),'incomplete_trace','missing root terminal')
    for pid in observed_pids | {str(x['child']) for x in result['clones']}:
        if pid not in result['exits']:unknown(int(pid),'incomplete_trace','missing traced PID terminal')
    if result['inet_activity']:result['status']='traffic'
    elif result['unknown']:result['status']='unchecked'
    return result


def attribute_unknowns(observation,binary,*,rule='v2'):
    """Attribute only a complete, temporally consistent pinned CLI leaf process.

    This is an operational assumption about byte-checked non-spawning source,
    not a claim that network or every hidden process has been observed.
    """
    if rule not in ('v2','v3'):raise ValueError('unsupported attribution rule')
    if observation['status']=='traffic':return None
    if observation['status']=='pass':return []
    roots=[x for x in observation['execs'] if x['path']==str(binary) and x['call'].rstrip().endswith('= 0')]
    if not roots:return None
    children=observation['clones'];exits=observation['exits'];activity=observation['activity'];attributed=[]
    for unknown in observation['unknown_events']:
        pid=unknown['pid'];seq=unknown['sequence']
        if unknown['kind']!='unknown_syscall' or pid is None or seq is None:return None
        if sum(x['kind']=='unknown_syscall' and x['pid']==pid for x in observation['unknown_events'])!=1:return None
        matches=[]
        for root in roots:
            root_pid=root['pid'];birth={root_pid:root['sequence']};valid=True;changed=True
            while changed and valid:
                changed=False
                for edge in children:
                    parent,child=edge['parent'],edge['child']
                    if parent not in birth:continue
                    # Any observed process child makes this group non-leaf.
                    if not edge['thread'] or child==root_pid:valid=False;break
                    if child in birth:
                        if birth[child]!=edge['sequence']:valid=False;break
                        continue
                    parent_exit=exits.get(str(parent));child_exit=exits.get(str(child))
                    if (not parent_exit or not child_exit or edge['sequence']<=birth[parent] or
                        edge['sequence']>=parent_exit['sequence'] or edge['sequence']>=child_exit['sequence']):
                        valid=False;break
                    birth[child]=edge['sequence'];changed=True
            if not valid or pid==root_pid or pid not in birth:continue
            # The existing v2 harness never opts in to broader v3 attribution.
            if rule=='v2' and any(edge['parent']==pid for edge in children):continue
            if any(not exits.get(str(t),{}).get('detail','').startswith('+++ exited with ') for t in birth):continue
            # Every member's observed activity must fit its lifetime. The root
            # may have pre-exec activity inherited from its parent; children
            # cannot act before the clone syscall that created them.
            if any(str(t) not in activity or activity[str(t)]['last']!=exits[str(t)]['sequence'] or
                   (t!=root_pid and activity[str(t)]['first']<=birth[t]) for t in birth):continue
            group_execs=[x for x in observation['execs'] if x['pid'] in birth]
            if len(group_execs)!=1 or group_execs[0]!=root:continue
            if not (birth[pid]<seq<exits[str(pid)]['sequence']):continue
            if any(x['pid']==pid and seq<x['sequence']<exits[str(pid)]['sequence'] for x in observation['syscalls']):continue
            # A group exit must begin after its caller was created and before
            # the unresolved thread event and the caller's normal terminal.
            if not any(x['pid'] in birth and birth[x['pid']]<x['sequence']<seq and
                       x['sequence']<exits[str(x['pid'])]['sequence'] for x in observation['exit_groups']):continue
            matches.append({'pid':pid,'root_pid':root_pid,'kind':unknown['kind'],'sequence':seq})
        # A single unknown may not be attributed to zero or multiple roots.
        if len(matches)!=1:return None
        attributed.append(matches[0])
    return attributed
