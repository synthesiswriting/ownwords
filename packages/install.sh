#!/bin/sh
# Build output binds this installer to an exact archive version and SHA-256.
set -eu
command -v python3 >/dev/null 2>&1 || { echo 'Ownwords installation requires Python 3.9 or later.' >&2; exit 2; }
exec python3 - "$@" <<'OWNWORDS_INSTALLER'
import argparse
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile

VERSION='@VERSION@'
ARCHIVE_SHA256='@ARCHIVE_SHA256@'
ARCHIVE_URL='https://github.com/synthesiswriting/ownwords/releases/download/v%s/ownwords-%s.tgz'%(VERSION,VERSION)


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def regular(path):
    if path.is_symlink() or (path.exists() and not path.is_file()):raise ValueError('Refusing non-regular installation target: '+str(path))


def directory(path):
    for ancestor in (path,*path.parents):
        if ancestor.is_symlink() or (ancestor.exists() and not ancestor.is_dir()):raise ValueError('Refusing directory or symlink conflict: '+str(ancestor))


def atomic(path,data,mode=0o600):
    descriptor,name=tempfile.mkstemp(prefix='.ownwords-write-',dir=path.parent)
    temporary=Path(name)
    try:
        with os.fdopen(descriptor,'wb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        temporary.chmod(mode);os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)


def inventory(root):
    observed={}
    for item in root.rglob('*'):
        if item.is_symlink() or not(item.is_file() or item.is_dir()):raise ValueError('Existing release contains a link or special object.')
        if item.is_file():observed[item.relative_to(root).as_posix()]=(sha(item),item.stat().st_mode&0o777)
    return observed


class Cancelled(BaseException):
    def __init__(self, signum):self.signum=signum


class ChildFailure(Exception):
    def __init__(self, code, arguments):
        self.code=code
        super().__init__('Child command failed with exit %s: %s'%(code,arguments[0]))


def run_child(arguments):
    cancelled=[]
    child=None
    def forward(signum,frame):
        cancelled.append(signum)
        if child is not None:
            try:os.killpg(child.pid,signum)
            except ProcessLookupError:pass
    previous={signum:signal.signal(signum,forward) for signum in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP)}
    try:
        child=subprocess.Popen(arguments,start_new_session=True)
        if cancelled:
            try:os.killpg(child.pid,cancelled[0])
            except ProcessLookupError:pass
        code=child.wait()
        if cancelled:raise Cancelled(cancelled[0])
        if code<0:raise Cancelled(-code)
        if code:raise ChildFailure(code,arguments)
    finally:
        for signum,handler in previous.items():signal.signal(signum,handler)


