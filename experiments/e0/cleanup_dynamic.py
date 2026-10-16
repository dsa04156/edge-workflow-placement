"""Remove exact E2-owned resources after validation, preserving evidence."""

from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time

base=Path('artifacts/e2-dynamic');run=json.loads((base/'run.json').read_text())
assert json.loads((base/'verification.json').read_text())['matching_stage_receipts']==4542
cmd=['rtk','proxy','kubectl','--request-timeout=15s','-n',run['namespace']]
owned=[]
for filename in ['workers.json','cache.json','postcheck.json']:
    if not (base/filename).exists():continue
    for item in json.loads((base/filename).read_text())['items']:
        assert item['metadata']['namespace']==run['namespace']
        assert item['metadata']['labels']['research.scheduler/run']==run['run_id']
        kind,name=item['kind'].lower(),item['metadata']['name']
        subprocess.run([*cmd,'delete',kind,name,'--ignore-not-found=true','--wait=false'],check=True)
        owned.append({'kind':kind,'name':name})
deadline=time.monotonic()+120
while True:
    remaining=json.loads(subprocess.check_output([*cmd,'get','pods,services,configmaps','-l',
                  'research.scheduler/run='+run['run_id'],'-o','json'],text=True))['items']
    if not remaining:break
    assert time.monotonic()<deadline,'Owned resources still terminating; do not force-delete'
    time.sleep(3)
deleted=[]
for w in json.loads((base/'wheel-manifest.json').read_text()):
    path=base/'wheelhouse'/w['filename']
    with path.open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()==w['sha256']
    path.unlink();deleted.append(w['filename'])
result={'completed_at':datetime.now(timezone.utc).isoformat(),'deleted_resources':owned,
        'remaining_owned_resources':0,'removed_local_wheels':deleted,'namespace_retained':True,
        'production_configuration_changed':False}
(base/'cleanup.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
