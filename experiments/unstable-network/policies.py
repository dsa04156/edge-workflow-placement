"""Existing-method placement policies over one shared 5-task DAG, one cost model and one observation stream.

Stdlib only: imported by the edge generator Pod (decisions are made at the request origin) and by local tests.

DAG (edge origin):  origin -image-> image_preprocess -tensor-> vision -feature-> fusion_join
                    origin -audio-> audio_preprocess -input_values-> speech -text-> fusion_join ; answer -> origin
A plan maps every task to one worker key. A worker key is one process on one allocated device unit; tasks placed on
the same worker share its one-unit lock (they queue), so the cost model serializes them.

Cost model (prediction only, never used as a result): list scheduling of the 5 tasks with
  compute_ms[(task, worker)]  measured closed-loop service time (profile phase receipts; synthetic only in tests)
  link (latency_ms, ms_per_byte) per ordered (src, dst) location, measured by route probes under the current network
  payload_bytes per edge kind, measured
  queue_ms(worker) = observed queue length x observed mean service time  (only policies with uses_observation)
Policies (names are generic and honest; none is a named-paper reproduction):
  fixed                  one frozen heterogeneous plan (control)
  task_fastest           per task the fastest quality-eligible worker by compute only (no transfer, no queue)
  greedy_comm            topological greedy: per task the worker with the earliest predicted finish incl. transfer + queue
  dag_static             exact enumeration of all eligible plans on the frozen profile once (empty queues), then fixed.
                         Exact enumeration is a diagnostic selector for the PREDICTED cost model, not an online goodput
                         oracle or upper bound.
  dag_reopt              the same enumeration per request with the current observation
  dag_reopt_hysteresis   dag_reopt that switches only if the predicted gain > max(rel x current, abs_ms) after a dwell time
Prior systems (JellyBean, OctopInf, ...) have adapter slots that raise NotImplementedError until their contracts are
audited and implemented; no generic policy is labelled with their names.

unstable-network copy (source experiments/quality-network-baselines/policies.py, sha256 recorded in the plan's
copied_sources). Changes, nothing else:
  - Observation keeps, per worker, the last SUCCESSFUL queue state and its time; a failed poll records only its error,
    error time and error streak (it never becomes an empty queue and never refreshes the success time). A worker that
    never answered has no queue estimate (PolicyError, never 0).
  - Every observation-driven policy uses one rule: if any candidate worker never answered or its last success is older
    than stale_after_s, the decision is that policy's own static plan (its choice on the frozen profile with no queue,
    e.g. dag_reopt -> the dag_static plan) and the decision records fallback{reason, never, stale}. This is a defect fix
    of the observation input, not a new algorithm.
  - dag_reopt_no_network: dag_reopt with the same observation, queue, compute estimates and feasible plans, but every
    transfer costs 0 in the SELECTION only; the recorded predicted_ms keeps the full (network) cost model and the data
    path is unchanged.
"""
import itertools
import time

TASKS = ('image_preprocess', 'vision', 'audio_preprocess', 'speech', 'fusion')
PREPROCESS = ('image_preprocess', 'audio_preprocess')
ORIGIN = 'origin'
# (src task or origin, dst task or origin, payload kind)
EDGES = ((ORIGIN, 'image_preprocess', 'image'), ('image_preprocess', 'vision', 'image_tensor'), ('vision', 'fusion', 'feature'),
         (ORIGIN, 'audio_preprocess', 'audio'), ('audio_preprocess', 'speech', 'input_values'), ('speech', 'fusion', 'text'),
         ('fusion', ORIGIN, 'answer'))
PREDS = {t: [(s, k) for s, d, k in EDGES if d == t] for t in TASKS}


class PolicyError(RuntimeError):
    pass


def plan_key(plan):
    return tuple(plan[t] for t in TASKS)


def plan_label(plan):
    return '|'.join(f'{t}={plan[t]}' for t in TASKS)


