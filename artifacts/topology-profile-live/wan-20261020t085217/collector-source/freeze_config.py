"""Verify and copy launch code into a Pod-private directory before readiness."""
import hashlib
import json
from pathlib import Path


def freeze_config(source, destination, expected):
    source,destination=Path(source),Path(destination)
    data={name:(source/name).read_bytes() for name in expected}
    if any(hashlib.sha256(content).hexdigest()!=expected[name] for name,content in data.items()):
        raise ValueError('Mounted launch code does not match frozen collector source')
    destination.mkdir(exist_ok=False)
    for name,content in data.items():
        if Path(name).name!=name:raise ValueError('Expected flat launch files')
        (destination/name).write_bytes(content)
    (destination/'code-freeze.json').write_text(json.dumps(expected,sort_keys=True))
    print(json.dumps(dict(event='code_frozen',directory=str(destination),sha256=expected)),flush=True)
