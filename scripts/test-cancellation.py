#!/usr/bin/env python3
"""Bounded cancellation checks; all workers are local disposable processes."""
import importlib.util
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
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

    def test_curl_forwards_transport_cancellation(self):
        for sig in [signal.SIGINT,signal.SIGTERM,signal.SIGHUP]:
            with self.subTest(signal=sig):
                case=curl_fixture.CurlInstaller();case.setUp()
                try:
                    (case.root/'transport/curl').write_text(self.worker(case.root))
                    self.assert_cancel(['sh',str(case.installer),'--prefix',str(case.prefix)],case.env,case.root,sig)
                    self.assertFalse(list(case.prefix.glob('.ownwords-download-*')))
                    self.assertFalse((case.prefix/'ownwords').exists())
                    self.assertFalse((case.prefix/'.ownwords-install.json').exists())
                finally:case.tearDown()

    def test_curl_propagates_transport_failure_and_signal(self):
        for expected in (23,-signal.SIGTERM):
            with self.subTest(exit=expected):
                case=curl_fixture.CurlInstaller();case.setUp()
                try:
                    (case.root/'transport/curl').write_text('#!/bin/sh\n'+('exit 23' if expected==23 else 'kill -TERM $$')+'\n')
                    self.assertEqual(case.run_installer().returncode,expected)
                    self.assertFalse(list(case.prefix.glob('.ownwords-download-*')))
                    self.assertFalse((case.prefix/'ownwords').exists())
                    self.assertFalse((case.prefix/'.ownwords-install.json').exists())
                finally:case.tearDown()


if __name__=='__main__':unittest.main()