class Profile:
    """Measured (or, in tests only, synthetic) inputs to the cost model. `source` must say which."""

    def __init__(self, compute_ms, links, payload_bytes, source, default_link=None):
        self.compute_ms = {tuple(k) if not isinstance(k, tuple) else k: float(v) for k, v in dict(compute_ms).items()}
        self.links = {tuple(k) if not isinstance(k, tuple) else k: dict(v) for k, v in dict(links).items()}
        self.payload_bytes = dict(payload_bytes)
        self.source = source
        self.default_link = default_link

    @classmethod
    def from_json(cls, value):
        split = lambda k: tuple(k.split('->')) if '->' in k else tuple(k.split('@'))
        return cls({split(k): v for k, v in value['compute_ms'].items()}, {split(k): v for k, v in value['links'].items()},
                   value['payload_bytes'], value['source'], value.get('default_link'))

    def to_json(self):
        return dict(compute_ms={f'{t}@{w}': v for (t, w), v in self.compute_ms.items()},
                    links={f'{a}->{b}': v for (a, b), v in self.links.items()}, payload_bytes=self.payload_bytes, source=self.source,
                    default_link=self.default_link)

    def compute(self, task, worker):
        try:
            return self.compute_ms[(task, worker)]
        except KeyError:
            raise PolicyError(f'no measured compute for {task}@{worker}') from None

    def transfer(self, src, dst, kind):
        link = self.links.get((src, dst)) or self.default_link
        if link is None:
            raise PolicyError(f'no measured link {src}->{dst}')
        return float(link['latency_ms']) + float(link['ms_per_byte']) * float(self.payload_bytes[kind])


class Observation:
    """What every policy may see at decision time: per-worker last successful queue state with its time and the last poll
    error (same poller for all policies), and estimates updated from completed requests' receipts (EWMA). Static policies
    ignore it; the state is still recorded with every decision."""

    def __init__(self, alpha=0.3):
        self.alpha, self.workers, self.service_ms, self.compute_est, self.link_est = alpha, {}, {}, {}, {}

    def record_poll(self, worker, state, error, at):
        """One poll result. error None = success (state = {waiting, active, joins_open}); otherwise only the error is kept."""
        w = dict(self.workers.get(worker) or dict(state=None, success_at=None, last_error=None, error_at=None, consecutive_errors=0,
                                                  polls=0, errors=0))
        w['polls'] += 1
        if error is None:
            w.update(state=dict(state), success_at=at, consecutive_errors=0)
        else:
            w.update(last_error=str(error)[:120], error_at=at, consecutive_errors=w['consecutive_errors'] + 1, errors=w['errors'] + 1)
        self.workers = dict(self.workers, **{worker: w})  # replaced whole: a reader never sees a half-updated worker

    def carry_polls(self, other):
        self.workers = dict(other.workers)

    def freshness(self, now, workers, stale_after_s):
        never = sorted(k for k in workers if (self.workers.get(k) or {}).get('success_at') is None)
        ages = {k: (now - self.workers[k]['success_at']) * 1000 for k in workers if k not in never}
        stale = sorted(k for k, age in ages.items() if age > stale_after_s * 1000)
        return dict(never=never, stale=stale, max_age_ms=max(ages.values()) if ages else None, stale_after_s=stale_after_s)

    def _ewma(self, table, key, value):
        old = table.get(key)
        table[key] = value if old is None else (1 - self.alpha) * old + self.alpha * value

    def record_receipt(self, task, worker, wall_ms):
        self._ewma(self.compute_est, (task, worker), wall_ms)
        self._ewma(self.service_ms, worker, wall_ms)

    def record_transfer(self, src, dst, transfer_ms, payload_bytes, latency_ms):
        if payload_bytes > 0 and transfer_ms > latency_ms:
            self._ewma(self.link_est, (src, dst), (transfer_ms - latency_ms) / payload_bytes)

    def queue_ms(self, worker, profile):
        q = (self.workers.get(worker) or {}).get('state')
        if q is None:
            raise PolicyError(f'no successful queue observation of {worker}')
        depth = int(q.get('waiting', 0)) + int(q.get('active', 0))
        if not depth:
            return 0.0
        service = self.service_ms.get(worker)
        if service is None:
            known = [v for (t, w), v in profile.compute_ms.items() if w == worker]
            service = sum(known) / len(known) if known else 0.0
        return depth * service

    def to_json(self, now=None, stale_after_s=None):
        """Per-worker times (age of the last success, last error and its age, error streak) and, separately, the summary."""
        workers = {}
        for key, w in self.workers.items():
            q = w['state'] or {}
            workers[key] = dict(waiting=q.get('waiting'), active=q.get('active'), joins_open=q.get('joins_open'),
                                age_ms=(now - w['success_at']) * 1000 if now is not None and w['success_at'] is not None else None,
                                last_error=w['last_error'],
                                error_age_ms=(now - w['error_at']) * 1000 if now is not None and w['error_at'] is not None else None,
                                consecutive_errors=w['consecutive_errors'], polls=w['polls'], errors=w['errors'])
        out = dict(workers=workers, compute_est={f'{t}@{w}': v for (t, w), v in self.compute_est.items()},
                   link_ms_per_byte={f'{a}->{b}': v for (a, b), v in self.link_est.items()})
        if now is not None and stale_after_s is not None:
            out['summary'] = self.freshness(now, sorted(self.workers), stale_after_s)
        return out


