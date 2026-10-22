"""Owned-Pod route recovery diagnostic; no AI performance observations."""
import json
from run_budget_placement import BudgetPilot


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
            if cycle == 0:
                replay(session, 'guard-True-source-e0')
                replay(session, 'guard-True-source-e1')
            session.guard(False)
            if cycle == 0:
                replay(session, 'guard-False-source-e0')
                replay(session, 'guard-False-source-e1')
        session.shape(None, 0, 'diagnostic-restored')
        session.save('control-diagnostic.json', dict(status='passed', cycles=3,
            completed_receipt_replays=4, restored_connectivity=True))
    finally:
        session.close()


if __name__ == '__main__':
    main()
