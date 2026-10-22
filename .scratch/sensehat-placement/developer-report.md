# Sense HAT 예측·배치 smoke — 구현 보고 (developer, 2026-10-21)

구현: Claude Opus 5.5 (공식 Claude Code 구독). 레벨: **코드 존재 + 관련 로컬 테스트 통과**. 클러스터 실행·실장비 성능·공식 수용은 **미수행**(coordinator 독립 리뷰 후 실행).

## 1. 범위와 산출물

새 파일만 추가했다(`experiments/sensehat-placement/`). 기존 코드·artifact는 읽기만 했고, discovery 입력은 `sha256sum -c SHA256SUMS` 결과 변경이 없다.

| 파일 | 역할 |
|---|---|
| `stage_runtime.py` | ConfigMap에 그대로 실리는 stdlib 런타임. T1 창 검증/특징(mean·last·source digest) → T2 동결 직선 예측(forecast·persistence) → T3 결과 포장. HTTP 서버(`serve`) / 단일 POST 클라이언트(`client`). |
| `forecast.py` | 원본 hash/연속성/metadata 검증, 채널별 floor 60/20/20 분할, 8행 창 → 다음 원본값, `statistics.linear_regression` 학습, held-out 평가, 요청 선택, `prepare()`. |
| `run.py` | CLI: `--prepare`, `--dry-run`(로컬 전용), `--execute`(클러스터). |
| `verify.py` | 읽기 전용 검증기. 원본 byte 사본에서 모델·창·target·reference·plan을 재구성해 대조한 뒤 receipt chain·수치·배치 coverage·Pod 안전성·cleanup을 확인한다. 부분 실행은 통과하지 않는다. |
| `fake_kubectl.py` | **테스트 전용** kubectl 대체물. 적용된 Pod마다 로컬 loopback IP(127.0.0.N)에서 실제 `stage_runtime.py` 프로세스를 띄운다. 클러스터 증거로 쓰지 않는다. |
| `test_*.py` | 26개 unittest. |

## 2. 고정 설계 구현 내용

- **입력**: `artifacts/sensehat-placement/discovery-20261021/history/{temp_humidity,humidity,pressure}.json`(manifest sha256 대조), 채널별 900행, seq 395..1294 연속, 단일 uid/config/session/generation. 간격 800–1201ms의 자연 jitter는 허용하고, 엄격 증가만 요구한다. 다음 경우는 보간/패딩/제거 없이 `SourceError`로 명시 실패한다: gap, metadata 변화, 시각 비증가, rawPayload 불일치, truncated, split별 행 부족.
- **분할(채널별)**: 540 / 180 / 180. 창+target은 split 경계를 넘지 않는다.
- **모델**: `y = a·mean(window8) + b`(`statistics.linear_regression`). x 분산이 0이면 `a=0, b=training target mean`이고 `zero_variance_x=true`로 기록한다. 학습 행 sha256·개수·처음/마지막 seq·시각을 `model.json`에 동결한다.
- **기준선**: persistence(창의 마지막 원본값).
- **선택 요청(실행 전 동결)**: 채널별로 warmup 1개(첫 calibration 쌍)와 evaluation 3개(첫·중간·마지막 쌍, 9행 span 서로소)를 고른다. ID 예: `humidity-evaluation-1115/1200/1286`, `*-calibration-935`.
- **plan**: `random.Random(20261024)`로 배치 블록 순서와 블록별 9개 eval 순서를 고정한다. 고유 배치당 3 warmup + 9 eval = 12건이고, 최대 4블록 = 48 workflow다.
- **target 격리**: `targets.json`은 로컬에만 두고 ConfigMap 키는 `stage_runtime.py, model.json, windows.json`뿐이다. T1은 schema 밖 키(예: `target`)가 있으면 400으로 거부한다. 오차는 runner가 **응답 수신 후에** target을 읽어 계산한다. source Pod가 아니라 runner 측에서 계산하며 Pod에는 target이 없다.
- **receipt**: 단계별로 다음을 기록한다: node, pod_uid, hostname, backend(impl/python/machine/pid), model_sha256, source_sha256, input/output sha256+bytes, `compute_ms`, `downstream_rpc_ms`(중첩 RPC), `handler_total_ms`. source receipt는 frozen payload sha와 `e2e_ms`(edge source Pod에서 T1 요청부터 최종 응답까지의 monotonic 시간; target·artifact·창 생성·학습 제외)를 기록한다.
- **HTTP**: stdlib `http.server` + `http.client`. body 상한 128 KiB(초과 413, Content-Length 없으면 411), 응답 상한 1 MiB, timeout 명시, 자동 재시도 없음(`attempts: 1`). 실패 시 그때까지의 receipt와 오류를 보존한다.
- **수치 reference**: prepare에서 동일 함수로 로컬 CPU reference를 만든다. 각 attempt 결과는 abs/rel 1e-9로 비교하고, T3 sha의 완전 일치 여부(`t3_sha_equals_reference`)는 별도로 기록한다.