class LiveProfile(Profile):
    """Profile view with observation estimates overlaid (used by observation-driven policies only)."""

    def __init__(self, base, observation):
        self.base, self.obs = base, observation
        self.payload_bytes, self.source, self.default_link = base.payload_bytes, base.source + '+observed', base.default_link
        self.compute_ms = {**base.compute_ms, **observation.compute_est}
        self.links = {}
        for key, link in base.links.items():
            per_byte = observation.link_est.get(key)
            self.links[key] = dict(link, ms_per_byte=per_byte) if per_byte is not None else link


def location(task, plan):
    return ORIGIN if task == ORIGIN else plan[task]


def predict(plan, profile, observation=None):
    """Predicted end-to-end ms of one request under the plan: list scheduling, tasks on one worker serialized, current
    queue (if observation is given) delays a worker's first free time. Returns dict(makespan_ms, tasks{t: start,end})."""
    free = {}
    if observation is not None:
        for w in set(plan.values()):
            free[w] = observation.queue_ms(w, profile)
    done, times = {}, {}
    pending = list(TASKS)
    while pending:
        ready = []
        for t in pending:
            if all(s == ORIGIN or s in done for s, _ in PREDS[t]):
                at = max((0.0 if s == ORIGIN else done[s]) + profile.transfer(location(s, plan), plan[t], kind) for s, kind in PREDS[t])
                ready.append((at, TASKS.index(t), t))
        at, _, t = min(ready)
        w = plan[t]
        start = max(at, free.get(w, 0.0))
        end = start + profile.compute(t, w)
        free[w], done[t], times[t] = end, end, (start, end)
        pending.remove(t)
    makespan = done['fusion'] + profile.transfer(plan['fusion'], ORIGIN, 'answer')
    return dict(makespan_ms=makespan, tasks=times)


class QualityGate:
    """Quality eligibility of whole pipeline plans, independent of numerical parity.

    mode 'strict': a plan is eligible only if its pipeline quality evidence (measured on the plan, or inferred because
    every task output equals the reference-composition output on all evaluated inputs) meets qmin. Parity failure alone
    never excludes a plan; parity success alone never qualifies one.
    mode 'diagnostic': every executable plan is selectable but every result is labelled diagnostic (not a same-quality
    comparison). Used while no plan meets qmin (2026-10-23: 0.355 < 0.40 on the reused 60 pairs)."""

    def __init__(self, mode, qmin, evidence=None):
        if mode not in ('strict', 'diagnostic'):
            raise ValueError('quality mode must be strict or diagnostic')
        self.mode, self.qmin, self.evidence = mode, float(qmin), {tuple(k.split('|')) if isinstance(k, str) else tuple(k): v
                                                               for k, v in (evidence or {}).items()}

    def eligible(self, plan):
        if self.mode == 'diagnostic':
            return True
        item = self.evidence.get(plan_key(plan))
        return bool(item) and item.get('source') in ('measured', 'identical_outputs') and item.get('quality') is not None \
            and float(item['quality']) >= self.qmin

    def label(self):
        return 'diagnostic (quality gate not met; not a same-quality comparison)' if self.mode == 'diagnostic' else f'strict qmin={self.qmin}'


