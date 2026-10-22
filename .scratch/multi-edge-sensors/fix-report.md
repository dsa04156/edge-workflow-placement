# Fix report — review r1 + r2 (task_c5832441e4db / ctx_d9c23a139c92)

- 구현 모델: 같은 Claude Code 구독 세션 `ee27a4b4-507a-4c62-ab3e-50f335bf8073`. transcript의 assistant `message.model` 집계
  (09:45:58Z 시점) `claude-opus-5-5` 371건/그 외 0건. coordinator가 09:38 이후 응답 99건 모두 `claude-opus-5-5`임을 별도 확인(msg_70e4be5491fe).
  모델/공급자 변경·하위 agent 없음.
- 클러스터 변경 0. 읽기 전용 확인 1건: 존재하지 않는 이름에 `kubectl delete --raw …?dryRun=All -f -` (NotFound) — stdin DeleteOptions 배선 확인용.
- 커밋/push 없음. 기존 artifact 수정 없음(새 artifact만 추가).

## r1 수용 결함

1. **소유권**: `Session.create`(server dry-run → `kubectl create -o json`, create-only), API 반환 UID·라벨 기록,
   `Session.delete_record`(`delete --raw` + `DeleteOptions.preconditions.uid`, API 서버가 UID 강제), 같은 이름의 다른 UID는 우리 것이 아님,
   시작 시 run 라벨 객체 존재하면 생성 전 거부, `pod_state`는 교체된 Pod 거부, cleanup은 생성 UID만 삭제.
2. **수치 검증**: `models.validate`(V/T/F 형태·유한성·정규화 계약, 미지원 task) + `compare`는 fail closed(zip 절단 없음, 정규화 cosine).
   verify.py는 모든 ok 평가 행 출력을 검증하고 형식 위반을 evidence error로.
3. **후보 품질 gate**: `reference.compute`(보정 seq만) → `calibration-reference.json`; qualification 후 `candidate_quality`가
   model hash·실행 node/Pod UID·source id/node/UID·입력 hash·출력 vs reference를 확인 → `candidate-quality.json`(valid/excluded+사유).
   profile·전송 probe·kube-default가 같은 valid 후보를 사용. verify가 gate 파일·seq·후보 부분집합·kube-default 후보 동일성을 확인.

### r1 통합 실패 원인 (진단)

첫 changed-collector 통합(`test_execute`, log `artifacts/multi-edge-sensors/offline-test_execute-r1a.log`)이 `payload_sizes`에서
"No quality-valid calibration rows for every task of w1"로 실패. TemporaryDirectory 삭제 때문에 같은 설정의 진단 run을 보존 경로로 재실행
(`artifacts/multi-edge-sensors/offline-diag-r1a-mes-diag/` + `.collect.log`). 그 `candidate-quality.json`에서 **F 96행 전부
`OSError(7, 'Argument list too long')`**, V/T는 주입한 ser0003 외 모두 통과(ser0003: V model hash, T similarity). F 보정 batch(12 `/fuse` 호출,
reference T 출력의 384차원 embedding 포함)가 controller `exec` 인자 하나로 Linux 단일 인자 한도(128 KiB)를 넘음 — 행 데이터가 원인을 직접
증명하므로 경쟁 가설 순위 매기기는 생략(coordinator msg_ff0326c03666 수용). 수정:
- F 보정 입력은 `/run`이 실제로 F에 주는 필드(`similarities`, `text_top_class`)만.
- `Session.remote`가 list body를 `REMOTE_ARG_LIMIT=96 KiB` 이하 순차 chunk로 나누고 행 순서 유지, 한 호출이 한도를 넘으면 명시적 예외.
- 회귀 테스트 `test_remote_batch`(같은 remote 경로, 실제 controller 프로세스).

두 번째 시도 `offline-test_execute-r2`(보존)는 `calibration-reference` 단계에서 `AttributeError: module 'reference' has no attribute 'compute'`:
새 `plan.py`가 real-topology-live 경로를 sys.path 앞에 다시 넣어 그쪽 `reference.py`가 import됨 → collector가 이 실험의 `reference.py`를
파일 경로로 로드하도록 수정.

## r2 수용 결함

