"""Audit archived control identity, bounded EOF recovery and returned snapshots."""
import argparse
import ast
import hashlib
import json
import re
from pathlib import Path


def verify(run):
    read = lambda p: json.loads(p.read_text())
    digest = lambda s: hashlib.sha256(s.encode()).hexdigest()
    source = (run/'collector-source/control_receipt.py').read_text()
    net_hash = hashlib.sha256((run/'collector-source/net_control.py').read_bytes()).hexdigest()
    expected_code = "import runpy,hashlib;from pathlib import Path;p=Path('/work/net-config/net_control.py');assert hashlib.sha256(p.read_bytes()).hexdigest()=="+repr(net_hash)+";runpy.run_path(str(p),run_name='__main__')"
    operations = {p.stem[len('control-'):]: read(p) for p in run.glob('control-*.json')
                  if len(p.stem[len('control-'):]) == 32}
    histories = {key: [] for key in operations}
    observed_pods = {}
    probe_failures = {}
    probe_successes = 0
    worker_fallbacks = 0
    for path in sorted(run.glob('[0-9]*.event.json'), key=lambda p: int(p.name.split('-')[0])):
        event = read(path)
        command = event.get('command', [])
        prefix = path.name.removesuffix('.event.json')
        if event.get('returncode') == 0 and command[4:6] == ['get', 'pod']:
            observed = read(run/(prefix+'.stdout'))
            assert observed['kind'] == 'Pod', 'Expected actual Pod observation'
            observed_pods[observed['metadata']['name']] = observed['metadata']
        stem=prefix.split('-',1)[1]
        if stem.startswith(('bypass-', 'relay-', 'restore-')):
            match=re.search(r'-retry-([12]|3-worker)$',stem)
            ordinal=0 if match is None else int(match.group(1)[0])
            base=re.sub(r'-retry-(?:[12]|3-worker)$','',stem)
            key=base.split('-',1)[1]
            target=run.name+'-'+key
            actual=observed_pods[target]
            container='worker' if ordinal==3 else 'netctl'
            assert command[:-1]==['rtk','proxy','kubectl','--request-timeout=20s',
                'exec','-n',actual['namespace'],target,'-c',container,'--','python3','-u','-c'], 'Wrong read-only execution target'
            assert command[-1].startswith("import os;assert os.environ['POD_UID']=="+repr(actual['uid'])+";"), 'Read-only UID mismatch'
            if 'sys.path.insert' in command[-1]:
                root='/work/config' if ordinal==3 else '/work/net-config'
                assert "sys.path.insert(0,"+repr(root)+")" in command[-1], 'Wrong container config'
            assert ordinal==probe_failures.get(base,0), 'Wrong read-only retry ordinal'
            if event.get('returncode')==0:
                probe_failures[base]=0
                probe_successes+=1
                worker_fallbacks+=ordinal==3
            else:
                stderr=(run/(prefix+'.stderr')).read_text()
                assert 'error sending request:' in stderr and 'unexpected EOF' in stderr, 'Non-EOF read-only failure'
                assert ordinal<3, 'Worker fallback failed'
                probe_failures[base]=ordinal+1
        if not command or 'print(run_once(' not in command[-1]:
            continue
        wrapped = command[-1]
        tree = ast.parse(wrapped)
        embedded = ast.literal_eval(tree.body[0].value.args[0].args[0])
        root, operation, uid, code, stdin = map(ast.literal_eval, tree.body[1].value.args[0].args)
        if operation not in operations:
            raise ValueError('Unregistered operation '+operation)
        meta = operations[operation]
        target = run.name+'-'+meta['key']
        actual = observed_pods[target]
        assert uid == actual['uid'], 'UID differs from actual preceding Pod observation'
        assert command == [
            'rtk', 'proxy', 'kubectl', '--request-timeout=20s',
            'exec', '-n', actual['namespace'], target, '-c', 'netctl',
            '--', 'python3', '-u', '-c', wrapped], 'Unexpected execution target/command'
        assert embedded == source, 'Receipt source changed'
        assert root == '/work/control-receipts', 'Wrong receipt root'
        assert uid == meta['pod_uid'], 'Pod identity changed'
        assert digest(wrapped) == meta['command_sha256'], 'Command changed'
        assert digest(code) == meta['code_sha256'], 'Control code changed'
        assert code == expected_code, 'Frozen network control source/entrypoint changed'
        assert stdin == meta['stdin'], 'Control input changed'
        histories[operation].append((path, event))
    recoveries = 0
    for operation, attempts in histories.items():
        assert 1 <= len(attempts) <= 3, 'Missing/unbounded operation attempts'
        for index, (path, event) in enumerate(attempts):
            prefix = path.name.removesuffix('.event.json')
            if event.get('returncode') != 0:
                stderr = (run/(prefix+'.stderr')).read_text()
                assert 'error sending request:' in stderr and 'unexpected EOF' in stderr, 'Non-EOF failure'
                assert index < len(attempts)-1, 'Unresolved operation'
                recoveries += 1
            else:
                assert index == len(attempts)-1, 'Unexpected replay after confirmed success'
                snapshot = read(run/(prefix+'.stdout'))
                assert snapshot['spec'] == json.loads(operations[operation]['stdin']), 'Snapshot input mismatch'
                assert snapshot['observer'] == 'native-TCA_STATS-v2', 'Wrong kernel observer'
    assert operations, 'No controls audited'
    assert not any(probe_failures.values()), 'Unresolved read-only probe'
    return dict(status='passed', operations=len(operations), recovered_eof_attempts=recoveries,
                readonly_probe_successes=probe_successes, worker_fallback_successes=worker_fallbacks,
                scope='archived identity/attempt/snapshot audit; connectivity is audited separately',
                verifier_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('run', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.run)
    with args.out.open('x') as handle:
        json.dump(result, handle, indent=2)
    print(json.dumps(result))