def all_plans(candidates, gate):
    for combo in itertools.product(*(candidates[t] for t in TASKS)):
        plan = dict(zip(TASKS, combo))
        if gate.eligible(plan):
            yield plan


class Decision(dict):
    pass


class Policy:
    name, uses_observation = None, False

    def __init__(self, candidates, gate, profile, stale_after_s=3.0):
        self.candidates, self.gate, self.profile = {t: list(candidates[t]) for t in TASKS}, gate, profile
        self.plans = list(all_plans(self.candidates, gate))
        if not self.plans:
            raise PolicyError(f'no quality-eligible plan ({gate.label()})')
        self._prefix = {}
        self.stale_after_s, self.workers = float(stale_after_s), sorted({w for ws in self.candidates.values() for w in ws})
        self._static = None

    def static_plan(self):
        """This policy's own choice on the frozen profile with no queue (the verified fallback for unusable observation)."""
        if self._static is None:
            self._static = dict(self.choose(None, 0.0)[0])
        return self._static

    def fallback(self, observation, now):
        if not self.uses_observation:
            return None
        fresh = observation.freshness(now, self.workers, self.stale_after_s) if observation is not None else \
            dict(never=list(self.workers), stale=[], max_age_ms=None, stale_after_s=self.stale_after_s)
        if not fresh['never'] and not fresh['stale']:
            return None
        return dict(fresh, reason='no_successful_poll' if fresh['never'] else 'stale_observation',
                    rule='static plan on the frozen profile (no queue); never an empty-queue reading of a failed poll')

    def view(self, observation):
        return LiveProfile(self.profile, observation) if self.uses_observation and observation is not None else self.profile

    def feasible_prefix(self, partial):
        key = tuple(sorted(partial.items()))
        if key not in self._prefix:
            self._prefix[key] = any(all(p[t] == w for t, w in partial.items()) for p in self.plans)
        return self._prefix[key]

    def decide(self, observation=None, now=None):
        started = time.perf_counter()
        now = time.monotonic() if now is None else now
        fallback = self.fallback(observation, now)
        if fallback:
            plan, extra, profile, seen = self.static_plan(), {}, self.profile, None
        else:
            plan, extra = self.choose(observation, now)
            profile, seen = self.view(observation), observation if self.uses_observation else None
        predicted = predict(plan, profile, seen)
        return Decision(policy=self.name, plan=dict(plan), plan_label=plan_label(plan), predicted_ms=predicted['makespan_ms'],
                        decision_ms=(time.perf_counter()-started)*1000, uses_observation=self.uses_observation, fallback=fallback,
                        **extra)

    def choose(self, observation, now):
        raise NotImplementedError


class Fixed(Policy):
    name = 'fixed'

    def __init__(self, candidates, gate, profile, plan):
        super().__init__(candidates, gate, profile)
        if any(plan[t] not in self.candidates[t] for t in TASKS) or not gate.eligible(plan):
            raise PolicyError(f'fixed plan {plan_label(plan)} is not an eligible candidate plan')
        self.plan = dict(plan)

    def choose(self, observation, now):
        return self.plan, {}


