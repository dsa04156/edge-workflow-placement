# 기한 지향 DAG 배치 v1 (`dag_deadline_guard`) — 2026-10-28

상태: **정책 프로토타입 코드와 관련 테스트 통과, 기존 r3 ledger 관측 감사 완료. R1 리뷰 수정 반영(§9).** 실제 클러스터 실행, 성능 비교, 공식 수용은 수행하지 않았다.
레벨 구분: 아이디어 ✔ / 코드 존재 ✔ / 관련 테스트 통과 ✔ (R1 후 39건) / 실제 운영 배포 ✘ / 실장비 성능 ✘ / 공식 수용 ✘.

## 1. 범위

- 사용자 지시(2026-10-28): "품질은 빼고 여기서 이제 이걸 고도화해봐야할거같아." 이 작업에서 품질은 최적화·admission·평가·결론에서 제외한다. 과거 품질 기록과 동결 artifact는 수정하지 않았다.
- 모델·정밀도·DAG(5 Task: image_preprocess, vision, audio_preprocess, speech, fusion)·입력 identity는 r3와 같고, 후보는 r3의 실행 가능 CPU/GPU/NPU 후보 그대로다 (`artifacts/unstable-network/un-collect-r3/live-candidates.json`, 144 plan):
  - image/audio_preprocess {cpu-c, cpu-e0}, vision {cpu-c, npu-c, cpu-e0, jgpu-dev0009-cu130}, speech/fusion {cpu-c, cpu-e0, jgpu-dev0009-cu130}.
- 품질 gate 대신 실행 전용 eligibility adapter `ExecutionOnly`를 쓴다(모든 실행 가능 조합 선택 가능). 기존 `QualityGate`는 수정하지 않은 동결 base 안에만 남아 있다.
- 새 알고리즘의 우월성·신규성을 주장하지 않는다. replay에서 성능을 주장하지 않는다.

## 2. 근거: 현재 코드와 로그 (관측이며 인과 분리가 아님)

- r3 수집: `artifacts/unstable-network/un-collect-r3`, 36 cell(2 block × stable/slow/burst × 6 정책), 3,462 arrival, plan `plan-20261027T111532Z` (sha256 `ee0aba54…c1e7`, release binding과 일치 확인).
- 정적 anchor: 시작 프로파일(`profile-good.json`, 큐 없음)의 정확 열거 최적 plan은 `image_preprocess=cpu-e0|vision=jgpu|audio_preprocess=cpu-e0|speech=jgpu|fusion=jgpu`(전부 엣지). 감사 도구로 재계산한 anchor가 dag_static의 logged 577행 전부와 같음을 확인했다(577/577). "timely 577/577"은 코디네이터 기록이며 이 문서에서 다시 집계하지 않았다.
- dag_reopt는 기한 여유가 큰데도 예측 makespan이 줄면 서버로 넘어간다. b0-slow-dag_reopt의 row 3(1.57 s)과 row 4(1.74 s)는 엣지 anchor 대신 cpu-c/npu-c plan(예측 128.2, 141.3 ms)을 골랐다. 같은 snapshot에서 anchor 예측은 422.0 ms와 377.2 ms이고, 둘 다 1,500 ms 이하이다.
- b0-slow-dag_reopt row 45(24.51 s)는 예측 324.1 ms였고 결과는 late_ok이다. b1-slow-dag_reopt_hysteresis row 44(24.31 s)는 예측 337.7 ms였고 결과는 timeout이다. 두 행의 anchor 예측은 각각 335.1 ms와 412.9 ms(budget 이내)이다. guard라면 anchor를 유지했겠지만, 그 결과가 어땠을지는 알 수 없다.
- `generator.learn`(`experiments/unstable-network/generator.py:235-244`)은 **두 branch가 모두 성공한 요청**에서만 compute/link EWMA를 갱신한다. 큐 poll 신선도(`policies.py:121` freshness)와 compute/link 증거 신선도는 서로 다른 것이다. stale fallback은 poll 나이만 본다.
- 결론: 복잡한 학습이나 모델 교체는 근거가 없다. v1은 기존 재최적화 위에 단순한 기한 조건만 더한다.

## 3. 결정 규칙 (`experiments/deadline-network/deadline_policies.py`)

