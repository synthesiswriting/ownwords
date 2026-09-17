#!/usr/bin/env python3
"""Bounded cancellation checks; all workers are local disposable processes."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parent.parent
SPEC=importlib.util.spec_from_file_location('curl_fixture',ROOT/'scripts/test-curl-installer.py')
curl_fixture=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(curl_fixture)


class Cancellation(unittest.TestCase):
    def worker(self,root):
        script=root/'worker.py'
        script.write_text('import os,signal,time\nfrom pathlib import Path\n'
            'def stop(sig,frame):\n Path(os.environ["OWNWORDS_STOPPED"]).write_text(str(sig))\n raise SystemExit(0)\n'
            'for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP):signal.signal(sig,stop)\n'
            'Path(os.environ["OWNWORDS_READY"]).write_text(str(os.getpid()))\ntime.sleep(30)\n')
        return '#!/bin/sh\nexec '+shlex.quote(sys.executable)+' '+shlex.quote(str(script))+'\n'

    def assert_cancel(self,command,env,root,sig):
        ready=root/'ready';stopped=root/'stopped'
        env={**env,'OWNWORDS_READY':str(ready),'OWNWORDS_STOPPED':str(stopped)}
        child=None
        process=subprocess.Popen(command,env=env,start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        try:
            deadline=time.monotonic()+5
            while not ready.exists() and time.monotonic()<deadline:
                if process.poll() is not None:self.fail('parent exited before worker readiness: '+process.stderr.read().decode())
                time.sleep(.01)
            self.assertTrue(ready.exists(),'worker did not start')
            child=int(ready.read_text());process.send_signal(sig)
            self.assertEqual(process.wait(timeout=5),-sig)
            self.assertTrue(stopped.exists(),'child never received cancellation')
            self.assertEqual(int(stopped.read_text()),sig)
            with self.assertRaises(ProcessLookupError):os.kill(child,0)
        finally:
            if child:
                try:os.kill(child,signal.SIGTERM)
                except ProcessLookupError:pass
            try:os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            process.wait(timeout=5)
            process.stderr.close()

    def test_bridge_forwards_incoming_cancellation(self):
        for method,args in [('runSetup',[]),('runSynthesis',['doctor'])]:
            for sig in [signal.SIGINT,signal.SIGTERM,signal.SIGHUP]:
                with self.subTest(method=method,signal=sig),tempfile.TemporaryDirectory(prefix='ownwords-cancel-') as temp:
                    root=Path(temp);bundle=root/'bundle';(bundle/'bin').mkdir(parents=True);(bundle/'lib').mkdir()
                    launcher=bundle/'bin/synthesis';launcher.write_text(self.worker(root));launcher.chmod(0o755)
                    (bundle/'lib/release.json').write_text(json.dumps({'schema_version':1,'version':'9.8.7','commit':'a'*40}))
                    files={}
                    for name in ['bin/synthesis','lib/release.json']:
                        p=bundle/name;files[name]={'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'mode':p.stat().st_mode&0o777}
                    (bundle/'bundle-integrity.json').write_text(json.dumps({'schema_version':1,'version':'9.8.7','commit':'a'*40,'archive_sha256':'b'*64,'files':files}))
                    js='const m=require('+json.dumps(str(ROOT/'lib/setup.js'))+');Promise.resolve(m['+json.dumps(method)+']('+json.dumps(args)+',{bundleRoot:'+json.dumps(str(bundle))+'})).then(code=>process.exit(code));'
                    self.assert_cancel(['node','-e',js],dict(os.environ),root,sig)

    def curl_case(self,phase,sig):
        case=curl_fixture.CurlInstaller();case.setUp()
        try:
            if phase=='transport':
                (case.root/'transport/curl').write_text(self.worker(case.root))
            else:
                code=b'const fs=require("fs"); for(const [s,n] of [["SIGINT",2],["SIGTERM",15],["SIGHUP",1]])process.on(s,()=>{fs.writeFileSync(process.env.OWNWORDS_STOPPED,String(n));process.exit(0)});fs.writeFileSync(process.env.OWNWORDS_READY,String(process.pid));setTimeout(()=>{},30000);\n'
                with tarfile.open(case.archive,'w:gz') as tar:
                    for name,data,mode in [('package/bin/ownwords.js',code,0o755),('package/package.json',b'{"version":"9.8.7"}',0o644),('package/vendor/synthesis/bin/synthesis',b'fixture\n',0o755)]:
                        info=tarfile.TarInfo(name);info.size=len(data);info.mode=mode;tar.addfile(info,io.BytesIO(data))
                case.installer.write_text((ROOT/'packages/install.sh').read_text().replace('@VERSION@','9.8.7').replace('@ARCHIVE_SHA256@',hashlib.sha256(case.archive.read_bytes()).hexdigest()))
            self.assert_cancel(['sh',str(case.installer),'--prefix',str(case.prefix),'--no-dormant-core'],case.env,case.root,sig)
            self.assertFalse(list(case.prefix.glob('.ownwords-download-*')))
            if phase=='transport':self.assertFalse((case.prefix/'ownwords').exists())
            else:self.assertTrue((case.prefix/'.ownwords-install.json').exists())
        finally:case.tearDown()

    def test_curl_forwards_transport_cancellation(self):
        for sig in [signal.SIGINT,signal.SIGTERM,signal.SIGHUP]:
            with self.subTest(signal=sig):self.curl_case('transport',sig)

    def test_curl_forwards_setup_cancellation(self):
        for sig in [signal.SIGINT,signal.SIGTERM,signal.SIGHUP]:
            with self.subTest(signal=sig):self.curl_case('setup',sig)

    def test_curl_propagates_transport_exit(self):
        case=curl_fixture.CurlInstaller();case.setUp()
        try:
            (case.root/'transport/curl').write_text('#!/bin/sh\nexit 23\n')
            self.assertEqual(case.run_installer().returncode,23)
            self.assertFalse((case.prefix/'ownwords').exists())
            self.assertFalse(list(case.prefix.glob('.ownwords-download-*')))
        finally:case.tearDown()


    def test_curl_propagates_child_failure_and_signal(self):
        for phase in ('transport','setup'):
            for expected in (23,-signal.SIGTERM):
                with self.subTest(phase=phase,exit=expected):
                    case=curl_fixture.CurlInstaller();case.setUp()
                    try:
                        if phase=='transport':
                            (case.root/'transport/curl').write_text('#!/bin/sh\n'+('exit 23' if expected==23 else 'kill -TERM $$')+'\n')
                        else:
                            code=b'process.exit(23);\n' if expected==23 else b'process.kill(process.pid,"SIGTERM");\n'
                            with tarfile.open(case.archive,'w:gz') as tar:
                                for name,data,mode in [('package/bin/ownwords.js',code,0o755),('package/package.json',b'{"version":"9.8.7"}',0o644),('package/vendor/synthesis/bin/synthesis',b'fixture\n',0o755)]:
                                    info=tarfile.TarInfo(name);info.size=len(data);info.mode=mode;tar.addfile(info,io.BytesIO(data))
                            case.installer.write_text((ROOT/'packages/install.sh').read_text().replace('@VERSION@','9.8.7').replace('@ARCHIVE_SHA256@',hashlib.sha256(case.archive.read_bytes()).hexdigest()))
                        self.assertEqual(case.run_installer().returncode,expected)
                        self.assertFalse(list(case.prefix.glob('.ownwords-download-*')))
                        self.assertEqual((case.prefix/'.ownwords-install.json').exists(),phase=='setup')
                    finally:case.tearDown()


if __name__=='__main__':unittest.main()
