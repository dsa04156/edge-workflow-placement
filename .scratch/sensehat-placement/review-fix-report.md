# 독립 리뷰 지적 수정 보고 (review-spec-resumed / review-standards-resumed)

작성: 2026-10-21, Claude Opus 5.5 (공식 구독). 클러스터 접근·배포·의존성 추가·commit 없음. 기존 artifact/prepared bundle 미변경.

## 범위와 수정

| 지적 | 수정 (파일) | 회귀 테스트 (red → green 확인) |
|---|---|---|
| (1) 실제 admitted Pod 안전성 미검증 | `run.project_pod`에 `security` 투영 추가: host namespace, Pod securityContext, init/일반/ephemeral container의 name·image·securityContext, volume 이름·source 종류·ConfigMap 이름. env/args는 복사하지 않음. `verify.observed_pod_problems`가 계약(컨테이너 `worker` 1개, rootfs read-only, escalation 없음, privileged/추가 cap 없음, drop ALL, runAsNonRoot, volume = run ConfigMap 1개)을 검사. `run.refuse_unsafe`가 scheduler probe와 pool 배포 직후, 측정 전에 위반 시 중단. `verify.pod_safety`도 관측 투영(scheduler-observation + pool-pods)을 다시 검사. | `AdmittedUnsafePod`: fake admission이 privileged sidecar(env에 sentinel secret)를 주입 → execute는 `admitted Pod violates safety contract`로 중단, `attempts.jsonl` 없음, owned Pod 정리됨, teardown 시작/종료 기록, secret이 artifact에 없음. `test_observed_pod_with_extra_container_fails_pod_safety`. |
| (1) 부수 발견 | 이 테스트가 실제 누출을 드러냄: `get pod -o json` 원본 stdout(admitted sidecar env 포함)이 Recorder에 의해 저장됐음. `Recorder.call(project=...)`로 원본은 메모리에서만 투영하고 투영 결과만 `.stdout`에 저장. | 위 sentinel 검사(수정 전 `00N-pod-state.stdout` 3개에서 검출). |
| (2) receipt 무결성 | `stage_runtime` receipt에 실제 stage `output` 포함. `verify.check_attempt`: receipt `run_id`/`request_id` = run/request, T1 입력 sha256+bytes = frozen payload의 canonical 재계산, T2/T3 입력 = 직전 출력 sha256+bytes, 각 출력은 기록된 output을 재해시해 sha256+bytes 일치, 출력 내용은 로컬 reference와 abs/rel 1e-9로 수치 비교(부동소수 byte 동일성 요구 안 함). source `payload_bytes` 검사. | `test_receipt_identity_and_byte_tampering_is_detected`(request-id, run-id, input/output/payload bytes, T1 출력·T2 입력 hash를 같은 임의값으로 동시 변경). `test_intermediate_output_uses_numeric_tolerance_not_float_bytes`: 자기일관적인 T2 변경 +1e-12는 통과, +1e-3은 실패. |
| (3) 동결 코드 hash 미검증 | `forecast.check_code`: `CODE_FILES` 전부가 prepared-manifest `code_sha256`과 일치해야 함. `run.execute`가 kubectl 호출·artifact 디렉터리 생성 전에 검사(불일치 시 rc 2, "새 디렉터리로 --prepare"). `verify.code_provenance`: preparation = collector-source 사본 = design.json = ConfigMap으로 보낸 runtime. | `test_execute_refuses_source_edited_after_prepare`: 복사한 코드로 prepare 후 `stage_runtime.py` 수정 → rc 2, kubectl/rtk 미호출, artifacts 미생성. `test_collector_source_differing_from_frozen_code_is_detected`. |
| (4) phase 경계 | placement별 `warmup:<label>`, `evaluation:<label>` phase span. `teardown`도 phase(실패 경로 포함, cleanup 오류 시 status `error`와 errors 기록). `summary.json` block에 `warmup_phase_s`, `evaluation_phase_s`. `verify.phases_closed`는 필수 phase 목록(기존 단계 + 실행된 placement별 warmup/evaluation + teardown)과 중복 시작을 검사. | `test_missing_placement_phase_is_detected`, 완료 run의 phase/summary 단언, 실패 run의 teardown 단언. |

## 로컬 검증

```
rtk python3 -m unittest discover -s experiments/sensehat-placement -p 'test_*.py'
Ran 34 tests in 18.977s — OK
```
수정 전 같은 suite: 신규 회귀 14건 FAIL/ERROR(의도된 red). 수정 후 34/34 통과. 클러스터 JSONPath 조회(358행×5열)와 host `test_execute` 7건은 지시대로 반복하지 않음.

## 영향·잔여

- **기존 prepared bundle(`artifacts/sensehat-placement/prepared-20261021t1116`, `prepared-20261021t1130`)은 그대로 보존**했지만, 코드(`forecast/stage_runtime/run/verify.py`)가 바뀌어 `--execute`가 이제 이들을 거부함. 실행 전 새 디렉터리로 `--prepare`를 다시 해야 함.
- receipt가 stage 출력(수백 byte)을 담아 응답이 조금 커짐. 측정 구간(source e2e)에 포함되는 직렬화 비용이며, 이전 bundle 측정값과 직접 비교하지 말 것.
- 관측 Pod 계약은 엄격함(volume은 run ConfigMap 1개만, 컨테이너는 `worker` 1개만). 실제 클러스터 admission이 무해한 필드를 추가하면 측정 전에 fail-closed로 중단됨. 실 kubectl 출력에 대한 동작은 미검증(fake kubectl 기준).
- 레벨: 코드 존재 + 관련 로컬 테스트 통과. 실제 운영 배포·실장비 실행·공식 수용 아님.
