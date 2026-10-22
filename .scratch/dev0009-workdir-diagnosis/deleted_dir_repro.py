"""Reproduce receipt mkdir against a directory whose inode was unlinked while still referenced (like a bind mount whose host source was removed)."""
import os, pathlib, traceback, json
d = '/work/deleted-dir-repro'
os.mkdir(d); os.chdir(d); os.rmdir(d)          # '.' now refers to an unlinked directory
s = os.stat('.')
print(json.dumps(dict(event='before_mkdir', nlink=s.st_nlink, is_dir=pathlib.Path('.').is_dir(), listing=os.listdir('.'))))
try:
    pathlib.Path('control-receipts').mkdir(parents=True, exist_ok=True, mode=0o700)
    print('RESULT green: mkdir succeeded')
except FileNotFoundError:
    traceback.print_exc(); print('RESULT red: FileNotFoundError in unlinked directory')
