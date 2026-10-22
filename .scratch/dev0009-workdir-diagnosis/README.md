# dev0009 /work/control-receipts ENOENT 진단 (task_25483a5c9141)

결론·해석은 `docs/experiments/dev0009-workdir-diagnosis.md`. 이 디렉터리는 재현 도구와 원본 증거만 둔다.

- `make_pod.py <name> <node>` — 원 source Pod의 /work 수명주기 최소 재현(모델·ConfigMap 없음, 같은 이미지 digest/securityContext/emptyDir 8Gi/fsGroup).
- `probe.py`, `run_probe.sh <pod> <label>` — exec 쪽과 PID 1 쪽 /work의 lstat/ino/nlink/mountinfo/mnt ns 비교 + mkdir 시험.
- `deleted_dir_repro.py` — unlinked 디렉터리 안에서 `Path.mkdir(parents=True, exist_ok=True)` → 원 traceback과 같은 이중 FileNotFoundError 재현.
- `evidence/01..08` — 원 출력(UTC 시각 포함). 진단 Pod는 `06-cleanup.txt`에서 모두 삭제 확인.

## 재현 명령 (실행한 그대로)
```sh
python3 make_pod.py diag-wd-e9-a etri-dev0009-jetorn > pod-e9-a.json   # e9-b, e8-a(etri-dev0008-raspi5) 동일
rtk proxy kubectl apply -f pod-e9-a.json; rtk proxy kubectl apply -f pod-e8-a.json
rtk proxy kubectl -n topology-profile-lab wait --for=condition=Ready pod/diag-wd-e9-a pod/diag-wd-e8-a --timeout=120s
./run_probe.sh diag-wd-e9-a t0-e9                                       # evidence/01
rtk proxy kubectl apply -f pod-e9-b.json && rtk proxy kubectl -n topology-profile-lab delete pod diag-wd-e9-b --wait=true
./run_probe.sh diag-wd-e9-a t2-after-b-delete-N                         # evidence/02 (10s 간격 6회)
rtk proxy kubectl -n topology-profile-lab exec diag-wd-e9-a -c netctl -- python3 -c "$(cat deleted_dir_repro.py)"   # evidence/04
# host view (읽기 전용): node-exporter에서 /host/proc/<pid>/mountinfo 의 'empty-dir|configmap|//deleted' 와 edgecore starttime  # evidence/03,05,08
rtk proxy kubectl -n topology-profile-lab delete pod -l experiment=dev0009-workdir-diagnosis --wait=true   # evidence/06
```
