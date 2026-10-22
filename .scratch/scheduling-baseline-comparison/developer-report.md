# 기본 스케줄러 대 프로파일 배치 소규모 비교: 구현 보고

2026-10-21. 구현 담당: Claude Opus 5.5 (공식 Claude Code 구독). 상태: **구현·로컬 테스트 완료, 실측 미실행**. 클러스터 접근, 배포, kubectl, commit/push는 하지 않았다.

## 변경 파일 (모두 신규)

- `experiments/real-topology-live/run_baseline_comparison.py` — 수집기
- `experiments/real-topology-live/verify_baseline_comparison.py` — 읽기 전용 감사기
- `experiments/real-topology-live/test_baseline_comparison.py` — 단위·통합 테스트 21개
- `.scratch/scheduling-baseline-comparison/developer-report.md` — 이 문서

기존 collector, runtime, 알고리즘, asset, 문서와 다른 작업자의 변경은 수정하지 않았다.

## 구현 요약

- `BaselinePilot(BudgetPilot)`: `BudgetPilot`의 journaled control 및 readonly EOF 재시도 경로를 그대로 상속한다. `__init__`에서는 `Pilot.__init__`만 호출하므로 greedy-joint-budget spec이 아니라 이 과제의 `implementation-task.md`가 `spec-frozen.md`로 동결된다. `calibration_run`은 항상 None이어서 기존 동결 profile을 재사용하지 않고, 매 실행마다 새로 보정한다.
- `deploy_operators`는 slot2 검증을 하지 않는다. 배포 직전에 실제 `capacity()`를 다시 읽어 `<label>-capacity-current.json`에 저장한다. 고정 배치는 동결 profile과 현재 물리 잔여 용량 양쪽에 대해 `validate_placement`로 확인한 뒤 `Pilot.deploy_operators`를 명시적으로 호출한다.
- 입력은 원래 `load-pairs.json` 순서에서 calibration 앞 4쌍(1713820, 3733440, 4556100, 3355890)과 evaluation 앞 3쌍(3506790, 5341940, 787710)이다. 기존 `qualify_all`, `transfer_profiles`, `trace`는 모두 `self.pairs`를 split으로 걸러 쓰므로, 축소 목록을 `self.pairs`로 두면 전체 경로가 이 부분집합만 사용한다. qualification은 9 task-node × (warmup 5 + 7) = 108행이다.
- 정책 6개:
  - kube-default: 고정하지 않은 실제 default-scheduler 선택. 후보 3노드를 `nodeSelectorTerms`로만 제한한다.
  - greedy, heft: `select_policies`의 sequential 경로.
  - greedy-swap: `simple_alternatives`.
  - exact: 기존 `ours` 열거를 출력 이름만 바꾼 것.
  - no-network: 기존 ablation.
  - profile에 `admission_budget`가 있으면 거부한다.
- K0는 조건마다 한 번 새로 관측한다(`scheduler-<cond>.json`: 노드, Pod UID, PodScheduled, Scheduled event, validation). 관측 후 Pod를 제거하고, 평가에서는 같은 배치를 다시 pin해 배포한다. 따라서 이 결과는 scheduler가 고른 배치의 성능 평가이며, 원래 스케줄링 비용을 재현한 것이 아니다. scheduler Pod 기록은 image, imageID, command/args, mount 경로, config hash로 한정하고 env는 저장하지 않는다.
- 일정: seed 20261023 하나로 조건 순서 [10, 2]를 정한 뒤 같은 rng로 조건별 고유 배치 그룹 순서를 섞는다. profile 정책은 `profile-plans-frozen.json`, K0를 포함한 전체는 `plans-frozen.json`, 일정은 `schedule-frozen.json`으로 평가 전에 저장한다. 같은 조건에서 같은 배치는 관측 하나를 공유하고, 조건이 다르면 공유하지 않는다. 그룹마다 warmup 1회(calibration)와 eval 3회를 0.1 rps, deadline 120 s로 실행하며 최대 48요청이다.
- 비용 기록: `phase(session, name)`이 `phase-NNN-<name>.start/.end.json`을 남긴다. end 기록에는 monotonic duration, status(ok/error/timeout), error, parent가 들어가며 `finally`에서 실패해도 기록된다. 대상 구간은 다음과 같다.
  - scheduler-provenance
  - deployment-helpers
  - network-preflight
  - qualification
  - transfer-calibration-<cond>
  - planning-<cond>
  - scheduler-selection-<cond>
  - evaluation-<label>

  evaluation 구간은 deployment를 포함하며, deployment 시간은 기존 `<label>-deployment.json`의 `elapsed_ms`에 별도로 남는다. 감사기는 겹치는 구간을 합산하지 않도록 parent가 없는 최상위 phase만 합산한다.
- 실패하면 `failure.json`에 error, traceback, 부분 mapping, 완료된 label을 남기고 `session.close()`로 정리한다. 이때 `cleanup.json`과 `protected-after.json`이 저장된다.
- 감사기는 다음을 확인하고 위반을 하나씩 기록한다.
  - 선언한 설계와 일치하는지
  - source hash: `baseline-provenance.json`, `git-source.json`, `planner-source.json`과 동결 사본의 일치
  - qualification 108행의 입력 순서, warmup, quality, model, Pod UID
  - p75: 4개 표본의 선형 보간 `sorted[2]*.75+sorted[3]*.25`를 직접 계산해 대조
  - transfer 관측 수: image/audio/return/control 12, vision/speech 36
  - `admission_budget` 부재와 물리 용량
  - profile digest, 정책 선택 재계산 일치, 평가 전 동결 여부
  - K0: 관측 배치와 같은지, default-scheduler인지, 3-term unpinned manifest인지
  - 6정책 전부의 물리 실행 가능성
  - 일정이 seed로 재계산한 결과와 같은지, 같은 조건·같은 배치 관측이 중복되지 않는지, policy map 일치
  - 요청마다 receipt 3개: Pod UID, node, model, device, thread, input hash/bytes, join hash/bytes, 최종 payload hash, Top5, score 오차, deadline
  - drain/backlog 0, phase 시작·종료 짝, cleanup `remaining_owned 0` 및 `errors []`, 보호 workload 불변

  실패하거나 timeout된 요청 행은 attempts와 failures에 포함하며 버리지 않는다. 결과는 `passed`, `invalid`, `partial`(failure.json이 있거나 completed.json이 없음) 중 하나다. stdout에 작은 JSON을 출력하고, `passed`가 아니면 exit 1을 반환한다. `--output`은 배타적으로 생성하므로 기존 파일을 덮어쓰지 않는다.

