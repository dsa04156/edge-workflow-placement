#!/usr/bin/env python3
"""Test-only kubectl stand-in. Each applied Pod becomes a real local stage_runtime process on its
own loopback IP; exec runs the client argv locally. State lives in $FAKE_KUBE_STATE.
Not a cluster: it proves runner I/O, ownership and cleanup wiring only.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request
import uuid

HERE = Path(__file__).resolve().parent
STATE = Path(os.environ['FAKE_KUBE_STATE'])
PORT = int(os.environ['FAKE_KUBE_PORT'])
SCHED = json.loads(os.environ.get('FAKE_SCHED', '{}'))
# Must equal run.PROTECTED_JSONPATH; any other -A output stays raw JSON so a regression leaks visibly.
PROTECTED_JSONPATH = ('jsonpath={range .items[*]}{.metadata.uid}{"\\t"}{.metadata.namespace}{"\\t"}'
                      '{.metadata.name}{"\\t"}{.status.phase}{"\\t"}'
                      '{.status.conditions[?(@.type=="Ready")].status}{"\\n"}{end}')
PROTECTED = [{'metadata': {'uid': 'protected-1', 'name': 'mapper', 'namespace': 'edgeai-devices'},
              'spec': {'containers': [{'name': 'mapper', 'env': [
                  {'name': 'MQTT_PASSWORD', 'value': os.environ.get('FAKE_PROTECTED_SECRET', 'unset')}]}]},
              'status': {'phase': 'Running', 'conditions': [{'type': 'Ready', 'status': 'True'}]}}]


def load():
    path = STATE / 'state.json'
    return json.loads(path.read_text()) if path.exists() else {'pods': {}, 'configmaps': {}, 'next_ip': 20,
                                                               'execs': 0, 'deleted': [], 'calls': []}


def save(state):
    (STATE / 'state.json').write_text(json.dumps(state, indent=1))


def option(args, name):
    for i, arg in enumerate(args):
        if arg == name:
            return args[i + 1]
        if arg.startswith(name + '='):
            return arg.split('=', 1)[1]
    return None


def positional(args):
    out, skip = [], False
    for arg in args[1:]:
        if skip:
            skip = False
        elif arg in ('-n', '-o', '-l'):
            skip = True
        elif not arg.startswith('-'):
            out.append(arg)
    return out


def selected(state, args):
    label = option(args, '-l')
    pods = list(state['pods'].values())
    if label:
        terms = [term.split('=', 1) for term in label.split(',')]  # equality terms only, AND-ed
        pods = [p for p in pods if all(p['metadata']['labels'].get(k) == v for k, v in terms)]
    return pods


def healthy(pod):
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                f'http://{pod["status"]["podIP"]}:{PORT}/health', timeout=1) as response:
            return response.status == 200
    except OSError:
        return False


def pod_candidates(item):
    """OR of nodeSelectorTerms; like the API server, matchFields metadata.name In must hold exactly one value."""
    terms = item['spec']['affinity']['nodeAffinity']['requiredDuringSchedulingIgnoredDuringExecution'][
        'nodeSelectorTerms']
    candidates = []
    for term in terms:
        for field in term['matchFields']:
            if field['key'] == 'metadata.name' and len(field['values']) != 1:
                raise ValueError(f'Pod "{item["metadata"]["name"]}" is invalid: must have one element')
            candidates.extend(field['values'])
    return candidates


def apply(state, items):
    for item in items:
        name = item['metadata']['name']
        if item['kind'] == 'ConfigMap':
            root = STATE / 'config' / name
            root.mkdir(parents=True)
            for key, text in item['data'].items():
                (root / key).write_text(text)
            state['configmaps'][name] = item
            continue
        candidates = pod_candidates(item)
        role = next(e['value'] for e in item['spec']['containers'][0]['env'] if e['name'] == 'TASK_ID')
        node = candidates[0] if len(candidates) == 1 else SCHED.get(role, candidates[0])
        ip = f'127.0.0.{state["next_ip"]}'
        state['next_ip'] += 1
        uid = str(uuid.uuid4())
        cm = item['spec']['volumes'][0]['configMap']['name']
        env = dict(os.environ, TASK_ID=role, NODE_NAME=node, POD_UID=uid, CONFIG_ROOT=str(STATE / 'config' / cm),
                   BIND_HOST=ip, PORT=str(PORT))
        log = (STATE / (name + '.log')).open('wb')
        proc = subprocess.Popen([sys.executable, str(STATE / 'config' / cm / 'stage_runtime.py'), 'serve'], env=env,
                                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        stamp = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        pod = json.loads(json.dumps(item))
        pod['metadata'].update(uid=uid, creationTimestamp=stamp)
        pod['spec']['nodeName'] = node
        if os.environ.get('FAKE_ADMIT') == 'privileged-sidecar':  # admission mutation the runner must refuse
            pod['spec']['containers'].append({'name': 'injected', 'image': 'example/sidecar:1',
                                              'securityContext': {'privileged': True}, 'env': [
                                                  {'name': 'TOKEN', 'value': os.environ.get('FAKE_PROTECTED_SECRET')}]})
        pod['status'] = {'phase': 'Running', 'podIP': ip, 'conditions': [
            {'type': 'PodScheduled', 'status': 'True', 'lastTransitionTime': stamp},
            {'type': 'Ready', 'status': 'True', 'lastTransitionTime': stamp}]}
        pod['fake_pid'] = proc.pid
        state['pods'][name] = pod


def main(args):
    while args and args[0].startswith('--request-timeout'):
        args = args[1:]
    state = load()
    state['calls'].append(args)
    stdin = sys.stdin.read() if '-f' in args or args[:2] == ['exec', '-i'] else ''
    verb = args[0]
    try:
        if verb == 'apply':
            items = json.loads(stdin)['items']
            try:
                for item in items:
                    if item['kind'] == 'Pod':
                        pod_candidates(item)
            except ValueError as error:
                print('The Pod is invalid: ' + str(error), file=sys.stderr)
                return 1
            if not any(a.startswith('--dry-run') for a in args):
                apply(state, items)
            print('applied', len(items))
        elif verb == 'wait':
            if '--for=delete' in args:
                if selected(state, args):
                    print('still present', file=sys.stderr)
                    return 1
                return 0
            pods = selected(state, args) if option(args, '-l') else [state['pods'][positional(args)[1]]]
            if not pods:
                print('error: no matching resources found', file=sys.stderr)
                return 1
            deadline = time.monotonic() + 15
            while not all(healthy(p) for p in pods):
                if time.monotonic() > deadline:
                    print('timed out', file=sys.stderr)
                    return 1
                time.sleep(0.05)
            print('condition met')
        elif verb == 'get':
            kinds = args[1]
            if '-A' in args:
                pods = PROTECTED + list(state['pods'].values())
                if option(args, '-o') != PROTECTED_JSONPATH:
                    print(json.dumps({'items': pods}))
                    return 0
                for p in pods:  # what real kubectl prints for that template; missing keys print empty
                    ready = next((c['status'] for c in p.get('status', {}).get('conditions', [])
                                  if c['type'] == 'Ready'), '')
                    print('\t'.join((p['metadata']['uid'], p['metadata']['namespace'], p['metadata']['name'],
                                     p.get('status', {}).get('phase', ''), ready)))
            elif kinds == 'events':
                print(json.dumps({'items': [{'involvedObject': {'name': n, 'kind': 'Pod'}, 'reason': 'Scheduled',
                                             'message': f'Successfully assigned {n} to {p["spec"]["nodeName"]}',
                                             'reportingComponent': 'default-scheduler', 'type': 'Normal',
                                             'source': {'component': 'default-scheduler'}}
                                            for n, p in state['pods'].items()]}))
            elif positional(args)[1:]:
                print(json.dumps(state['pods'][positional(args)[1]]))
            else:
                items = selected(state, args)
                if 'configmaps' in kinds:
                    label = dict(t.split('=', 1) for t in option(args, '-l').split(','))['run-id']
                    items += [c for c in state['configmaps'].values() if c['metadata']['labels']['run-id'] == label]
                print(json.dumps({'items': items}))
        elif verb == 'logs':
            print((STATE / (args[-1] + '.log')).read_text())
        elif verb == 'exec':
            state['execs'] += 1
            if state['execs'] == int(os.environ.get('FAKE_FAIL_EXEC', '0')):
                print('error: unable to upgrade connection', file=sys.stderr)
                return 1
            name = args[args.index('-n') + 2]
            cm = state['pods'][name]['spec']['volumes'][0]['configMap']['name']
            command = args[args.index('--') + 1:]
            command = [sys.executable if c == 'python3' else c.replace('/config/', str(STATE / 'config' / cm) + '/')
                       for c in command]
            return subprocess.run(command, input=stdin.encode()).returncode
        elif verb == 'delete':
            for item in json.loads(stdin)['items']:
                name = item['metadata']['name']
                if item['kind'] == 'Pod' and name in state['pods']:
                    try:
                        os.killpg(state['pods'].pop(name)['fake_pid'], signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                elif item['kind'] == 'ConfigMap':
                    state['configmaps'].pop(name, None)
                state['deleted'].append([item['kind'], name])
            print('deleted')
        else:
            print('unsupported ' + verb, file=sys.stderr)
            return 1
        return 0
    finally:
        save(state)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
