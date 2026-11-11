"""Pure safety and evidence helpers for the preregistered evaluation."""
import hashlib
import json
import os
import shutil
import stat
import time
import uuid
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    def invalid(value):
        raise ValueError('non-finite JSON: '+value)
    return json.loads(Path(path).read_text(encoding='utf-8'), parse_constant=invalid)


def digest(path):
    h=hashlib.sha256(); size=0
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024*1024),b''):
            h.update(chunk);size+=len(chunk)
    return {'sha256':h.hexdigest(),'size':size}


def no_symlinks(path):
    path=Path(os.path.abspath(path))
    for part in (path,*path.parents):
        require(not part.is_symlink(), 'symlink in path')
    return path


def new_root(path, protected=()):
    path=no_symlinks(path)
    require(not path.exists() and path.parent.is_dir(), 'root must be new; parent must exist')
    for p in protected:
        other=no_symlinks(p).resolve(strict=True)
        require(path!=other and path not in other.parents and other not in path.parents,'overlapping root')
    path.mkdir(mode=0o700)
    return path


def safe_copy_tree(source,target):
    source=no_symlinks(source).resolve(strict=True);target=no_symlinks(target)
    require(target!=source and target not in source.parents and source not in target.parents,'overlapping copy')
    require(not target.exists(), 'target exists')
    for p in source.rglob('*'):
        mode=p.lstat().st_mode
        require(stat.S_ISDIR(mode) or stat.S_ISREG(mode),'unsafe source entry')
    shutil.copytree(source,target,copy_function=shutil.copy2,symlinks=False)


def file_inventory(root):
    root=Path(root); out={}
    for p in sorted(root.rglob('*')):
        require(not p.is_symlink(),'symlink in inventory')
        if p.is_file(): out[p.relative_to(root).as_posix()]=digest(p)
    return out


def append_event(index, event):
    payload=(json.dumps(event,sort_keys=True,allow_nan=False)+'\n').encode()
    fd=os.open(index,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
    try:
        with os.fdopen(fd,'ab',closefd=False) as f:
            f.write(payload);f.flush();os.fsync(f.fileno())
    finally: os.close(fd)


def write_json_new(path, value):
    with Path(path).open('x',encoding='utf-8') as f:
        json.dump(value,f,indent=2,sort_keys=True,allow_nan=False);f.write('\n');f.flush();os.fsync(f.fileno())


def mutation(path,kind,target):
    """Change only the caller-selected disposable object; preserve its mode."""
    path=no_symlinks(path);require(path.exists(),'target missing')
    require(kind in ('remove','corrupt'),'invalid mutation')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK);write_fd=None;restore=False
    try:
        original=os.fstat(fd)
        require(stat.S_ISREG(original.st_mode) and original.st_uid==os.geteuid() and original.st_nlink==1,
                'target must be an owned, unshared regular file')
        current=os.stat(path,follow_symlinks=False)
        require((current.st_dev,current.st_ino)==(original.st_dev,original.st_ino),'target changed')
        chunks=[]
        while True:
            chunk=os.read(fd,1024*1024)
            if not chunk:break
            chunks.append(chunk)
        data=b''.join(chunks)
        require(len(data)==target['size'] and hashlib.sha256(data).hexdigest()==target['sha256'],
                'target does not match protocol')
        if kind=='remove':
            current=os.stat(path,follow_symlinks=False)
            require((current.st_dev,current.st_ino)==(original.st_dev,original.st_ino) and current.st_nlink==1,
                    'target changed')
            path.unlink();return {'state':'missing'}
        require(len(data)>0,'invalid mutation')
        modified=bytes([data[0]^1])+data[1:]
        require(hashlib.sha256(modified).hexdigest()==target['mutated_sha256'],'mutation hash mismatch')
        current=os.fstat(fd);at_path=os.stat(path,follow_symlinks=False)
        require((at_path.st_dev,at_path.st_ino)==(original.st_dev,original.st_ino) and
                current.st_uid==os.geteuid() and current.st_nlink==1 and
                stat.S_IMODE(current.st_mode)==stat.S_IMODE(original.st_mode),'target changed')
        original_mode=stat.S_IMODE(original.st_mode);restore=True
        if not original_mode&stat.S_IWUSR:os.fchmod(fd,original_mode|stat.S_IWUSR)
        write_fd=os.open(path,os.O_WRONLY|os.O_NOFOLLOW)
        current=os.fstat(write_fd)
        require((current.st_dev,current.st_ino)==(original.st_dev,original.st_ino) and
                stat.S_ISREG(current.st_mode) and current.st_uid==os.geteuid() and current.st_nlink==1,
                'target changed')
        require(os.pwrite(write_fd,modified[:1],0)==1,'short mutation write')
        os.fsync(write_fd)
        os.lseek(fd,0,os.SEEK_SET);h=hashlib.sha256();size=0
        while True:
            chunk=os.read(fd,1024*1024)
            if not chunk:break
            h.update(chunk);size+=len(chunk)
        actual={'sha256':h.hexdigest(),'size':size}
        require(actual=={'sha256':target['mutated_sha256'],'size':target['size']},'mutation hash mismatch')
        return {'state':'corrupt',**actual}
    finally:
        try:
            if restore:
                os.fchmod(fd,original_mode);os.fsync(fd)
                require(stat.S_IMODE(os.fstat(fd).st_mode)==original_mode,'failed to restore target mode')
        finally:
            if write_fd is not None:os.close(write_fd)
            os.close(fd)


def start_attempt(index,case,system):
    event={'event':'started','attempt_id':uuid.uuid4().hex,'case':case,'system':system,'unix_ns':time.time_ns()}
    append_event(index,event);return event
