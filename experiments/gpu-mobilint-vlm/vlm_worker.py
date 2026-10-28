"""WORKER_MODULE for the unchanged quality-network-heterogeneous bootstrap.py: its Worker/serve with the VLM stage loader.

Receipts (node, Pod UID, input/output sha256, model sha256, device, queue, timing) and fail-closed load handling are the
reviewed heterogeneous worker.py behavior; only the stage loader differs. The shared Worker keeps only the exception text
of a failed load, so this adapter also attaches the bounded vlm_stage.LoadFailed record (reason incl. load_timeout, child
stdout/stderr tail, kill status) to the public load error, which /health and the /run 503 reply both return.
"""
import worker
import vlm_stage

serve = worker.serve


class Worker(worker.Worker):
    def __init__(self, backend, stage_names, **kwargs):
        records = {}

        def load(name, *args, **kw):
            try:
                return vlm_stage.load(name, *args, **kw)
            except vlm_stage.LoadFailed as error:
                records[name] = error.record
                raise
        super().__init__(backend, stage_names, **dict(kwargs, loader=load))
        for name, record in records.items():
            if name in self.errors:
                self.errors[name]['record'] = record
