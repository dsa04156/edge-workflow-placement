"""Freeze public image bytes and CPU reference; never select on model output."""

from datetime import datetime,timezone
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import sys
import urllib.request

import numpy as np
import onnxruntime as ort
from PIL import Image
from probe import MODEL_SHA256,IMAGE_URL,IMAGE_SHA256
from transport import preprocess

base=Path('artifacts/e3-input-replication');base.mkdir(exist_ok=True)
assert not (base/'corpus.json').exists(),'Do not overwrite reference'
assets=base/'assets';(assets/'corpus').mkdir(parents=True,exist_ok=True)
notes=Path('docs/research/kubeedge-e3-corpus-notes.md').read_text()
expected=dict(re.findall(r'\| ([\w.]+) \| `([a-f0-9]{64})` \|',notes))
assert len(expected)==7
model=Path('artifacts/e2-dynamic/wheelhouse/model.onnx')
assert hashlib.sha256(model.read_bytes()).hexdigest()==MODEL_SHA256
shutil.copyfile(model,assets/'model.onnx')
shutil.copyfile('artifacts/e2-dynamic/wheelhouse/dog.jpg',assets/'dog.jpg')
options=ort.SessionOptions();options.intra_op_num_threads=options.inter_op_num_threads=1
session=ort.InferenceSession(str(model),options,providers=['CPUExecutionProvider'])
corpus={}
for filename,digest in {'dog.jpg':IMAGE_SHA256,**expected}.items():
    url=IMAGE_URL if filename=='dog.jpg' else 'https://raw.githubusercontent.com/scikit-image/scikit-image/e8a42ba85aaf5fd9322ef9ca51bc21063b22fcae/skimage/data/'+filename
    path=assets/'corpus'/filename
    if filename=='dog.jpg':data=(assets/'dog.jpg').read_bytes()
    else:
        with urllib.request.urlopen(url,timeout=30) as response:data=response.read(2000001)
    assert len(data)<=2000000 and hashlib.sha256(data).hexdigest()==digest,filename
    path.write_bytes(data)
    decoded=Image.open(io.BytesIO(data));tensor=preprocess(data)
    scores=session.run(None,{session.get_inputs()[0].name:tensor})[0].reshape(-1)
    assert scores.shape==(1000,) and np.isfinite(scores).all()
    image_id=filename.split('.')[0]
    corpus[image_id]={'image_id':image_id,'filename':filename,'url':url,'sha256':digest,'bytes':len(data),
        'width':decoded.width,'height':decoded.height,'mode':decoded.mode,'tensor_sha256':hashlib.sha256(tensor.tobytes()).hexdigest(),
        'reference_top5':np.argsort(scores)[-5:][::-1].tolist(),'reference_scores':scores.tolist()}
(base/'corpus.json').write_text(json.dumps(corpus,indent=2)+'\n')
(base/'reference-environment.json').write_text(json.dumps({'created_at':datetime.now(timezone.utc).isoformat(),
    'python':sys.version,'numpy':np.__version__,'onnxruntime':ort.__version__,'model_sha256':MODEL_SHA256,
    'purpose':'CPU numerical reference, not semantic ground truth'},indent=2)+'\n')
print(json.dumps({'images':len(corpus),'assets_bytes':sum(x['bytes'] for x in corpus.values()),'ids':list(corpus)}))