## 3. 클러스터 runner (`--execute`, coordinator 실행용; 미실행)

1. prepared hash를 확인한다. `artifacts/sensehat-placement/sensehat-<UTC>`를 새로 만들고(`exist_ok=False`) code/prepared 사본을 둔다. 보호 Pod는 uid/name/ns/phase/ready만 projection해 저장한다.
2. 불변 ConfigMap 1개를 만든다.
3. **default-scheduler probe**: t1/t2/t3 Pod 3개를 dev0003/ser0002 후보로 unpinned 배치한다(동일 requests 100m/64Mi). Ready를 기다린 뒤 안전 projection(env 제외)과 해당 Pod events를 `scheduler-observation.json`에 저장한다. 배치 overhead(`apply_to_all_ready_s`)는 따로 기록한다. pool 배포 전에 관측한다.
4. **pinned pool**: source@dev0003 1개와 t1/t2/t3 × {dev0003, ser0002} 6개, 총 7 Pod. default 배치는 같은 pool Pod로 re-pin한다. fixed 배치와 같으면 `shares_with`로 측정을 공유하고 블록을 생략한다.
5. health: source Pod에서 각 Pod의 `/health`를 확인한다(node/pod_uid/model sha 일치 필수).
6. 측정: 요청마다 `kubectl exec -i <src> -- python3 /config/stage_runtime.py client --url http://<srcIP>:8080/run`를 실행하고, attempts.jsonl에 **요청마다 즉시** flush한다. 오류 행도 보존한다.
7. finally: events, logs를 남기고 **run manifest의 resource만** delete한다. 이어서 wait delete, `run-id` 라벨 잔여 0 확인, 보호 Pod before/after 비교를 하고 `cleanup.json`에 기록한다. 측정 중 오류가 나면 그 오류를 다시 raise하고, 그렇지 않은데 cleanup이 실패하면 RuntimeError를 낸다. 실패 attempt가 하나라도 있으면 exit 1이다.

- 모든 kubectl 호출은 argv 배열로 실행하고 shell을 쓰지 않는다. 호출마다 `NNN-stem.{stdout,stderr,event.json}`을 남겨, timeout이 나도 부분 출력이 보존된다. `phases.jsonl`에는 각 phase의 start/end/duration을 기록한다.
- Pod는 hostNetwork/hostPath/privileged가 없고, capabilities drop ALL, readOnlyRootFilesystem, runAsNonRoot 65532, SA token 미마운트, `enableServiceLinks: false` 설정이다. 이미지는 지정 digest를 그대로 재사용하며 pip이나 모델 다운로드는 없다.
- qdisc·지연 주입·재배치·Session/네트워크 제어 import는 없다.

## 4. 실행 명령 (확인한 것)

```bash
cd experiments/sensehat-placement
rtk proxy python3 -m unittest test_forecast test_stage_runtime test_run test_execute   # 26 OK, ~18s
rtk proxy python3 run.py --prepare ../../artifacts/sensehat-placement/discovery-20261021/history --output <NEW_DIR>
rtk proxy python3 run.py --dry-run --prepared <NEW_DIR>          # kubectl/rtk 미호출(테스트로 확인)
# coordinator 전용 (미실행):
rtk proxy python3 run.py --execute --prepared <NEW_DIR>
rtk proxy python3 verify.py --prepared <NEW_DIR> --run ../../artifacts/sensehat-placement/sensehat-<UTC>
```

scratchpad prepare 결과(로컬, 참고용): model_sha256 `5ae6e406…58f3`. dry-run 결과는 `reference_reproduced=true`, ConfigMap 146,592 bytes, Pod 10개(probe 3 + pool 7), max 48 workflow, 블록 순서 `default-scheduler, edge-all, mixed, server-all`이다.

