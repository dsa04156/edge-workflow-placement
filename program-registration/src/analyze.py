"""Summarize all real-system attempts, retaining failures and mismatches."""
import argparse,collections,json,math,statistics
from pathlib import Path

def summarize(rows):
    groups=collections.defaultdict(list);seen=set()
    for r in rows:
        key=(r['run_id'],r['request_id'])
        if key in seen: raise ValueError('Duplicate request identity')
        seen.add(key)
        if not isinstance(r['warmup'],bool):raise ValueError('warmup must be bool')
        if r['warmup']:continue
        groups[(r['condition'],r['block'],r['policy'])].append(r)
    out=[]
    for key,rs in sorted(groups.items()):
        valid=[]
        for r in rs:
            if r['status']=='ok' and r['quality_ok'] is True:
                ms=r['e2e_ms']
                if not isinstance(ms,(int,float)) or not math.isfinite(ms) or ms<0:raise ValueError('Invalid latency')
                valid.append(ms)
        vals=sorted(valid);pos=(len(vals)-1)*.95 if vals else 0;i=int(pos)
        p95=vals[i]+(vals[min(i+1,len(vals)-1)]-vals[i])*(pos-i) if vals else None
        out.append(dict(zip(['condition','block','policy'],key),attempts=len(rs),valid=len(vals),
            failures=sum(r['status']!='ok' for r in rs),quality_failures=sum(r['status']=='ok' and r['quality_ok'] is not True for r in rs),
            mean_success_ms=statistics.mean(vals) if vals else None,p95_success_ms=p95))
    return out

def main():
    p=argparse.ArgumentParser();p.add_argument('input');p.add_argument('--out',required=True);a=p.parse_args()
    rows=[json.loads(l) for l in Path(a.input).read_text().splitlines() if l.strip()]
    result={'unit':'condition x independent block x policy','warning':'Success-only latency; compare failures too. No request-level independence or significance claim.', 'groups':summarize(rows)}
    with open(a.out,'x') as f:json.dump(result,f,indent=2)
if __name__=='__main__':main()
