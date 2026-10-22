"""Exec-side probe: compare /work as seen by this exec vs PID 1 of the same container, then mkdir test."""
import json, os, sys, uuid
def st(p):
    try:
        s = os.lstat(p); return dict(ino=s.st_ino, dev=s.st_dev, nlink=s.st_nlink, mode=oct(s.st_mode), uid=s.st_uid, gid=s.st_gid, islink=os.path.islink(p))
    except OSError as e: return repr(e)
def mi(pid):
    try: return [l.strip() for l in open(f'/proc/{pid}/mountinfo') if ' /work ' in l]
    except OSError as e: return repr(e)
def ns():
    try: return [os.readlink('/proc/self/ns/mnt'), os.readlink('/proc/1/ns/mnt')]
    except OSError as e: return repr(e)
def ls(p):
    try: return sorted(os.listdir(p))
    except OSError as e: return repr(e)
out = dict(label=sys.argv[1] if len(sys.argv) > 1 else '', pid=os.getpid(), uid=os.getuid(),
           exec_work=st('/work'), pid1_work=st('/proc/1/root/work'), readlink=os.path.realpath('/work'),
           exec_mountinfo=mi('self'), pid1_mountinfo=mi(1), same_mntns=ns(),
           listing=ls('/work'), netctl_ready=st('/work/netctl-ready'))
d = '/work/control-receipts-diag-' + uuid.uuid4().hex[:6]
try: os.mkdir(d, 0o700); out['mkdir'] = 'ok:' + d; os.rmdir(d)
except OSError as e: out['mkdir'] = repr(e)
print(json.dumps(out))
