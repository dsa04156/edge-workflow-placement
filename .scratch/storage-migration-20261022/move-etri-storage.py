import pathlib,subprocess,os,sys,json,shutil,time
src=pathlib.Path(sys.argv[1]); allowed={pathlib.Path("/home/etri/Downloads"),pathlib.Path("/home/etri/nvidia/nvidia_sdk")}
assert src in allowed and src.is_dir() and not src.is_symlink()
assert subprocess.check_output(["findmnt","-n","-o","UUID","-T","/mnt/data3tb"],text=True).strip()=="c04a3ad2-464d-4c86-b108-739be080acc3"
base=pathlib.Path("/mnt/data3tb/etri-storage");base.mkdir(exist_ok=True)
st=os.stat("/home/etri");os.chown(base,st.st_uid,st.st_gid);os.chmod(base,0o755)
dst=base/src.relative_to("/home/etri");dst.parent.mkdir(parents=True,exist_ok=True)
if dst.parent!=base:os.chown(dst.parent,st.st_uid,st.st_gid)
assert not dst.exists();dst.mkdir()
print("COPY",src,"to",dst,flush=True)
subprocess.run(["rsync","-aHAXS","--numeric-ids",str(src)+"/",str(dst)+"/"],check=True)
print("VERIFY",src,flush=True)
r=subprocess.run(["rsync","-aHAXSnc","--numeric-ids","--delete","--itemize-changes",str(src)+"/",str(dst)+"/"],check=True,capture_output=True,text=True)
assert not r.stdout.strip(),r.stdout
# Refuse cutover if any process has a file or cwd open beneath the source.
for proc in pathlib.Path("/proc").iterdir():
 if not proc.name.isdigit():continue
 try: entries=list((proc/"fd").iterdir())+[proc/"cwd"]
 except OSError:continue
 for e in entries:
  try:t=os.readlink(e)
  except OSError:continue
  assert not (t==str(src) or t.startswith(str(src)+"/")),("OPEN",proc.name,t)
backup=src.with_name(src.name+".ssd-migration-original");assert not backup.exists()
st=src.stat();src.rename(backup)
try:src.symlink_to(dst,target_is_directory=True);os.lchown(src,st.st_uid,st.st_gid)
except BaseException:
 if src.is_symlink():src.unlink()
 backup.rename(src);raise
assert src.resolve()==dst and os.stat(src).st_dev==os.stat(base).st_dev
shutil.rmtree(backup)
print(json.dumps({"source":str(src),"destination":str(dst),"verified":"full checksum, ownership, ACLs, xattrs; sparse copy","completed":time.strftime("%Y-%m-%dT%H:%M:%S%z")}),flush=True)
