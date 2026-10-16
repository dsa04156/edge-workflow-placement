"""Actual HTTP workflow test, including a worker→worker→original-worker path."""

import hashlib
import http.client
import json
import os
from pathlib import Path
import selectors
import socket
import subprocess
import sys
import tempfile
import unittest

from probe import IMAGE_URL, IMAGE_SHA256, fetch


class DAGContract(unittest.TestCase):
    extra_env={}
    aliases=('a','b')
    @classmethod
    def setUpClass(cls):
        cls.directory=tempfile.TemporaryDirectory();cls.processes=[];cls.logs=[]
        cls.peers={};reservations={}
        for alias in cls.aliases:
            sock=socket.socket();sock.bind(('0.0.0.0',0))
            reservations[alias]=sock;port=sock.getsockname()[1]
            cls.peers[alias]={'alias':alias,'node':'local-'+alias,'backend':'cpu','url':f'http://127.0.0.1:{port}'}
        for alias,peer in cls.peers.items():
            work=Path(cls.directory.name)/alias;work.mkdir()
            error=open(work/'stderr.log','w+');cls.logs.append(error)
            env=dict(os.environ,MODE='server',ALIAS=alias,NODE_NAME=peer['node'],BACKEND='cpu',
                     PORT=peer['url'].rsplit(':',1)[1],WORKDIR=str(work),PEERS_JSON=json.dumps(cls.peers))
            env.update(cls.extra_env)
            reservations.pop(alias).close()
            p=subprocess.Popen(['rtk','proxy',sys.executable,'-u',str(Path(__file__).with_name('dag_profile.py'))],
                               env=env,stdout=subprocess.PIPE,stderr=error,text=True)
            cls.processes.append(p)
            selector=selectors.DefaultSelector();selector.register(p.stdout,selectors.EVENT_READ)
            while selector.select(timeout=60):
                line=p.stdout.readline()
                if not line:break
                if json.loads(line)['kind']=='server_ready':break
            else:line=''
            selector.close()
            if not line or json.loads(line)['kind']!='server_ready':
                error.seek(0);message=error.read()[-1500:]
                for process in cls.processes:
                    if process.poll() is None:process.terminate();process.wait(timeout=5)
                for reservation in reservations.values():reservation.close()
                raise AssertionError(message)
        cls.body=fetch(IMAGE_URL,IMAGE_SHA256)

    @classmethod
    def tearDownClass(cls):
        for p in cls.processes:
            p.terminate();p.wait(timeout=5);p.stdout.close()
        for log in cls.logs:log.close()
        cls.directory.cleanup()

    def test_colocated_and_forwarded_workflows_preserve_task_data(self):
        a,b=self.aliases
        for plan in [[a,a,a],[a,b,a]]:
            with self.subTest(plan=plan):
                conn=http.client.HTTPConnection('127.0.0.1',int(self.peers[a]['url'].rsplit(':',1)[1]),timeout=15)
                conn.request('POST','/stage/0',self.body,{'X-Plan':json.dumps(plan),'X-Request-ID':'test-'+''.join(plan),
                                                       'X-Payload-SHA256':hashlib.sha256(self.body).hexdigest()})
                response=conn.getresponse();result=json.loads(response.read());conn.close()
                self.assertEqual(response.status,200)
                self.assertEqual(result['top5'],[258,259,157,279,152])
                self.assertEqual([t['alias'] for t in result['trace']],plan)
                self.assertEqual([t['stage'] for t in result['trace']],[0,1,2])
                self.assertEqual(result['trace'][0]['output_sha256'],result['trace'][1]['input_sha256'])
                self.assertEqual(result['trace'][1]['output_sha256'],result['trace'][2]['input_sha256'])


if __name__=='__main__':unittest.main()
