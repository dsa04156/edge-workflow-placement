#!/usr/bin/env python3
"""Test-only `rtk` stand-in (installed as `rtk` on PATH). NOT a cluster; nothing here is cluster evidence.

Understands `rtk proxy kubectl --request-timeout=.. <args>` for the calls the collector makes before and
after a failed deployment: pod listings, create (server dry-run / real, UIDs assigned), wait (always fails:
no Pod becomes Ready), get, logs, events and `delete --raw` honouring DeleteOptions.preconditions.uid.
Every invocation is appended to FAKE_LOG. State lives in FAKE_STATE. Hooks: FAKE_PRESEED (JSON list of
objects present before the run), FAKE_REPLACE_ON_WAIT=1 (the waited Pod is replaced by a same-name foreign
object with a new UID), FAKE_PROTECTED_CHANGE=1 (the protected Pod is NotReady in later listings).
FAKE_READY=1 makes waits succeed and schedules created Pods on their nodeAffinity node (podIP assigned);
with it, FAKE_REPLACE_READY=<key> swaps Pod <run>-<key> for a Ready same-name foreign Pod (new UID) during
the wait, and FAKE_WRONG_NODE=<key> schedules our own Pod <run>-<key> on edge node e0. `exec` is recorded
(stdin consumed) and prints nothing.
"""
import json
import os
from pathlib import Path
import sys
import uuid

STATE = Path(os.environ['FAKE_STATE'])
NS = 'topology-profile-lab'


def load():
    if STATE.exists():
        return json.loads(STATE.read_text())
    state = dict(objects={}, listings=0)
    for item in json.loads(os.environ.get('FAKE_PRESEED', '[]')):
        state['objects'][item['kind']+'/'+item['metadata']['name']] = dict(item, metadata=dict(item['metadata'], uid=str(uuid.uuid4())))
    return state


def fail(state, message, code=1):
    STATE.write_text(json.dumps(state))
    sys.stderr.write(message+'\n')
    sys.exit(code)


def main():
    argv = sys.argv[1:]
    with open(os.environ['FAKE_LOG'], 'a') as log:
        log.write(json.dumps(argv)+'\n')
    if argv[:2] != ['proxy', 'kubectl']:
        sys.exit(2)
    args = [a for a in argv[2:] if not a.startswith('--request-timeout')]
    state = load()
    objects = state['objects']
    out = None
    if args == ['get', 'pods', '-A', '-o', 'json']:
        state['listings'] += 1
        ready = 'False' if os.environ.get('FAKE_PROTECTED_CHANGE') and state['listings'] > 1 else 'True'
        out = dict(items=[dict(metadata=dict(name='protected-canary', namespace='edge-recovery', uid='protected-1'),
                               spec=dict(nodeName='etri-ser0002-cgnmsb'),
                               status=dict(phase='Running', conditions=[dict(type='Ready', status=ready)]))])
    elif args[:2] == ['get', 'pods,configmaps']:
        run = next(a for a in args if a.startswith('run-id=')).split('=', 1)[1]
        out = dict(items=[o for o in objects.values() if o['metadata'].get('labels', {}).get('run-id') == run])
    elif args[0] == 'create':
        items = json.loads(sys.stdin.read())['items']
        if '--dry-run=server' in args:
            out = dict(kind='List', items=items)
        else:
            created = []
            for item in items:
                k = item['kind']+'/'+item['metadata']['name']
                if k in objects:
                    fail(state, f'Error from server (AlreadyExists): {k}')
                obj = dict(item, metadata=dict(item['metadata'], uid=str(uuid.uuid4()), creationTimestamp='2026-10-23T00:00:00Z'))
                if os.environ.get('FAKE_READY') and item['kind'] == 'Pod':
                    terms = item['spec']['affinity']['nodeAffinity']['requiredDuringSchedulingIgnoredDuringExecution']['nodeSelectorTerms']
                    wrong = os.environ.get('FAKE_WRONG_NODE')
                    node = 'etri-dev0003-raspi5' if wrong and item['metadata']['name'].endswith('-'+wrong) else terms[0]['matchFields'][0]['values'][0]
                    obj['spec'] = dict(item['spec'], nodeName=node)
                    obj['status'] = dict(phase='Running', podIP=f'10.0.0.{len(objects)+1}')
                objects[k] = obj
                created.append(obj)
            out = dict(kind='List', items=created)
    elif args[0] == 'wait' and os.environ.get('FAKE_READY'):
        key = os.environ.get('FAKE_REPLACE_READY')
        for k, obj in list(objects.items()):
            if key and k.startswith('Pod/') and k.endswith('-'+key):
                objects[k] = dict(obj, metadata=dict(name=obj['metadata']['name'], namespace=NS, uid=str(uuid.uuid4()),
                                                     labels=dict(experiment='someone-else')))
        out = ''
    elif args[0] == 'exec':
        sys.stdin.buffer.read()
        out = ''
    elif args[0] == 'wait':
        name = args[4]  # wait pod -n NS NAME ...
        if os.environ.get('FAKE_REPLACE_ON_WAIT') and 'Pod/'+name in objects:
            objects['Pod/'+name] = dict(objects['Pod/'+name], metadata=dict(name=name, namespace=NS, uid=str(uuid.uuid4()),
                                                                              labels=dict(experiment='someone-else')))
        fail(state, 'error: timed out waiting for the condition')
    elif args[0] == 'get' and args[1] in ('pod', 'configmap'):
        k = ('Pod/' if args[1] == 'pod' else 'ConfigMap/')+args[4]  # get pod -n NS NAME -o json
        if k not in objects:
            fail(state, f'Error from server (NotFound): {k} not found')
        out = objects[k]
    elif args[:2] == ['get', 'events']:
        out = dict(items=[])
    elif args[0] == 'logs':
        out = ''
    elif args[:2] == ['delete', '--raw']:
        plural, name = args[2].split('/')[-2:]
        k = ('Pod/' if plural == 'pods' else 'ConfigMap/')+name
        want = json.loads(sys.stdin.read()).get('preconditions', {}).get('uid')
        if k not in objects:
            fail(state, f'Error from server (NotFound): {k}')
        if want != objects[k]['metadata']['uid']:
            fail(state, f'Error from server (Conflict): Precondition failed: UID in precondition: {want}')
        del objects[k]
        out = dict(kind='Status', status='Success')
    else:
        fail(state, 'fake rtk: unsupported '+json.dumps(args), 2)
    STATE.write_text(json.dumps(state))
    sys.stdout.write(out if isinstance(out, str) else json.dumps(out))


if __name__ == '__main__':
    main()
