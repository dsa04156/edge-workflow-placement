# Fix four independent review findings only

You are not alone; preserve other changes. Own only the three new baseline comparison Python files from implementation-task.md and .scratch/scheduling-baseline-comparison/fix-report.md. No cluster, commits, dependencies, deleting existing artifacts. All shell commands through rtk/proxy including Orca. Read review-spec.md and review-standards.md and fix all four concrete defects. Use test-first for these public verifier/collector boundaries, not a rewrite.

- Cross-check real scheduler captured Pod node, UID, scheduler, scheduling state, candidate whitelist against observation. Never trust two duplicated placement dicts alone.
- Reconcile config/terminal run/phase/request identity against actual load_trace.py and edge_client semantics. Failed terminal rows may have different field availability; preserve failure without demanding success-only fields. Tests must mirror actual protocol, not invent it.
- Check final output_bytes against serialized output length.
- Avoid persisting scheduler Pod env in raw stdout. Restrict kubectl output BEFORE Session.call persistence using a field projection (e.g. JSONPath/go-template of only required fields) or a tightly scoped safe capture path, maintaining command start/end/timeout behavior. Do not just sanitize after saving raw response. No logs of credentials.

Run targeted tests and existing relevant tests if code affects them. Keep experiment size unchanged. Write concise fix report with actual red/green evidence and exact files. Then worker_done. Coordinator handles second independent review and limited live smoke. Reviewer models were host-verified via rollout as gpt-6-astra despite reviewer self-identification uncertainty; no need changing their reports.
