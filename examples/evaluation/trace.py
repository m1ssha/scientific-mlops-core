"""Conservative strace classification: uncertain evidence never becomes a pass."""
import re

# -yy marks open sockets as TCP/UDP/UNIX descriptors, even after fork/dup.
SOCKET=re.compile(r'\b\d+<(TCPv6?|UDPv6?|UNIX[^>]*)[^>]*>')
PID=re.compile(r'^\s*(?:\[pid\s+)?(\d+)\]?\s+(.*)$')
CALL=re.compile(r'^([a-zA-Z_][a-zA-Z_0-9]*)\(')
SEND={'connect','send','sendto','sendmsg','sendmmsg','write','writev','sendfile','splice'}
INCOMING={'listen','accept','accept4'}

def classify(text, completed=True):
    result={'status':'pass','inet_activity':[],'probes':[],'unix':[],'unknown':[],'execs':[]}
    if not completed:result['unknown'].append('tracer did not finish')
    pending={}
    normalized=[]
    for line in text.splitlines():
        m=PID.match(line)
        if not m:
            normalized.append(line);continue
        pid,body=m.groups()
        if body.endswith('<unfinished ...>'):
            call=CALL.match(body)
            if not call or pid in pending:result['unknown'].append('ambiguous unfinished syscall')
            else:pending[pid]=(call.group(1),body[:-len('<unfinished ...>')])
            continue
        resumed=re.match(r'^<\.\.\. (\w+) resumed>(.*)$',body)
        if resumed:
            prior=pending.pop(pid,None)
            if not prior or prior[0]!=resumed.group(1):result['unknown'].append('orphan or mismatched resumed syscall')
            else:normalized.append(pid+' '+prior[1]+resumed.group(2))
            continue
        normalized.append(line)
    if pending:result['unknown'].append('unfinished syscall at EOF')
    for line in normalized:
        m=PID.match(line)
        if not m:
            if line.strip(): result['unknown'].append('unparsed trace line')
            continue
        pid,body=m.groups()
        if body.startswith(('+++','---')):continue
        match=CALL.match(body)
        if not match:result['unknown'].append('unparsed syscall');continue
        call=match.group(1)
        if call in ('execve','execveat'):
            if '...' in body:result['unknown'].append('truncated exec arguments')
            else:result['execs'].append({'pid':int(pid),'call':body})
        family='inet' if ('AF_INET' in body or re.search(r'<(?:TCP|UDP)v?\d*[:\[]',body)) else 'unix' if 'AF_UNIX' in body or '<UNIX' in body else None
        if call in ('socket','bind') and family=='inet':result['probes'].append(body)
        if call in SEND|INCOMING:
            if family=='inet':result['inet_activity'].append(body)
            elif family=='unix':result['unix'].append(body)
            elif call in ('connect','send','sendto','sendmsg','sendmmsg','listen','accept','accept4'):
                # An unlabelled/unknown descriptor is not safe to classify.
                result['unknown'].append(body)
            elif call in ('write','writev','sendfile','splice'):
                # Inspect descriptor arguments, never payload text that may itself contain '<'.
                arguments=body.split('(',1)[1].split(',')
                indexes={'write':(0,),'writev':(0,),'sendfile':(0,1),'splice':(0,2)}[call]
                if any(i>=len(arguments) or not re.fullmatch(r'\s*\d+<[^>]+>\s*',arguments[i]) for i in indexes):result['unknown'].append(body)
    if result['unknown']:result['status']='unchecked'
    elif result['inet_activity']:result['status']='traffic'
    return result
