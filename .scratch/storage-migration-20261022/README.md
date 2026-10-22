# Scheduler HDD storage migration — 2026-10-22

Purpose: keep large source archives, model assets and upload bundles on the HDD while preserving existing absolute paths. No experiment results are discarded.

Destination: `/mnt/data3tb/user-storage/jinuk/scheduler-storage/`.
Expected mount UUID: `c04a3ad2-464d-4c86-b108-739be080acc3`.
Targets are listed in `targets.json`; successful per-target migrations are recorded in `migration.jsonl`. Each original is removed from SSD only after rsync full-content checksum and metadata comparison succeeds, and its symlink resolves onto the HDD filesystem. No tracked files were selected. Cross-filesystem copying preserves timestamps and hard links within each target.

## Future storage policy

- Keep code, Git metadata, active development environments and frequently accessed IDE indexes on SSD.
- Keep source datasets, model archives and deployment bundles under the linked asset directories. New files created within these directories go to HDD automatically.
- Keep experiment evidence. Do not delete or regenerate frozen results to reclaim storage.
- Go build cache is already linked to HDD; preserve that link.
- Do not relocate all of `artifacts`: it includes Git-tracked evidence and source. Do not relocate `/tmp`, `.scratch`, containerd, kubelet, or other users' homes wholesale.
- Before large download/build/run: verify the HDD is actually mounted with the UUID below and check free space on both filesystems. A symlink does not itself enforce mount availability or capacity limits.
- Operational target: keep SSD usage below 80%; investigate at 80%, clean/move verified cold files before 85%. These are working thresholds, not changed Kubernetes eviction settings.
- HDD has an existing UUID-based fstab mount with `nofail`. If absent at boot, the system can start without it. Do not replace broken storage symlinks with ordinary SSD directories; restore the mount first.
- This operation does not enable an automated disk alert or change global service startup dependencies. A service storing data on HDD should have a tested mount dependency before it is moved.

Read-only preflight:

```bash
rtk proxy findmnt -n -o UUID,TARGET -T /mnt/data3tb
rtk proxy df -h / /mnt/data3tb
```

Moving executable environments can affect tooling/performance; the EdgeAI `.tools`, JetBrains caches and etri home were only inventoried and were not moved.

## Follow-up migration

- `/home/etri/Downloads` -> `/mnt/data3tb/etri-storage/Downloads`; full rsync checksum, owner/group, ACL and xattr comparison completed. Original-directory jinuk write ACL verified through link.
- `/home/jinuk/.cache/huggingface` -> `/mnt/data3tb/models/huggingface-jinuk`; full checksum comparison completed. Future default Hugging Face downloads use HDD, unless an application explicitly overrides its cache directory.
- Existing Ollama model path `/mnt/data3tb/models/ollama` is already configured in running process and persistent user service drop-in. API `/api/tags` responded successfully with four model entries; no inference benchmark performed.
- NVIDIA SDK folder migration authorized by user after confirming no install/flash/download is active. Script preserves sparse files (`rsync -S`), ACLs, xattrs and numeric ownership. Completion is recorded separately in `etri-sdk-migration-20261022.log`.
- Full privileged measurements: containerd 41GiB; Docker 14GiB; kubelet 9MiB (same-filesystem traversal only); local-path-provisioner 3.8GiB. No container storage configuration was changed. The prior ~24GiB image filesystem metric is not the complete containerd directory usage.

SDK migration completed at 2026-10-22 17:21 KST after full checksum/metadata comparison. SSD original removed only after symlink cutover. See completion log.
