# Preserve control outcomes across exec EOF
Status: ready-for-human

Observed: wan-20261020t053336 command528 guard-False-vision ended with
`error sending request: ... unexpected EOF`. No final restore proof.
The remote mutation's execution status cannot be inferred from that stderr.

Acceptance: reproduce the collector abort on injected transport EOF; validate
no duplicate side effects after lost response, no replay after partial execution,
and immutable command binding; run owned-Pod guard/restore diagnostic and then
fresh two-block collection. Preserve failures and cleanup evidence.

## Outcome
Local receipt recovery passed and live081744 completed3guard/restore cycles,
4receipt replays, cleanup0. Fresh082011 qualified585 and evaluated firstblock24,
then3consecutive readonlyrestoreEOFs stopped it. Observed evidence audit passed;
missing secondblock and failed restore remain explicit. Diagnostic085217 then
failed after2workerproofs on repeated network-control EOF; cleanup0 for both.
Unvalidated workerfallback/calibrationreuse candidate saved as patch and removed
from default collector. Infrastructure control blocker remains, no root-cause fix.
Report docs/experiments/budget-completion-attempt.md.
