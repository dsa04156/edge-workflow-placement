"""Owned-Pod route recovery diagnostic; no AI performance observations."""
import json
from run_budget_placement import BudgetPilot
from run_smoke import NS


def worker_probe(session, cycle, blocked):
    key='source-e1'
    uid=session.actual[key]['metadata']['uid']
    url=session.urls['source-c']+'/health'
    code="import os,json,urllib.request;assert os.environ['POD_UID']=="+repr(uid)+";op=urllib.request.build_opener(urllib.request.ProxyHandler({}));\ntry:\n r=op.open("+repr(url)+",timeout=2);print(json.dumps({'blocked':False,'health':json.loads(r.read())}))\nexcept Exception as e:print(json.dumps({'blocked':True,'error':repr(e)}))"
    result=json.loads(session.call(f'worker-proof-{cycle}-{blocked}',
        ['exec','-n',NS,session.run+'-'+key,'-c','worker','--','python3','-u','-c',code],timeout=10))
    assert result['blocked']==blocked
    if not blocked:assert result['health']['status']=='ready'


def replay(session, stem):
    events = list(session.out.glob('*-'+stem+'.event.json'))
    if len(events) != 1:
        raise RuntimeError('Ambiguous original control')
    event = json.loads(events[0].read_text())
    # Reuse the exact operation/UID/code, not a new mutation operation ID.
    original = events[0].with_name(events[0].name.replace('.event.json', '.stdout')).read_text()
    actual = session.call('replay-'+stem, event['command'][4:], timeout=60)
    if actual != original:
        raise RuntimeError('Completed control receipt changed')


def main():
    session = BudgetPilot()
    session.save('diagnostic-design.json', dict(
        cycles=3, kind='live completed-receipt replay and actual guard/restore',
        scope='control diagnostic, no inference or transport-root-cause claim'))
    try:
        session.deploy()
        for cycle in range(3):
            session.guard(True)
            worker_probe(session,cycle,True)
            if cycle == 0:
                replay(session, 'guard-True-source-e0')
                replay(session, 'guard-True-source-e1')
            session.guard(False)
            worker_probe(session,cycle,False)
            if cycle == 0:
                replay(session, 'guard-False-source-e0')
                replay(session, 'guard-False-source-e1')
        session.shape(None, 0, 'diagnostic-restored')
        session.save('control-diagnostic.json', dict(status='passed', cycles=3,
            completed_receipt_replays=4, worker_same_pod_proofs=6, restored_connectivity=True))
    finally:
        session.close()


if __name__ == '__main__':
    main()