1. **P1 Task-fastest**: `plan.taskwise_fastest` — V,T,F 고정 순서, 남은 CPU/메모리에 맞는 quality-valid 후보 중 측정 실행비용 최소(동률은
   노드 이름), 선택 시 자원 예약, 네트워크·임계경로 무시, 이후 task를 둘 곳이 없으면 `unavailable`(joint optimizer로 대체하지 않음).
   collector와 새 읽기 전용 CLI `plan.py --profile …`가 같은 `plan_all` 사용. 기존 `profile_only` 호출 제거(no-network joint 정책은 추가하지 않음).
2. **P2 decode**: V 계획 비용 = 행별 `decode_ms + compute_ms`의 median, `fetch_ms`(전송)와 분리; `profiles-frozen.json.candidates.<w>.cost_components`에 decode/compute 별도 보존.
3. **P2 F 응답 크기**: `run_response_sizes` — transfer-probe 단계에서 quality-valid V/T worker(서버 우선) + F probe Pod로 각 workflow 보정 seq의
   실제 `/run`을 호출, 응답 body bytes(결과 + V/T/F receipt, HTTP header 제외)의 median을 F→목적지 probe payload로 사용.
   `run-response-sizes.json`과 `payload_sizes.categories`에 task 데이터 bytes(`*_task_bytes`)와 receipt envelope bytes를 분리 기록.
   V/T→F는 F가 실제로 받는 `/infer` body(출력 + receipt).
4. **P2 F 품질**: `validate(..., names)` — V name이 class_id의 이름과 일치, T `text_top_class` = 최대 similarity 클래스, F top_class = top3 첫
   이름(점수>0), detections는 bool이 아닌 정수 0–300, 모든 클래스 이름이 모델 클래스에 존재. `compare`는 F top3 이름 순서, detection 수,
   text class도 비교(라벨/순서와 점수 위반을 별도 문자열로). verify: 클래스 이름 전달 + 같은 행의 F detections == V 검출 수(아니면 error).
5. **P1 중단된 생성**: `created_objects`는 완전한 문서를 파싱되는 즉시 보존하고 잘린 꼬리에서 멈춤(`complete=False`), 각 레코드는 즉시
   `session.resources`에 추가. UID가 돌아오지 않은 항목은 `unresolved`: cleanup에서 현재 존재 여부만 조회·보고, 삭제·채택하지 않음.
   `cleanup.json.cleanup_verified`는 잔여 0 + 미해결 0 + 삭제/목록 오류 없음일 때만 true, 아니면 collector가 실패. verify도 이를 요구.

## 테스트 (실제 실행; cwd `experiments/multi-edge-sensors`, `P=/mnt/data3tb/multi-edge-sensors-20261022/envs/pod311/bin/python`, 모두 `rtk proxy`)

| 명령 | 결과 |
|---|---|
| `$P -m unittest test_verify_numeric` (r1 verify 수정 전) | 6 중 5 FAIL — red 확인 |
| `$P -m unittest test_verify_numeric` (r1 후) | 6 OK |
| `$P -m unittest test_ownership` (r1 구현 후 첫 실행) | 3 OK (red 실행 없음) |
| `$P -m unittest test_execute -v` r1a | FAIL(1)/ERROR(9) — 위 진단, log 보존 |
| 진단 run (`fk3_run`, 보존 `offline-diag-r1a-mes-diag`) | collector rc 1 at payload_sizes, cleanup 잔여 0 |
| `$P -m unittest test_verify_numeric test_ownership test_plan …` (r2 구현과 함께 작성) | verify 11 OK, ownership 4 OK, plan 3 OK (red 실행 없음) |
| `$P -m unittest test_prepare test_collect test_verify_numeric test_ownership test_plan` | 28 OK, 16.1 s |
| `MES_TEST_RETAIN=… $P -m unittest test_execute -v` r2 (보존 `offline-test_execute-r2/`) | FAIL — reference import 충돌, 위 참조 |
| `$P -m unittest test_remote_batch` | 2 OK (첫 실행은 `/payload`가 JSON이 아니어서 테스트 자체 오류, `/health`로 수정) |
| `$P -m unittest test_prepare test_collect test_verify_numeric test_ownership test_plan test_remote_batch` (최종 코드) | 30 OK, 17.5 s |
| `MES_TEST_RETAIN=artifacts/multi-edge-sensors/offline-test_execute-r2b $P -m unittest test_execute -v` | 13 OK / 1 FAIL, 505.9 s (`offline-test_execute-r2b/unittest.log`): 실패는 테스트 기대 문구뿐 — ser0003 T 제외 사유가 r2 #4의 더 엄격한 검증으로 `invalid: text_top_class is not the highest-similarity class`가 되어 기대 문자열 'similarity diff'와 불일치. 제외 자체는 의도대로 |
| 기대 문구를 'highest-similarity'로 정정 후 `MES_TEST_REUSE=artifacts/multi-edge-sensors/offline-test_execute-r2b $P -m unittest test_execute -v` | Collection 8 OK, Audit 6 skip (보존 run 재검사 모드: 새 수집 없음; Audit 6개는 같은 r2b 전체 실행에서 이미 OK) — `recheck-collection.log` |