### held-out 전체 평가 (채널별 172쌍, 로컬, 단위별; 교차 단위 집계 없음)

| 채널 | 단위 | 직선 MAE / RMSE | persistence MAE / RMSE |
|---|---|---|---|
| temp_humidity | °C | 0.0568 / 0.0713 | 0.0780 / 0.0998 |
| humidity | %RH | 0.2162 / 0.2915 | 0.3028 / 0.3919 |
| pressure | hPa | 0.01495 / 0.01798 | **0.01209 / 0.01558** (persistence가 더 좋음) |

15분 단일 구간, 단일 세션의 값이다. pressure에서는 개선이 없으므로 그대로 보고한다. 이상 탐지나 신경망 결과가 아니며 일반화하지 않는다.

## 5. TDD red → green 기록

| 슬라이스 | red | green |
|---|---|---|
| A 원본 검증/분할/학습 (`test_forecast`, 10) | `ModuleNotFoundError: forecast` | 10 OK |
| B HTTP 다중 프로세스 체인 (`test_stage_runtime`, 6) | `server did not start` (serve 미구현) | 6 OK |
| C CLI prepare/dry-run 실데이터 (`test_run`, 4) | run.py 없음 → 2 fail + 1 error | 4 OK |
| D fake kubectl execute + verify (`test_execute`, 6) | `ModuleNotFoundError: verify` | 첫 실행 1 fail(검증기 `"target` 문자열 검사가 note `"target-free…`에 오탐) → 재귀 key 검사로 수정 → 6 OK |

포함된 독립 예제는 다음과 같다:
- 선형 ramp 정확 예측(a=1, b=9)
- 미래 target 변경 시 모델·창·예측 불변
- sequence gap 거부(`149 -> 151`)
- metadata(session) 변경 거부
- hash 불일치 거부
- 변조 receipt 검출
- forecast 1e-6 변조 검출
- 원본 재구성 불일치 검출(manifest까지 재동결해도 검출)
- 실패 exec 보존 + cleanup 완료 + verify 거부
- default 배치 공유(mixed)와 별도 4번째 블록(48건) 두 경우

green 이후 수정:
- (1) `urllib.build_opener`가 호출마다 CA 저장소를 로드해 hop당 약 13ms가 측정에 섞이던 문제를 `http.client`로 교체해 해결했다. 로컬 체인 e2e가 약 38ms에서 약 3ms로 줄었다. Pi에서는 이 artifact가 더 컸을 것이다.
- (2) fake kubectl이 set-based selector를 빈 집합으로 처리해 pool wait가 공허하게 통과하던 문제를 고쳤다. source도 `group=pool`로 라벨링하고, fake는 equality AND를 파싱하며 매칭이 없으면 실패한다.
- 위 수정 후 전체 26 OK.

## 6. 한계·미검증 (coordinator 확인 필요)

- **실제 클러스터 미실행.** fake kubectl은 runner I/O·소유권·cleanup 배선만 증명하며, KubeEdge 동작은 검증하지 않는다.
- 미확인 항목:
  - edge Pod `kubectl exec` 동작(기존 run_smoke는 dev0003에서 사용)
  - Pod IP 간 edge↔server 도달성
  - 이미지 python 버전(3.11+ 가정; stage_runtime은 3.8+ 기능만 사용)
  - `immutable` ConfigMap 및 runAsNonRoot 65532의 ConfigMap 읽기
- e2e는 HTTP 연결을 매번 새로 연다(keep-alive 없음). 연결 수립 비용이 포함되며, 순수 네트워크 시간으로 해석하지 않는다. compute는 μs 수준이라 배치 차이는 사실상 전달·연결 overhead다.
- 표본은 블록당 eval 9건이다. 기술통계(중앙값/최소/최대)만 제공하며 p95·유의성·throughput 주장은 하지 않는다.
- 입력은 동결된 물리 Sense HAT 이력을 edge source Pod에서 재생한 것이며, 연속 라이브 센서 E2E가 아니다.
- 이 실험은 고정 배치 3종과 실제 default-scheduler 관측 1종의 진단이다. greedy/exact 최적화 비교나 새 알고리즘이 아니다.
- 요청된 `claude-implementer`/`tdd` 스킬은 이 세션의 스킬 목록에 없어 로드하지 못했다. TDD는 위 표처럼 수동으로 진행했다.
- 커밋·배포·클러스터 쓰기는 하지 않았다.
