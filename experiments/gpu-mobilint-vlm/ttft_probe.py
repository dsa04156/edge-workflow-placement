"""TTFT stage-boundary probe for the Qwen3-VL CUDA arm, and a read-only environment snapshot for both arms.

Trimmed from the first-stage prototype (.scratch/gpu-mobilint-placement-20261029/ttft_probe.py): no profiler, no variants.
Probe attaches forward pre/post hooks to the loaded upstream transformers 4.57.1 Qwen3VLForConditionalGeneration
(model.model.visual, model.model.language_model, model.lm_head; pinned source modeling_qwen3_vl.py:897/898/1281) and records
per call: host wall span, calling-thread CPU span (time.thread_time), process CPU span and, on CUDA, a
torch.cuda.Event(enable_timing) pair recorded on the current stream. The probe never synchronizes: events are resolved only
after the caller's own torch.cuda.synchronize(). host_ms is launch/CPU-side time; cuda_event_span_ms is the elapsed time
between the two events on the stream, which includes queueing and gaps, so it is NOT pure kernel-active time.
Hooks never raise into the model: a hook failure is kept in Probe.errors and the call proceeds unchanged.
environment() reads cgroup v2 / PSI / devfreq files; anything missing is recorded as unavailable, never as zero.
"""
import glob
import time
from pathlib import Path

MODULES = (('vision', ('model', 'visual')), ('language', ('model', 'language_model')), ('lm_head', ('lm_head',)))
CGROUP = Path('/sys/fs/cgroup')
CGROUP_FILES = ('cpu.stat', 'cpu.max', 'cpu.pressure', 'memory.current', 'memory.peak', 'memory.max', 'memory.pressure',
                'io.pressure')
MEMORY_STAT_KEYS = ('anon', 'file', 'pgfault', 'pgmajfault', 'workingset_refault_file', 'workingset_refault_anon',
                    'pgscan', 'pgsteal')
DEVFREQ_GLOBS = ('/sys/class/devfreq/*/cur_freq', '/sys/class/devfreq/*/max_freq', '/sys/class/devfreq/*/min_freq',
                 '/sys/devices/platform/*gpu*/load', '/sys/devices/platform/bus@0/*gpu*/load')
EVENT_NOTE = 'cuda_event_span_ms: stream time between two recorded events (includes queueing/gaps); not kernel-active time'
ROWS_MAX = 512  # max_new_tokens=64 gives ~130 calls; anything above this is dropped and counted
ERRORS_MAX = 8


def resolve(model, path):
    for name in path:
        model = getattr(model, name)
    return model


def _seq_len(role, args, kwargs):
    """vision: patch rows of hidden_states; language/lm_head: sequence dim of the (B, L, ...) input."""
    if role == 'vision':
        x = args[0] if args else kwargs.get('hidden_states')
        return None if x is None else int(x.shape[0])
    x = kwargs.get('inputs_embeds')
    if x is None:
        x = kwargs.get('input_ids')
    if x is None and args:
        x = args[0]
    return None if x is None else int(x.shape[1])


class Probe:
    def __init__(self, model, cuda):
        self.cuda, self.records, self.handles, self.errors, self.dropped = cuda, [], [], [], 0
        if cuda:
            import torch
            self._event = lambda: torch.cuda.Event(enable_timing=True)
        try:
            for role, path in MODULES:
                module = resolve(model, path)
                self.handles.append(module.register_forward_pre_hook(self._pre(role), with_kwargs=True))
                self.handles.append(module.register_forward_hook(self._post(role), with_kwargs=True))
        except BaseException:
            self.detach()
            raise

    def _error(self, role, error):
        if len(self.errors) < ERRORS_MAX:
            self.errors.append(f'{role}: {error!r}'[:300])

    def _pre(self, role):
        def hook(module, args, kwargs):
            try:
                if len(self.records) >= ROWS_MAX:
                    self.dropped += 1
                    return
                row = dict(role=role, seq=_seq_len(role, args, kwargs), wall0=time.perf_counter(), thread0=time.thread_time(),
                           proc0=time.process_time())
                if self.cuda:
                    row['ev0'] = self._event()
                    row['ev0'].record()
                self.records.append(row)
            except Exception as error:
                self._error(role, error)
        return hook

    def _post(self, role):
        def hook(module, args, kwargs, output):
            try:
                row = next((r for r in reversed(self.records) if r['role'] == role and 'wall1' not in r), None)
                if row is None:
                    return
                if self.cuda:
                    row['ev1'] = self._event()
                    row['ev1'].record()
                row.update(wall1=time.perf_counter(), thread1=time.thread_time(), proc1=time.process_time())
            except Exception as error:
                self._error(role, error)
        return hook

    def detach(self):
        """Removes every handle; returns the removal errors (never raises, so it cannot mask the caller's exception)."""
        problems = []
        for handle in self.handles:
            try:
                handle.remove()
            except Exception as error:
                problems.append(repr(error)[:300])
        self.handles = []
        return problems

    def rows(self):
        """Call only after the caller's synchronize (CUDA). Complete calls only; absolute perf_counter stamps."""
        out = []
        for r in self.records:
            if 'wall1' not in r:
                continue
            row = dict(role=r['role'], seq=r['seq'], wall0=r['wall0'], wall1=r['wall1'],
                       host_ms=(r['wall1']-r['wall0'])*1000, thread_cpu_ms=(r['thread1']-r['thread0'])*1000,
                       proc_cpu_ms=(r['proc1']-r['proc0'])*1000)
            if self.cuda:
                row['cuda_event_span_ms'] = r['ev0'].elapsed_time(r['ev1'])
            out.append(row)
        return out


