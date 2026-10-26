# 기존 방법 우선 이기종 DAG 비교 — 공통 실행기·정책 연결 구현 (2026-10-26)

현재 상태(2026-10-26 갱신): **실장비 수집·분석 완료 — pilot 36/36 cell, compare 69개 OOM 이전 수집 + 1개 장애 영향 + 2개 미실행**. 결과와 한계는 [기존 방법 우선 비교 실측 결과](quality-network-baseline-results.md)에 있다. 품질은 Qmin 미달의 진단 상태다. 아래 본문은 실행기 구현·로컬 검증·release 계약의 기록이다. 두 차례 독립 리뷰와 coordinator 검증 후 hash로 묶인 release를 발급해 실행했다. 근거 계획은 [기존 방법 우선 검증 계획](../research/quality-network-baseline-first-plan.md), 명세는 `.scratch/quality-network-baseline-first/spec.md`다. 구현·테스트 주체는 Claude Code `claude-opus-5-5`(claude.ai firstParty, max 구독)다.

## 1. 범위와 결론의 한계

- 같은 CPU·GPU·NPU 후보, 같은 관측 스냅샷 경로, 같은 요청 trace에서 기존 배치 방법들을 같은 실행기로 비교하는 **측정 도구**다. 제안 방법이 아니며 새 알고리즘의 필요성·우월성을 주장하지 않는다.
- 품질: 재사용 60쌍에서 cal 0.515 / eval 0.355 < Qmin 0.40(2026-10-23)이므로 plan의 품질 모드는 `diagnostic`이다. 모든 비교 결과는 **진단**이며 같은 품질에서의 개선 근거로 쓰지 않는다. Qmin·SLO 2 s는 바꾸지 않았다. 60쌍은 이미 본 진단 자료이며 독립 holdout이 아니다.
- 수치 parity(reference-v1 대비)와 서비스 품질 gate는 분리한다. parity 실패만으로 계획을 제외하지 않고, parity 통과만으로 품질 적격이 되지 않는다(`policies.QualityGate`).

## 2. 구성 (`experiments/quality-network-baselines/`)

| 파일 | 역할 |
|---|---|
| `capture.py` | 읽기 전용 preflight. `rtk proxy kubectl get nodes/pods -o json`(30 s request timeout)만 사용하고 즉시 inventory 형식으로 투영한다. env/args/volume과 원본 stdout은 저장하지 않는다. 성공·timeout·오류 모든 경우에 `capture.json`(시작/종료 시각, 호출별 결과와 stderr 끝부분, stdout byte 수)을 남긴다. |
| `dag_worker.py` | qualification `worker.Worker`를 상속한 DAG 모드. 단계 실행 후 출력을 다음 hop으로 실제 전송(forward chain)하고, `fusion_join`에서 두 분기를 합류한다. sink/probe/queue 모드 제공. |
| `generator.py` | 엣지 origin Pod. open-loop 도착 발생, 정책 결정, 두 분기 동시 실행, ledger, 관측 poller, 보정 job(profile, route probe). stdlib만 사용한다. |
| `policies.py` | 5-Task DAG, 비용 모델(list scheduling), 품질 gate, 기존 정책 6종, 선행 시스템 adapter 슬롯. stdlib만 사용한다. |
| `gateway.py` | 공유 gateway relay. `real-topology-live/wan_gateway.py`(변경 없음)의 패턴을 강화했다. |
| `bounded_http.py` | 절대 deadline POST. 연결부터 마지막 응답 byte까지 전체 전송을 하나의 monotonic deadline으로 제한하고(timer가 socket을 닫음), 응답 body 상한을 둔다. gateway upstream, DAG forward, generator 요청이 사용한다. |
| `design.py` | 후보·조건·trace·부하 동결·예산·release binding (순수 함수). |
| `live.py` | release 후 live 수집(소유 UID, 스트림 차단, gateway netns tc). |
| `analyze.py` | 오프라인 분석: frozen cohort goodput, 식별·hash 연속성 검증, 품질 분리. |
| `baselines.py` | CLI: `plan`/`dry-run`/`execute`/`freeze`/`analyze`. |
| `smoke_local.py` | 로컬 실제 CPU 모델 end-to-end smoke(loopback). |

`quality-network-heterogeneous/`에는 추가만 했다. 기존 qualification 경로와 CLI는 그대로다.