## 실행한 명령과 결과

```
rtk proxy python3 -m unittest discover -s experiments/real-topology-live -p 'test_baseline_comparison.py' -v
→ Ran 21 tests ... OK
```

첫 실행의 red는 테스트 기반 결함이었다. `cls.run`이 `TestCase.run`을 가렸고, fake qdisc에 native 필드가 없었으며, tamper 디렉터리 이름이 충돌했다. 감사기 자체에도 결함이 두 가지 있었다. tamper한 profile 때문에 `native_backlogs`와 `profile_plans`가 예외로 중단되던 것을 감사 오류로 기록하도록 고쳤다.

테스트를 먼저 쓰지 않았으므로 의미 있는 red는 변이 검사로 확인했다(`/tmp/claude-1002/-home-jinuk-codex-work-scheduler/8d24843c-66eb-40b1-9c2d-16a4f560dba4/scratchpad/mutants.py`). 변이 4개 모두 해당 테스트를 실패시켰다.

- M1: `BudgetPilot.deploy_operators`의 slot2 검증을 복원 → server C/C/C 배포 테스트 실패
- M2: evaluation 부분집합 크기 변경 → 축소 설계 literal 테스트 실패
- M3: 감사기의 receipt 검사 제거 → pod_uid 변조 거부 테스트 실패
- M4: kube-default를 server 고정으로 바꿔치기 → 손으로 유도한 그룹 테스트 실패

기존 관련 테스트는 다음과 같다.

```
rtk proxy python3 -m unittest discover -s experiments/real-topology-live -p test_budget_placement.py   → OK
... -p test_placement_policies.py → OK
... -p test_swap_move_baseline.py → OK
... -p test_control_receipt.py    → OK
rtk proxy python3 experiments/real-topology-live/run_baseline_comparison.py --help     → (--dry-run | --execute) 필수
rtk proxy python3 experiments/real-topology-live/run_baseline_comparison.py --dry-run  → 축소 설계 출력, artifacts/topology-profile-live 항목 수 56→56
```

## 테스트 근거

- 기댓값 literal 중 profile 재계산값은 `offline-decisions.json`에서 가져왔다. 동결된 `wan-20261021t044624/profile-calibration-{10,2}.json`으로 계산하면 10 Mbps에서는 5정책 모두 C/C/C이고 exact proxy는 683.6156961871893이다. 2 Mbps에서는 greedy/heft/swap/exact가 E0/C/C, no-network가 C/C/C다. 이 profile 파일은 읽기만 했다.
- fake cluster는 `call`(kubectl) seam에서만 끼워 넣었다. 그래서 `Pilot.pod`, `Pilot.deploy_operators`, `remove_operators`, `capacity`, `observe_scheduler`는 실제 코드가 실행된다. 데이터 경로(trace, qualify, transfer, drain)는 fake다.
- 손으로 계산한 fixture는 다음과 같다. 10 Mbps에서는 vision C 70 < E0 200이고 fusion C 100이므로 C/C/C다. 2 Mbps에서는 vision E0 200 < C 420이고 fusion C 240이므로 E0/C/C이며, no-network는 C/C/C다. fake scheduler가 10 Mbps에서 E1/E1/C, 2 Mbps에서 C/C/C를 고르면 그룹은 2+2 = 4개, trace 요청은 4×(1+3) = 16, 평가 시도는 12, receipt는 48이 된다.

## 한계와 미완료

- **실측 미실행.** 실제 kube-scheduler 선택, 네트워크 shaping, 모델 품질은 실장비 실행 전까지 미확인이다. fake 통합 테스트는 계약 검증일 뿐 성능 근거가 아니다.
- 단일 block이고, 조건당 고유 배치마다 eval 3회만 한다. smoke 수준이므로 CI, p95, 처리량 우월성, 독립 환경 반복, 새 holdout, 원 HEFT 재현은 주장하지 않는다.
- K0 선택은 shaping을 적용하지 않은 상태(guard 해제)에서 관측한다. scheduler 입력에 네트워크 상태가 없다는 전제이며, 별도로 검증하지 않았다.
- 평가 배포 직전의 물리 용량 재측정은 requests 기반이다. 실제 CPU 사용 압력은 반영하지 않는다.
- 감사기는 qualification RPC 응답 원문(`*-http-*.response.json`)을 다시 대조하지 않는다. 기존 `verify_placement.py`의 585행 전용 검사는 수정하지 않았고 이번 축소 run에는 적용되지 않는다. 필요하면 후속으로 추가한다.
- live 재개 명령(coordinator 승인 후): `rtk proxy python3 experiments/real-topology-live/run_baseline_comparison.py --execute` → `rtk proxy python3 experiments/real-topology-live/verify_baseline_comparison.py artifacts/topology-profile-live/<wan-run>`
