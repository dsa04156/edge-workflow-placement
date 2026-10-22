# Restartable init container 용량 계산 수정 (2026-10-22)

## 문제

`wan-20261022t050417`(Ready9 topology `ready-nodes-20261022`, config sha256 `ca90c999…`)는 qualification 324/324 품질 통과 후
`collect()`의 `session.capacity()`에서 `RuntimeError('Restartable init containers need explicit resource accounting')`로 중단됐다
(`failure.json`). 저장된 `701-pods.projected.json`에서 `restartPolicy: Always` init container를 가진 Pod는 34개였다.
모두 `etri-ser0001-cg0msb`에서 Running 상태인 Kubeflow/Knative Istio sidecar Pod이며, 형태는 `[일반 init 100m/128Mi, sidecar 100m/128Mi]`이다.
이 Pod들은 실험 workload가 아니다.

## 변경 (`experiments/real-topology-live/run_placement.py` `Pilot.capacity()`만)

Kubernetes v1.31.14 `pkg/api/v1/resource/helpers.go` `PodRequests` L46–118의 기본 옵션 의미론을 따른다
(검토한 원본 sha256 `1d9ceea06b84aa5784dbe3273f9d0eb9ad4c53bbc8f9923f8ebf48378c8557e2`). 계산은 자원별로 한다.

- steady = 일반 container request 합 + 모든 restartable init request 합
- init stage i 사용량 = init i request + i보다 앞선 restartable init request 합. restartable init은 자기 자신을 포함한 누적합이다.
- request = max(steady, max_i stage i) + overhead

다음 동작은 그대로다: 일반 init만 있는 Pod의 계산, request 누락 시 0, limit 미사용, Succeeded/Failed와 미할당 Pod 제외, 음수 잔여 허용, CPU m·memory MiB 단위.
`run_baseline_comparison.capacity_pods` projection은 이미 init 순서와 `restartPolicy`를 보존하고 env/args/annotation은 버린다. 그래서 이 파일은 수정하지 않았다.
새 collect phase도 추가하지 않았다. 대신 coordinator 재실행 전에 읽기 전용 live 용량 probe를 수행했다(coordinator 지시).

## 검증

| 레벨 | 결과 |
|---|---|
| 기존 실패 재현 | 저장된 `700-nodes.stdout` + `701-pods.projected.json`을 수정 전 코드로 replay하면 동일한 RuntimeError가 난다(`evidence/red.txt`, 3 errors). 클러스터는 변경하지 않았다. |
| 관련 테스트 red→green | `CapacityPersistenceTests` 7개 통과(`evidence/green-capacity.txt`). 다룬 경우: sidecar 순서(앞선 일반 init이 뒤의 sidecar와 겹치지 않음), 여러 restartable init, CPU/memory별로 다른 peak, overhead, 누락 request, limit 무시, 음수 잔여, raw 경로와 projection 경로의 결과 일치, 실패 snapshot 9노드 |
| 전체 `unittest discover` (real-topology-live) | 90 tests OK (`evidence/full-discover.txt`) |
| 읽기 전용 live probe | 2026-10-22 실행. `kubectl get nodes/pods`만 사용했다. topology preflight passed. 9노드 용량이 모두 양수이고 최소값은 dev0006의 2280m/2625MiB다. sidecar Pod는 여전히 ser0001에 34개 있다(`evidence/live-capacity-probe.json`). |
| 실장비 재실행 | 미실행. 다음 단계로 coordinator가 담당한다. |

정확한 diff는 `.scratch/restartable-init-capacity/delta-vs-before.diff`(기준 `before/` snapshot)에 있다.

## 한계

- Pod-level `resources`와 in-place resize(`AllocatedResources`)는 반영하지 않는다(v1.31에서 resize는 alpha 기본 off이고 Pod-level resources는 없다). `NonMissingContainerRequests`(scoring용 기본 request)도 반영하지 않는다. 이 값은 Fit 판정 의미론이 아니다.
- CPU/memory만 계산한다. GPU·hailo·ephemeral-storage 등 extended resource는 기존처럼 계산하지 않는다.
- `amount()`의 단위 파서는 기존과 같다. 10진 접미사(`M`, `G`, `k`)와 지수 표기는 지원하지 않는다. 이번 snapshot에는 없었다.
- 용량은 kube-scheduler NodeResourcesFit 근사값이다. scheduler cache나 reservation과 같다고 주장하지 않는다.

## Coordinator final validation and restart

Host independently reran all 90 tests: passed (20.948s). Live read-only Ready9 capacity check passed; evidence `.scratch/restartable-init-capacity/coordinator-live-probe/`. Independent Spec review: 0 findings. Standards review of supplied actual diff: 0 findings (initial reviewer file reads blocked by approval guard; final text review preserved). Developer actual model claude-opus-5-5; reviewers gpt-6-astra. New collection started: `wan-20261022t055541`; this is a running experiment, not a completed comparison result.