- `stages.py`: `PREPROCESS_STAGES`, `PreprocessStage`(CPU 전용)를 추가했다. `TorchStage.run_tensor`, `OpenVinoVision.run_tensor`, tensor 검증 함수도 추가했다.
- `worker.py`: `input='tensor'` 경로를 추가했다. 장치 lock 대기열(`waiting/active`)을 `/health`와 receipt(`queue_wait_ms`, `queue_at_arrival`)에 기록한다. round 1 수정으로 선택 필드 `queue_deadline_s`를 추가했고, round 2 수정(12절)으로 절대 단조 시각 `deadline_monotonic`을 추가했다. 만료 시각은 입력 decode·hash 전에 고정되고, lock 대기를 제한하며, lock을 얻은 뒤 다시 확인한다. 만료된 요청은 모델 호출 없이 504 `queue_deadline_expired`로 끝난다. 두 필드가 모두 없으면(qualification 경로) 이전처럼 무기한 대기한다.
- `bootstrap.py`: `WORKER_MODULE` env를 추가했다(기본값 `worker`).

`qualify.py`는 바꾸지 않았다. 다만 shipped source hash가 바뀌었으므로, 기존 10/23 plan을 그대로 재실행하면 `verify_shipped`가 drift로 거부한다. 이는 의도된 동작이다(동일 recipe 재실행 금지).

## 3. 실행 계약

**DAG(엣지 origin)**: `origin → image_preprocess → vision → fusion_join`, `origin → audio_preprocess → speech → fusion_join`, 답 → origin.

- 데이터는 hop에 기록된 경로로 worker에서 다음 worker로 직접 이동한다. 같은 zone이면 Pod IP 직접, zone이 다르면 gateway를 거친다. 중간 결과가 origin으로 돌아오지 않는다.
- 답은 두 chain의 HTTP 응답으로 역경로를 따라 origin에 돌아온다. 그 bytes는 기록된다.
- origin은 수집기가 아니라 엣지 Pod(dev0003)다. 서버 origin 수집기나 캐시된 단계 출력 재조합은 이 실험이 아니다.

**전처리 분리**

- `image_preprocess`: torchvision Resize(448)·CenterCrop·Normalize, float32 [1,3,448,448], 2,408,576 B.
- `audio_preprocess`: 정확한 Wav2Vec2Processor 1회(zero-mean/unit-var, padding longest), 1-D float32 input_values.
- vision/speech는 tensor를 받아 같은 모델 연산을 수행한다. 정규화는 두 번 적용하지 않는다.
- 실제 모델 테스트에서 분리 경로의 출력이 기존 분기 내 경로와 **bitwise 동일**함을 확인했다(feature bytes, transcript).

**장치 cap과 큐**

- 장치 1단위당 프로세스 하나이고, 모델 호출은 lock 하나로 직렬화된다.
- forward 대기와 join 대기 중에는 lock을 잡지 않는다. 자기 자신으로 forward하거나 자기 자신에서 join해도 deadlock이 생기지 않음을 테스트로 확인했다.
- DAG 요청은 worker 도착 시각 + budget − hop margin 0.1 s를 절대 단조 시각 `deadline_monotonic`으로 넘긴다(입력 준비 시간 포함). 만료된 대기 작업은 실행되지 않으며 fusion join의 두 호출자 모두 terminal 응답을 받는다. 이미 실행 중인 native kernel은 선점하지 않는다.
- 연결 admission 상한 `DAG_MAX_ACTIVE=96`은 장치 cap과 별개다. 중첩 동기 RPC라서 요청 하나가 branch당 최대 3개 연결을 점유하기 때문이다. 초과분은 503 `worker_rejected`로 기록된다.

**open loop와 terminal 상태**

- 예정 시각에 발사하며 완료를 기다리지 않는다. `max_inflight=32`를 넘는 요청은 `admission_drop`이다.
- 상태: `ok` / `late_ok`(deadline 후 cancel 전) / `failed` / `worker_rejected`(worker 503 또는 gateway 429) / `timeout`(cancel_s, 내부 504 forward·join·gateway timeout 포함) / `admission_drop` / `not_sent` / `decision_error`.
- 명시적 원인(failed, rejected)이 형제 분기의 timeout보다 우선한다.
- 중첩 예산은 hop마다 0.1 s씩 줄어든다. 그래서 내부 timeout이 먼저 응답한다.
- 모든 data-path HTTP(gateway upstream, worker→worker forward, generator→worker)는 `bounded_http`로 전체 전송 시간이 budget을 넘지 못한다(socket inactivity timeout이 아니다). generator의 branch future도 cancel 시각+5 s에서 timeout으로 끝난다. generator HTTP 서버는 요청 읽기에 절대 deadline 90 s를 둔다.

**측정 경계**

