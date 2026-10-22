"""Same-Pod, at-most-once control execution across lost exec responses.

The receipt is a transport recovery mechanism, not a kernel state proof.
An interrupted operation is deliberately never re-executed.
"""
import hashlib
import json
from pathlib import Path


def run_once(root, operation, pod_uid, code, stdin):
    import contextlib
    import fcntl
    import io
    import os
    import re
    import sys

    if not re.fullmatch(r'[a-f0-9]{32}', operation):
        raise ValueError('Invalid operation ID')
    if os.environ.get('POD_UID') != pod_uid:
        raise RuntimeError('Pod UID changed')
    identity = hashlib.sha256(json.dumps(
        [operation, pod_uid, code, stdin], separators=(',', ':')).encode()).hexdigest()
    directory = Path(root)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    receipt = directory / (operation + '.json')

    def persist(value):
        temp = receipt.with_suffix('.tmp')
        with temp.open('w') as handle:
            json.dump(value, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, receipt)

    with (directory / 'control.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if receipt.exists():
            result = json.loads(receipt.read_text())
            if result['identity'] != identity:
                raise RuntimeError('Operation identity changed')
            if result['status'] != 'completed':
                raise RuntimeError('Indeterminate control; refusing replay')
            return result['stdout']
        persist(dict(identity=identity, status='started'))
        output = io.StringIO()
        previous_stdin = sys.stdin
        try:
            sys.stdin = io.StringIO(stdin or '')
            with contextlib.redirect_stdout(output):
                exec(compile(code, '<owned-control>', 'exec'), {'__name__': '__main__'})
        except BaseException as error:
            persist(dict(identity=identity, status='failed', error=repr(error),
                         stdout=output.getvalue()))
            raise
        finally:
            sys.stdin = previous_stdin
        persist(dict(identity=identity, status='completed', stdout=output.getvalue()))
        return output.getvalue()


def command(operation, pod_uid, code, stdin, root='/work/control-receipts'):
    """Embed the frozen module, so the model runtime/ConfigMap is unchanged."""
    source = Path(__file__).read_text()
    return (
        "exec(compile(" + repr(source) + ",'<control-receipt>','exec'));"
        "print(run_once(" + ','.join(map(repr, [root, operation, pod_uid, code, stdin]))
        + "),end='')"
    )
