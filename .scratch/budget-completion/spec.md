# Complete the static server-budget comparison
Status: ready-for-human

User authorized proceeding with the static comparison on 2026-10-20.
Review base: 992b85e. Preserve all prior partial observations.

First validate recovery at the existing collector → owned Pod control / cleanup
boundary. An exec EOF may precede execution, follow a completed command, or
interrupt a partially executed command. Never blindly repeat a network mutation.
Keep per-attempt events/stdout/stderr, assert Pod UID and frozen source hash.
Test this public process boundary using a real local child process and injected
transport response loss. This extends the previously approved execution/cleanup
validation seam for the requested control recovery.

If safe recovery is validated, collect a fresh calibration and complete the
existing two-block 10/2Mbps, two-server-slot design without changing its policy,
input, quality, randomization or failure accounting contracts. Old and new
observations remain separate. No tuning until an advantageous plan appears.
Report descriptive block effects for greedy, HEFT service variant, one-swap and
exact enumeration. Two temporal blocks do not establish statistical superiority.
Do not claim a paper-ready dynamic method or complete independent replication.

Recovery receipts must bind operation ID, Pod UID, source and input. Lock and
journal before side effects; reuse completed receipts after a transport EOF.
If an operation was started but has no complete receipt, stop as indeterminate.
Never replay inference. Kernel state and connectivity remain independently
checked by the existing guard/restore/drain gates.

Semantica discovery failed (unsupported tool call); durable outcomes go in the
experiment documentation and this issue until the service is available.

Outcome: first block24/24 and fresh qualification585/585 verified, but readonly
restore exec failed3times; later control diagnostic also failed3times.
Secondblock and final restore incomplete. Unvalidated fallback candidate removed
from default code; owned resources0. See docs/experiments/budget-completion-attempt.md.