- 지연 = 완료 시각 − **예정** 도착 시각이며, generator monotonic clock 하나로 잰다.
- 정책 선택·관측·전환이 이 경계 안에 포함된다.
- hop `rpc_ms`는 하위 작업을 포함하므로 합산하지 않는다. hop 전송 추정은 `rpc − 수신측 보고 작업`이며, 관측 정책의 EWMA 입력으로만 쓴다.

**공정성**

- 모든 정책이 같은 후보, 같은 trace(block·조건·부하별 seed), 같은 admission 상한, 같은 관측 poller(`mode=queue`, 데이터와 같은 경로, 1 s)를 받는다.
- trace마다 관측 추정을 초기화한다.
- trace 사이와 네트워크 조건 변경 전에 drain한다: 모든 worker의 queue·active·join과 **admitted 요청 수(`inflight`: forward·join 대기 포함)**가 0이고 gateway active가 0이어야 한다. settle(2 s) 뒤 다시 확인해 여전히 idle이고 진행 counter(`completed`, `dag_requests`)가 변하지 않았을 때만 idle로 인정하고 gateway counter를 reset한다(안정 barrier). drain 상한은 60 s다. 조건 변경 전 drain이 실패하면 수집을 중단하고, 마지막 복원(relay-unshaped)은 drain 결과를 기록한 뒤 항상 적용한다.
- 정책 순서는 비교 group(block·조건·부하) 12개에 걸친 cyclic rotation이다. 6정책 각각이 모든 순서 위치를 정확히 2번씩 차지한다.
- 미래 label이나 미래 network trace는 주지 않는다. 각 조건의 link profile은 그 조건을 적용한 직후 측정한 route probe다.

**데이터 연속성 검증(`analyze.validate_row`)**: 다음 중 하나라도 어긋나면 그 응답은 `invalid`이며 goodput과 품질에서 제외한다. 형식이 깨진 증거(base64·JSON 오류, 없는 corpus 항목, receipt 필드 누락)는 예외가 아니라 이유가 붙은 `invalid`다.

- 모든 단계 receipt와 fusion receipt: 계획이 지정한 worker의 생성 UID(비어 있으면 불인정)·노드·backend·장치, 이 run의 run id, 이 요청의 request id, **plan에 고정된** model_sha256·derived_artifact_sha256(`plan.model_provenance`, 11절; round 1 supplement 이전에는 `/health` 자기 보고였다). CPU 답을 GPU/NPU 답으로 인정하지 않는다.
- 두 join part가 돌려준 fusion receipt는 완전히 같아야 한다(한 번의 fusion 실행).
- corpus sha와 첫 단계 input sha의 일치.
- 각 hop 출력 sha와 다음 receipt input sha의 일치.
- join hop payload sha와 fusion input sha의 일치.
- fusion output sha와 응답 payload의 일치. 채점하는 `answer`·`answer_ids`·`output_sha256`은 검증된 payload를 decode한 값과 같아야 한다.

**frozen cohort 분석(`analyze.analyze`, `trace_summary`)**

- run의 release binding plan sha가 이 plan과 다르거나, GT 파일·`corpus.json`·채점 소스 `vqa_eval.py`가 plan에 기록된 hash와 다르면 분석을 거부한다.
- 각 trace의 실행 계약(label, 정책 spec — fixed는 plan의 고정 배치, deadline, cancel, admission 상한, 품질 계약)은 plan·cell 값이어야 하고 ledger는 그 request와 같아야 한다. 다르면 분석을 거부한다. SLO는 plan 값이다. compare 분석은 release에 묶인 frozen-loads 파일(`--frozen`)을 요구하고 `cells.json`이 그 설계와 같아야 한다.
- 완료 offset이 유한하지 않거나 예정 도착보다 이르면 그 응답은 `impossible_timestamp` invalid다.
- 수집기가 저장한 `cells.json`(pilot은 plan의 pilot 설계와 같아야 함)과 `trace-<label>.request.json`이 cohort다. 요청 도착은 cell의 rate·seed·plan window·평가 질문으로 다시 생성한 seeded trace와 같아야 한다.
- 예정 도착마다 결과는 정확히 1개다. 없으면 `missing_result`, 여러 개면 `duplicate_result`(어느 것도 세지 않음), cohort 밖 행은 `foreign_rows`로 따로 센다. ledger가 없는 trace는 cohort 전체가 `missing_result`다. 행의 request id·question·예정 offset이 cohort와 다르면 `cohort_mismatch`다.
- deadline 판정은 행의 status 문자열이 아니라 generator clock의 완료 offset − frozen 예정 offset과 frozen deadline으로 다시 계산한다.
- 품질 추정량: `returned`(유효한 모든 답, 지각 포함 평균), `in_deadline`(제때 답 평균), `all_offered_eventual_zero_imputed`(cohort 전체, 답 없음 0, 지각 답 유지), `all_offered_timely_zero_imputed`(cohort 전체, 지각 답도 0), `coverage_returned`, `coverage_in_deadline`. 지연 분위수는 유효 응답만의 값이며 실패·drop·누락 수는 counts에 있다.

