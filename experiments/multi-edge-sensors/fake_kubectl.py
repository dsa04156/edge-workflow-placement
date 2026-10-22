#!/usr/bin/env python3
"""Test-only kubectl stand-in (installed as `kubectl` on PATH). NOT a cluster.

Every applied Pod becomes a real local runtime.py process bound to its own
loopback IP (real MediaMTX, PyAV RTSP, ONNX Runtime models); `exec` runs the
argv locally with the Pod env, mapping /config and /work to per-Pod dirs.
Bundles uploaded to the cache Pod are extracted by the fake in place of the
in-Pod bootstrap pip install; the Python packages come from FAKE_PYTHON's env.
Nodes come from FAKE_NODES; FAKE_FAIL maps node -> waiting reason (never Ready); FAKE_CORRUPT_NODE
gets reversed class names and a hash-changed YOLO file in its worker Pods (wrong outputs/provenance);
FAKE_SCHED maps component -> node for unpinned Pods. State in FAKE_KUBE_STATE.
Ownership hooks: FAKE_PRESEED is a JSON list of foreign objects present before the run;
FAKE_ON_CREATE is {"trigger": name, "target": name, "action": "replace"|"inject", "real_only": bool}:
when a create names the trigger, the target is replaced (new UID, foreign labels) or injected
(a foreign object with that name appears) first. `create` is create-only (AlreadyExists per item,
other items still created) and `delete --raw` honours DeleteOptions.preconditions.uid (409 Conflict).
FAKE_TRUNCATE_CREATE {"trigger": name}: that real create succeeds but its stdout stops inside the second object.
All numbers produced through this fake are offline/local, never cluster evidence.
"""
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import urllib.request
import uuid

STATE = Path(os.environ['FAKE_KUBE_STATE'])
PYTHON = os.environ['FAKE_PYTHON']
NODES = json.loads(Path(os.environ['FAKE_NODES']).read_text())
FAIL = json.loads(os.environ.get('FAKE_FAIL', '{}'))
SCHED = json.loads(os.environ.get('FAKE_SCHED', '{}'))
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Locked:
    def __enter__(self):
        STATE.mkdir(parents=True, exist_ok=True)
        self.handle = (STATE/'lock').open('w')
        fcntl.flock(self.handle, fcntl.LOCK_EX)
        path = STATE/'state.json'
        if path.exists():
            self.state = json.loads(path.read_text())
        else:
            self.state = dict(pods={}, configmaps={}, next_ip=10, calls=[])
            for item in json.loads(os.environ.get('FAKE_PRESEED', '[]')):
                foreign(self.state, item)
        return self.state

    def __exit__(self, *exc):
        (STATE/'state.json').write_text(json.dumps(self.state, indent=1))
        fcntl.flock(self.handle, fcntl.LOCK_UN)
        self.handle.close()


def foreign(state, item):
    """A non-run object (no process): preseeded, injected or replacing a run object."""
    obj = dict(item, metadata=dict(item['metadata'], uid=str(uuid.uuid4())))
    if item['kind'] == 'Pod':
        obj.update(spec=dict(item.get('spec', {}), nodeName=item.get('spec', {}).get('nodeName', 'foreign-node')),
                   status=dict(phase='Running', podIP='127.9.9.9', conditions=[]), fake=dict(env={}, pid=None, root=str(STATE/'foreign')))
        state['pods'][item['metadata']['name']] = obj
    else:
        state['configmaps'][item['metadata']['name']] = obj


def on_create(state, names, dry_run):
    hook = json.loads(os.environ.get('FAKE_ON_CREATE', '{}'))
    if not hook or hook['trigger'] not in names or (dry_run and hook.get('real_only')) or state.get('hook_fired'):
        return
    state['hook_fired'] = True
    target = hook['target']
    if hook['action'] == 'replace':
        obj = state['pods'].get(target) or state['configmaps'][target]
        obj['metadata'] = dict(obj['metadata'], uid=str(uuid.uuid4()), labels=dict(experiment='someone-else'))
    else:
        foreign(state, dict(apiVersion='v1', kind='Pod', metadata=dict(name=target, namespace='topology-profile-lab',
                                                                       labels=dict(experiment='someone-else'))))


def option(args, *names):
    for i, arg in enumerate(args):
        for name in names:
            if arg == name:
                return args[i+1]
            if arg.startswith(name+'='):
                return arg.split('=', 1)[1]
    return None


