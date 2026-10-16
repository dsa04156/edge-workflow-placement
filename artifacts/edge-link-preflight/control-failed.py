"""Destination-filtered qdisc control inside an explicitly owned research Pod only."""

import ipaddress
import json
import os
from pathlib import Path
import sys


def configure(destinations,mbps=None,delay_ms=0):
    from pyroute2 import IPRoute
    from pyroute2.netlink.exceptions import NetlinkError
    assert os.environ.get('RESEARCH_LINK_CONTROL')=='owned-pod-netns'
    destinations=[str(ipaddress.IPv4Address(x)) for x in destinations]
    assert destinations and all(x.startswith('10.') for x in destinations)
    assert mbps in [None,20,50] and delay_ms in [0,15]
    with IPRoute() as ip:
        index=ip.link_lookup(ifname='eth0')[0]
        before=[dict(x) for x in ip.get_qdiscs(index=index)]
        # Refuse replacing an unrelated root qdisc. Only our handle and default noqueue are allowed.
        roots=[x for x in before if x['parent']==0xffffffff]
        assert all(x['handle'] in [0,0x10000] for x in roots),roots
        if any(x['handle']==0x10000 for x in roots):ip.tc('del','prio',index,'1:')
        if mbps is not None or delay_ms:
            ip.tc('add','prio',index,'1:',bands=3,priomap=[2]*16)
            args={'parent':'1:1','delay':delay_ms*1000,'limit':10000}
            if mbps is not None:args['rate']=f'{mbps}mbit'
            ip.tc('add','netem',index,'10:',**args)
            for priority,destination in enumerate(destinations,1):
                ip.tc('add-filter','u32',index,parent='1:',prio=priority,protocol=0x0800,
                      target='1:1',keys=[f'{hex(int(ipaddress.IPv4Address(destination)))}/0xffffffff+16'])
        return {'netns':os.readlink('/proc/self/ns/net'),'destinations':destinations,'mbps':mbps,'delay_ms':delay_ms,
                'before':before,'after':[dict(x) for x in ip.get_qdiscs(index=index)],
                'filters':[dict(x) for x in ip.get_filters(index=index,parent=0x10000)]}


if __name__=='__main__':
    spec=json.load(sys.stdin)
    print(json.dumps(configure(**spec),default=str))
