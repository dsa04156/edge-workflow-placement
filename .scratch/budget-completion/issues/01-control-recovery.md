# Preserve control outcomes across exec EOF
Status: ready-for-agent

Observed: wan-20261020t053336 command528 guard-False-vision ended with
`error sending request: ... unexpected EOF`. No final restore proof.
The remote mutation's execution status cannot be inferred from that stderr.

Acceptance: reproduce the collector abort on injected transport EOF; validate
no duplicate side effects after lost response, no replay after partial execution,
and immutable command binding; run owned-Pod guard/restore diagnostic and then
fresh two-block collection. Preserve failures and cleanup evidence.
