"""Same-snapshot decision compatibility and representative live HTTP applications."""

import http.client
import json
import os
from pathlib import Path
import socket
import sys
from urllib.parse import urlparse

from measured_policy_bridge import E2Policy,task_fastest
from policy_runner import PolicyRunner
from probe import IMAGE_SHA256,fetch,emit


def main():
    spec=json.load(sys.stdin)
    source=spec['source'];peers=json.loads(os.environ['PEERS_JSON'])
    contract=json.loads(Path('/config/bridge-contract.json').read_text())
    profile=contract['profiles'][source]
    body=fetch(os.environ['ASSET_BASE']+'/dog.jpg',IMAGE_SHA256)
    names=['fixed','warm_dag','ready_dag','ready_greedy','credit_hysteresis','task_fastest']
    expected={name:([source]*3 if name!='fixed' else ['arm']*3) for name in names}
    expected['task_fastest']=['intel_npu']*3 if source=='arm' else ['intel_npu','intel_npu','arm']
    representatives=['fixed','task_fastest'] if source=='arm' else ['fixed','warm_dag','task_fastest']

    def evict():
        url=urlparse(peers['intel_npu']['url'])
        connection=http.client.HTTPConnection(url.hostname,url.port,timeout=15)
        try:
            connection.connect();connection.sock.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
            connection.request('POST','/evict',b'')
            response=connection.getresponse();result=json.loads(response.read())
            assert response.status==200 and not result['model_ready']
            emit('control',source=source,operation='service_object_evict',**result)
        finally:connection.close()

    with PolicyRunner(peers) as runner:
        for readiness in ['warm','eviction']:
            if readiness=='warm':
                setup=runner.run(request_id=spec['run_id']+'-'+source+'-setup',input_id='dog',body=body,
                    source=source,profile=profile,policy_name='setup-only',policy=lambda s:['intel_npu']*3)
                emit('setup_workflow',**setup)
                assert setup['status']=='ok',setup.get('error')
            for representative in representatives:
                if readiness=='eviction':evict()
                decisions={}

                def compare(snapshot):
                    assert snapshot['workers']['intel_npu']['service_object_ready']==(readiness=='warm')
                    for name in names:
                        policy=task_fastest if name=='task_fastest' else E2Policy(name,contract)
                        plan=policy(snapshot)
                        decisions[name]={'snapshot_id':snapshot['snapshot_id'],'plan':plan,
                                         'information':{} if name=='task_fastest' else policy.information}
                        assert plan==expected[name],(source,readiness,name,plan,expected[name])
                    return decisions[representative]['plan']

                result=runner.run(request_id=f"{spec['run_id']}-{source}-{readiness}-{representative}",
                    input_id='dog',body=body,source=source,profile=profile,
                    policy_name='shared-snapshot-'+representative,policy=compare)
                emit('bridge_attempt',source=source,readiness=readiness,representative=representative,
                     decisions=decisions,**result)
                assert result['status']=='ok',result.get('error')
                assert result['result']['top5']==[258,259,157,279,152]
        emit('client_complete',source=source,attempts=2*len(representatives),setups=1)


if __name__=='__main__':main()
