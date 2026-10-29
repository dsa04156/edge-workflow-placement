"""Diagnostic VLM stage: the unchanged vlm_stage lifecycle plus TTFT stage boundaries (CUDA) and environment snapshots (both arms).

diag_live.py ships this file under the ConfigMap name vlm_stage.py, so the unchanged vlm_worker.py and bootstraps load it,
and ships the unchanged vlm_stage.py next to it as vlm_stage_base.py (imported here as `base`; in the repository layout the
base is imported as vlm_stage). Everything else is the base: spawned child, load/call deadlines, kill-and-never-reuse,
LoadFailed records, weight hashes, device assertions, processor/generation contract, TokenClock, timing/receipt fields and
run() validation. Additions:
- child_main: the base child loop, with the base module's Engine name bound to Engine below inside the fresh spawned child.
- Engine.__init__: base load, then (CUDA) the hooked module paths must resolve, otherwise the load fails.
- Engine.generate: base generate, unchanged and called once; around it ttft_probe.environment() before/after (both arms)
  and, CUDA only, ttft_probe hooks plus an instance-level pass-through on model.generate that stamps the entry time and
  keeps the streamer (the base TokenClock) for the split. Both are removed on every path; a cleanup problem after a failure
  is attached to the original exception as a note and never replaces it.
- result['diagnostic'] travels inside the existing output JSON -> VlmStage.run -> HTTP /run reply; timing keys are unchanged.
Mobilint: the vendor MXQ model is not instrumented and its module layout is not assumed; environment snapshots only.
"""
import importlib
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ttft_probe  # noqa: E402

base = importlib.import_module('vlm_stage_base' if __name__ == 'vlm_stage' else 'vlm_stage')
P, LoadFailed, DeviceMismatch, TokenClock = base.P, base.LoadFailed, base.DeviceMismatch, base.TokenClock
NPU_TIMING_UNAVAILABLE = base.NPU_TIMING_UNAVAILABLE
_base_child_main = base.child_main
_MISSING = object()


class Engine(base.Engine):
    def __init__(self, backend, model_dir, contract):
        super().__init__(backend, model_dir, contract)
        hooked = None
        if backend == 'cuda':
            hooked = {role: type(ttft_probe.resolve(self.model, path)).__name__ for role, path in ttft_probe.MODULES}
        self.evidence['diagnostic'] = dict(hooked_modules=hooked, event_note=ttft_probe.EVENT_NOTE if hooked else None)

    def _restore(self, probe, previous):
        problems = probe.detach()
        slots = self.model.__dict__
        if previous is _MISSING:
            slots.pop('generate', None)
        else:
            slots['generate'] = previous
        if slots.get('generate', _MISSING) is not previous:
            problems.append('model.generate pass-through not restored')
        return problems

    def generate(self, data):
        before = ttft_probe.environment()
        if self.backend != 'cuda':
            result = super().generate(data)
            result['diagnostic'] = dict(scope='environment snapshots only; vendor MXQ model not instrumented',
                                        environment=dict(before=before, after=ttft_probe.environment()))
            return result
        seen, previous, original = {}, self.model.__dict__.get('generate', _MISSING), self.model.generate

        def entry(*args, **kwargs):
            seen.update(start=time.perf_counter(), streamer=kwargs.get('streamer'))
            return original(*args, **kwargs)
        probe = ttft_probe.Probe(self.model, cuda=True)
        try:
            self.model.__dict__['generate'] = entry
            result = super().generate(data)  # synchronizes the device before returning
        except BaseException as error:
            for problem in self._restore(probe, previous):
                error.add_note(f'diagnostic cleanup: {problem}')
            raise
        problems = self._restore(probe, previous)
        if problems:
            raise RuntimeError(f'diagnostic cleanup failed: {problems}')
        diagnostic = dict(scope='cuda: hooked vision/language_model/lm_head calls; host wall/thread/process spans + CUDA event spans',
                          event_note=ttft_probe.EVENT_NOTE, probe_errors=probe.errors, dropped_rows=probe.dropped)
        try:
            stamps = getattr(seen.get('streamer'), 'stamps', [])[1:]
            diagnostic['split'] = ttft_probe.split(probe.rows(), seen['start'], stamps)
        except Exception as error:
            diagnostic['split_error'] = repr(error)[:500]
        diagnostic['environment'] = dict(before=before, after=ttft_probe.environment())
        result['diagnostic'] = diagnostic
        return result


def child_main(backend, model_dir, contract, log_path, conn):
    base.Engine = Engine  # this spawned child's own base module; the unchanged loop then constructs the diagnostic Engine
    _base_child_main(backend, model_dir, contract, log_path, conn)


class VlmStage(base.VlmStage):
    """base.VlmStage unchanged; its spawn target is child_main above, bound only while the base __init__ starts the child."""

    def __init__(self, *args, **kwargs):
        base.child_main = child_main
        try:
            super().__init__(*args, **kwargs)
        finally:
            base.child_main = _base_child_main


def load(name, backend, model_root=None, source_root=None, onnx_path=None, expected_onnx_sha256=None):
    """Same signature/behavior as vlm_stage.load, with the diagnostic stage."""
    if name != P.STAGE:
        raise ValueError(f'unknown stage {name!r}')
    return VlmStage(backend, model_root)
