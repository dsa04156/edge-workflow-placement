# 리뷰 결함 4건 수정 보고 (2026-10-21)

범위: `fix-task.md`만. 변경 파일은 아래 3개(모두 미추적 신규 파일)와 이 보고서뿐. 클러스터 접근·커밋·의존성 추가·기존 artifact 삭제 없음. 실험 규모 불변(`--dry-run`: qualification 108행, 최대 workflow 요청 48, calibration `[1713820, 3733440, 4556100, 3355890]`, evaluation `[3506790, 5341940, 787710]`).

| 파일 | sha256 (수정 후) |
|---|---|
| `experiments/real-topology-live/run_baseline_comparison.py` | `7cadf382…cb62` |
| `experiments/real-topology-live/verify_baseline_comparison.py` | `282f562b…d7a0` |
| `experiments/real-topology-live/test_baseline_comparison.py` | `9596c76d…c566` |

수정 전 사본: `.scratch/scheduling-baseline-comparison/before-fix/`(coordinator가 만든 것, 손대지 않음).

## 실제 프로토콜 확인 (테스트 fixture 근거)

- `run_load.Session.trace`: config `run_id=self.run`, `phase=label`; 산출물 디렉터리 `ASSETS/<run_id>`.
- `load_trace.run_trace.log`: **모든** terminal 행에 `phase` 부착. 성공 행의 `run_id`/`request_id`는 `http_runtime` fusion 결과(`request_id=phase-index`)에서 옴. 실패 행(`status='error'`, `driver_capacity`)에는 `run_id`/`request_id`/`e2e_ms`/`receipts`가 없음.
- 실장비 행으로 확인: `artifacts/topology-profile-live/wan-20261020t024540/eval-0-compressed-100-warm.rows.json`(성공: `run_id`, `request_id=eval-0-compressed-100-warm-0`, `phase`), `wan-20261019t103603/load-2-0.5-cloud.rows.json`(실패: `phase`만 있음).
- fixture를 이에 맞춤: FakePilot `run`=디렉터리 이름, 성공 행에 3개 필드, timeout 행은 실제 실패 행 형태(`status='error'`, `phase`만), fusion `output_bytes=len(compact JSON)`.

## 수정 내용

1. **스케줄러 Pod 증거 대조(P1)** — `verify_baseline_comparison.check_scheduler_pods`: 캡처된 `scheduler-<cond>-deployment.json` Pod 자체의 `metadata.name/uid/labels.run-id`, `spec.nodeName`, `schedulerName`, `PodScheduled=True`, 캡처 Pod affinity whitelist(=manifest=3개 후보, 노드 포함), profile 후보, 관측 레코드(`scheduler-<cond>.json`)의 name/uid/node/scheduler/scheduled/candidates, 그리고 uid별 `Scheduled` event(메시지가 있으면 노드명 포함)를 교차 확인. 중복 placement dict만 비교하던 문제 해소.
2. **workflow identity(P1)** — config `run_id == run 디렉터리 이름`, `phase == name`; 모든 terminal 행 `event=='terminal'`, `phase==name`; 성공 행(또는 id 필드가 있는 실패 행)은 `run_id`, `request_id == f'{name}-{index}'`. 실패 행에 성공 전용 필드를 요구하지 않음.
3. **최종 output_bytes(P2)** — `len(compact JSON) == fusion output_bytes` (`verify_load.py:83`과 동일 직렬화).
4. **스케줄러 env 영속화(P2)** — `run_baseline_comparison.projected_call`: `Session.call`과 같은 counter/tag, `rtk proxy kubectl --request-timeout=20s`, `timeout=40`, start/finish/returncode/status(`completed|error|timeout`) event를 `finally`에서 저장하되, stdout은 `PIPE`로 메모리에만 두고 `scheduler_provenance` 투영 결과만 `NNN-scheduler-snapshot.projected.json`에 저장. TimeoutExpired는 부분 stdout을 담으므로 event에 타입명만 기록. 검증기는 `*-scheduler-snapshot.stdout` 존재 시 거부.

## Red → Green 증거 (모두 `rtk proxy python3 -B -m unittest discover -s experiments/real-topology-live -p test_baseline_comparison.py`, 28 tests)

| 단계 | 결과 | 의미 |
|---|---|---|
| red1: 테스트·fixture만 변경 | `FAILED (failures=9, errors=16)` | 수집기가 kube-system Pod JSON을 영속 `Session.call`로 보냄(fixture가 거부) |
| red2: `projected_call` 적용 | `FAILED (failures=20)` | 남은 실패 중 2건은 테스트 파일 자체에 canary 리터럴이 있어 collector-source 사본에 걸린 테스트 결함 → canary를 런타임 생성으로 수정 |
| red3: 검증기 수정 전 | `FAILED (failures=17)` | 스케줄러 Pod 8개 변조, identity 6개 변조, 실패 행 phase 변조, output_bytes −123, raw snapshot 파일 → 모두 기존 검증기가 `passed` |
| green | `Ran 28 tests … OK` | |

추가 관련 테스트(각각 `rtk proxy python3 -B -m unittest <module>` in `experiments/real-topology-live`): `test_budget_placement`, `test_load_trace`, `test_swap_move_baseline`, `test_placement_policies`, `test_control_receipt` 모두 OK.

신규 테스트: `test_verifier_rejects_scheduler_pod_evidence_mismatch`(8 subtests), `test_verifier_rejects_wrong_workflow_identity`(6), `test_verifier_rejects_wrong_final_output_bytes`, `test_scheduler_snapshot_is_projected_before_persistence`(실행 디렉터리 전체 파일에서 canary 부재 + event 필드), `test_verifier_rejects_raw_scheduler_snapshot`, `test_scheduler_snapshot_failure_and_timeout_persist_no_env`(returncode 1 / TimeoutExpired 부분 출력 모두 canary 미영속, phase end status, cleanup, verify=partial), `test_failed_row_keeps_phase_identity_without_success_fields`.

## 한계

- 실장비 미실행. `Scheduled` event는 `kubectl get events` 캡처에 의존하므로, live에서 event가 누락·만료되면 검증기는 invalid를 낸다(의도적 보수 판정; 2차 리뷰/smoke에서 확인 필요).
- `run_id == 디렉터리 이름` 전제: artifact 디렉터리를 이름 변경해 검증하면 invalid.
- stderr는 기존 `Session.call`과 같이 파일로 남긴다(kubectl stderr에는 Pod env가 출력되지 않음). `kubectl` 자체가 stdout 외 경로로 env를 남기는지는 범위 밖.
