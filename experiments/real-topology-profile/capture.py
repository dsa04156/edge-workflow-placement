"""Read-only Kubernetes snapshot, keeping partial command failures as evidence."""
import argparse,datetime,json,subprocess
from pathlib import Path

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--namespace',required=True);ap.add_argument('--run-id',required=True);ap.add_argument('--out',required=True);a=ap.parse_args()
    dest=Path(a.out);dest.mkdir(parents=True,exist_ok=False)
    commands={'version':['version','-o','json'],'nodes':['get','nodes','-o','json'],
        'pods':['get','pods','-n',a.namespace,'-l','run-id='+a.run_id,'-o','json'],
        'events':['get','events','-n',a.namespace,'-o','json']}
    failed=[]
    for key,args in commands.items():
        cmd=['kubectl',*args];record={'command':cmd,'started':datetime.datetime.now(datetime.timezone.utc).isoformat()}
        try:
            result=subprocess.run(cmd,capture_output=True,text=True,timeout=60)
            record.update(returncode=result.returncode,stdout=result.stdout,stderr=result.stderr)
            if result.returncode:failed.append(key)
        except (subprocess.TimeoutExpired,OSError) as e:
            def text(v):return v.decode(errors='replace') if isinstance(v,bytes) else v
            record.update(error=str(e),stdout=text(getattr(e,'stdout',None)),stderr=text(getattr(e,'stderr',None)));failed.append(key)
        record['finished']=datetime.datetime.now(datetime.timezone.utc).isoformat();(dest/(key+'.json')).write_text(json.dumps(record,indent=2))
    if failed:raise SystemExit('Snapshot incomplete: '+','.join(failed))
if __name__=='__main__':main()