1. **anchor**: 시작 시 동결 프로파일과 관측 큐 없이 기존 `best_plan`을 돌린 결과(= dag_static plan)를 고정한다.
2. 매 결정에서 dag_reopt와 **같은** live profile view(`LiveProfile`)와 같은 큐 모델(`Observation.queue_ms`)로 anchor를 예측한다.
3. `anchor_predicted_ms <= deadline_ms × anchor_fraction`이면 anchor를 유지한다(`anchor_within_budget`).
4. 그렇지 않으면 기존 dag_reopt 열거로 예측 makespan이 가장 작은 plan을 고른다(anchor도 후보에 포함). 결과는 `over_budget_reoptimized` 또는 `over_budget_anchor_still_best`로 기록한다.
5. poll이 한 번도 성공하지 않았거나 `stale_after_s`보다 오래된 경우, 상속받은 fallback이 anchor를 반환한다(`observation_fallback`). 이때 `fallback.freshness_guarantee=false`이고 `freshness_note`에 다음을 명시한다: poll 나이만 보는 fallback이고 compute/link 추정에는 나이가 없으므로 신선도를 보장하지 않는다.
6. 기본값은 `deadline_ms=2000`, `anchor_fraction=0.75`(threshold 1,500 ms)이다. 사전에 정한 공학적 여유값이며 결과를 보고 튜닝하지 않았다. `stale_after_s`는 guard 생성 시 유한한 양수(bool 제외)인지 검사한다(R1). NaN·inf·0 이하 값은 상속 fallback을 조용히 끄기 때문이다. 이 검사는 새 guard에만 있고, 기존 baseline build의 `stale_after_s` 처리는 그대로다.
7. 다음을 하지 않는다: trace·phase oracle, 노드·zone·장치 이름 기반 규칙, 정답이나 품질 입력, 신규 요청 거절, dwell. 예측이 budget 안으로 들어오면 즉시 anchor로 복귀한다.
8. 매 결정 기록: `anchor_plan_label`, `anchor_predicted_ms`, `deadline_ms`, `anchor_fraction`, `threshold_ms`, `selected_predicted_ms`, `choice_reason`, `fallback`.

한계: 이것은 SLO 인지 선택 **가설**이며 신뢰성 보장이 아니다. live 예측이 부정확하면 나쁜 anchor를 유지하거나 나쁜 대안을 고를 수 있다. 예측 makespan에는 origin dispatch 지연, 관측되지 않은 경합, 재시도가 들어 있지 않다.

## 4. 구현과 사용법

| 파일 | 내용 |
|---|---|
| `experiments/deadline-network/frozen_unstable_policies.py` | `experiments/unstable-network/policies.py`의 byte 동일 복사본(sha256 `f6ecfade…8870`, plan `copied_sources` 값과 같음). 수정하지 않는다. |
| `experiments/deadline-network/SOURCES.json` | 위 복사본의 출처와 sha256 |
| `experiments/deadline-network/deadline_policies.py` | `ExecutionOnly`, `DagDeadlineGuard(base.DagReopt)`, `policy_spec()`, `build()`. 기존 spec은 base `build()`에 그대로 넘긴다. |
| `experiments/deadline-network/audit.py` | CLI: `spec`(정확한 정책 spec과 통합 경계 출력), `audit`(기존 ledger 관측 감사) |
| `experiments/deadline-network/test_deadline_policies.py`, `test_audit.py` | stdlib unittest |

실제로 실행한 명령 (repo root):

```bash
rtk proxy python3 experiments/deadline-network/audit.py spec
# {"policy": {"name": "dag_deadline_guard", "deadline_ms": 2000.0, "anchor_fraction": 0.75}, "integration": {...}}

# R1 수정 후 감사 (새 경로; R0 출력 audit-un-collect-r3-20261028T015311Z 는 그대로 보존)
rtk proxy env PYTHONDONTWRITEBYTECODE=1 python3 experiments/deadline-network/audit.py audit \
  --run artifacts/unstable-network/un-collect-r3 \
  --plan artifacts/unstable-network/plan-20261027T111532Z \
  --out artifacts/deadline-network/audit-un-collect-r3-20261028T020451Z

cd experiments/deadline-network && rtk proxy env PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v test_deadline_policies test_audit
```

`audit`은 출력 디렉터리가 이미 있으면 거부한다(exit 2). 입력 파일은 읽기만 한다. 테스트에서 실행 전후 36개 ledger의 sha256이 같음을 확인한다.

### 통합 경계 (미구현)