## 4. 비교군 (모두 같은 실행기)

| 정책 | 계약 |
|---|---|
| `fixed` | 10/23 혼합 고정 배치에 가장 가까운 대응(이미지 전처리 cpu-c → NPU vision, 음성 전처리 cpu-e0 → Jetson speech, fusion cpu-c). 대조군 |
| `task_fastest` | task마다 측정 compute가 가장 작은 적격 worker. 전송·큐는 무시하고 한 번 결정한다 |
| `greedy_comm` | 위상 순서 greedy. 선행 위치에서의 전송, 현재 큐, 같은 요청 내 장치 점유를 반영한다. look-ahead는 없다(반례 테스트 포함) |
| `dag_static` | 동결 profile에서 적격 계획 전체(현재 144개)를 정확 열거해 한 번 고정한다. **예측 비용 모델의 진단 선택기**이며 online goodput oracle이나 상한이 아니다 |
| `dag_reopt` | 같은 열거를 요청마다 현재 관측(큐와 receipt EWMA)으로 다시 한다 |
| `dag_reopt_hysteresis` | 예측 이득이 max(15 %×현재, 50 ms)를 넘고 5 s dwell을 지난 뒤에만 전환한다 |
| `jellybean`, `octopinf` | adapter 슬롯만 있다. 호출하면 `NotImplementedError`를 낸다. 계약 감사는 별도 research worker가 맡는다. 일반 정책에 논문 이름을 붙이지 않는다 |

## 5. 후보와 네트워크 (2026-10-26 preflight `preflight-20261026T020419Z`, 수정 후 재확인 `preflight-20261026T031111Z`)

**후보**

| task | 후보 |
|---|---|
| image/audio_preprocess | cpu-c(ser0002), cpu-e0(dev0003) |
| vision | cpu-c, npu-c(Intel NPU, 직접 NPU), cpu-e0, jgpu-dev0009-cu130 |
| speech | cpu-c, cpu-e0, jgpu-dev0009 |
| fusion | cpu-c, cpu-e0, jgpu-dev0009 |

- 모든 task에 복수 후보가 있다.
- NPU ASR은 후보가 아니다. 10/23 probe가 27/60 뒤 exit 137로 끝났고, 같은 recipe는 재실행하지 않는다.
- CPU fallback을 GPU/NPU로 인정하지 않는다.

**catalog(12노드)**

- dev0001 JP6.1: runtime_failed, 보류.
- dev0007: deployment_blocked. 02:04Z capture 중 NotReady→Ready로 변동했고, 03:11Z capture에서는 Unknown이었다.
- dev0004: Unknown.
- 서버 GPU 3대: occupied.
- Hailo(dev0006): occupied. Hailo(dev0002)·Mobilint: HEF/compiler가 없어 dependency_unavailable.

**네트워크**

- 소유 gateway Pod(ser0002)의 netctl sidecar가 **자기 Pod netns 안에서만** tc를 건다. NET_ADMIN만 허용하고 privileged·escalation은 없다. 근거는 `run_load.py` 패턴과 수정하지 않은 `net_control.py`다.
- 승인된 조건은 세 가지다.
  - `relay-unshaped`: 대조군. relay 자체 overhead를 본다. direct 경로와 비교하는 probe 포함.
  - `good`: 100 Mbps, 0 ms.
  - `limited`: 10 Mbps, 20 ms.
- 이것은 **gateway 제약**이며 물리 링크 조작이 아니다. edge↔server만 제약하고, edge↔edge(dev0003↔dev0009) LAN은 제약하지 않는다.
- gateway 강화 내용:
  - 소켓 inactivity 30 s, 절대 읽기 deadline 90 s.
  - upstream 전체 전송 = min(60 s, 요청 budget_s)의 절대 deadline(`bounded_http.exchange`). 상한(16 MiB)을 넘는 upstream body는 502로 거부한다.
  - peer allow-list는 1회 동결한다.
  - reset은 active 0일 때만 허용한다.

## 6. 실행 절차 (live 미실행; release 필요)

