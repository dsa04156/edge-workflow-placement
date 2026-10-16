"""Stage the exact diagnostic wheels, validating upstream SHA256 after transfer."""

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import time
from urllib.parse import unquote, urlparse
import urllib.request

from packaging.tags import cpython_tags, compatible_tags
from packaging.utils import parse_wheel_filename


base=Path('artifacts/e0-rpc-profile/wheelhouse');base.mkdir(exist_ok=True)
platforms=lambda arch:[f'manylinux_2_{n}_{arch}' for n in range(28,16,-1)]+[f'manylinux2014_{arch}',f'linux_{arch}']
tags={arch:set(cpython_tags((3,11),platforms=platforms(arch)))|set(compatible_tags((3,11),platforms=platforms(arch))) for arch in ['aarch64','x86_64']}
tokens=Path('artifacts/e0-accelerators/gpu-installed.txt').read_text().splitlines()
specs=[(*s.rsplit('-',1),'aarch64') for s in tokens if not s.startswith(('torch-','torchvision-'))]
specs += [(n,v,'x86_64') for n,v in [('numpy','2.2.6'),('onnxruntime','1.23.2'),('Pillow','11.3.0')]]


def metadata(spec):
    name,version,arch=spec
    with urllib.request.urlopen(f'https://pypi.org/pypi/{name}/{version}/json',timeout=30) as response:
        data=json.load(response)
    candidates=[]
    for f in data['urls']:
        if f['filename'].endswith('.whl'):
            _,_,_,wheel_tags=parse_wheel_filename(f['filename'])
            if wheel_tags & tags[arch]: candidates.append(f)
    assert candidates,(name,version,arch)
    f=sorted(candidates,key=lambda x:(arch not in x['filename'],x['filename']))[0]
    return {'name':name,'version':version,'filename':f['filename'],'url':f['url'],'size':f['size'],'sha256':f['digests']['sha256']}


with ThreadPoolExecutor(max_workers=6) as pool:
    wheels=list(pool.map(metadata,specs))
urls=json.loads(Path('artifacts/e0-accelerators/gpu-package-urls.json').read_text())
for name,url in urls.items():
    wheels.append({'name':name,'version':'2.9.1+cu130' if name=='torch' else '0.24.1',
                   'filename':unquote(Path(urlparse(url).path).name),'url':url.split('#')[0],
                   'sha256':url.split('sha256=')[1],'size':512551234 if name=='torch' else None})
for w in wheels:
    if w['size'] is None:
        with urllib.request.urlopen(urllib.request.Request(w['url'],method='HEAD'),timeout=30) as r:
            w['size']=int(r.headers['Content-Length'])
unique={w['filename']:w for w in wheels};wheels=list(unique.values())
Path('artifacts/e0-rpc-profile/wheel-manifest.json').write_text(json.dumps(wheels,indent=2)+'\n')
tasks=[]
for w in wheels:
    path=base/w['filename']
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()==w['sha256']: continue
    with path.open('wb') as f: f.truncate(w['size'])
    for start in range(0,w['size'],8*1024*1024):
        tasks.append((w,start,min(start+8*1024*1024,w['size'])-1))


def download(task):
    w,start,end=task
    for attempt in range(3):
        try:
            request=urllib.request.Request(w['url'],headers={'Range':f'bytes={start}-{end}'})
            with urllib.request.urlopen(request,timeout=40) as r:
                if r.status==206: assert r.headers['Content-Range']==f"bytes {start}-{end}/{w['size']}"
                else: assert start==0 and end==w['size']-1 and r.status==200
                offset=start
                with (base/w['filename']).open('r+b') as f:
                    while offset<=end:
                        data=r.read(min(1024*1024,end-offset+1))
                        assert data,'Unexpected EOF'
                        written=os.pwrite(f.fileno(),data,offset)
                        assert written==len(data)
                        offset+=len(data)
            return end-start+1
        except Exception:
            if attempt==2: raise
            time.sleep(1)


total=0
with ThreadPoolExecutor(max_workers=8) as pool:
    futures=[pool.submit(download,t) for t in tasks]
    for i,f in enumerate(as_completed(futures)):
        total+=f.result()
        if i%32==0: print(json.dumps({'chunks':i+1,'total_chunks':len(tasks),'MiB':round(total/1024**2)}),flush=True)
for w in wheels:
    with (base/w['filename']).open('rb') as f: digest=hashlib.file_digest(f,'sha256').hexdigest()
    assert digest==w['sha256'],w['filename']
print(json.dumps({'status':'all_upstream_checksums_verified','wheels':len(wheels),'bytes':sum(w['size'] for w in wheels)}),flush=True)
