#!/usr/bin/env python3
"""Offline safety and determinism fixtures for the distribution builder."""
import hashlib
import importlib.util
import io
import json
import os
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
        self.assertIn('PATH: "#{Formula["node"].opt_bin}:#{ENV["PATH"]}"',formula)
        self.assertIn('SYNTHESIS_BOOTSTRAP_PYTHON: "#{Formula["python@3.12"].opt_bin}/python3.12"',formula)
        self.assertEqual(json.loads((output/'package/package.json').read_text())['dependencies'],{})


if __name__=='__main__':
    unittest.main()