def split(rows, generate_start, token_stamps):
    """rows: Probe.rows(); generate_start: perf_counter at model.generate entry; token_stamps: TokenClock.stamps[1:]."""
    vision = [r for r in rows if r['role'] == 'vision']
    language = [r for r in rows if r['role'] == 'language']
    heads = [r for r in rows if r['role'] == 'lm_head']
    if not language:
        raise ValueError('no language_model call recorded')
    prefill, decode = language[0], language[1:]
    rel = lambda t: (t-generate_start)*1000  # noqa: E731
    keys = ('host_ms', 'thread_cpu_ms', 'proc_cpu_ms', 'cuda_event_span_ms')
    pick = lambda r: {k: r[k] for k in keys if k in r}  # noqa: E731
    return dict(
        vision_calls=len(vision), language_calls=len(language), lm_head_calls=len(heads), prefill_seq=prefill['seq'],
        decode_seqs=sorted({r['seq'] for r in decode if r['seq'] is not None}),
        pre_vision_gap_ms=rel(vision[0]['wall0']) if vision else None,
        vision=[pick(r) for r in vision],
        vision_to_prefill_gap_ms=(prefill['wall0']-vision[-1]['wall1'])*1000 if vision else None,
        prefill_start_ms=rel(prefill['wall0']),
        prefill=pick(prefill),
        first_lm_head=pick(heads[0]) if heads else None,
        first_token_after_lm_head_ms=(token_stamps[0]-heads[0]['wall1'])*1000 if heads and token_stamps else None,
        decode=[pick(r) for r in decode],
        ttft_ms=rel(token_stamps[0]) if token_stamps else None)


def _read(path, limit=4096):
    try:
        return Path(path).read_text()[:limit].strip()
    except OSError as error:
        return f'unavailable: {error.__class__.__name__}'


def environment():
    """Read-only snapshot. Taken before and after a request; deltas are computed offline."""
    snap = dict(at=time.time(), cgroup={name: _read(CGROUP/name) for name in CGROUP_FILES},
                psi={name: _read(f'/proc/pressure/{name}') for name in ('cpu', 'memory', 'io')},
                threads=next((line.split()[1] for line in _read('/proc/self/status', 1 << 16).splitlines()
                              if line.startswith('Threads:')), None),
                loadavg=_read('/proc/loadavg'))
    stat = _read(CGROUP/'memory.stat', 1 << 16)
    snap['memory_stat'] = stat if stat.startswith('unavailable') else {
        k: v for k, v in (line.split()[:2] for line in stat.splitlines() if len(line.split()) >= 2) if k in MEMORY_STAT_KEYS}
    devfreq = {path: _read(path) for pattern in DEVFREQ_GLOBS for path in sorted(glob.glob(pattern))[:16]}
    snap['devfreq'] = devfreq or 'unavailable: no devfreq/gpu load sysfs path visible'
    try:
        import torch
        snap['torch_threads'] = torch.get_num_threads()
        if torch.cuda.is_available():
            snap['cuda_memory_allocated'] = torch.cuda.memory_allocated(0)
            snap['cuda_memory_reserved'] = torch.cuda.memory_reserved(0)
    except Exception as error:
        snap['torch'] = f'unavailable: {error!r}'[:300]
    return snap