def positional(args):
    out, skip = [], False
    for arg in args:
        if skip:
            skip = False
        elif arg in ('-n', '-o', '-l', '-f', '-c'):
            skip = True
        elif not arg.startswith('-'):
            out.append(arg)
    return out


def labels_match(meta, selector):
    if not selector:
        return True
    return all(meta.get('labels', {}).get(k) == v for k, v in (part.split('=', 1) for part in selector.split(',')))


def pod_dir(name):
    return STATE/'pods'/name


def choose_node(spec, component):
    terms = spec['affinity']['nodeAffinity']['requiredDuringSchedulingIgnoredDuringExecution']['nodeSelectorTerms']
    candidates = [v for t in terms for e in t['matchFields'] for v in e['values']]
    if len(candidates) == 1:
        return candidates[0]
    return SCHED.get(component, candidates[0])


def start(state, item):
    name = item['metadata']['name']
    ip = f"127.0.{state['next_ip'] // 250 + 1}.{state['next_ip'] % 250 + 2}"
    state['next_ip'] += 1
    node = choose_node(item['spec'], item['metadata']['labels'].get('component', ''))
    uid = str(uuid.uuid4())
    root = pod_dir(name)
    (root/'work/tmp').mkdir(parents=True)
    container = item['spec']['containers'][0]
    env = {e['name']: e.get('value') for e in container.get('env', []) if 'value' in e}
    env.update(NODE_NAME=node, POD_UID=uid, POD_IP=ip, BIND_IP=ip, ASSET_ROOT=str(root/'work'), TMPDIR=str(root/'work/tmp'))
    pod = dict(item, metadata=dict(item['metadata'], uid=uid, creationTimestamp=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())),
               spec=dict(item['spec'], nodeName=node), status=dict(phase='Pending', podIP=ip, conditions=[dict(type='PodScheduled', status='True')]),
               fake=dict(env=env, pid=None, root=str(root)))
    for volume in item['spec'].get('volumes', []):
        if 'configMap' in volume:
            config = state['configmaps'][volume['configMap']['name']]
            (root/'config').mkdir()
            for key, value in config['data'].items():
                (root/'config'/key).write_text(value)
    if node in FAIL:
        pod['status'].update(phase='Pending', containerStatuses=[dict(name='worker', ready=False, state=dict(waiting=dict(reason=FAIL[node])))])
        state['pods'][name] = pod
        return
    role = env.get('ROLE')
    log = (root/'log.txt').open('ab')
    if role is None:  # cache Pod: static file server over its emptyDir
        argv = [PYTHON, '-u', '-m', 'http.server', str(port(pod)), '--bind', ip, '--directory', str(root/'work')]
    else:
        # Stand-in for bootstrap.py: extract this role's bundles from the cache Pod dir, no pip.
        sys.path.insert(0, str(root/'config'))
        import importlib
        bootstrap = importlib.import_module('bootstrap')
        sys.path.pop(0)
        manifest = json.loads((root/'config/bundles.json').read_text())
        for template in bootstrap.ROLE_BUNDLES[role]:
            cache = next(p for p in state['pods'].values() if p['metadata']['labels'].get('component') == 'cache')
            bundle = Path(cache['fake']['root'])/'work'/template.format(arch='x86_64')
            assert manifest[bundle.name]['sha256'] == __import__('hashlib').sha256(bundle.read_bytes()).hexdigest()
            with tarfile.open(bundle) as archive:
                archive.extractall(root/'work', filter='data')
        if node == os.environ.get('FAKE_CORRUPT_NODE') and (root/'work/models/class-names.json').exists():
            # Test-only broken node: permuted class names make its V/T/F outputs numerically wrong.
            names = json.loads((root/'work/models/class-names.json').read_text())
            (root/'work/models/class-names.json').write_text(json.dumps(names[::-1]))
            # And a different (still loadable) model file: an unknown protobuf field changes its hash only.
            with (root/'work/models/yolo11n.onnx').open('ab') as model:
                model.write(bytes([0xc0, 0x3e, 0x01]))
        if (root/'work/mediamtx/mediamtx').exists():
            (root/'work/mediamtx/mediamtx').chmod(0o755)
        argv = [PYTHON, '-u', str(root/'config/runtime.py'), 'serve']
    process = subprocess.Popen(argv, cwd=str(root/'work'), env=dict(os.environ, **env), stdout=log, stderr=subprocess.STDOUT,
                               start_new_session=True)
    pod['fake']['pid'] = process.pid
    pod['status']['phase'] = 'Running'
    state['pods'][name] = pod


