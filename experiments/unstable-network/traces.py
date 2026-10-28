"""Pre-registered time-varying network traces for the unstable-edge-network stress test (pure, stdlib only).

Shipped to the netctl sidecar of the owned gateway Pod (the only component that knows a trace) and used offline by the
planner and the analysis. The generator and the policies never receive a trace: they adapt from real observation only.

A trace is a list of contiguous half-open phases [start_s, end_s) that covers exactly [0, window_s). Each phase is one
egress constraint of the gateway Pod netns on the edge<->server path (tbf rate + netem delay/jitter/loss). The values
are the spec's fixed table; the traces are a controlled stress test, not a claimed distribution of real edge links.
"""
import hashlib
import json

WINDOW_S = 60
GOOD = dict(mbps=100, delay_ms=0, jitter_ms=0, loss_pct=0.0)
_MID = dict(mbps=10, delay_ms=20, jitter_ms=5, loss_pct=0.5)


def _phase(name, start, end, cond):
    return dict(name=name, start_s=start, end_s=end, **cond)


TRACES = {
    'stable': [_phase('good', 0, 60, GOOD)],
    'slow': [_phase('good', 0, 20, GOOD), _phase('slow', 20, 40, dict(mbps=10, delay_ms=20, jitter_ms=0, loss_pct=0.0)),
             _phase('good', 40, 60, GOOD)],
    'burst': [_phase('good', 0, 10, GOOD), _phase('lossy', 10, 20, _MID),
              _phase('narrow', 20, 28, dict(mbps=2, delay_ms=20, jitter_ms=5, loss_pct=1.0)),
              _phase('outage', 28, 30, dict(mbps=2, delay_ms=20, jitter_ms=5, loss_pct=100.0)),
              _phase('good', 30, 40, GOOD), _phase('lossy', 40, 50, _MID), _phase('good', 50, 60, GOOD)],
}
# Every impairment value the netctl sidecar may apply (anything else is refused before a netlink call).
APPROVED = dict(mbps=(2, 10, 100), delay_ms=(0, 20), jitter_ms=(0, 5), loss_pct=(0.0, 0.5, 1.0, 100.0))
CONDITION_KEYS = tuple(APPROVED)


def condition(phase):
    return {k: phase[k] for k in CONDITION_KEYS}


def validate(phases, window_s):
    """ValueError unless the phases are contiguous, non-empty, start at 0, end exactly at window_s and use approved values."""
    if not phases:
        raise ValueError('trace has no phase')
    previous = 0
    for i, p in enumerate(phases):
        if p['start_s'] != previous:
            raise ValueError(f'phase {i} starts at {p["start_s"]}, expected {previous} (gap, overlap or late start)')
        if not p['end_s'] > p['start_s']:
            raise ValueError(f'phase {i} is empty or reversed')
        if p['end_s'] > window_s:
            raise ValueError(f'phase {i} ends at {p["end_s"]} after the {window_s}s window')
        for key, allowed in APPROVED.items():
            if p[key] not in allowed:
                raise ValueError(f'phase {i}: {key}={p[key]!r} is not an approved value {allowed}')
        previous = p['end_s']
    if previous != window_s:
        raise ValueError(f'trace ends at {previous}, not at the {window_s}s window')
    return phases


def phase_at(phases, t):
    """Index of the phase containing offset t ([start, end) half-open), None outside [0, window)."""
    for i, p in enumerate(phases):
        if p['start_s'] <= t < p['end_s']:
            return i
    return None


def trace_sha256(name):
    return hashlib.sha256(json.dumps(TRACES[name], sort_keys=True, separators=(',', ':')).encode()).hexdigest()


for _name, _phases in TRACES.items():
    validate(_phases, WINDOW_S)
