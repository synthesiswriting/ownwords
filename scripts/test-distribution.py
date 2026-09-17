#!/usr/bin/env python3
"""Exercise actual temporary npm, Bun and direct consumers without services."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parent.parent


def run(arguments, *, cwd, env):
    result = subprocess.run(arguments, cwd=cwd, env=env, text=True, capture_output=True, timeout=180)
    if result.returncode:
        raise AssertionError('%r\n%s\n%s' % (arguments, result.stdout, result.stderr))
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core-archive', type=Path, required=True)
    parser.add_argument('--core-sha256', required=True)
    parser.add_argument('--npm-cache', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    environment = {key: value for key, value in os.environ.items() if not key.startswith(('SYNTHESIS_', 'GIT_', 'NODE_OPTIONS'))}
    environment.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1',
                       GIT_AUTHOR_NAME='Fixture', GIT_COMMITTER_NAME='Fixture',
                       GIT_AUTHOR_EMAIL='fixture@example.invalid', GIT_COMMITTER_EMAIL='fixture@example.invalid')
    # Pending source bytes are committed only in this disposable fixture.
    source = args.output / 'source'
    source.mkdir()
    names = run(['git','-C',str(ROOT),'ls-files','--cached','--others','--exclude-standard','-z'],cwd=ROOT,env=environment).split('\0')
    for name in sorted(set(filter(None,names))):
        original=ROOT/name
        if original.is_symlink() or not original.is_file():
            raise AssertionError('fixture source contains an unsupported file')
        target=source/name
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(original,target)
    run(['git','init','-q','-b','main'],cwd=source,env=environment)
    run(['git','add','.'],cwd=source,env=environment)
    run(['git','-c','core.hooksPath=/dev/null','commit','-qm','Fixture source'],cwd=source,env=environment)
    version=json.loads((source/'package.json').read_text())['version']
    run(['git','tag','v'+version],cwd=source,env=environment)
    for suffix in ('one','two'):
        if suffix=='two':
            ignored=source/'lib/__pycache__';ignored.mkdir()
            (ignored/'local-build-note.pyc').write_bytes(b'benign ignored build debris')
            assert run(['git','status','--porcelain','--untracked-files=all'],cwd=source,env=environment).strip()==''
        run([sys.executable,'-B',str(source/'scripts/build-distribution.py'),'--repo-root',str(source),'--output',str(args.output/suffix),'--core-archive',str(args.core_archive),'--core-sha256',args.core_sha256,'--npm-cache',str(args.npm_cache)],cwd=source,env=environment)
    # Builder output includes npm progress; durable distribution.json is authoritative.
    first=json.loads((args.output/'one/distribution.json').read_text())
    second=json.loads((args.output/'two/distribution.json').read_text())
    assert first['sha256']==second['sha256'], 'release archive is not deterministic'
    archive=args.output/'one'/first['archive']
    with tarfile.open(archive) as bundle:
        assert not any('__pycache__' in name for name in bundle.getnames()), 'ignored checkout bytes entered the artifact'
    channels=[]
    for channel in ('npm','bun','direct'):
        consumer=args.output/('consumer-'+channel)
        consumer.mkdir()
        home=consumer/'home';home.mkdir()
        temporary=consumer/'tmp';temporary.mkdir()
        env={**environment,'HOME':str(home),'XDG_CONFIG_HOME':str(home/'.config'),'XDG_STATE_HOME':str(home/'.local/state'),'XDG_CACHE_HOME':str(home/'.cache'),'XDG_DATA_HOME':str(home/'.local/share'),'TMPDIR':str(temporary),'BUN_INSTALL_CACHE_DIR':str(consumer/'bun-cache')}
        if channel=='npm':
            run(['npm','install','--prefix',str(consumer),'--ignore-scripts','--offline','--no-audit','--no-fund','--cache',str(args.npm_cache),str(archive)],cwd=consumer,env=env)
            command=[str(consumer/'node_modules/.bin/ownwords')]
        elif channel=='bun':
            (consumer/'package.json').write_text('{"name":"fixture-consumer","private":true}')
            run(['bun','add','--ignore-scripts','--no-cache',str(archive)],cwd=consumer,env=env)
            command=[str(consumer/'node_modules/.bin/ownwords')]
        else:
            with tarfile.open(archive) as bundle:
                bundle.extractall(consumer,filter='data')
            command=['node',str(consumer/'package/bin/ownwords.js')]
        assert 'ownwords v'+version in run(command+['--version'],cwd=consumer,env=env)
        assert 'ownwords synthesis activate --profile full' in run(command+['synthesis','--help'],cwd=consumer,env=env)
        before=list(home.rglob('*'))
        declined=json.loads(run(command+['setup','--no-dormant-core','--json'],cwd=consumer,env=env))
        assert declined['core_state']=='declined' and declined['optional_core_bytes']==0
        assert list(home.rglob('*'))==before, 'setup opt-out changed the isolated home'
        status=subprocess.run(command+['synthesis','status'],cwd=consumer,env=env,text=True,capture_output=True,timeout=30)
        assert status.returncode == 2, 'an absent core must not be reported ready: '+status.stdout+status.stderr
        assert list(home.rglob('*'))==before, 'bridge status changed an unconfigured home'
        installed_root=consumer/('package' if channel=='direct' else 'node_modules/ownwords')
        assert (installed_root/'docs/distribution.md').is_file(), 'packaged documentation link is missing'
        html=consumer/'article.html';markdown=consumer/'article.md'
        html.write_text('<html><body><article><h1>Fixture article</h1><p>Retained author words.</p></article></body></html>')
        run(command+['convert',str(html),str(markdown)],cwd=consumer,env=env)
        assert 'Retained author words' in markdown.read_text()
        channels.append(channel)
    consumer=args.output/'consumer-curl'; consumer.mkdir()
    home=consumer/'home';home.mkdir()
    transport=consumer/'transport';transport.mkdir()
    curl=transport/'curl'
    curl.write_text('#!/bin/sh\nwhile [ "$1" != "-o" ]; do shift; done\ncp "$OWNWORDS_CURL_ARCHIVE" "$2"\n')
    curl.chmod(0o755)
    env={**environment,'HOME':str(home),'XDG_CONFIG_HOME':str(home/'.config'),'XDG_STATE_HOME':str(home/'.local/state'),
         'XDG_CACHE_HOME':str(home/'.cache'),'PATH':str(transport)+os.pathsep+environment['PATH'],'OWNWORDS_CURL_ARCHIVE':str(archive)}
    prefix=consumer/'prefix'
    install=['sh',str(args.output/'one/install.sh'),'--prefix',str(prefix),'--no-dormant-core']
    for attempt in range(2):
        run(install,cwd=consumer,env=env)
    assert 'ownwords v'+version in run([str(prefix/'ownwords'),'--version'],cwd=consumer,env=env)
    assert 'ownwords synthesis activate' in run([str(prefix/'ownwords'),'synthesis','--help'],cwd=consumer,env=env)
    assert list(home.iterdir())==[], 'curl opt-out changed isolated HOME'
    receipt=json.loads((prefix/'.ownwords-install.json').read_text())
    assert receipt['version']==version and receipt['archive_sha256']==first['sha256']
    assert not (prefix/'.ownwords-install.pending.json').exists()
    channels.append('curl')
    report={'status':'PASS' ,'fixture_only':True,'channels':channels,'deterministic_sha256':first['sha256'],'core':first['core'],'checks':['real installed version','explicit inert opt-out','unchanged home','explicit lifecycle bridge help','absent-core status fails without writes','packaged documentation','existing local conversion','actual checksum-bound curl consumer and repeat','ignored local build bytes excluded without changing archive digest']}
    (args.output/'acceptance.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