def port(pod):
    return pod['spec']['containers'][0]['readinessProbe']['httpGet']['port']


def ready(pod):
    if pod['spec']['nodeName'] in FAIL or not pod['fake']['pid']:
        return False
    path = '/' if pod['fake']['env'].get('ROLE') is None else '/health'
    try:
        with OPENER.open(f"http://{pod['status']['podIP']}:{port(pod)}{path}", timeout=2) as response:
            return response.status == 200
    except Exception:
        return False


def view(pod):
    out = {k: v for k, v in pod.items() if k != 'fake'}
    ok = ready(pod)
    out['status'] = dict(pod['status'], conditions=pod['status']['conditions'] + [dict(type='Ready', status='True' if ok else 'False')])
    if ok:
        out['status']['containerStatuses'] = [dict(name='worker', ready=True, state=dict(running={}))]
    return out


def stop(pod):
    if pod['fake']['pid']:
        try:
            os.killpg(pod['fake']['pid'], signal.SIGTERM)
        except ProcessLookupError:
            pass


def main(args):
    args = [a for a in args if not a.startswith('--request-timeout')]
    verb = args[0]
    with Locked() as state:
        state['calls'].append(args[:3])
    if verb == 'apply':
        items = json.loads(sys.stdin.read())['items']
        if '--dry-run=server' in args:
            return 0
        with Locked() as state:
            for item in items:
                if item['kind'] == 'ConfigMap':
                    state['configmaps'][item['metadata']['name']] = item
            for item in items:
                if item['kind'] == 'Pod':
                    start(state, item)
        return 0
    if verb == 'create':
        items = json.loads(sys.stdin.read())['items']
        dry = '--dry-run=server' in args
        failed = 0
        truncate = json.loads(os.environ.get('FAKE_TRUNCATE_CREATE', '{}')).get('trigger') in [i['metadata']['name'] for i in items]
        printed = []
        with Locked() as state:
            on_create(state, [i['metadata']['name'] for i in items], dry)
            for item in items:
                name, store = item['metadata']['name'], ('pods' if item['kind'] == 'Pod' else 'configmaps')
                if name in state[store]:
                    sys.stderr.write(f'Error from server (AlreadyExists): {store} "{name}" already exists\n')
                    failed += 1
                    continue
                if dry:
                    print(json.dumps(item))
                    continue
                if item['kind'] == 'ConfigMap':
                    state['configmaps'][name] = dict(item, metadata=dict(item['metadata'], uid=str(uuid.uuid4())))
                    printed.append(json.dumps(state['configmaps'][name]))
                else:
                    start(state, item)
                    printed.append(json.dumps(view(state['pods'][name])))
        if truncate and not dry:
            # Every object was created, but stdout is cut inside the second document (rc 0).
            sys.stdout.write(printed[0]+'\n'+printed[1][:200])
            return 0
        for line in printed:
            print(line)
        return 1 if failed else 0
    if verb == 'delete' and option(args, '--raw'):
        uri = option(args, '--raw')
        path, _, query = uri.partition('?')
        parts = path.strip('/').split('/')  # api v1 namespaces NS pods|configmaps NAME
        store, name = parts[4], parts[5]
        body = json.loads(sys.stdin.read() or '{}')
        with Locked() as state:
            obj = state[store].get(name)
            if obj is None:
                sys.stderr.write(f'Error from server (NotFound): {store} "{name}" not found\n')
                return 1
            want = body.get('preconditions', {}).get('uid')
            if want and want != obj['metadata']['uid']:
                sys.stderr.write(f'Error from server (Conflict): Operation cannot be fulfilled on {store} "{name}": Precondition failed: '
                                 f'UID in precondition: {want}, UID in object meta: {obj["metadata"]["uid"]}\n')
                return 1
            if 'dryRun=All' not in query:
                state[store].pop(name)
                if store == 'pods':
                    stop(obj)
                    shutil.rmtree(pod_dir(name), ignore_errors=True)
        print(json.dumps(dict(kind='Status', status='Success')))
        return 0
    if verb == 'delete':
        items = json.loads(sys.stdin.read())['items'] if option(args, '-f') == '-' else []
        with Locked() as state:
            for item in items:
                name = item['metadata']['name']
                if item['kind'] == 'Pod' and name in state['pods']:
                    stop(state['pods'].pop(name))
                    shutil.rmtree(pod_dir(name), ignore_errors=True)
                elif item['kind'] == 'ConfigMap':
                    state['configmaps'].pop(name, None)
        return 0
    if verb == 'wait':
        timeout = float(option(args, '--timeout').rstrip('s'))
        selector = option(args, '-l')
        names = positional(args[1:])[1:]
        deadline = time.monotonic() + timeout
        if option(args, '--for') == 'delete':
            return 0
        while True:
            with Locked() as state:
                pods = [p for n, p in state['pods'].items() if (n in names) or (selector and labels_match(p['metadata'], selector))]
            if pods and all(ready(p) for p in pods):
                return 0
            if time.monotonic() > deadline:
                sys.stderr.write('error: timed out waiting for the condition\n')
                return 1
            time.sleep(0.3)
    if verb == 'get':
        kinds = positional(args[1:])
        selector = option(args, '-l')
        with Locked() as state:
            pods, configmaps = state['pods'], state['configmaps']
            if kinds[0] == 'nodes':
                print(json.dumps(dict(items=NODES)))
            elif kinds[0] in ('pod', 'pods', 'configmap', 'configmaps') and len(kinds) > 1:
                store = pods if kinds[0].startswith('pod') else configmaps
                if kinds[1] not in store:
                    sys.stderr.write(f'Error from server (NotFound): {kinds[0]} "{kinds[1]}" not found\n')
                    return 1
                print(json.dumps(view(store[kinds[1]]) if store is pods else store[kinds[1]]))
            elif kinds[0] == 'pods' and '-A' in args:
                print(json.dumps(dict(items=[view(p) for p in pods.values()] + [dict(
                    metadata=dict(name='protected-canary', namespace='edge-recovery', uid='protected-1'),
                    spec=dict(nodeName=NODES[0]['metadata']['name'], containers=[dict(name='c', env=[dict(name='SECRET', value='do-not-store')],
                              resources=dict(requests=dict(cpu='10m', memory='16Mi')))]),
                    status=dict(phase='Running', conditions=[dict(type='Ready', status='True')]))])))
            elif kinds[0] == 'pods' and option(args, '-n') == 'kube-system':
                print(json.dumps(dict(items=[dict(metadata=dict(name='kube-scheduler-fake', uid='sched-1', annotations={}),
                    spec=dict(nodeName='fake', containers=[dict(name='kube-scheduler', image='fake', command=['kube-scheduler'])]),
                    status=dict(containerStatuses=[dict(name='kube-scheduler', imageID='fake')]))])))
            elif kinds[0] in ('pods', 'pods,configmaps'):
                items = [view(p) for p in pods.values() if labels_match(p['metadata'], selector)]
                if 'configmaps' in kinds[0]:
                    items += [c for c in configmaps.values() if labels_match(c['metadata'], selector)]
                print(json.dumps(dict(items=items)))
            elif kinds[0] == 'events':
                print(json.dumps(dict(items=[])))
            else:
                sys.stderr.write('fake kubectl: unsupported get '+repr(args)+'\n')
                return 1
        return 0
    if verb == 'logs':
        name = positional(args[1:])[0]
        path = pod_dir(name)/'log.txt'
        sys.stdout.write(path.read_text() if path.exists() else '')
        return 0
    if verb == 'exec':
        split = args.index('--')
        name = positional(args[1:split])[0]
        with Locked() as state:
            pod = state['pods'][name]
        root = Path(pod['fake']['root'])
        argv = [a.replace('/config/', str(root/'config')+'/').replace("'/work'", repr(str(root/'work'))) for a in args[split+1:]]
        if argv[0] == 'python3':
            argv[0] = PYTHON
        stdin = sys.stdin.buffer if '-i' in args[:split] else subprocess.DEVNULL
        return subprocess.run(argv, cwd=str(root/'work'), env=dict(os.environ, **pod['fake']['env']), stdin=stdin).returncode
    sys.stderr.write('fake kubectl: unsupported '+repr(args)+'\n')
    return 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
