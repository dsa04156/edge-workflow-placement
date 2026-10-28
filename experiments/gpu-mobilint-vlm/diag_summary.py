"""Summarize a diag_probe run directory: per-step seconds plus memcg/PSI/dirty deltas over each step (read-only)."""
import json
from pathlib import Path
import sys


def pressure_delta(start, end, key, kind):
    try:
        return round((end[key][kind]['total'] - start[key][kind]['total'])/1e6, 3)  # usec -> s
    except (KeyError, TypeError):
        return None


def summarize(run_dir):
    health = json.loads((Path(run_dir)/'probe-health-final.json').read_text())
    rows = []
    for s in health['steps']:
        a, b = s['start'], s.get('end') or {}
        ms, me = a['mem_stat'], b.get('mem_stat', {})
        rows.append(dict(
            step=s['name'], seconds=s.get('seconds'), timed_out=s.get('timed_out'), rc=s.get('returncode'), MBps=s.get('MBps'),
            finished=s.get('finished'), mem_current_MiB=[round(int(a['mem_current'])/2**20), round(int(b['mem_current'])/2**20) if b else None],
            mem_max_MiB=round(int(a['mem_max'])/2**20) if a['mem_max'].isdigit() else a['mem_max'],
            file_dirty_MiB=[round(ms.get('file_dirty', 0)/2**20), round(me.get('file_dirty', 0)/2**20) if me else None],
            file_writeback_MiB=[round(ms.get('file_writeback', 0)/2**20), round(me.get('file_writeback', 0)/2**20) if me else None],
            node_dirty_MiB=[round(a['node_meminfo_kB'].get('Dirty', 0)/1024), round(b['node_meminfo_kB'].get('Dirty', 0)/1024) if b else None],
            pgscan=me.get('pgscan', 0) - ms.get('pgscan', 0) if me else None,
            refault_file=me.get('workingset_refault_file', 0) - ms.get('workingset_refault_file', 0) if me else None,
            pgmajfault=me.get('pgmajfault', 0) - ms.get('pgmajfault', 0) if me else None,
            mem_events_max=[a['mem_events'].get('max'), b.get('mem_events', {}).get('max')],
            mem_events_high=[a['mem_events'].get('high'), b.get('mem_events', {}).get('high')],
            memory_full_s=pressure_delta(a, b, 'mem_pressure', 'full') if b else None,
            memory_some_s=pressure_delta(a, b, 'mem_pressure', 'some') if b else None,
            io_full_s=pressure_delta(a, b, 'io_pressure', 'full') if b else None,
            io_some_s=pressure_delta(a, b, 'io_pressure', 'some') if b else None,
            throttled_s=round((b['cpu_stat'].get('throttled_usec', 0) - a['cpu_stat'].get('throttled_usec', 0))/1e6, 3) if b else None,
            stdout=(s.get('stdout_tail') or '').strip()[:80], stderr=(s.get('stderr_tail') or '').strip()[-200:]))
    return dict(phase=health['phase'], error=health.get('error'), config=health['config'], steps=rows)


if __name__ == '__main__':
    out = summarize(sys.argv[1])
    print(json.dumps(out, indent=1))