재실행하지 않은 것: YOLO export/MiniLM parity, 로컬 smoke(코드 경로 중 runtime 변경은 batch GET 지원뿐). export/RTSP/model 증거는 재사용.

## 최종 보존 증거 (오프라인 fake kubectl, 클러스터 증거 아님)

- `artifacts/multi-edge-sensors/offline-test_execute-r2b/runs/mes-fake/` (+ `result.stdout/.stderr`, `reference.stdout`, `unittest.log`,
  `recheck-collection.log`). 주입: ser0004 ImagePullBackOff, ser0003 손상(class 이름 역순 + YOLO 파일 hash 변경).
- candidate gate: ser0003만 제외 — V model hash 12행, T `text_top_class…` 8행, F fusion decision/score 12행; 각 task·workflow 유효 7노드.
- 계획: task-fastest/greedy-network/exact/source-local/server-fixed/kube-default 모두 ok, 고유 job 18, attempt 72(평가 workflow 54 ≤72).
  (fake는 한 x86 호스트이므로 배치 선택·지연 값에는 의미 없음.)
- 크기(body bytes, header 제외; w1): V→F 1651(V task 737), T→F 2623(T task 1717), F→목적지 2816(F task 340 + receipt envelope 2473).
  평가 `/run` body median과 10% 이내(테스트).
- cleanup: created 107, remaining 0, unresolved 0, preserved 0, `cleanup_verified=true`.
- 이전 실패 증거 보존: `offline-test_execute-r1a.log`, `offline-diag-r1a-mes-diag/`, `offline-test_execute-r2/`.
- 최종 bundle: `/mnt/data3tb/multi-edge-sensors-20261022/prepared-20261022d` (prepared.json sha256 `255126e5…ef32`, 사본
  `artifacts/multi-edge-sensors/prepared-20261022d/prepared.json`), dry-run `artifacts/multi-edge-sensors/dryrun-20261022d.json`(preflight passed).
  최종 `test_execute` 기대 문구 수정은 테스트 파일만이며 bundle code hash(runtime/models/rtsp/scenario/bootstrap)에 영향 없음.

## 남은 문제

- **실제 클러스터 실행 미수행**(coordinator 최종 targeted acceptance 후). `--execute`는 `prepared-20261022d`로.
- `delete --raw` + UID precondition은 fake와 읽기 전용 NotFound 배선 확인뿐; 실제 409 Conflict 경로는 live에서 처음 실행된다.
- 미해결 생성 객체(`unresolved_creation`)는 의도적으로 자동 정리하지 않는다 — 발생하면 사람이 UID를 확인해 처리해야 한다.
- `/run` 크기 보정을 위해 transfer-probe 단계에 V/T worker 2개가 추가된다(배포 비용 증가, E2E 미포함).
- 단일 인자 한도 대응은 chunking이며 한 호출 body가 96 KiB를 넘으면 명시적 실패(현재 최대 호출은 수 KB).
- MediaMTX API의 deprecated 필드(`ready`, `tracks`, `bytesReceived`) 사용은 그대로; v1.21.2에서 동작 확인, 결과 문서에 명시.
- arm64 실행, 4GB 엣지 메모리, dev0008/0009 image pull 가능성은 이전과 동일(라이브 qualification에서 기록).
