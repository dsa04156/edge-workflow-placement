"""Deadline-oriented DAG placement v1 (dag_deadline_guard) on the unchanged unstable-network policy code.

Stdlib only. The base is frozen_unstable_policies.py: a byte-identical copy of experiments/unstable-network/policies.py
(sha256 in SOURCES.json, checked by the tests). Nothing in the base is edited or monkey-patched; baseline specs are
built by the base build() itself, so their decisions are the original ones.

Scope (2026-10-28): quality is excluded from optimization, admission, evaluation and conclusions. Models, precision,
DAG and input identity stay fixed; every executable CPU/GPU/NPU candidate combination is selectable (ExecutionOnly).

dag_deadline_guard (a SLO-aware selection hypothesis, not a reliability guarantee):
  anchor     = the base best_plan() on the frozen startup profile with no observed queue (the dag_static plan), frozen.
  per decision, with the SAME live profile view and queue model as dag_reopt:
    anchor_predicted_ms <= deadline_ms x anchor_fraction  -> keep the anchor              (anchor_within_budget)
    otherwise the base dag_reopt enumeration: lowest predicted makespan over every plan, the anchor included
                                                           (over_budget_reoptimized | over_budget_anchor_still_best)
  observation never answered / older than stale_after_s -> the anchor (the inherited base fallback, observation_fallback).
    That fallback only reacts to poll age; it gives no freshness guarantee for the compute/link estimates.
  No request is rejected, no node/zone/device name is read, no trace phase, outcome or answer quality is an input.
  A return to the anchor happens on the first decision whose anchor prediction is within budget (no dwell).
Defaults deadline_ms=2000 and anchor_fraction=0.75 are an a priori engineering reserve, not tuned on any result.
Limitation: an inaccurate live prediction can still hold a poor anchor or pick a bad alternative.
"""
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frozen_unstable_policies as base  # noqa: E402

NAME = 'dag_deadline_guard'
DEFAULTS = dict(deadline_ms=2000.0, anchor_fraction=0.75)
SPEC_KEYS = frozenset({'name', 'deadline_ms', 'anchor_fraction'})
NO_FRESHNESS = ('inherited poll-age fallback only: it reacts to queue-poll age; compute/link estimates are learned only from '
                'requests whose both branches succeeded and carry no age; no freshness guarantee')


class ExecutionOnly:
    """Eligibility adapter of this scope: every executable candidate combination is selectable; quality is not consulted
    (the legacy QualityGate stays only inside the unchanged frozen base)."""

    def eligible(self, plan):
        return True

    def label(self):
        return 'execution-only (quality excluded from this scope; every executable candidate combination)'


def _finite(key, value, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{NAME}: {key} must be a finite number, got {value!r}')
    value = float(value)
    if not low < value <= high:
        raise ValueError(f'{NAME}: {key}={value} outside ({low}, {high}]')
    return value


def policy_spec(deadline_ms=DEFAULTS['deadline_ms'], anchor_fraction=DEFAULTS['anchor_fraction']):
    """The exact, validated spec a runner passes to build()."""
    return dict(name=NAME, deadline_ms=_finite('deadline_ms', deadline_ms, 0.0, math.inf),
                anchor_fraction=_finite('anchor_fraction', anchor_fraction, 0.0, 1.0))


class DagDeadlineGuard(base.DagReopt):
    name = NAME

    def __init__(self, candidates, gate, profile, deadline_ms=DEFAULTS['deadline_ms'], anchor_fraction=DEFAULTS['anchor_fraction'],
                 stale_after_s=3.0):
        spec = policy_spec(deadline_ms, anchor_fraction)
        # validated here only: a NaN/inf/non-positive stale_after_s would silently disable the inherited fallback
        super().__init__(candidates, gate, profile, _finite('stale_after_s', stale_after_s, 0.0, math.inf))
        self.deadline_ms, self.anchor_fraction = spec['deadline_ms'], spec['anchor_fraction']
        self.threshold_ms = self.deadline_ms * self.anchor_fraction
        self.anchor, _ = base.best_plan(self.plans, profile, None)

    def static_plan(self):
        return dict(self.anchor)

    def _common(self, anchor_ms):
        return dict(enumerated=len(self.plans), anchor_plan_label=base.plan_label(self.anchor), anchor_predicted_ms=anchor_ms,
                    deadline_ms=self.deadline_ms, anchor_fraction=self.anchor_fraction, threshold_ms=self.threshold_ms)

    def choose(self, observation, now):
        view = self.view(observation)
        anchor_ms = base.predict(self.anchor, view, observation)['makespan_ms']
        extra = self._common(anchor_ms)
        if anchor_ms <= self.threshold_ms:
            return dict(self.anchor), dict(extra, choice_reason='anchor_within_budget', selected_predicted_ms=anchor_ms)
        best, ranked = base.best_plan(self.plans, view, observation)
        same = base.plan_key(best) == base.plan_key(self.anchor)
        return best, dict(extra, choice_reason='over_budget_anchor_still_best' if same else 'over_budget_reoptimized',
                          selected_predicted_ms=ranked[0][0])

    def decide(self, observation=None, now=None):
        d = super().decide(observation, now)
        if d['fallback']:
            # base decide() predicts the anchor on the frozen profile without queue here; recorded as such
            d.update(self._common(d['predicted_ms']), choice_reason='observation_fallback', selected_predicted_ms=d['predicted_ms'],
                     fallback=dict(d['fallback'], freshness_guarantee=False, freshness_note=NO_FRESHNESS))
        return d


def build(spec, candidates, gate, profile, stale_after_s=3.0):
    """Base build() for every existing spec (original behavior); dag_deadline_guard spec {name, deadline_ms, anchor_fraction}."""
    if spec.get('name') != NAME:
        return base.build(spec, candidates, gate, profile, stale_after_s=stale_after_s)
    unknown = set(spec) - SPEC_KEYS
    if unknown:
        raise ValueError(f'{NAME}: unknown spec keys {sorted(unknown)}')
    return DagDeadlineGuard(candidates, gate, profile, spec.get('deadline_ms', DEFAULTS['deadline_ms']),
                            spec.get('anchor_fraction', DEFAULTS['anchor_fraction']), stale_after_s=stale_after_s)