def main():
    if sys.version_info<(3,9):raise ValueError('Python 3.9 or later is required for installation.')
    parser=argparse.ArgumentParser(description='Install checksum-bound Ownwords; preserve unknown or edited files.')
    parser.add_argument('--prefix',default=os.environ.get('OWNWORDS_INSTALL_DIR',str(Path.home()/'.local/bin')))
    parser.add_argument('--no-dormant-core',action='store_true')
    args=parser.parse_args()
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',VERSION) or not re.fullmatch(r'[a-f0-9]{64}',ARCHIVE_SHA256):raise ValueError('Use the built release installer, not maintainer template source.')
    node=shutil.which('node')
    if not node:raise ValueError('Node.js 18 or later is required.')
    run_child([node,'-e','process.exit(Number(process.versions.node.split(".")[0])>=18?0:2)'])
    if not shutil.which('curl'):raise ValueError('curl is required for HTTPS archive download.')
    prefix=Path(args.prefix).expanduser().absolute();directory(prefix);prefix.mkdir(parents=True,exist_ok=True)
    target=prefix/'ownwords';receipt=prefix/'.ownwords-install.json';journal=prefix/'.ownwords-install.pending.json';lock=prefix/'.ownwords-install.lock'
    release=prefix/('.ownwords-'+VERSION);directory(release)
    body=('#!/bin/sh\nexec '+shlex.quote(node)+' '+shlex.quote(str(release/'bin/ownwords.js'))+' "$@"\n').encode()
    record={'schema_version':1,'target':str(target),'release':str(release),'version':VERSION,'sha256':hashlib.sha256(body).hexdigest(),'mode':0o755,'source':ARCHIVE_URL,'archive_sha256':ARCHIVE_SHA256}
    for path in (target,receipt,journal,lock):regular(path)
    with lock.open('a') as held:
        fcntl.flock(held,fcntl.LOCK_EX)
        for path in (target,receipt,journal):regular(path)
        receipt_before=receipt.read_bytes() if receipt.exists() else None
        journal_before=journal.read_bytes() if journal.exists() else None
        prior=json.loads(receipt_before) if receipt_before is not None else {}
        pending=json.loads(journal.read_text()) if journal.exists() else None
        if not isinstance(prior,dict) or (pending is not None and pending!=record):raise ValueError('Unrecognized interrupted installation; evidence preserved.')
        if target.exists():
            owner=pending if pending is not None and sha(target)==pending['sha256'] else prior
            if owner.get('target')!=str(target) or owner.get('sha256')!=sha(target) or owner.get('mode')!=(target.stat().st_mode&0o777):raise ValueError('Existing Ownwords launcher is unknown or edited; preserved.')
        descriptor,name=tempfile.mkstemp(prefix='.ownwords-download-',dir=prefix);os.close(descriptor);download=Path(name)
        try:
            run_child(['curl','--proto','=https','--proto-redir','=https','-fsSL',ARCHIVE_URL,'-o',str(download)])
            if sha(download)!=ARCHIVE_SHA256:raise ValueError('Release SHA-256 mismatch; no executable installed.')
            files={};seen=set()
            with tarfile.open(fileobj=io.BytesIO(download.read_bytes()),mode='r:gz') as archive:
                members=archive.getmembers()
                if len(members)>10000 or sum(item.size for item in members)>100000000:raise ValueError('Release exceeds archive limits.')
                for member in members:
                    name=PurePosixPath(member.name)
                    if name.is_absolute() or '..' in name.parts or not name.parts or name.parts[0]!='package' or not(member.isfile() or member.isdir()) or member.mode&0o7000:raise ValueError('Release contains an unsafe archive member.')
                    relative=PurePosixPath(*name.parts[1:]).as_posix()
                    if relative in seen or relative=='.':raise ValueError('Release contains a duplicate or empty member.')
                    seen.add(relative)
                    if member.isfile():files[relative]=(archive.extractfile(member).read(),0o755 if member.mode&0o111 else 0o644)
            if not {'bin/ownwords.js','package.json','vendor/synthesis/bin/synthesis'}<=set(files):raise ValueError('Release is missing executable components.')
            if json.loads(files['package.json'][0])['version']!=VERSION:raise ValueError('Release package version differs from the installer.')
            expected={name:(hashlib.sha256(data).hexdigest(),mode) for name,(data,mode) in files.items()}
            directory(release)
            if release.exists():
                if inventory(release)!=expected:raise ValueError('Existing release bytes or permissions changed; preserved.')
            else:
                with tempfile.TemporaryDirectory(prefix='.ownwords-stage-',dir=prefix) as temporary:
                    staged=Path(temporary)/'release';staged.mkdir()
                    for name,(data,mode) in files.items():
                        destination=staged/name;destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(data);destination.chmod(mode)
                    if inventory(staged)!=expected:raise ValueError('Staged release verification failed.')
                    directory(release)
                    if release.exists():raise ValueError('Release appeared during installation; preserved.')
                    staged.rename(release)
            # A valid pending record is completed only after archive and payload verification.
            regular(target)
            if target.exists():
                owner=pending if pending is not None and sha(target)==pending['sha256'] else prior
                if owner.get('sha256')!=sha(target) or owner.get('mode')!=(target.stat().st_mode&0o777):raise ValueError('Launcher changed during installation; preserved.')
            for path,before in ((receipt,receipt_before),(journal,journal_before)):
                regular(path)
                if (path.read_bytes() if path.exists() else None)!=before:raise ValueError('Installation ownership changed during download; evidence preserved.')
            encoded=(json.dumps(record,sort_keys=True)+'\n').encode()
            atomic(journal,encoded);atomic(target,body,0o755);atomic(receipt,encoded);journal.unlink()
        finally:download.unlink(missing_ok=True)
    try:run_child([str(target),'setup']+(['--no-dormant-core'] if args.no_dormant_core else []))
    except ChildFailure:
        print('Ownwords is installed, but explicit optional setup failed; retry after resolving the reported cause.',file=sys.stderr)
        raise
    print('Installed Ownwords %s; optional setup completed without activating agent hooks or services.'%VERSION)
    if str(prefix) not in os.environ.get('PATH','').split(os.pathsep):print('Add %s to PATH to use ownwords.'%prefix)


try:main()
except Cancelled as exc:
    signal.signal(exc.signum,signal.SIG_DFL);os.kill(os.getpid(),exc.signum)
    sys.exit(128+exc.signum)
except ChildFailure as exc:
    print('Ownwords install: '+str(exc),file=sys.stderr);sys.exit(exc.code)
except (OSError,ValueError,KeyError,tarfile.TarError,subprocess.SubprocessError) as exc:
    print('Ownwords install: '+str(exc),file=sys.stderr);sys.exit(2)
OWNWORDS_INSTALLER