```bash
# 1) preflight (읽기 전용, 이번 dispatch에서 실행함)
rtk proxy python3 experiments/quality-network-baselines/capture.py
# 2) plan (kubectl 없음) — 현행 plan은 developer-fix-r1-supplement.md에 sha와 함께 기록 (02:35·03:11 plan은 보존, 현재 소스로는 drift로 거부됨)
rtk proxy python3 experiments/quality-network-baselines/baselines.py plan --inventory <preflight>/node-inventory.json \
  --export artifacts/quality-network-heterogeneous/export-20261023T075427Z \
  --deps artifacts/quality-network-heterogeneous/deps-20261023T075919Z --deps artifacts/quality-network-heterogeneous/deps-20261023T080028Z
# 3) dry-run: 실행 cell, 예산, 추정 시간, 정확한 release binding JSON 출력 (kubectl 없음)
rtk proxy python3 experiments/quality-network-baselines/baselines.py dry-run --plan <plan> --stage pilot --run-id qnb-<id>
# 4) coordinator release JSON {"release":"RELEASE", stage, run_id, plan_sha256, shipped_sha256, budgets_sha256} 후에만.
#    execute는 kubectl 전에 plan이 소비하는 모든 입력을 다시 검증한다(9절 binding); drift가 하나라도 있으면 rc 2.
rtk proxy python3 experiments/quality-network-baselines/baselines.py execute --plan <plan> --stage pilot --run-id qnb-<id> --release <release.json>
# 5) pilot -> 동결 부하 (오프라인; 포화 규칙)
rtk proxy python3 experiments/quality-network-baselines/baselines.py freeze --plan <plan> --pilot-run artifacts/quality-network-baselines/qnb-<id>
# 6) 본 비교 (release에 frozen_loads_sha256 추가 binding)
rtk proxy python3 experiments/quality-network-baselines/baselines.py execute --plan <plan> --stage compare --frozen <frozen-loads.json> --run-id qnb-<id2> --release <release2.json>
rtk proxy python3 experiments/quality-network-baselines/baselines.py analyze --plan <plan> --run artifacts/quality-network-baselines/qnb-<id2>
```

**pilot(`design.PILOT`)**

- 3조건 × 도착률 {0.25, 0.5, 1, 2} rps × 정책 {fixed, greedy_comm, dag_reopt}, 각 20 s. 총 36 cell, 추정 약 2,340 s.
- 포화율 λ*: good 조건, 어떤 pilot 정책이든 **실제로 예정된 frozen cohort**의 0.9 이상이 유효한 제때 답이고 admission drop이 없는 최대 r. 명목 rate×window가 아니라 실현된 도착 수가 분모다. 지속 가능한 r이 없으면 freeze가 거부한다.
- 더 큰 시험 rate가 실패했으면 `capacity=bracketed`, `upper_failure_rps`를 기록한다. 가장 큰 시험 rate까지 지속되면 `censored_at_largest_tested_rate`이며 측정된 포화가 아니라 검열된 시험점이다.
- 동결 부하: low 0.3λ*, near 0.8λ*, high 1.3λ*. 모든 정책과 조건에 같은 절대값을 쓴다.

**compare(`design.COMPARE`)**

- {good, limited} × 3부하 × 6정책 × 2 block, 각 60 s. 총 72 cell, 추정 약 6,700 s.
- 반복 수 2는 임시값이다. 최종 반복 수와 길이는 pilot 분산으로 statistical-power 절차에서 정하며, 이것으로 유의성을 선언하지 않는다.

**예산(`design.BUDGETS`)**

| 항목 | 값 |
|---|---|
| readiness | 2,100 s |
| cache upload | 1,800 s |
| profile job | 1,800 s |
| probe job | 1,800 s |
| 수집 | pilot 6,000 s / compare 9,000 s |
| cleanup 예비 | 900 s |
| Pod 수명 | worker·helper 12,600 s, cache 14,700 s |
| 요청 | deadline 2 s, cancel 10 s |
| drain | 60 s |
| 관측 poll | 1 s (timeout 0.5 s) |

- 저장: asset 4.36 GB(plan 출력에 정확한 값). worker emptyDir은 recipe별 storage budget을 따르고, generator와 gateway는 1 GiB다.

## 7. 로컬 검증 (실제 실행; 결과는 developer-report.md)

**테스트(stdlib)**

