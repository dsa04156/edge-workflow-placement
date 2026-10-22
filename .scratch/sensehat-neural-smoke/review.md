# Independent Sense HAT neural smoke review

Verdict: PASS for the recorded CPU-only smoke, with the bounded-execution defect and evidence limitations below. No critical numerical or leakage defect found in the observed run. Implementation and cluster were not modified or operated by this reviewer.

Scope: spec.md; current experiments/sensehat-neural-smoke/*.py and tests; frozen run nsmoke-20261022t025228z including source, model, inputs, uploads, results, phases and cleanup events; docs/experiments/sensehat-neural-smoke-20261022.md. Orca 1.4.235 reviewer launch receipt records requested/effective gpt-6-astra. The scoped independent review takes precedence over the generic code-review skill's broader parallel review workflow.

## Standards and numerical assessment

- MLP forward, ReLU derivatives, mean-squared-error gradient scaling and Adam bias corrections are consistent by inspection. No padding, repeated artificial inference work or inflated task payload is present.
- Chronological 540/180/180 split produces 461/101/101 windows without boundary crossing. fit uses training rows only for statistics and updates; changing heldout values leaves fitted parameters unchanged in the test. Validation supplies warmup only, with no model selection loop. First/middle/last test indices are fixed 0/50/100.
- Forward timing excludes normalization, loading, denormalization, serialization and exec overhead. The nested clock reads add small measurement overhead; thread_time measures the invoking thread rather than whole-process CPU. Thread environment requests one BLAS thread but actual thread-pool size is not independently attested. These limitations do not invalidate this short smoke.
- Frozen model bytes independently hash to 993c1d851754f2f6bb6230957d94858b7d6a2f704b8d57824953da31848bfdc4; frozen inputs hash to c7f1bc963789518419fc690a2e02c3084a200234a8b4079e7e8b998ff125120b. Both upload manifests match those values; both remote reports independently attest the model hash. Input hash is recorded at upload construction, not independently recomputed inside each Pod.
- All saved predictions and model parameters are finite. Independent arithmetic on saved reports reproduces each site's maximum error versus reference, 1.9073486328125e-06 %RH, below 0.001. Recomputed local predictions from saved model and inputs differ from reference by at most the same amount.

## Spec findings

1. Medium, nonblocking for this completed run: smoke.py cleanup() uses fresh per-command timeouts outside execute()'s left() deadline. Two log calls, delete, deletion wait and remaining-resource check can consume up to another 230 seconds after the nominal 300-second live budget; preparation also has no explicit 60-second timeout. Therefore the code does not enforce the spec's whole-phase timeout under failure. The saved successful run took only 10.1007 seconds including cleanup and training took 0.1849 seconds, so neither bound was exceeded in the reviewed evidence. Keep cleanup safety, but describe its separate allowance or enforce the agreed end-to-end bound before claiming all failure paths are capped at five minutes.
2. Evidence limitation: frozen source neural.py lacks the nonfinite guard now present in current neural.py, and current test_neural.py adds the regression test. This concurrent change was not made by the reviewer. Frozen infer.py and smoke.py match current bytes. The old parity helper could accept NaN; all recorded predictions were independently checked finite, so this does not overturn the run. The final document explicitly identifies this later validator correction and preserved frozen source; no remaining correction is requested for this point.
3. Documentation's experiment-wide zero-resources check is not evidenced by this run's retained events: 014-remaining queries the exact run-id only. The exact owned-run cleanup claim is supported by successful named deletion, deletion wait and empty remaining output. Additional coordinator evidence may support the broader statement, but it was not inspected here.

## Validation and supported claims

Read-only test invocation (no bytecode writes, existing environment, no installation):

```sh
rtk proxy env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=experiments/sensehat-neural-smoke /home/jinuk/.cache/uv/environments-v2/a18ede1e7dd8e4d9/247089773a96f0f2/bin/python -B -m unittest discover -s experiments/sensehat-neural-smoke -p test_neural.py -v
```

Result: 8 tests passed in 0.151 seconds. Reviewer environment NumPy is 2.5.4, distinct from the recorded training/remote 1.26.4; this is additional validation, not a reproduction of that environment. No retraining or cluster commands were performed. Original history file and reused forecast/Recorder implementation are outside the assigned read scope, so their manifest/continuity enforcement is relied on rather than independently re-audited. Full 101-window MAE was not independently recomputed from the original history; its calculation is correct by code inspection and the saved result is 0.268667 versus baseline 0.288567 %RH.

Node/Pod identities, Python/NumPy versions and phase records are present. Recorded wall forwards are edge 0.095221/0.095314/0.093722 ms and server 0.039613/0.038886/0.038464 ms. Reports appropriately limit conclusions to historical October 21 replay, three overlapping inputs, CPU forward, uncontrolled interference and no placement/network/DAG or significance claims. The last selected window is worse than last-value baseline and is retained. Training-host version/authentication assertions in the document were not independently attested from the scoped run artifacts.

Only this review report was written. Remaining work is the coordinator's disposition of the timeout finding and documentation/evidence limitations; no live rerun is necessary to establish the recorded finite-output parity.
