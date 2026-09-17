#!/usr/bin/env python3
"""Run the real installer against local archive transport and isolated homes."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

ROOT=Path(__file__).resolve().parent.parent


class CurlInstaller(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='ownwords-curl-test-')
        self.root=Path(self.temporary.name).resolve()
        self.home=self.root/'home'; self.home.mkdir()
        self.prefix=self.root/'prefix'
        self.archive=self.root/'ownwords.tgz'
        script=b'if(process.argv[2]==="setup") console.log("fixture setup: "+process.argv.slice(3).join(" ")); else console.log("ownwords v9.8.7");\n'
        with tarfile.open(self.archive,'w:gz') as tar:
            for name,data,mode in [('package/bin/ownwords.js',script,0o755),('package/package.json',b'{"version":"9.8.7"}',0o644),('package/vendor/synthesis/bin/synthesis',b'fixture\n',0o755)]:
                info=tarfile.TarInfo(name);info.size=len(data);info.mode=mode
                tar.addfile(info,io.BytesIO(data))
        self.installer=self.root/'install.sh'
        self.installer.write_text((ROOT/'packages/install.sh').read_text().replace('@VERSION@','9.8.7').replace('@ARCHIVE_SHA256@',hashlib.sha256(self.archive.read_bytes()).hexdigest()))
        traps=self.root/'transport';traps.mkdir()
        curl=traps/'curl';curl.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$OWNWORDS_CURL_ARGS"\nwhile [ "$1" != "-o" ]; do shift; done\ncp "$OWNWORDS_CURL_ARCHIVE" "$2"\n');curl.chmod(0o755)
        self.env={k:v for k,v in os.environ.items() if not k.startswith(('SYNTHESIS_','XDG_','GIT_'))}
        self.env.update(HOME=str(self.home),PATH=str(traps)+os.pathsep+self.env['PATH'],OWNWORDS_CURL_ARCHIVE=str(self.archive),OWNWORDS_CURL_ARGS=str(self.root/'curl.args'))

    def tearDown(self):
        self.temporary.cleanup()

    def run_installer(self):
        return subprocess.run(['sh',str(self.installer),'--prefix',str(self.prefix),'--no-dormant-core'],env=self.env,text=True,capture_output=True,timeout=30)

    def test_installs_repeats_and_recovers_bound_journal(self):
        first=self.run_installer();self.assertEqual(first.returncode,0,first.stdout+first.stderr)
        args=(self.root/'curl.args').read_text().splitlines()
        self.assertIn('https://github.com/synthesiswriting/ownwords/releases/download/v9.8.7/ownwords-9.8.7.tgz',args)
        self.assertIn('--proto-redir',args)
        binary=self.prefix/'ownwords'; receipt=self.prefix/'.ownwords-install.json'; pending=self.prefix/'.ownwords-install.pending.json'
        self.assertIn('fixture setup: --no-dormant-core',first.stdout)
        before=binary.read_bytes()
        self.assertEqual(self.run_installer().returncode,0)
        pending.write_bytes(receipt.read_bytes()); receipt.unlink()
        self.assertEqual(self.run_installer().returncode,0)
        self.assertEqual(binary.read_bytes(),before);self.assertFalse(pending.exists())
        pending.write_bytes(receipt.read_bytes());receipt.unlink();binary.unlink()
        self.assertEqual(self.run_installer().returncode,0)
        self.assertEqual(binary.read_bytes(),before);self.assertFalse(pending.exists())
        self.assertEqual(list(self.home.iterdir()),[])

    def test_refuses_foreign_launcher_and_symlink_prefix(self):
        self.prefix.mkdir();target=self.prefix/'ownwords';target.write_text('foreign')
        self.assertNotEqual(self.run_installer().returncode,0);self.assertEqual(target.read_text(),'foreign')
        self.assertFalse((self.root/'curl.args').exists())
        other=self.root/'other';other.mkdir();link=self.root/'link';link.symlink_to(other);self.prefix=link
        self.assertNotEqual(self.run_installer().returncode,0);self.assertEqual(list(other.iterdir()),[])

    def test_refuses_checksum_permission_and_payload_drift(self):
        original=self.archive.read_bytes();self.archive.write_bytes(original+b'corrupt')
        self.assertNotEqual(self.run_installer().returncode,0);self.assertFalse((self.prefix/'ownwords').exists())
        self.archive.write_bytes(original);self.assertEqual(self.run_installer().returncode,0)
        target=self.prefix/'ownwords';target.chmod(0o644)
        self.assertNotEqual(self.run_installer().returncode,0);self.assertEqual(target.stat().st_mode&0o777,0o644)
        target.chmod(0o755);payload=self.prefix/'.ownwords-9.8.7/bin/ownwords.js';payload.write_text('foreign edit')
        self.assertNotEqual(self.run_installer().returncode,0);self.assertEqual(payload.read_text(),'foreign edit')

    def test_refuses_unbound_pending_journal(self):
        self.assertEqual(self.run_installer().returncode,0)
        receipt=self.prefix/'.ownwords-install.json';pending=self.prefix/'.ownwords-install.pending.json'
        record=json.loads(receipt.read_text());record['archive_sha256']='0'*64;pending.write_text(json.dumps(record))
        snapshot=pending.read_bytes();before=(self.prefix/'ownwords').read_bytes()
        self.assertNotEqual(self.run_installer().returncode,0)
        self.assertEqual(pending.read_bytes(),snapshot);self.assertEqual((self.prefix/'ownwords').read_bytes(),before)

    def test_refuses_unsafe_archive_even_when_checksum_matches(self):
        for case in ('traversal','symlink','duplicate'):
            self.prefix=self.root/('prefix-'+case)
            with tarfile.open(self.archive,'w:gz') as tar:
                name='package/../../escape' if case=='traversal' else 'package/file'
                item=tarfile.TarInfo(name)
                if case=='symlink':item.type=tarfile.SYMTYPE;item.linkname=str(self.root/'retained')
                else:item.size=4
                tar.addfile(item,None if case=='symlink' else io.BytesIO(b'test'))
                if case=='duplicate':tar.addfile(item,io.BytesIO(b'test'))
            self.installer.write_text((ROOT/'packages/install.sh').read_text().replace('@VERSION@','9.8.7').replace('@ARCHIVE_SHA256@',hashlib.sha256(self.archive.read_bytes()).hexdigest()))
            result=self.run_installer()
            self.assertNotEqual(result.returncode,0)
            self.assertFalse((self.prefix/'ownwords').exists())
            self.assertFalse((self.root/'escape').exists())

    def test_failed_transport_preserves_prior_install_and_receipt(self):
        self.assertEqual(self.run_installer().returncode,0)
        binary=self.prefix/'ownwords';receipt=self.prefix/'.ownwords-install.json'
        before=(binary.read_bytes(),receipt.read_bytes())
        curl=self.root/'transport/curl';curl.write_text('#!/bin/sh\nexit 23\n')
        self.assertNotEqual(self.run_installer().returncode,0)
        self.assertEqual((binary.read_bytes(),receipt.read_bytes()),before)


    def test_failed_transport_preserves_pending_recovery_boundaries(self):
        for after_launcher in (False,True):
            self.prefix=self.root/('pending-'+str(after_launcher))
            self.assertEqual(self.run_installer().returncode,0)
            receipt=self.prefix/'.ownwords-install.json';pending=self.prefix/'.ownwords-install.pending.json';binary=self.prefix/'ownwords'
            pending.write_bytes(receipt.read_bytes());receipt.unlink()
            if not after_launcher:binary.unlink()
            before=[p.read_bytes() if p.exists() else None for p in (pending,receipt,binary)]
            curl=self.root/'transport/curl';transport=curl.read_text();curl.write_text('#!/bin/sh\nexit 23\n')
            self.assertEqual(self.run_installer().returncode,23)
            self.assertEqual([p.read_bytes() if p.exists() else None for p in (pending,receipt,binary)],before)
            curl.write_text(transport)


if __name__=='__main__':unittest.main()
