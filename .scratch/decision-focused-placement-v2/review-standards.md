# Standards review

Reviewed actual `before/` snapshot diff, all four new Python files, module README, AGENTS.md/common instructions, spec preservation rules, and issue-tracker/domain documents. No implementation edits. GLOSSARY.md and docs/adr are absent; nothing created.

**Implementation verdict:** no blocking documented-standard violation found in changed code.

## Procedural findings (separate from smells)

- **P2 — ownership/preservation risk.** Rule: spec “범위와 소유권” and AGENTS.md preservation of other work. Evidence: `developer-report.md:89` reports selecting `/tmp/tmp*/r/results.json` by newest mtime and deleting its directory with `rm -rf`; ownership was not established. `developer-report.md:64` also records shell redirection to `/home/jinuk/tmp_unused`, outside allowed paths; redirection could truncate pre-existing content. These are reported execution actions, not implementation diff hunks. Actual unrelated-data loss is **unverified**, not asserted. Record exact deleted target/ownership evidence if available; future cleanup must use the exact allocated path. The three frozen artifact hashes still match, so this is not evidence that benchmark data was destroyed.
- **P3 — confirmed procedural violation.** Rule: common instructions/spec require `rtk` or `rtk proxy` for shell commands. `developer-report.md:88` acknowledges unwrapped reads, authentication checks and tests. No demonstrated numerical impact; subsequent correction does not erase the deviation.

The early benchmark run is disclosed. Current v2 result/source hashes match, overrides are empty, and all 750 runs/raw counts are present. No evidence found of result-driven retuning; the history of execution and ownership of the redirected file cannot be established from these artifacts alone.

## Smell baseline

Checked Mysterious Name, Duplicated Code, Feature Envy, Data Clumps, Primitive Obsession, Repeated Switches, Shotgun Surgery, Divergent Change, Speculative Generality, Message Chains, Middle Man, and Refused Bequest. No actionable smell finding. Possible duplication in new `run_synthetic_v2.py:84,133` mirrors v1 summary/CLI logic, but preserving the explicitly frozen v1 CLI outweighs extracting shared infrastructure now. Factory/stream-key seams are expressly required; dictionary schemas and compact mathematical names follow existing conventions.

## Evidence and limits

Independent unittest discovery: **68 passed, 11.891s**. Strict JSON/provenance checks passed; all three frozen v1 hashes match. No cluster validation claimed.

Model verified through Orca `worker-show --dispatch ctx_e6e3811245e5`: effective launch and live provider both `gpt-6-astra`, corroborated by terminal banner. Review covers Standards only; Spec assessment remains separate.
