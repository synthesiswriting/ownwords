#!/usr/bin/env python3
"""Exercise actual temporary npm, Bun and direct consumers without services."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parent.parent
REMOVED_COMMANDS = (['setup'], ['setup', '--no-dormant-core', '--json'], ['synthesis', 'status'])


def run(arguments, *, cwd, env):
    result = subprocess.run(arguments, cwd=cwd, env=env, text=True, capture_output=True, timeout=180)
    if result.returncode:
        raise AssertionError('%r\n%s\n%s' % (arguments, result.stdout, result.stderr))
    return result.stdout


def assert_standalone_cli(command, *, cwd, env, home):
    """The installed CLI keeps its authoring commands and has no setup or synthesis command."""
    help_text = run(command + ['--help'], cwd=cwd, env=env)
    assert re.search(r'^  convert <input> \[output\] ', help_text, re.M), 'help lost an authoring command'
    assert not re.search(r'^  (setup|synthesis)\b', help_text, re.M), 'help still lists a removed command'
    before = sorted(home.rglob('*'))
    for removed in REMOVED_COMMANDS:
        result = subprocess.run(command + removed, cwd=cwd, env=env, text=True, capture_output=True, timeout=30)
        assert result.returncode == 1 and re.search('^Unknown command: %s$' % removed[0], result.stderr, re.M), \
            'removed command %r is still dispatched: %s%s' % (removed, result.stdout, result.stderr)
    assert sorted(home.rglob('*')) == before, 'a removed command changed the isolated home'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--npm-cache', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    environment = {key: value for key, value in os.environ.items() if not key.startswith(('GIT_', 'NODE_OPTIONS'))}
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
        run([sys.executable,'-B',str(source/'scripts/build-distribution.py'),'--repo-root',str(source),'--output',str(args.output/suffix),'--npm-cache',str(args.npm_cache)],cwd=source,env=environment)
    # Builder output includes npm progress; durable distribution.json is authoritative.
    first=json.loads((args.output/'one/distribution.json').read_text())
    second=json.loads((args.output/'two/distribution.json').read_text())
    assert first['sha256']==second['sha256'], 'release archive is not deterministic'
    assert 'core' not in first, 'distribution metadata still describes a bundled core'
    archive=args.output/'one'/first['archive']
    with tarfile.open(archive) as bundle:
        members=bundle.getnames()
    assert not any('__pycache__' in name for name in members), 'ignored checkout bytes entered the artifact'
    # Positive control: the archive does carry Ownwords and its locked dependencies.
    assert {'package/bin/ownwords.js','package/lib/index.js','package/node_modules/turndown/package.json'} <= set(members), 'archive lacks Ownwords or its dependencies'
    assert not any(name == 'package/vendor' or name.startswith('package/vendor/') for name in members), 'archive bundles a vendor tree'
    assert 'package/lib/setup.js' not in members, 'archive carries the removed setup module'
    installer_text=(args.output/'one/install.sh').read_text()
    for retired in ('setup','dormant','vendor/'):
        assert retired not in installer_text, 'built installer still refers to '+retired
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
        assert_standalone_cli(command,cwd=consumer,env=env,home=home)
        installed_root=consumer/('package' if channel=='direct' else 'node_modules/ownwords')
        assert (installed_root/'docs/distribution.md').is_file(), 'packaged documentation link is missing'
        assert not (installed_root/'vendor').exists(), 'installed package carries a vendor tree'
        html=consumer/'article.html';markdown=consumer/'article.md'
        html.write_text('<html><body><article><h1>Fixture article</h1><p>Retained author words.</p></article></body></html>')
        before=sorted(home.rglob('*'))
        run(command+['convert',str(html),str(markdown)],cwd=consumer,env=env)
        assert 'Retained author words' in markdown.read_text()
        assert sorted(home.rglob('*'))==before, 'local conversion changed the isolated home'
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
    install=['sh',str(args.output/'one/install.sh'),'--prefix',str(prefix)]
    for attempt in range(2):
        assert 'Installed Ownwords %s at %s.' % (version, prefix/'ownwords') in run(install,cwd=consumer,env=env)
    assert 'ownwords v'+version in run([str(prefix/'ownwords'),'--version'],cwd=consumer,env=env)
    assert list(home.iterdir())==[], 'curl installation changed isolated HOME'
    release=prefix/('.ownwords-'+version)
    assert (release/'bin/ownwords.js').is_file() and not (release/'vendor').exists(), 'curl release directory carries a vendor tree'
    assert_standalone_cli([str(prefix/'ownwords')],cwd=consumer,env=env,home=home)
    receipt=json.loads((prefix/'.ownwords-install.json').read_text())
    assert receipt['version']==version and receipt['archive_sha256']==first['sha256']
    assert not (prefix/'.ownwords-install.pending.json').exists()
    channels.append('curl')
    report={'status':'PASS' ,'fixture_only':True,'channels':channels,'deterministic_sha256':first['sha256'],'checks':['real installed version','no bundled core or vendor tree in archive, metadata or installed packages','no setup step in the built installer','help lists no setup or synthesis command','removed commands are unknown and leave home unchanged','packaged documentation','existing local conversion leaves home unchanged','actual checksum-bound curl consumer and repeat','curl installation leaves home unchanged','ignored local build bytes excluded without changing archive digest']}
    (args.output/'acceptance.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
