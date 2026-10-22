"""Small owned-Pod probe for a mounted script disappearing after peer deletion."""
import json
import time
from run_load import Session
from run_smoke import IMAGE, NODES, NS


def main():
    s=Session()
    try:
        cm=dict(apiVersion='v1',kind='ConfigMap',metadata=s.meta(s.run),data={'probe.py':"print('config-present',flush=True)\n"})
        def pod(key):
            return dict(apiVersion='v1',kind='Pod',metadata=s.meta(s.run+'-'+key),spec=dict(
                restartPolicy='Never',activeDeadlineSeconds=300,automountServiceAccountToken=False,
                nodeSelector={'kubernetes.io/hostname':NODES['e1']},
                containers=[dict(name='netctl',image=IMAGE,command=['python3','-u','-c','import time;time.sleep(280)'],
                    volumeMounts=[dict(name='config',mountPath='/config',readOnly=True)],
                    resources={'requests':{'cpu':'10m','memory':'32Mi'},'limits':{'cpu':'100m','memory':'64Mi'}},
                    securityContext={'runAsUser':65532,'runAsNonRoot':True,'readOnlyRootFilesystem':True,'allowPrivilegeEscalation':False,'capabilities':{'drop':['ALL']}})],
                volumes=[dict(name='config',configMap={'name':s.run})]))
        holder,mate=pod('holder'),pod('mate')
        s.apply('probe',[cm,holder,mate])
        s.call('ready',['wait','pod','-n',NS,'-l','run-id='+s.run,'--for=condition=Ready','--timeout=90s'],timeout=100)
        code="import runpy;runpy.run_path('/config/probe.py',run_name='__main__')"
        s.execute('holder',code,'before-delete',container='netctl')
        s.call('delete-peer',['delete','pod','-n',NS,s.run+'-mate','--wait=true','--timeout=60s'],timeout=70)
        s.resources.remove(mate)
        outcomes=[]
        for index in range(25):
            try:
                value=s.execute('holder',code,'after-delete-'+str(index),container='netctl')
                outcomes.append(dict(index=index,present=value.strip()=='config-present'))
            except Exception as error:
                outcomes.append(dict(index=index,present=False,error=repr(error)))
                break
            time.sleep(5)
        s.save('probe-result.json',dict(reproduced=any(not r['present'] for r in outcomes),observations=outcomes,
            scope='shared ConfigMap holder after one same-node peer deletion; does not reproduce full experiment'))
    finally:s.close()


if __name__=='__main__':main()