class TaskFastest(Policy):
    """Per task (fixed task order) the eligible worker with the smallest measured compute; transfer and queue ignored.
    Ties break by candidate order. Decided once from the frozen profile (it uses no observation)."""
    name = 'task_fastest'

    def __init__(self, candidates, gate, profile, stale_after_s=3.0):
        super().__init__(candidates, gate, profile, stale_after_s)
        partial = {}
        for t in TASKS:
            options = [w for w in self.candidates[t] if self.feasible_prefix(dict(partial, **{t: w}))]
            partial[t] = min(options, key=lambda w: (profile.compute(t, w), self.candidates[t].index(w)))
        self.plan = partial

    def choose(self, observation, now):
        return self.plan, {}


class GreedyComm(Policy):
    """Topological greedy per request: tasks in ready order; each takes the eligible worker with the earliest predicted
    finish given the choices already made, transfer from its predecessors' locations, the worker's current queue and
    tasks already assigned to it in this request. No look-ahead (the fan-in cost of later tasks is not considered)."""
    name, uses_observation = 'greedy_comm', True

    def choose(self, observation, now):
        profile = self.view(observation)
        free = {}
        if observation is not None:
            for w in {w for ws in self.candidates.values() for w in ws}:
                free[w] = observation.queue_ms(w, profile)
        partial, done = {}, {}
        for t in ('image_preprocess', 'audio_preprocess', 'vision', 'speech', 'fusion'):
            best = None
            for w in self.candidates[t]:
                if not self.feasible_prefix(dict(partial, **{t: w})):
                    continue
                at = max((0.0 if s == ORIGIN else done[s]) + profile.transfer(ORIGIN if s == ORIGIN else partial[s], w, kind)
                         for s, kind in PREDS[t])
                end = max(at, free.get(w, 0.0)) + profile.compute(t, w)
                score = end + (profile.transfer(w, ORIGIN, 'answer') if t == 'fusion' else 0.0)
                if best is None or score < best[0]:
                    best = (score, end, w)
            _, end, w = best
            partial[t], done[t], free[w] = w, end, end
        return partial, {}


class DagStatic(Policy):
    """Exact enumeration of all eligible plans on the frozen profile (no queue), chosen once. Diagnostic selector."""
    name = 'dag_static'

    def __init__(self, candidates, gate, profile, stale_after_s=3.0):
        super().__init__(candidates, gate, profile, stale_after_s)
        self.plan, self.ranked = best_plan(self.plans, profile, None)

    def choose(self, observation, now):
        return self.plan, dict(enumerated=len(self.plans))


def best_plan(plans, profile, observation):
    scored = sorted(((predict(p, profile, observation)['makespan_ms'], plan_key(p), p) for p in plans), key=lambda x: (x[0], x[1]))
    return dict(scored[0][2]), [(s, k) for s, k, _ in scored[:5]]


class DagReopt(Policy):
    """Same enumeration per request with the current observation (queues + receipt EWMA)."""
    name, uses_observation = 'dag_reopt', True

    def choose(self, observation, now):
        plan, ranked = best_plan(self.plans, self.view(observation), observation)
        return plan, dict(enumerated=len(self.plans))


class DagReoptHysteresis(DagReopt):
    name = 'dag_reopt_hysteresis'

    def __init__(self, candidates, gate, profile, rel=0.15, abs_ms=50.0, dwell_s=5.0, stale_after_s=3.0):
        super().__init__(candidates, gate, profile, stale_after_s)
        self.rel, self.abs_ms, self.dwell_s = float(rel), float(abs_ms), float(dwell_s)
        self.current, self.switched_at, self.switches = None, None, 0

    def static_plan(self):
        """dag_reopt's static plan; computing it must not start the hysteresis state (a fallback leaves current/dwell as is)."""
        if self._static is None:
            self._static = dict(DagReopt.choose(self, None, 0.0)[0])
        return self._static

    def choose(self, observation, now):
        profile = self.view(observation)
        best, _ = best_plan(self.plans, profile, observation)
        if self.current is None:
            self.current, self.switched_at = best, now
            return best, dict(enumerated=len(self.plans), switched=True, hold_reason='initial')
        current_ms = predict(self.current, profile, observation)['makespan_ms']
        best_ms = predict(best, profile, observation)['makespan_ms']
        gain = current_ms - best_ms
        dwell_ok = now - self.switched_at >= self.dwell_s
        if plan_key(best) != plan_key(self.current) and gain > max(self.rel * current_ms, self.abs_ms) and dwell_ok:
            self.current, self.switched_at = best, now
            self.switches += 1
            return best, dict(enumerated=len(self.plans), switched=True, predicted_gain_ms=gain)
        reason = 'same plan' if plan_key(best) == plan_key(self.current) else ('dwell' if not dwell_ok else 'gain below threshold')
        return self.current, dict(enumerated=len(self.plans), switched=False, hold_reason=reason, predicted_gain_ms=gain)