- `test_policies` 18개: 품질 gate와 parity 분리, 장치 직렬화, 네트워크 반전 시 dag/greedy의 배치 변화와 task_fastest 불변, hysteresis 유지/전환, 관측 사용 여부, adapter 미구현.
- `test_executor` 14개: 실제 loopback HTTP에 fake 단계를 붙였다. 분기 동시성, self-forward 무 deadlock과 lock 배타성, open-loop 독립성, admission drop, timeout 후 누수 0, join timeout, 503 거부, hash·식별 위조 검출, gateway allow-list·budget·reset, zone 우회 거부, profile·probe job, goodput cohort.
- `test_design` 13개: release binding, release 없으면 kubectl 0회로 rc 2, seed trace 동일성, 균형 순서, 부하 동결, 현재 inventory의 CPU+GPU+NPU 후보, Pod 보안, 엣지 스트림 거부.

- `test_review_r1` 48개: round 1 지적별 행동 회귀(대응표는 developer-fix-r1.md).

**실제 모델 테스트**

- `test_stages_dag` 5개(venv-ref, torch CPU): 분리 경로의 bitwise 동등성, 정규화 1회, 전처리 CPU 전용, tensor receipt.
- 기존 qualification suite 88개: 변경 후 재실행해 모두 통과했다(round 1 수정 후 재실행 포함).

**smoke** `smoke_local.py`

- 구성: 실제 CPU 모델, worker 2개(edge/cloud), gateway, generator.
- 결과: profile → probe → dag_reopt open-loop 11건 모두 `ok`, 검증 통과. 답과 profile transcript가 reference-v1과 같았다. round 1 수정 후 재실행(`logs/local-smoke-20261026T031044Z.json`)에서도 11/11 `ok`이고, 새 split-vs-original 검사 8건(vision·speech × CPU worker 2 × 보정 질문 2)이 모두 최종 답 동일·출력 bytes 동일이었다.
- 한계: loopback, 단일 host CPU, 무 shaping이다. 성능·장치 증거가 아니다.

## 8. 미완료와 위험

- live 미실행이다. Jetson·NPU의 DAG 모드(tensor 입력) 실행, 엣지 generator, gateway tc 동작은 실장비에서 확인되지 않았다.
- 품질 미달로 모든 결과는 진단이다. 품질 개선과 독립 평가 자료 분리는 별도 작업이다.
- 중첩 동기 chain에서 답은 역경로로 돌아온다(fusion → speech/vision worker → 전처리 worker → origin). 그만큼 응답 bytes가 경유한다. 별도 callback 경로는 구현하지 않았다.
- timeout된 요청의 실행 중 하위 작업은 선점되지 않는다(대기 중 작업은 queue deadline으로 실행되지 않음). drain에서 inflight 0과 안정 barrier를 확인한 뒤 다음 trace나 조건 변경을 시작한다.
- profile job은 split-vs-original 검사 때문에 길어진다(vision·speech 후보마다 보정 질문당 원 경로 1회 + fusion 2회). 실장비 소요 시간은 아직 측정하지 않았고 profile job 상한 1,800 s 안에 끝나는지 pilot에서 확인해야 한다.
- NPU vision의 원 경로는 numpy 전처리, 분리 경로는 torchvision CPU 전처리다. 둘의 최종 답이 다르면 보정 gate가 live 수집을 거부한다(우회하지 않음).
- link 모델은 2점 선형 fit이다. 2.4 MB tensor는 1 MiB probe로부터 외삽한다.
- 관측 정책의 hop 전송 추정에는 HTTP overhead가 포함된다.
- JellyBean·OctopInf adapter는 미구현이다(OctopInf 기준 revision `ab9bc150`은 coordinator 메모). Kubernetes/Koordinator·DRL 비교는 이 dispatch 범위 밖이다.
- (11절) 수집기 제어 HTTP는 이제 절대 deadline이지만 kubectl 호출(`net`, cache upload)은 HTTP가 아니며 기존 kubectl timeout을 따른다. NPU vision의 `model_sha256`은 worker가 다시 계산하는 값이 아니라 고정 export 기록이 연결한 원 가중치 hash다(11절 사슬). GPU/NPU에서 이 provenance 검사가 실제로 통과하는지는 live에서만 확인된다.

## 9. Round 1 리뷰 수정 (2026-10-26)

근거: `.scratch/quality-network-baseline-first/review-spec-r1.md`, `review-standards-r1.md`, `coordinator-review-notes-r1.md`. 지적별 red/green 증거와 명령은 `developer-fix-r1.md`에 있다.