- `experiments/unstable-network/generator.py`는 자기 `policies.build`와 `QualityGate`를 import하므로 `dag_deadline_guard`를 **받지 않는다**. 다음 runner는 `deadline_policies.build(spec, candidates, deadline_policies.ExecutionOnly(), profile, stale_after_s=...)`를 import하는 generator 복사본이 필요하다. 이 복사본은 이번 변경 범위에 들어 있지 않다.
- `spec.deadline_ms`는 같은 run의 trace `deadline_s × 1000`과 같아야 한다. audit CLI는 값이 다르면 경고를 낸다.
- decide 입력은 base `Observation`과 generator의 monotonic clock 그대로다. 경과 시간 의미는 Python `time.monotonic` 공식 문서를 따르며, 이 문서 확인은 코디네이터가 했다.

## 5. 테스트 결과

- R1 후: `rtk proxy env PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v test_deadline_policies test_audit` → **Ran 39 tests, OK** (`.scratch/deadline-network-20261028/logs/fix-r1-07-tests-final.log`). 기존 32건 + R1 회귀 7건(§9).
- R0: 같은 명령 → Ran 32 tests, OK (`logs/tests-final.log`).
- 정책 18건: 여유 기한에서 더 빠른 대안이 있어도 anchor 유지, 같은 관측에서 dag_reopt는 이동함을 대조, 높은 anchor 큐에서 서버 CPU+NPU plan으로 이동, NPU도 바쁘면 GPU로 이동, 부하가 풀리면 다음 결정에서 anchor 복귀, 같은 관측에서 두 deadline이 다른 결정을 냄, threshold 경계(같음 → 유지, 바로 위 → 재최적화 후 anchor 유지), stale·never·None fallback과 `freshness_guarantee=false`, NaN·inf·0·음수·문자열·bool 설정 거부, 미지 spec key 거부, 오해를 부르는 이름으로 바꿔도 결정이 이름 치환과 같음(지리 규칙 없음), 7개 baseline spec의 결정이 동결 base와 동일, base registry 비확장, 동결 base sha256 일치.
- 감사 14건: idle snapshot 재구성이 live 결정과 일치, 바쁜 anchor worker의 service 미기록은 non_reconstructible, receipt 없는 바쁜 worker는 profile 평균으로 정확히 재구성, stale·never snapshot은 정확한 fallback, snapshot 없는 행은 집계만 함, 결과·logged 결정·phase를 바꿔도 guard 결정이 같음, 행 순서 무관, 잘못된 snapshot 8종 거부, CLI subprocess(spec, 잘못된 fraction, 다른 plan 거부, 기존 출력 거부, 실제 동결 run 감사 3,462행 전수 계정과 입력 불변).
- TDD 기록(정직하게): 첫 behavior만 red(모듈 없음) → green이었다. 구현을 한 번에 작성했기 때문에 이후 테스트는 처음부터 통과했다. 테스트가 결함을 잡는지는 mutation 4건으로 확인했다. `<=`를 `<`로 바꾼 경우, fraction을 무시한 경우, budget 안에서 재최적화한 경우, 서비스 미기록 검사를 제거한 경우 모두 실패가 났다(`logs/tdd-03-mutation-check.log`).
- 기존 unstable-network 테스트는 다시 돌리지 않았다. base가 hash 동일 복사본이고 기존 파일은 수정하지 않았기 때문이다. 통계 검정은 하지 않았다.

## 6. 관측 감사 결과 (R1: `artifacts/deadline-network/audit-un-collect-r3-20261028T020451Z/`)

R1 재감사의 모든 집계 수치와 `rows.json`은 R0 출력(`audit-un-collect-r3-20261028T015311Z/`, 보존)과 같다. `audit.json`에서 달라진 것은 `code_sha256`뿐이다(§9).

무엇인가: 각 logged 행의 snapshot(`row.observation`, generator가 logged 정책 결정 직전에 기록한 값)에서 guard가 **내렸을** 결정을 계산하고, logged 정책이 실제로 고른 plan과 비교한 것이다.
무엇이 아닌가: replay가 아니다. guard 자신의 plan은 큐와 이후 snapshot을 바꿨을 것이다. guard의 지연·기한 충족·goodput·성공률은 **추정하지 않는다**(`claims` 필드). 행의 결과(status)는 logged plan의 결과이며, 기술적 층화를 위해 나중에 join만 한다.