class ZeroTransfer(Profile):
    """Selection view of the network-cost ablation: the same compute estimates and payloads, every transfer costs 0."""

    def __init__(self, base):
        self.base, self.compute_ms, self.links, self.payload_bytes = base, base.compute_ms, base.links, base.payload_bytes
        self.source, self.default_link = base.source + '+no-network-cost', base.default_link

    def transfer(self, src, dst, kind):
        return 0.0


class DagReoptNoNetwork(DagReopt):
    """dag_reopt with transfer cost 0 in the selection only (same observation, queue, compute, feasibility, data path).
    decide() records predicted_ms from the full cost model; selection_cost_ms is the no-network makespan."""
    name = 'dag_reopt_no_network'

    def choose(self, observation, now):
        view = ZeroTransfer(self.view(observation))
        plan, ranked = best_plan(self.plans, view, observation)
        return plan, dict(enumerated=len(self.plans), uses_network_cost=False, selection_cost_ms=ranked[0][0])


class PriorSystemAdapter(Policy):
    """Slot for a near prior system ported onto this executor. Not implemented until its contract (paper version,
    official code revision, objective, inputs, decision unit, backend adaptation) is audited; never substituted."""
    paper, audit = None, None

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(f'{self.name}: prior-system adapter not implemented ({self.audit})')


class JellyBeanAdapter(PriorSystemAdapter):
    name, paper, audit = 'jellybean', 'JellyBean (MLSys 2022)', 'contract audit owned by the coordinator/root; not implemented here'


class OctopInfAdapter(PriorSystemAdapter):
    name, paper, audit = 'octopinf', 'OctopInf', 'contract audit owned by the coordinator/root; not implemented here'


POLICIES = {c.name: c for c in (Fixed, TaskFastest, GreedyComm, DagStatic, DagReopt, DagReoptHysteresis, DagReoptNoNetwork)}
PRIOR_ADAPTERS = {c.name: c for c in (JellyBeanAdapter, OctopInfAdapter)}


def build(spec, candidates, gate, profile, stale_after_s=3.0):
    """spec: {name, plan? (fixed), rel/abs_ms/dwell_s (hysteresis)}; stale_after_s is the observation contract."""
    name = spec['name']
    if name in PRIOR_ADAPTERS:
        return PRIOR_ADAPTERS[name](candidates, gate, profile)
    cls = POLICIES[name]
    if cls is Fixed:
        return cls(candidates, gate, profile, spec['plan'])
    if cls is DagReoptHysteresis:
        return cls(candidates, gate, profile, **{k: spec[k] for k in ('rel', 'abs_ms', 'dwell_s') if k in spec}, stale_after_s=stale_after_s)
    return cls(candidates, gate, profile, stale_after_s=stale_after_s)


def link_fit(small, large):
    """Two-point fit from probe medians: (bytes_small, ms_small), (bytes_large, ms_large) -> latency_ms, ms_per_byte >= 0."""
    (b0, t0), (b1, t1) = small, large
    per_byte = max(0.0, (t1 - t0) / (b1 - b0)) if b1 != b0 else 0.0
    return dict(latency_ms=max(0.0, t0 - per_byte * b0), ms_per_byte=per_byte, effective_mbps=(8 / (per_byte * 1000)) if per_byte else None)