- **실행 계약(release binding)**: `binding()`의 drift는 shipped·planner source, 예산 외에 재사용 frozen source(`qualify.py`, `catalog.py`, `http_runtime.py`, `net_control.py`, `collect.py`), load-pairs·GT·inventory 입력, assets manifest, 각 asset source 파일(크기·sha256, 4 GB 전체 hash), `corpus.json` hash(plan에 `corpus_sha256` 신규 기록)와 질문→파일 mapping(고정 load-pairs manifest와 일치, manifest에 포함)을 검사한다. corpus binding이 없는 이전 plan은 거부된다.
- **수집 gate**: live worker 확인 뒤 CPU+GPU+NPU와 복수 선택 task 조건(`coverage.ok`), fixed 이기종 계획이 모두 살아 있어야 한다. 아니면 후보 inventory를 저장한 뒤 수집을 거부한다. 축소된 비교로 조용히 진행하지 않는다.
- **보정 = 후보 자격**: profile 결과는 (1) 모든 receipt가 생성 UID·노드·backend·장치·run·request·`/health` 모델 hash와 일치, (2) 모든 (task, 후보)가 모든 보정 질문에 응답, (3) 모든 vision·speech 후보의 분리 경로 최종 답이 같은 worker의 원 경로(raw 입력, 단계 내 전처리) 최종 답과 같음(기준 fusion worker)일 때만 비용으로 동결된다. 결과는 `profile-qualification.json`에 남는다.
- **조건 receipt**: 같은 네트워크 조건을 여러 번 적용해도(`relay-unshaped` 대조, pilot 첫 조건, 복원) `net-NN-<조건>.json`으로 각각 보존한다(exclusive create 유지).
- **분석·부하 동결·순서**: 3절과 6절 참조.

## 10. 정정 (errata)

- 이전 developer-report.md는 "로컬 `tdd` skill이 없다"고 적었다. **사실이 아니다.** `/home/jinuk/.agents/skills/tdd/SKILL.md`가 존재한다(coordinator 확인, round 1 수정에서 읽고 따름). 원래 구현의 테스트는 각 모듈 작성 직후에 작성했으며 test-first가 아니었다는 기록은 그대로 사실이다. round 1 수정은 지적마다 실패하는 행동 회귀를 먼저 기록하고 최소 수정 후 통과를 기록했다.
- 이전 문서의 "upstream timeout = min(60 s, 요청 budget_s)"는 socket inactivity timeout이었고 절대 시간 상한이 아니었다(standards S5). 위 5절이 현재 계약이다.
- 이전 문서의 "trace 사이 drain"은 forward 중인 요청을 보지 못했다(spec R2, standards S4). 3절이 현재 계약이다.
- 이전 `all_offered_zero_imputed`는 지각한 정답도 0으로 처리한 timely 추정량이었다. 이름을 `all_offered_timely_zero_imputed`로 바꾸고 eventual 추정량을 따로 보고한다.
- 원본 developer-report.md와 review-source는 frozen 리뷰 증거로 수정하지 않았다.

## 11. Round 1 보완 — 남은 부분 수정 2건 (2026-10-26)

근거: `.scratch/quality-network-baseline-first/fix-r1-supplement-task.md`. 증거와 명령은 `developer-fix-r1-supplement.md`. `developer-fix-r1.md`는 완료된 기록으로 수정하지 않았다.