재구성 규칙:
- 큐 상태, 나이(정규화 시계 NOW에서 `success_at = NOW − age_ms/1000`), `compute_est`, `link_ms_per_byte`는 기록된 값 그대로 쓴다.
- `compute_est`와 `link_ms_per_byte`는 snapshot에 dict로 **있어야** 한다. 기록된 빈 dict `{}`는 "아직 receipt 없음"으로 유효하다. 필드가 없거나 null·list 등 dict가 아니면, 또는 값이 음수·비유한이면 거부한다(R1, ValueError → CLI exit 2). 없는 필드를 "receipt 없음"으로 읽으면 바쁜 worker가 exact로 둔갑하기 때문이다.
- `Observation.service_ms`는 **기록되지 않는다**. worker의 service_ms는 그 worker의 compute_est가 있을 때 정확히 존재한다(`record_receipt`가 둘을 함께 씀). 그래서 service_ms가 있던 바쁜 worker의 큐 추정은 compute_est로 **만들어내지 않고** `non_reconstructible`로 분리한다. idle worker와 receipt가 없는 바쁜 worker(base가 동결 프로파일 평균을 씀)는 정확하다. 근사 sensitivity 경로는 만들지 않았다.
- snapshot(`generator.py:344`)과 결정(`:346`)은 live 객체를 수 µs 간격으로 두 번 읽은 값이다. 그 사이 poller나 learn이 끼어들 수 있으므로, 상태 없는 logged 정책(greedy_comm, dag_reopt, dag_reopt_no_network)을 자기 snapshot에서 다시 결정시켜 fidelity를 쟀다.

- non_reconstructible 행의 `worker` 필드: base `predict()`가 plan의 worker를 set 순서(PYTHONHASHSEED 의존)로 방문하므로, 바쁜 미기록 worker가 둘 이상이면 처음 걸린 worker가 seed마다 달랐다. R1부터는 같은 part(anchor/alternative)에서 바쁜 미기록 worker 중 정렬상 첫 worker를 보고한다. 분류(category·reason)는 원래부터 seed와 무관했다.

출력 hash (R1): `rows.json` sha256 `90ce618f06a85a74a6a255157aa816d9852893d50351834fa9c67d34163a24d7`(R0과 같음), `audit.json` sha256 `da9bf11a8e6266322b769772fd65326eef370e8d2e6c1ed408113c47382a2a2e`. code sha256은 `audit.py fe759c12…`, `deadline_policies.py ae01b5f6…`, `frozen_unstable_policies.py f6ecfade…`(불변)이다. R0의 code sha256은 `audit.py d08b2597…`, `deadline_policies.py b8018aa0…`였고, R0 `audit.json`은 `f75dda7b…`이다. 입력 sha256 전체(plan, release, cells, profile, candidates, collection, 36 request, 36 ledger)는 `audit.json.input_sha256`에 있다.

### 6.1 감사 가능 범위와 재구성 fidelity

| 항목 | 행 |
|---|---|
| 전체 arrival | 3,462 |
| snapshot 없음 (전부 admission_drop) | 187 |
| fallback (poll 나이 기준, 정확) | 59 |
| exact | 2,621 |
| non_reconstructible (전부 anchor worker 바쁨 + service 미기록; 보고 worker jgpu-dev0009-cu130 488, cpu-e0 107. 그중 anchor worker 둘 다 해당하는 35행은 R0에서 seed에 따라 보고 worker가 달랐음) | 595 |
| **guard 결정이 정해지는 행 (exact + fallback)** | **2,680 / 3,462 = 77.4%** (snapshot 있는 3,275행 중 81.8%) |
| replay fidelity (greedy_comm/dag_reopt/dag_reopt_no_network 1,613행) | match 1,252, predicted_mismatch 1 (plan은 같고 predicted_ms만 다름, 쓰레드 경합 추정), non_reconstructible 360, plan·fallback 불일치 0 |

### 6.2 정책별 결정 비교 (같은 snapshot에서 guard vs logged 정책)

정의: agree/differ는 guard plan과 logged plan을 비교한 것이며, guard plan과 logged 결정이 모두 있는 행만 센다. anchor 유지/이동은 guard plan이 있는 행이 기준이다. logged=anchor는 logged plan이 anchor와 같은 행의 수다.

