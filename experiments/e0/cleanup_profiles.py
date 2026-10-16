"""Remove exact owned experiment names; retain logs, manifests and checksums."""

from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time

bases=[Path('artifacts/e1-dag'),Path('artifacts/e0-rpc-profile')]
operations=[]
for base in bases:
    run=json.loads((base/'run.json').read_text());cmd=['rtk','proxy','kubectl','-n',run['namespace']]
    names={'job':run.get('client_jobs',[]),'pod':run.get('worker_pods',[])+run.get('diagnostic_pods',[])+run.get('failed_worker_pods',[])+run.get('aborted_worker_pods',[])+run.get('auxiliary_pods',[]),
           'service':run.get('services',[])+run.get('auxiliary_services',[]),
           'configmap':run.get('configmaps',[run['configmap']])}
    for kind,items in names.items():
        if not items:continue
        result=subprocess.run([*cmd,'delete',kind,*items,'--ignore-not-found=true','--wait=false'],capture_output=True,text=True)
        assert result.returncode==0,result.stderr
        operations.append({'run_id':run['run_id'],'kind':kind,'names':items,'stdout':result.stdout})
deadline=time.monotonic()+120
while True:
    remaining={}
    for base in bases:
        run=json.loads((base/'run.json').read_text())
        raw=subprocess.check_output(['rtk','proxy','kubectl','-n',run['namespace'],'get','pods,jobs,services,configmaps',
                                     '-l','research.scheduler/run='+run['run_id'],'-o','json'],text=True)
        remaining[run['run_id']]=[{'kind':x['kind'],'name':x['metadata']['name']} for x in json.loads(raw)['items']]
    count=sum(len(v) for v in remaining.values())
    print(json.dumps({'kind':'cleanup_progress','remaining_count':count}),flush=True)
    if not count or time.monotonic()>deadline:break
    time.sleep(5)
for base in bases:
    run=json.loads((base/'run.json').read_text())
    report={'observed_at':datetime.now(timezone.utc).isoformat(),'namespace_retained':run['namespace'],
            'operations':[o for o in operations if o['run_id']==run['run_id']],
            'remaining':remaining[run['run_id']],'remaining_count':len(remaining[run['run_id']])}
    (base/'cleanup.json').write_text(json.dumps(report,indent=2)+'\n')
assert count==0,remaining
removed=[]
for base,manifest,folder in [(bases[1],'wheel-manifest.json','wheelhouse'),(bases[0],'additional-wheel-manifest.json','additional-assets')]:
    for item in json.loads((base/manifest).read_text()):
        file=base/folder/item['filename']
        if not file.exists():continue
        with file.open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()==item['sha256']
        file.unlink();removed.append(item['filename'])
(bases[1]/'local-cache-cleanup.json').write_text(json.dumps({'removed_verified_wheels':removed,'count':len(removed),'manifests_retained':True},indent=2)+'\n')
print(json.dumps({'status':'all_experiment_resources_removed','local_temporary_wheels_removed':len(removed)}),flush=True)
