#!/usr/bin/env python3
"""Offline safety and determinism fixtures for the distribution builder."""
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
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

    def build_fixture(self):
        """Build a dependency-free fixture through the real builder and the real installer template."""
        source=self.root/'source';source.mkdir()
        for directory in ('bin','lib','types','docs','packages'):
            (source/directory).mkdir()
        metadata={'name':'ownwords','version':'9.8.7','dependencies':{},'files':['bin/','lib/']}
        (source/'package.json').write_text(json.dumps(metadata))
        (source/'package-lock.json').write_text(json.dumps({'name':'ownwords','version':'9.8.7','lockfileVersion':3,'packages':{'':metadata}}))
        for name in ('README.md','LICENSE','bin/ownwords.js','lib/index.js','types/index.d.ts','docs/distribution.md'):
            (source/name).write_text('fixture only\n')
        shutil.copyfile(builder.ROOT/'packages/install.sh',source/'packages/install.sh')
        output=self.root/'output'
        # This fixture has no dependencies. The full distribution consumer
        # suite separately exercises real locked npm acquisition.
        with mock.patch.object(builder.subprocess,'run',return_value=subprocess.CompletedProcess([],0)) as npm:
            result=builder.build(source,output)
        npm.assert_called_once()
        return output,result

    def test_source_tree_cannot_be_an_output(self):
        source=self.root/'source';source.mkdir()
        with self.assertRaisesRegex(ValueError,'outside'):
            builder.build(source,source/'lib/release')
        self.assertFalse((source/'lib').exists())
        adjacent=self.root/'adjacent';adjacent.mkdir()
        with self.assertRaisesRegex(ValueError,'traversal|outside'):
            builder.build(source,adjacent/'..'/'source'/'generated')
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

    def test_release_inputs_carry_no_core(self):
        workflow=(builder.ROOT/'.github/workflows/distribution.yml').read_text()
        for retired in ('core_archive','--core-','synthesis-skills'):
            self.assertNotIn(retired,workflow)
        output=self.root/'refused'
        refused=subprocess.run([sys.executable,'-B',builder.__file__,'--output',str(output),'--core-archive',str(self.root/'core.tgz'),'--core-sha256','0'*64],capture_output=True,text=True,timeout=60)
        self.assertEqual(refused.returncode,2,refused.stdout+refused.stderr)
        self.assertIn('unrecognized arguments: --core-archive',refused.stderr)
        self.assertFalse(output.exists())

    def test_build_packages_ownwords_alone(self):
        output,result=self.build_fixture()
        archive=output/'ownwords-9.8.7.tgz'
        with tarfile.open(archive) as tar:
            names=sorted(tar.getnames())
        # The exact inventory proves nothing beyond the Ownwords source is injected: no vendor tree, no core launcher.
        self.assertEqual(names,['package/LICENSE','package/README.md','package/bin','package/bin/ownwords.js','package/docs',
                                'package/docs/distribution.md','package/lib','package/lib/index.js','package/package-lock.json',
                                'package/package.json','package/types','package/types/index.d.ts'])
        manifest=json.loads((output/'package/package.json').read_text())
        self.assertEqual(manifest['files'],['bin/','docs/','lib/','package-lock.json'])
        self.assertEqual(manifest['dependencies'],{})
        self.assertEqual(manifest['bundleDependencies'],[])
        self.assertEqual(sorted(result),['archive','aur','brew_formula','channels','formula_revision','installer','npm_package',
                                         'schema_version','sha256','source','url','version'])
        self.assertEqual(result['channels'],['curl','npm','bun','homebrew','direct'])
        self.assertEqual(json.loads((output/'distribution.json').read_text()),result)
        self.assertEqual(sorted(p.name for p in output.iterdir()),['SHA256SUMS','distribution.json','install.sh','ownwords-9.8.7.tgz','ownwords.rb','package'])
        installer=(output/'install.sh').read_text()
        self.assertEqual(builder.sha(output/'install.sh'),result['installer']['sha256'])
        self.assertIn("VERSION='9.8.7'\n",installer)
        self.assertIn("ARCHIVE_SHA256='"+result['sha256']+"'\n",installer)
        self.assertEqual((output/'SHA256SUMS').read_text(),result['sha256']+'  ownwords-9.8.7.tgz\n'+result['installer']['sha256']+'  install.sh\n')
        for retired in ('@VERSION@','@ARCHIVE_SHA256@','setup','dormant','vendor/'):
            self.assertNotIn(retired,installer)

    def test_homebrew_formula_declares_only_node(self):
        output,result=self.build_fixture()
        formula=(output/'ownwords.rb').read_text()
        self.assertEqual(re.findall(r'^  depends_on .+$',formula,re.M),['  depends_on "node"'])
        self.assertIn('    (bin/"ownwords").write_env_script libexec/"bin/ownwords.js",\n      PATH: "#{Formula["node"].opt_bin}:$PATH"\n  end\n',formula)
        self.assertIn('  sha256 "'+result['sha256']+'"\n',formula)
        self.assertIn('assert_match "ownwords v9.8.7", shell_output("#{bin}/ownwords --version")',formula)
        self.assertIn('system bin/"ownwords", "convert", "article.html", "article.md"',formula)
        self.assertIn('assert_match "Retained author words", (testpath/"article.md").read',formula)
        for retired in ('Formula["git"]','python','SYNTHESIS','setup'):
            self.assertNotIn(retired,formula)

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

    def test_actual_homebrew_writer_resolves_commands_through_runtime_path(self):
        brew=shutil.which('brew')
        if not brew:
            self.skipTest('actual Homebrew writer requires Homebrew; portable formula checks still run')
        node=shutil.which('node'); curl=shutil.which('curl')
        self.assertTrue(node and curl)
        output,_=self.build_fixture()
        formula=(output/'ownwords.rb').read_text()
        expression=re.search(r'^      PATH: (.+)$',formula,re.M).group(1)
        for part in ('node/bin','build-shims','runtime-bin'):
            (self.root/part).mkdir(parents=True)
        (self.root/'node/bin/node').symlink_to(node)
        # Ownwords shells out to curl for HTML fetches; a build-time shim must never shadow the runtime one.
        marker=self.root/'build-shim-ran'
        (self.root/'build-shims/curl').write_text('#!/bin/sh\nprintf bad > "'+str(marker)+'"\nexit 97\n')
        (self.root/'build-shims/curl').chmod(0o755)
        (self.root/'runtime-bin/curl').symlink_to(curl)
        (self.root/'runtime-bin/runtime-marker').write_text('#!/bin/sh\nprintf runtime-ok\n')
        (self.root/'runtime-bin/runtime-marker').chmod(0o755)
        target=self.root/'probe.js'
        target.write_text('#!/usr/bin/env node\nconst {spawnSync}=require("node:child_process"); for(const [c,a] of [["curl",["--version"]],["runtime-marker",[]]]){const r=spawnSync(c,a,{encoding:"utf8"}); if(r.status!==0)process.exit(r.status||98);console.log(r.stdout.trim());}\n')
        target.chmod(0o755)
        wrapper=self.root/'wrapper'
        ruby='require "pathname"; class Formula; def self.[](name); Struct.new(:opt_bin).new(Pathname.new(ARGV[0])/name/"bin"); end; end; ENV["PATH"]=ARGV[0]+"/build-shims:/usr/bin:/bin"; value=eval(ARGV[1]); Pathname.new(ARGV[2]).write_env_script(ARGV[3], PATH: value)'
        written=subprocess.run([brew,'ruby','-e',ruby,str(self.root),expression,str(wrapper),str(target)],capture_output=True,text=True)
        self.assertEqual(written.returncode,0,written.stdout+written.stderr)
        wrapper.chmod(0o755)
        ran=subprocess.run([str(wrapper)],env={'PATH':str(self.root/'runtime-bin')+':/usr/bin:/bin','HOME':str(self.root)},capture_output=True,text=True)
        self.assertEqual(ran.returncode,0,ran.stdout+ran.stderr)
        self.assertTrue(ran.stdout.startswith('curl '),ran.stdout)
        self.assertIn('runtime-ok',ran.stdout)
        self.assertFalse(marker.exists())
        self.assertNotIn('build-shims',wrapper.read_text())
        # Positive failure control: a recipe that captures the build PATH
        # through the same real Homebrew writer resolves the poisoned shim.
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