| logged 정책 | 행 | exact | fallback | snapshot 없음 | non_recon | guard anchor 유지 | guard 이동 | agree | differ | logged=anchor |
|---|---|---|---|---|---|---|---|---|---|---|
| dag_reopt | 577 | 453 | 17 | 17 | 90 | 466 | 4 | 174 | 296 | 198 |
| dag_reopt_hysteresis | 577 | 458 | 11 | 28 | 80 | 464 | 5 | 184 | 285 | 225 |
| dag_reopt_no_network | 577 | 428 | 17 | 73 | 59 | 445 | 0 | 0 | 445 | 0 |
| dag_static | 577 | 456 | 0 | 0 | 121 | 452 | 4 | 452 | 4 | 577 |
| greedy_comm | 577 | 385 | 5 | 28 | 159 | 385 | 5 | 144 | 246 | 192 |
| task_fastest | 577 | 441 | 9 | 41 | 86 | 450 | 0 | 0 | 450 | 0 |
| **합계** | 3,462 | 2,621 | 59 | 187 | 595 | 2,662 | 18 | 954 | 1,726 | 1,192 |

trace별(합계 같은 정의): burst 1,032행 (exact 704 / fallback 55 / 없음 89 / non_recon 184, 이동 2), slow 1,290행 (957/4/98/231, 이동 9), stable 1,140행 (960/0/0/180, 이동 7). block별 수치는 `audit.json.by_block`에 있다.

이유 합계: anchor_within_budget 2,603, over_budget_reoptimized 18, observation_fallback 59, over_budget_anchor_still_best 0.
guard가 이동한 18행의 위치: b0-slow-dag_static 3, b1-slow-dag_static 1, b0-stable-dag_reopt_hysteresis 4, b0-slow-dag_reopt_hysteresis 1, b0-stable-greedy_comm 3, b1-burst-greedy_comm 2, b1-slow-dag_reopt 4. 예를 들어 b0-stable-dag_reopt_hysteresis row 3에서 hysteresis는 anchor 예측 2,260.5 ms인 상태로 anchor를 유지했고, guard라면 cpu-c/npu-c plan(예측 136.5 ms)으로 이동했을 것이다.

### 6.3 결과의 기술적 join (logged plan의 결과이며 guard 결과가 아님)

- agree 954행의 logged status: ok 954. differ 1,726행: ok 1,447, late_ok 203, timeout 76.
- 이 수치는 **guard의 성공률이 아니다**. differ 행에서 guard plan은 실행되지 않았다. agree 행도 guard 실행이었다면 큐와 이후 상태가 달랐을 것이다. 이 수치로 정책 성공을 채점하지 않는다.

### 6.4 해석 한계

- 감사는 "같은 관측에서 무엇을 골랐을지"만 보여준다. guard를 실제로 실행하면 anchor(엣지 cpu-e0+jgpu)에 부하가 몰리고, 그 결과로 큐와 이후 결정이 달라진다. 1.6 rps에서 엣지가 포화되는지는 이 감사로 알 수 없다.
- non_reconstructible 595행은 전부 anchor worker가 바쁜 행이다. guard의 기한 조건이 실제로 의미를 갖는 바로 그 상황에서 감사 범위가 비어 있다.
- 증거 신선도: compute/link EWMA는 성공한 요청에서만 갱신되고 나이가 기록되지 않는다. 그래서 fallback 59행 이외의 행에서도 오래된 추정이 쓰였을 수 있다.

## 7. 다음 실측 비교 계약 (PREPARED, 미실행)

