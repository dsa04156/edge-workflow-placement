"""Kernel impairment and /32 bypass guards in explicitly owned Pod netns only."""
import ipaddress
import json
import os
import sys


def configure(spec):
    from pyroute2 import IPRoute
    if os.environ.get('RESEARCH_LINK_CONTROL') != 'owned-pod-netns':
        raise RuntimeError('Refusing unowned network namespace')
    def address(value):
        result = str(ipaddress.IPv4Address(value))
        if not result.startswith('10.'):
            raise ValueError('Only frozen cluster Pod IPs are allowed')
        return result
    with IPRoute() as ip:
        index = ip.link_lookup(ifname='eth0')[0]
        if spec['action'] in ('block', 'unblock'):
            for value in spec['destinations']:
                ip.route('add' if spec['action'] == 'block' else 'del',
                         dst=address(value) + '/32', type='blackhole', table=254)
        elif spec['action'] == 'shape':
            roots = [q for q in ip.get_qdiscs(index=index) if q['parent'] == 0xffffffff]
            if not all(q['handle'] in (0, 0x10000) for q in roots):
                raise RuntimeError('Unrelated root qdisc; refusing replacement')
            if any(q['handle'] == 0x10000 for q in roots):
                ip.tc('del', 'prio', index, '1:')
            rate, delay = spec.get('mbps'), spec.get('delay_ms', 0)
            if rate not in (None, 2, 10, 100) or delay not in (0, 20):
                raise ValueError('Unapproved impairment condition')
            if rate is not None or delay:
                ip.tc('add', 'prio', index, '1:', bands=3, priomap=[2]*16)
                for band, zone in ((1, 'cloud'), (2, 'edge')):
                    tbf, netem = f'{band}0:', f'{band}1:'
                    # netem leaf owns the packet queue. 64 packets is frozen;
                    # TCP retransmission/drop is measured rather than hidden.
                    ip.tc('add', 'tbf', index, tbf, parent=f'1:{band}',
                          rate=f'{rate or 10000}mbit', burst=4096, limit=131072)
                    ip.tc('add', 'netem', index, netem, parent=f'{band}0:1', delay=delay*1000, limit=64)
                    for priority, value in enumerate(spec[zone], band*100):
                        destination = address(value)
                        ip.tc('add-filter', 'u32', index, parent='1:', prio=priority, protocol=0x0800,
                              target=f'1:{band}', keys=[f'{hex(int(ipaddress.IPv4Address(destination)))}/0xffffffff+16'])
        elif spec['action'] != 'snapshot':
            raise ValueError('Unknown netns operation')
        return dict(netns=os.readlink('/proc/self/ns/net'), spec=spec,
            qdiscs=[dict(x) for x in ip.get_qdiscs(index=index)],
            filters=[dict(x) for x in ip.get_filters(index=index, parent=0x10000)],
            routes=[dict(x) for x in ip.get_routes(family=2, table=254)],
            links=[dict(x) for x in ip.get_links(index)])


if __name__ == '__main__':
    print(json.dumps(configure(json.load(sys.stdin)), default=str))
