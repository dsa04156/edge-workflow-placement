# Final fix — 상속 capacity() 전체 Pod env 저장 차단

## 범위

review-standards-2.md P2만 수정. 변경 파일: `experiments/real-topology-live/run_baseline_comparison.py`, `experiments/real-topology-live/test_baseline_comparison.py`, 이 보고서. 원본 collector(`run_placement.py`, `run_budget_placement.py`, `run_load.py`), verifier, `snapshot()`은 수정하지 않음. 수정 전 사본은 `before-final-fix/`에 보존. 클러스터 접근·commit·의존성 추가·삭제 없음.

## 구현 (diff 45줄)

- `capacity_pods(snapshot)`: 허용 목록 projection. Pod마다 `metadata.{name,uid,namespace}`(있는 경우만), `spec.nodeName`, `spec.containers[].resources.requests`, `spec.initContainers[].resources.requests`와 `restartPolicy`, `spec.overhead`, `status.phase`만 남긴다. env/args/annotations/limits 등은 버린다.
- `BaselinePilot.call` override: `args == ['get','pods','-A','-o','json']`이고 stdin이 없을 때만 기존 `projected_call(..., capacity_pods, timeout)`으로 보내고, projection의 JSON 문자열을 반환한다. 부모 `capacity()`는 그대로 이 JSON을 파싱한다. 그 외 호출은 모두 `Session.call`을 그대로 사용한다. 결과 artifact는 `NNN-pods.projected.json` + `NNN-pods.event.json` + `NNN-pods.stderr`이고 `NNN-pods.stdout`은 남지 않는다.

## 테스트 (test-first)

수정 전 신규 테스트 결과: 2개 FAIL. `persisted_with == ['002-pods.stdout']`으로 리뷰 probe를 재현함.

신규 `CapacityPersistenceTests`(5개)는 실제 `BaselinePilot`/`Session.call`/`Pilot.capacity()`를 사용하고 `subprocess.run`만 mock했다(cluster용 `__init__`은 우회). canary는 env, args, last-applied annotation에 넣었다.
- raw env가 남지 않음: 디렉터리 전체 canary 스캔 결과 `[]`. nodes 호출은 기존 `.stdout`을 유지하고, pods event는 command/returncode/timeout을 유지한다.
- 물리 계산은 손으로 계산한 값과 일치한다. C: max(750m, init 1000m)+overhead 100m → 2900m, max(1536Mi, 256Mi)+64Mi → 6592Mi. Succeeded Pod는 제외. E0 Pending: init 우세 → 1000m/5120Mi. 같은 입력에 원본 `Pilot.capacity`를 raw 경로로 실행한 결과와도 동일하다.
- `restartPolicy: Always` init container는 여전히 `RuntimeError`이고, canary는 남지 않는다.
- pods timeout(전체 canary가 담긴 partial output)에서도 canary가 남지 않고 event status는 `timeout`이다.
- 다른 호출(namespace pods, apply+stdin)은 부모 `Session.call`로 가고 `projected_call`은 호출되지 않는다.

기존 scheduler-snapshot timeout fixture도 `raw[:50]` 대신 전체 raw를 쓰도록 바꾸고, 그 안에 canary가 들어 있는지 assert하도록 강화했다.

## 실행 결과 (로컬, 클러스터 없음)

- `rtk proxy python3 -B -m unittest discover -s experiments/real-topology-live -p test_baseline_comparison.py`: **33 passed** (기존 28 + 신규 5)
- `rtk proxy python3 -B -m unittest discover -s experiments/real-topology-live -p 'test_*.py'`: **65 passed**
- `run_baseline_comparison.py --dry-run`: OK
- tracked 기준 8,084개 해시 변경 0. verifier는 before-final-fix와 동일하다.

SHA-256:
- run_baseline_comparison.py `0344fd05a0cd9238af28d1d6c8a07fde1c4d71449244468d0b3476687ddda2ad`
- test_baseline_comparison.py `ac68a2c05527fabd579a01cb1cc7425da16c3e8734a1fdb0a17ebe1a27a865ad`
- verify_baseline_comparison.py `282f562ba27acdbbe2a99c6fa9e1a70eff87916c8e4e62a36a47233004fbd7a0`

## 한계

- 수준: 코드 존재 + 로컬 mock 테스트 통과. 실클러스터 수집은 실행하지 않았다.
- `get nodes -o json` raw stdout은 기존대로 저장된다(이번 지시 범위 밖; Node 객체에는 Pod env가 없음).
- projection은 `status`가 없는 Pod를 `phase: None`으로 보존한다. 원본은 KeyError가 나지만 실제 API 응답에서는 발생하지 않는다.
- `snapshot()`은 메모리에서만 축약하므로 변경하지 않았다.