1. **비교군**: dag_static(=anchor), dag_reopt, dag_reopt_hysteresis(rel 0.15, abs 50 ms, dwell 5 s), dag_deadline_guard(2000 ms, 0.75). 네 정책 모두 같은 generator 실행기·입력 cohort·후보 144 plan·poller·관측을 쓴다. 품질은 제외하고 `ExecutionOnly`를 쓴다.
2. **로깅 추가**: 결정마다 worker별 `service_ms`, compute/link 추정의 마지막 갱신 나이와 갱신 수, guard 기록 필드 전체. generator 복사본에서 추가하며 기존 파일은 수정하지 않는다. 기록하는 snapshot은 `decide()`가 실제로 소비한 **바로 그 원자적 관측 복사본**이어야 한다(service_ms·증거 나이 포함). 현재 generator처럼 live 객체를 snapshot용과 결정용으로 두 번 읽지 않는다. 그래야 §6의 snapshot/결정 경합(predicted_mismatch 1행)이 생기지 않는다. 이번 R1에서 runner는 구현하지 않았다.
3. **부하 조건**: (a) r3와 같은 1.6 rps·60 s·stable/slow/burst·같은 seed 규칙·deadline 2 s·cancel 10 s·max_inflight 8·stale 3 s. (b) 그보다 높은 부하는 **비교 전 별도 calibration**에서 anchor 고정 실행으로 엣지 포화 지점(offered rps 대비 기한 내 완료와 큐 길이)을 찾고, 그다음 부하 값을 동결한다. 평가 결과를 본 뒤 후보가 이기는 부하를 고르지 않는다.
4. **분모**: timeout·admission_drop·failed·not_sent·미완료를 포함한 전체 offered 요청을 분모로 쓴다.
5. **보고 항목**: 기한 내 goodput과 완료율, tail(성공 지연 분위수와 censored 실패를 따로), 선택 overhead(`decision_ms`), plan 전환 수, 경로별 bytes, CPU/GPU/NPU 사용, 비용. 같은 workload 겹침과 네트워크 제어(netctl trace, 정책 순서 block 균형) 조건을 유지한다.
6. **주장 제한**: 신규성이나 우월성을 주장하지 않는다. 2 block 탐색은 유의성 근거가 아니다. 같은 plan이 선택된 행을 독립 표본으로 중복 집계하지 않는다.

## 8. 외부 근거

외부 인용은 최소로 했다. Context7은 quota로 실패했고, GitHub 코드 검색에서 이전 가능한 검증된 구현은 찾지 못했다(코디네이터 기록). 경과 시간 의미는 Python 공식 문서 https://docs.python.org/3/library/time.html#time.monotonic 만 참조한다. 외부 알고리즘에 대한 주장은 하지 않는다.

## 9. R1 리뷰 수정 이력 (2026-10-28)

Spec 리뷰 r1(P2 ×2)과 Standards 리뷰 r1(P2 ×2, 하나는 Spec 1번과 같음)을 반영했다. 수정마다 회귀 테스트를 먼저 쓰고 실패를 확인한 뒤 최소 수정으로 통과시켰다(로그: `.scratch/deadline-network-20261028/logs/fix-r1-*.log`).

| # | 결함 | red | 수정 | green |
|---|---|---|---|---|
| 1 | snapshot에 `compute_est`/`link_ms_per_byte`가 없거나 null·list이면 `or {}`가 "receipt 없음"으로 읽어 바쁜 worker를 exact로 만듦. 음수 값도 허용 | 8 subtest 실패 (`fix-r1-01`) | `audit.reconstruct`: 두 표가 dict인지 요구하고, 값은 `_duration`(유한, 0 이상)으로 검사. 빈 dict는 유효 | `fix-r1-02` |
| 2 | `stale_after_s` NaN·inf 등이 검사 없이 base로 넘어가 상속 fallback이 꺼짐 | 6 subtest 실패 (`fix-r1-03`) | `DagDeadlineGuard.__init__`에서만 `_finite`로 유한 양수(bool 제외) 검사. `_finite`는 값을 직접 받도록 바꾸면서 쓰이지 않던 `low_open` 인자를 제거(private, 호출자 1곳, 동작 동일) | `fix-r1-04` |
| 3 | non_reconstructible `worker`가 PYTHONHASHSEED에 따라 달라 `rows_sha256`이 비결정적(seed 0/1 `90ce618f…`, seed 3 `cb9cac83…`) | 3-seed subprocess 감사 불일치 (`fix-r1-05`) | `audit.guard_on_snapshot`에서 같은 part의 바쁜 미기록 worker 중 정렬상 첫 worker를 보고. 동결 base는 수정하지 않음 | `fix-r1-06` |

- 추가 회귀 7건: 표 누락·null·list 거부, 음수 duration 거부, 빈 dict 유효, `stale_after_s` 무효값 거부, 정수 `stale_after_s=3`의 fallback 유지, baseline의 `stale_after_s` 처리 불변, 실제 run 감사의 seed 0/1/3 byte 동일.
- 실제 재감사(`fix-r1-08-audit-run.log`): 36 cell, 3,462 arrival. 범주와 집계는 R0과 같고, `rows.json`도 byte 동일(R0이 우연히 정렬상 첫 worker를 보고했음). 실제 r3 snapshot에는 표 누락이나 음수 값이 없었다.
- 과정 이력: R0에서 TDD red→green은 첫 behavior에만 적용됐다(§5). 이 사실은 그대로 둔다. R1 수정 3건은 각각 red→green 순서를 지켰다.
