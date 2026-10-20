# Mounted launch-code failure and bounded mitigation

First collector wan-20261020t015739 completed9 task×node qualification groups and4 compressed transfer profiles/plans, then failed before any policy evaluation. Exact failure:3221-guard-False-source-e1.stderr reports FileNotFoundError for /config/net_control.py. Owned cleanup:0 remaining,0 cleanup errors. This first attempt is retained and must not be merged with the fresh retry's calibration/evaluation.

Ranked hypotheses: (1) deleting same-node Pods sharing a ConfigMap invalidates another mount; (2) transient edge ConfigMap/volume projection refresh; (3) edge exec targets a different container/context. Initial successful use of the same path weakens a static typo hypothesis. No root cause is claimed from one stack trace.

A two-Pod shared-ConfigMap probe on E1 (wan-20261020t021827) deleted its peer and repeatedly invoked runpy on the holder's mounted script. The25 post-delete observations retained the file: the full failure was not reproduced. Thus the shared-peer deletion hypothesis is not established; this smaller probe does not rule out long-lived refresh/exec behavior.

A controlled local replay of the observed missing-file condition reproduces FileNotFoundError on the legacy mounted-script path. The startup-artifact regression test then removes the mount after copying, asserts legacy runpy fails, and asserts the same script remains executable from its private frozen copy. This tests the mitigation for the confirmed symptom, not the unexplained disappearance cause.

Mitigation: snapshot and SHA256-check all launch files to each container's private emptyDir directory before readiness; bootstrap and lazy imports use that frozen root. Network-control commands execute the private script and compare downward-API Pod UID and script hash before any mutation. Existing collector timeout/stdout/stderr evidence remains. No cluster/host volume manager or existing workloads were changed.

Validation:12 runtime tests plus7 unchanged planner tests. A fresh full collector run is required because startup/runtime source changed. No first-attempt evaluation result exists.
