#!/usr/bin/env python3
"""Offline safety and determinism fixtures for the distribution builder."""
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock

SPEC=importlib.util.spec_from_file_location('ownwords_distribution',Path(__file__).with_name('build-distribution.py'))
builder=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class Packaging(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='ownwords-package-test-')
        self.root=Path(self.temporary.name).resolve()

    def tearDown(self):
        self.temporary.cleanup()

    def archive(self,names):
        target=self.root/'core.tar.gz'
        with tarfile.open(target,'w:gz') as tar:
            for name in names:
                item=tarfile.TarInfo(name);item.size=4
                tar.addfile(item,io.BytesIO(b'test'))
        return target

    def test_checksum_failure_writes_nothing(self):
        source=self.archive(['synthesis/file'])
        with self.assertRaisesRegex(ValueError,'checksum'):
            builder.vendor_core(source,'0'*64,self.root/'vendor')
        self.assertFalse((self.root/'vendor').exists())

    def test_archive_traversal_and_duplicates_are_rejected(self):
        for names in (['synthesis/../../escaped'],['synthesis/file','synthesis/file']):
            source=self.archive(names)
            with self.assertRaisesRegex(ValueError,'unsafe|duplicate'):
                builder.vendor_core(source,hashlib.sha256(source.read_bytes()).hexdigest(),self.root/str(len(names)))
        self.assertFalse((self.root.parent/'escaped').exists())

    def test_source_tree_cannot_be_an_output(self):
        source=self.root/'source';source.mkdir()
        with self.assertRaisesRegex(ValueError,'outside'):
            builder.build(source,source/'lib/release',self.root/'absent','0'*64)
        self.assertFalse((source/'lib').exists())
        adjacent=self.root/'adjacent';adjacent.mkdir()
        with self.assertRaisesRegex(ValueError,'traversal|outside'):
            builder.build(source,adjacent/'..'/'source'/'generated',self.root/'absent','0'*64)
        self.assertFalse((source/'generated').exists())

    def test_archive_is_deterministic_and_rejects_foreign_symlink(self):
        source=self.root/'source';source.mkdir()
        (source/'cli.js').write_text('console.log("fixture");\n')
        first=self.root/'one.tgz';second=self.root/'two.tgz'
        builder.archive_tree(source,first)
        builder.archive_tree(source,second)
        self.assertEqual(first.read_bytes(),second.read_bytes())
        (source/'foreign').symlink_to(self.root/'private')
        with self.assertRaisesRegex(ValueError,'symlink'):
            builder.archive_tree(source,self.root/'bad.tgz')

    def test_tagged_source_snapshot_excludes_ignored_local_files(self):
        source=self.root/'tagged';source.mkdir()
        env={**os.environ,'GIT_CONFIG_GLOBAL':os.devnull,'GIT_CONFIG_NOSYSTEM':'1'}
        def git(*args):return subprocess.check_output(['git',*args],cwd=source,env=env,text=True).strip()
        git('init','-q','-b','main')
        (source/'.gitignore').write_text('__pycache__/\n*.log\n')
        (source/'package.json').write_text('{"version":"9.8.7"}')
        (source/'lib').mkdir();(source/'lib/owned.js').write_text('retained source\n')
        git('add','.')
        git('-c','core.hooksPath=/dev/null','-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','Fixture')
        git('tag','v9.8.7')
        ignored=source/'lib/__pycache__';ignored.mkdir();(ignored/'build.pyc').write_bytes(b'normal build debris')
        self.assertEqual(git('status','--porcelain','--untracked-files=all'),'')
        destination=self.root/'snapshot'
        identity=builder.materialize_release(source,destination)
        self.assertEqual(identity['commit'],git('rev-parse','HEAD'))
        self.assertEqual((destination/'lib/owned.js').read_text(),'retained source\n')
        self.assertFalse((destination/'lib/__pycache__').exists())
        (source/'lib/owned.js').write_text('uncommitted change\n')
        with self.assertRaisesRegex(ValueError,'clean'):
            builder.materialize_release(source,self.root/'dirty-snapshot')
        self.assertFalse((self.root/'dirty-snapshot').exists())

    def test_release_workflow_selects_verified_oidc_toolchain(self):
        text=(builder.ROOT/'.github/workflows/distribution.yml').read_text()
        self.assertIn('npm@11.15.0',text)
        self.assertIn('test "$(npm --version)" = "11.15.0"',text)
        self.assertLess(text.index('npm@11.15.0'),text.index('npm publish '))
        self.assertNotIn('NODE_AUTH_TOKEN:',text)

    def test_homebrew_formula_declares_and_pins_explicit_setup_prerequisites(self):
        source=self.root/'source';source.mkdir()
        for directory in ('bin','lib','types','docs','packages'):
            (source/directory).mkdir()
        metadata={'name':'ownwords','version':'9.8.7','dependencies':{},'files':['bin/','lib/']}
        (source/'package.json').write_text(json.dumps(metadata))
        (source/'package-lock.json').write_text(json.dumps({'name':'ownwords','version':'9.8.7','lockfileVersion':3,'packages':{'':metadata}}))
        for name in ('README.md','LICENSE','bin/ownwords.js','lib/index.js','types/index.d.ts','docs/distribution.md'):
            (source/name).write_text('fixture only\n')
        (source/'packages/install.sh').write_text('#!/bin/sh\n# @VERSION@ @ARCHIVE_SHA256@\n')
        bootstrap=b'#!/bin/sh\nexit 0\n'
        release={'schema_version':1,'version':'9.8.7','commit':'1'*40,'bootstrap_sha256':hashlib.sha256(bootstrap).hexdigest()}
        files={'bin/synthesis':bootstrap,'lib/onboard.sh':bootstrap,'lib/package_launcher.py':b'# fixture\n',
               'lib/release_runtime.py':b'# fixture\n','lib/release.json':json.dumps(release).encode(),
               'LICENSE':b'fixture\n','README.md':b'fixture\n','package.json':b'{}\n'}
        core=self.root/'core.tgz'
        with tarfile.open(core,'w:gz') as archive:
            for name,data in files.items():
                member=tarfile.TarInfo('synthesis/'+name);member.size=len(data)
                member.mode=0o755 if name=='bin/synthesis' else 0o644
                archive.addfile(member,io.BytesIO(data))
        output=self.root/'output'
        # This renderer fixture has no dependencies. The full distribution
        # consumer suite separately exercises real locked npm acquisition.
        with mock.patch.object(builder.subprocess,'run',return_value=subprocess.CompletedProcess([],0)) as npm:
            builder.build(source,output,core,hashlib.sha256(core.read_bytes()).hexdigest())
        npm.assert_called_once()
        formula=(output/'ownwords.rb').read_text()
        self.assertIn('depends_on "node"',formula)
        self.assertIn('depends_on "git"',formula)
        self.assertIn('depends_on "python@3.12"',formula)
        self.assertIn('PATH: "#{Formula["node"].opt_bin}:#{Formula["git"].opt_bin}:$PATH"',formula)
        self.assertIn('SYNTHESIS_BOOTSTRAP_PYTHON: "#{Formula["python@3.12"].opt_bin}/python3.12"',formula)
        self.assertEqual(json.loads((output/'package/package.json').read_text())['dependencies'],{})



    def test_formula_only_validates_published_archive_without_rebuilding_it(self):
        archive=self.root/'ownwords-1.6.1.tgz'
        data=b'{"name":"ownwords","version":"1.6.1"}'
        with tarfile.open(archive,'w:gz') as tar:
            member=tarfile.TarInfo('package/package.json');member.size=len(data)
            tar.addfile(member,io.BytesIO(data))
        distribution=self.root/'distribution.json'
        metadata={'version':'1.6.1','sha256':builder.sha(archive),'archive':archive.name,
                  'url':builder.SOURCE+'/releases/download/v1.6.1/'+archive.name,
                  'source':{'kind':'git','url':builder.SOURCE,'commit':'4'*40,'tag':'v1.6.1'}}
        distribution.write_text(json.dumps(metadata))
        before=archive.read_bytes(); output=self.root/'formula-only'
        with mock.patch.object(builder,'build',side_effect=AssertionError('must not rebuild payload')):
            result=builder.formula_from_published(distribution,archive,output,revision=1)
        self.assertEqual(archive.read_bytes(),before)
        self.assertEqual(result['payload_source'],metadata['source'])
        self.assertEqual(result['formula_revision'],1)
        self.assertEqual(result['archive_sha256'],metadata['sha256'])
        self.assertIn('  revision 1\n',(output/'ownwords.rb').read_text())
        self.assertEqual(sorted(p.name for p in output.iterdir()),['formula-provenance.json','ownwords.rb'])
        self.assertEqual(result['recipe_source']['file_sha256'],builder.sha(Path(builder.__file__)))
        with self.assertRaisesRegex(ValueError,'new directory'):
            builder.formula_from_published(distribution,archive,output,revision=1)
        # A syntactically adjacent output can physically resolve back into
        # the source tree. Use an actual fixture Git repo and preserve it.
        protected=self.root/'protected-source';protected.mkdir()
        adjacent=self.root/'adjacent';adjacent.mkdir()
        env={**os.environ,'GIT_CONFIG_GLOBAL':os.devnull,'GIT_CONFIG_NOSYSTEM':'1'}
        def git(*args):
            return subprocess.check_output(['git',*args],cwd=protected,env=env,text=True).strip()
        git('init','-q','-b','main')
        sentinel=protected/'sentinel';sentinel.write_text('retained source bytes')
        git('add','sentinel')
        git('-c','core.hooksPath=/dev/null','-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','Fixture')
        for attempted in (adjacent/'..'/'protected-source'/'generated', protected/'absent'/'..'/'generated'):
            with mock.patch.object(builder,'ROOT',protected):
                with self.assertRaisesRegex(ValueError,'traversal|outside'):
                    builder.formula_from_published(distribution,archive,attempted,revision=1)
            self.assertFalse((protected/'generated').exists())
            self.assertEqual(sentinel.read_text(),'retained source bytes')
            self.assertEqual(git('status','--porcelain','--untracked-files=all'),'')
        metadata['sha256']='0'*64;distribution.write_text(json.dumps(metadata))
        with self.assertRaisesRegex(ValueError,'checksum'):
            builder.formula_from_published(distribution,archive,self.root/'bad',revision=1)
        self.assertFalse((self.root/'bad').exists())
        metadata['sha256']=builder.sha(archive);metadata['version']='1.6.2';distribution.write_text(json.dumps(metadata))
        with self.assertRaisesRegex(ValueError,'identity|version'):
            builder.formula_from_published(distribution,archive,self.root/'wrong-version',revision=1)
        self.assertFalse((self.root/'wrong-version').exists())

    def test_formula_revision_preserves_release_payload_binding(self):
        version='1.6.1'; checksum='a'*64
        default=builder.render_formula(version,checksum)
        revised=builder.render_formula(version,checksum,revision=1)
        self.assertNotIn('  revision ',default)
        self.assertIn('  revision 1\n',revised)
        self.assertIn('/v1.6.1/ownwords-1.6.1.tgz',revised)
        self.assertIn('sha256 "'+checksum+'"',revised)
        for invalid in (-1,True,'1',1.2):
            with self.assertRaisesRegex(ValueError,'revision'):
                builder.render_formula(version,checksum,revision=invalid)

    def test_actual_homebrew_writer_keeps_runtime_path_and_dependency_git(self):
        brew=shutil.which('brew')
        if not brew:
            self.skipTest('actual Homebrew writer requires Homebrew; portable formula checks still run')
        node=shutil.which('node'); git=shutil.which('git')
        self.assertTrue(node and git)
        # Use the generated formula from the existing physical renderer fixture.
        self.test_homebrew_formula_declares_and_pins_explicit_setup_prerequisites()
        formula=(self.root/'output/ownwords.rb').read_text()
        expression=re.search(r'^      PATH: (.+),$',formula,re.M).group(1)
        for part in ('node/bin','git/bin','build-shims','runtime-bin'):
            (self.root/part).mkdir(parents=True)
        (self.root/'node/bin/node').symlink_to(node)
        (self.root/'git/bin/git').symlink_to(git)
        marker=self.root/'build-shim-ran'
        (self.root/'build-shims/git').write_text('#!/bin/sh\nprintf bad > "'+str(marker)+'"\nexit 97\n')
        (self.root/'build-shims/git').chmod(0o755)
        (self.root/'runtime-bin/runtime-marker').write_text('#!/bin/sh\nprintf runtime-ok\n')
        (self.root/'runtime-bin/runtime-marker').chmod(0o755)
        target=self.root/'probe.js'
        target.write_text('#!/usr/bin/env node\nconst {spawnSync}=require("node:child_process"); for(const [c,a] of [["git",["--version"]],["runtime-marker",[]]]){const r=spawnSync(c,a,{encoding:"utf8"}); if(r.status!==0)process.exit(r.status||98);console.log(r.stdout.trim());}\n')
        target.chmod(0o755)
        wrapper=self.root/'wrapper'
        ruby='require "pathname"; class Formula; def self.[](name); Struct.new(:opt_bin).new(Pathname.new(ARGV[0])/name/"bin"); end; end; ENV["PATH"]=ARGV[0]+"/build-shims:/usr/bin:/bin"; value=eval(ARGV[1]); Pathname.new(ARGV[2]).write_env_script(ARGV[3], PATH: value)'
        written=subprocess.run([brew,'ruby','-e',ruby,str(self.root),expression,str(wrapper),str(target)],capture_output=True,text=True)
        self.assertEqual(written.returncode,0,written.stdout+written.stderr)
        wrapper.chmod(0o755)
        ran=subprocess.run([str(wrapper)],env={'PATH':str(self.root/'runtime-bin')+':/usr/bin:/bin','HOME':str(self.root)},capture_output=True,text=True)
        self.assertEqual(ran.returncode,0,ran.stdout+ran.stderr)
        self.assertIn('git version',ran.stdout)
        self.assertIn('runtime-ok',ran.stdout)
        self.assertFalse(marker.exists())
        self.assertNotIn('build-shims',wrapper.read_text())
        # Positive failure control: the prior formula captures the poisoned
        # build PATH through the same real Homebrew writer and exits at Git.
        prior='"#{Formula["node"].opt_bin}:#{ENV["PATH"]}"'
        bad_wrapper=self.root/'prior-wrapper'
        written=subprocess.run([brew,'ruby','-e',ruby,str(self.root),prior,str(bad_wrapper),str(target)],capture_output=True,text=True)
        self.assertEqual(written.returncode,0,written.stdout+written.stderr)
        bad_wrapper.chmod(0o755)
        refused=subprocess.run([str(bad_wrapper)],env={'PATH':str(self.root/'runtime-bin')+':/usr/bin:/bin','HOME':str(self.root)},capture_output=True,text=True)
        self.assertEqual(refused.returncode,97,refused.stdout+refused.stderr)
        self.assertEqual(marker.read_text(),'bad')


if __name__=='__main__':
    unittest.main()
