"""Post-measurement read-only EdgeCore/process and NIC link snapshot."""

from datetime import datetime,timezone
import json
from pathlib import Path

processes=[]
for directory in Path('/host/proc').glob('[0-9]*'):
    try:
        if (directory/'comm').read_text().strip()!='edgecore':continue
        status=(directory/'status').read_text().splitlines()
        processes.append({'pid':int(directory.name),**{line.split(':',1)[0]:line.split(':',1)[1].strip()
                         for line in status if line.startswith(('State:','VmRSS:','Threads:'))}})
    except (OSError,ValueError):continue
interfaces={}
for interface in Path('/host/sys/class/net').iterdir():
    properties={}
    for attribute in ['operstate','speed','duplex','carrier']:
        try:properties[attribute]=(interface/attribute).read_text().strip()
        except OSError:properties[attribute]=None
    interfaces[interface.name]=properties
print(json.dumps({'observed_at':datetime.now(timezone.utc).isoformat(),'edgecore_processes':processes,
                  'interfaces':interfaces,'scope':'post_measurement_link_negotiation_not_effective_throughput_or_packet_path'}),flush=True)