- **S5 수집기 제어 HTTP**: `Live`의 기본 제어 HTTP seam은 `bounded_http.control`이다(`qualify.http`와 같은 계약: body 없으면 GET, 있으면 POST, JSON 응답, 400 이상은 HTTPError). connect부터 마지막 byte까지 단조 시계 절대 deadline 하나로 끊고(byte를 흘리는 peer도 붙잡지 못함), 응답 body는 `BUDGETS['control_max_body_bytes']` = 32 MiB로 제한한다. 근거: 로컬 smoke ledger 최대 9,112 B/행, compare trace(최대 2 rps × 1.3, 60 s) 약 156행 ≈ 1.4 MB, 10배 이상 여유. 제한된 phase(job 폴링, drain의 queue/health 조회, trace 상태 폴링) 안의 각 호출은 그 phase의 **남은** 예산만 받으며, 예산이 다 되면 호출 없이 끝난다. `qualify.py`는 수정하지 않았다(Live의 seam에서 교체).
- **S7 plan 측 모델 provenance**: plan 생성 시 `design.model_expectations`가 worker·단계마다 기대 hash를 고정 asset에서 도출해 `plan.model_provenance`에 기록한다.
  - torch CPU/CUDA: assets manifest 파일 hash(`models/resnet18-f37072fd.pth`, `models/asr/model.safetensors`, `models/asr/preprocessor_config.json`, `models/mutan-resnet18.pth.tar`). 사슬: manifest(plan의 `assets_manifest_sha256`) → bootstrap이 받은 파일마다 sha256 검사 → `stages.py`가 고정 경로 `MODEL_ROOT/<파일>`을 읽고 그 파일을 hash해 보고. 이전 보고서의 "fusion 가중치는 jellybean.tar 안에 있다"는 틀렸다. fusion 가중치는 manifest의 별도 파일이다(jellybean.tar는 소스).
  - `image_preprocess`: 모델 파일 없음(None/None). 연산은 shipped `stages.py`이며 `shipped_sha256`으로 묶인다.
  - Intel NPU vision: derived = manifest의 `onnx/vision-static-448.onnx` hash = 고정 export 기록(`export-20261023T075427Z/manifest.json`) `stages.vision.export.sha256`, model = export 기록의 `original_model_sha256` = manifest resnet18 hash. 셋 중 하나라도 어긋나면 plan 생성이 거부된다. worker는 ONNX를 `expected_onnx_sha256`로 검사하고 읽은 파일을 hash한다.
  - 이것은 **artifact 동일성**만이다. CPU와의 수치 동등성, 최종 품질은 별도 gate이며 NPU를 CPU 수치 차이로 거부하지 않는다.
  - `binding()`은 export 기록 hash와 기대값 재계산을 drift로 검사한다(`model_provenance` 없는 plan은 거부). `Live.verify_workers`는 `/health` 단계 hash가 plan과 다르면 그 worker를 제거하고(→ 이기종 gate), `Live.profile`과 `analyze`는 plan 값으로만 receipt를 검증한다. `/health`는 검사 대상이지 기대값의 출처가 아니다.
  - 로컬 smoke(실제 CPU 모델)의 모든 receipt hash가 새 plan의 cpu-torch 기대값과 일치했다(`logs/fix-r1s-smoke-vs-plan-provenance.json`; CPU만, GPU/NPU 증거 아님).
- 새 plan: `plan-20261026T032821Z`(preflight `preflight-20261026T032816Z`), dry-run drift `[]`. 03:11 plan은 보존했고 현재 소스로는 drift로 거부된다(`plan predates model provenance binding` 포함).

## 12. Round 2 최종 리뷰 수정 (2026-10-26)

근거: `.scratch/quality-network-baseline-first/review-spec-r2.md`(R5, 미결속 deadline), `review-standards-r2.md`(S2·S3·S5·S9), 범위 `fix-r2-live-task.md`. 증거와 명령은 `developer-fix-r2.md`.

- **전처리 후보 자격(R5)**: 보정에서 기준 producer가 아닌 모든 image/audio 전처리 후보를 질문마다 검사한다(`generator.preprocessing`, `Live.profile_problems`). 증명 1 `bytes_identical`: 출력 sha256·크기·receipt shape/dtype이 기준 producer와 같다. 기준 tensor는 모든 모델 후보의 split-vs-original 검사에 쓰였으므로 모델 호출을 반복하지 않는다. 증명 2 `downstream_answer`(바이트가 다를 때만): 기준 모델 worker가 후보 tensor를 처리하고 기준 fusion worker가 기준 partner와 합친 최종 답이 기준 모델의 원 경로 답과 같아야 한다. 둘 다 아니면 후보 비용을 동결하지 않고 수집을 거부하며 증거(`profile.json`의 `preprocess_equivalence`, `profile-qualification.json`)를 남긴다. 후보를 조용히 빼지 않는다. 실장비 cpu-e0(ARM)와 cpu-c(x86)의 바이트 동일성은 live에서만 확인된다.
- **분석 실행 계약(S9, spec P2)**: 3절 참조. 저장된 request·ledger 값을 믿지 않는다.
- **의존 소스 결속(S2)**: plan의 `reused_frozen_sources`에 실행 중 쓰이는 `real-topology-live/run_baseline_comparison.py`(phase, projected_call)와 채점 `quality-network-vqa/vqa_eval.py`를 추가했다. release drift와 분석이 둘 다 검사한다. 두 파일은 수정하지 않았다.
- **입력 준비 포함 절대 deadline(S3)**: 3절 참조. 실행 중인 native kernel은 여전히 선점하지 않는다.
- **corpus fetch(S5)**: `generator.fetch`가 `bounded_http.exchange` GET을 쓴다(연결부터 마지막 byte까지 절대 deadline, 만료 시 연결 종료, 64 MiB 상한, sha256 검사). `/corpus` 전체 load는 `budget_s`(collector 호출 600 s보다 10 s 짧음) 안에서 파일마다 min(120 s, 남은 예산)을 받는다. corpus bytes와 mapping은 바뀌지 않았다.
- 예산(`design.BUDGETS`)은 바꾸지 않았다. 새 plan은 `developer-fix-r2.md`에 sha와 함께 기록한다.
