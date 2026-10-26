# 기존 방법 우선 이기종 DAG 비교 — 실측 결과 (2026-10-26)

수준: **실장비 실행 + 오프라인 분석 완료**. 공식 수용이나 품질 적격 결과가 아니다.

- 품질: eval 0.355 < Qmin 0.40이다. 재사용 60쌍은 holdout이 아니므로 모든 수치는 **진단**이다.
- 아래 goodput은 **식별·모델 hash·데이터 연결 검증을 통과한 응답의 기한 내 처리량**(SLO 2 s)이며, 품질 적격 goodput이 아니다.
- 정책은 일반 기준선이다. JellyBean·OctopInf 등 논문 이름의 재현이 아니며, 해당 adapter는 없다.
- compare는 block 2개의 탐색적 실험이다. 유의성은 선언하지 않는다.

## 근거 자료

- 실행기·계약: [quality-network-baselines.md](quality-network-baselines.md)
- 수정 기록: `.scratch/quality-network-baseline-first/developer-fix-r2.md`
- 상세 보고: `.scratch/quality-network-baseline-first/live-pilot-report.md`
- plan과 release
  - plan: `artifacts/quality-network-baselines/plan-20261026T035720Z` (plan sha256 `68c4f302…b034`)
  - release: `.scratch/quality-network-baseline-first/release-{pilot,compare}-r2.json`
- pilot
  - run: `artifacts/quality-network-baselines/qnb-pilot-r2/`
  - 분석: `qnb-pilot-r2-analysis.json` (`2287002f…68d4`)
  - 동결 부하: `frozen-loads-20261026T042429Z.json` (`67b3ca95…7e26`)
- compare
  - run: `artifacts/quality-network-baselines/qnb-compare-r2/`
  - 분석: `qnb-compare-r2-analysis.json` (`0570853e…839e`)
- 실행 로그·진단: `.scratch/quality-network-baseline-first/logs/live-{pilot,compare}-r2/`
  - 실행 기록: `execute.*`
  - cell 표: `summary-table.txt`
  - 보조 진단: `diagnostics.{py,json}`

## 실행 환경과 실제 장치 증거

**source**
- 요청 source는 엣지 generator Pod(dev0003, raspi5)다.
- 입력은 고정 corpus 재생이며, 실제 센서 스트림이 아니다.

**worker 4개** (각각 생성 UID·노드·장치를 `/health`와 receipt로 확인)

| worker | 위치 | backend | 장치 증거 |
|---|---|---|---|
| cpu-c | ser0002, x86 | cpu-torch | `cpu` |
| npu-c | ser0002 | openvino-npu | `device=NPU`, `execution_devices=['NPU']` |
| cpu-e0 | dev0003, ARM | cpu-torch | `cpu` |
| jgpu-dev0009-cu130 | dev0009, Jetson Orin | cuda-torch | output `cuda:0` |

**Mobilint 범위**: 이번 비교에는 실제 Mobilint 추론이 없다. ser0004에서 장치 할당과 `/dev/aries*` 접근은 확인했으나, 10월 23일 확인한 공개 compiler 이미지에 `qbcompiler`가 없었고 이번 VQA 모델의 MXQ 산출물이 없어 실행 후보로 등록하지 못했다. 전체 후보 검증 목록에는 유지한다.

**GitHub 공개 자료**: [공개 증거 목록](../../artifacts/quality-network-baselines/publication-20261026/README.md)에 포함 파일과 로컬에만 보존한 원시 자료를 구분했다.

**모델 provenance**
- 모든 receipt의 model·derived hash가 plan의 고정 asset hash와 같았다.
- 두 run 모두 사전 보정에서 제외된 worker가 없었다. 실행 중 cpu-c의 OOM 종료는 아래에 별도로 기록한다.

**보정** (두 run 각각 새로 수행했고, 둘 다 problems [])
- 전처리 후보 cpu-e0(ARM)의 출력은 cpu-c(x86)와 byte 단위로 같았다: image 8/8, audio 8/8.
- vision·speech 후보 7개 모두 split 경로와 원 경로의 최종 답·출력 bytes가 같았다.

**보정 median compute (ms, pilot / compare)**

| task | 결과 |
|---|---|
| vision | NPU 11.4/11.4, Jetson 38.2/35.9, cpu-c 120.7/118.5, cpu-e0 286.6/289.8 |
| speech | Jetson 52.5/52.5, cpu-c 217.5/277.4, cpu-e0 576/590 |
| fusion | cpu-c 25.5/26.4, Jetson 31.4/27.0, cpu-e0 279/288 |

**DAG 경로** (image_pre | vision | audio_pre | speech | fusion)
- fixed: `cpu-c|npu-c|cpu-e0|jgpu|cpu-c`
- task_fastest: `cpu-c|npu-c|cpu-c|jgpu|cpu-c`
- dag_static은 trace마다 단일 계획을 쓴다.
  - good: `cpu-e0|jgpu|cpu-e0|jgpu|cpu-c`
  - limited: `cpu-e0|jgpu|cpu-e0|jgpu|jgpu`
- greedy_comm·dag_reopt·hysteresis는 구간 내 요청들에 걸쳐 여러 계획을 사용했다(관측된 다중 계획 구간에서 2–11개).
  - limited에서는 vision을 raspi CPU로 옮긴 계획(`cpu-e0|cpu-e0|cpu-e0|jgpu|jgpu`)이 자주 나왔다.

**네트워크 조건**
- 소유 gateway Pod netns의 tc 제약만 사용했다.
  - good: 100 Mbps, 0 ms
  - limited: 10 Mbps, 20 ms
  - relay-unshaped: 제약 없는 relay(대조군)
- 물리 링크 조작이 아니다. edge↔server 구간만 제약했다.

## Pilot `qnb-pilot-r2`

**실행**
- 03:58:49–04:24:01Z, rc 0.
- 36/36 cell을 수집했다. 결측·중복·invalid 0.
- 정책 3개(fixed, greedy_comm, dag_reopt), 도착률 4개(0.25/0.5/1/2 rps), 20 s window.

**결과**
- relay-unshaped·good: 세 정책 모두 모든 rate에서 요청이 100 % 기한 내였다. p50 latency(ms)는 다음과 같다.

  | 정책 | p50 |
  |---|---|
  | fixed | 121–141 |
  | dag_reopt | 128–161 |
  | greedy_comm | 127–196 |

- limited: fixed는 원본 image(0.8 MB)가 edge→cloud를 지나 p50이 약 1 s였다. 2 rps에서는 goodput 0.15 rps(ok 3, late 23, timeout 13 / 39)로 무너졌다. greedy_comm·dag_reopt는 1.95 rps였다.

**포화율과 동결 부하**
- λ* = 2.0 rps이며, **가장 큰 시험 rate에서 검열**되었다. 실패한 상한이 없으므로 측정된 포화점이 아니다.
- 동결 부하는 **0.6 / 1.6 / 2.6 rps**다. 실제 포화 위치를 뜻하지 않는다.

## Compare `qnb-compare-r2`

**실행**
- 04:26:55–05:59:12Z, rc 0.
- 6정책 × {good, limited} × 3부하 × 2 block, window 60 s.

**72 cell이 모두 유효하지 않다.**
- 69 cell은 OOM 발생 전에 수집을 완료했다. 이 표현은 모든 요청의 성공이나 품질 적격을 뜻하지 않는다.
- **cell 70 `b1-limited-high-greedy_comm`은 장애 영향 cell이다.**
  - cpu-c worker가 05:55:26Z에 **OOMKilled**(exit 137, memory limit 8 Gi)되었다.
  - failed 22건은 모두 audio chain에서 cpu-c로 가는 연결 오류다(RemoteDisconnected 15, ConnectionRefused 6, Reset 1).
- 그 뒤 `b1-limited-high-dag_static`·`dag_reopt`는 drain gate가 skip했다(후보 가용성 변화).
- cell 70의 수치는 장애 진단으로만 제시하고 정상 후보 비교에는 사용하지 않는다. 이후 두 cell은 미실행이며 조용히 제외하지 않았다.
- 실제 유입 6,672건: 기한 내 5,003, 늦은 완료 689, timeout 871, admission drop 17, gateway 거절 70, 기타 실패 22. 이 총계에는 장애 영향 cell을 포함한다. 미실행 두 cell의 계획 요청은 실제 유입에 포함하지 않는다.

**표 읽는 법**
- goodput(rps) 값은 b0 / b1 순서다.
- `=`는 fixed와 같은 goodput이다(모든 요청이 기한 내, coverage 1.0).

| 조건·부하 | fixed | task_fastest | greedy_comm | dag_static | dag_reopt | dag_reopt_hysteresis |
|---|---|---|---|---|---|---|
| good 0.6 | 0.667 / 0.650 | = | = | = | = | = |
| good 1.6 | 1.800 / 1.550 | = | = | = | = | = |
| good 2.6 | 2.650 / 2.433 | = | = | = | = | = |
| limited 0.6 | 0.617 / 0.500 | 0.517 / 0.467 | 0.667 / 0.550 | 0.667 / 0.550 | 0.667 / 0.550 | 0.667 / 0.550 |
| limited 1.6 | 0.200 / 0.050 | 0.033 / 0.017 | 1.400 / 1.100 | 1.633 / 1.567 | 1.550 / 0.483 | 1.633 / 0.817 |
| limited 2.6 | 0.033 / 0.050 | 0.017 / 0.017 | 1.867 / *0.883* | 2.667 / skip | 0.767 / skip | 0.667 / 0.467 |

기울임 값은 장애 영향 cell(cell 70)이다.

**good의 p50 latency (ms, 두 block)**

| 정책 | p50 |
|---|---|
| fixed | 140–145 |
| hysteresis | 146–170 |
| task_fastest | 148–163 |
| dag_reopt | 153–174 |
| dag_static | 163–177 |
| greedy_comm | 174–275 |

**limited의 실패 구성**

| cell | 결과 |
|---|---|
| limited 2.6 fixed (b0) | ok 2, late 15, timeout 136, admission_drop 7 / 160 |
| limited 2.6 task_fastest (b0) | ok 1, late 11, timeout 106, rejected 38, drop 4 / 160 |
| limited 2.6 hysteresis | b0 ok 40, late 54, timeout 66 / 160; b1 ok 28, late 52, timeout 73 / 153 |

- rejected는 모두 gateway 429다. relay slot 96개가 소진된 것이며, 가속기 거부가 아니다.
- client cancel은 10 s다. gateway upstream 상한은 min(60 s, 요청 budget)이다.
- 따라서 이 붕괴에는 응용 relay 병목이 섞여 있다. 순수 물리 네트워크 효과로 읽지 않는다.

**품질 (공식 VQA, 진단)**
- 전부 답한 cell은 정책과 무관하게 같은 cohort의 같은 값이다(예: good b0 0.355 / 0.345 / 0.357).
- 실패·지연이 많은 cell에서는 응답 coverage와 timely zero-imputed 점수가 낮아진다(예: limited 2.6 fixed 0.013, task_fastest 0.006). 반환된 응답만의 품질도 표본 선택 때문에 달라지므로 이를 모델 자체의 품질 변화로 해석하지 않는다.

## 진단적 해석 (주장 아님)

**good**
- 동결 부하 2.6 rps까지 6정책의 goodput이 같았다. 이 부하는 이 fleet을 포화시키지 못했다.
- 정책 차이는 latency뿐이며, NPU vision을 쓰는 fixed가 가장 낮았다.

**limited**
- 전송을 보지 않는 배치(fixed, task_fastest)는 1.6 rps부터 무너졌다.
- 단일 동결 all-edge DAG 계획(dag_static)은 수집된 모든 부하·block에서 100 % 기한 내였다.
- 요청마다 다시 결정하는 정책(dag_reopt, hysteresis, greedy)은 block 간 편차가 컸다(1.6 rps dag_reopt 1.550 vs 0.483). 높은 부하에서는 goodput을 잃었다.

**예측 잔차**
- 잔차 = 실측 latency − 정책 예측 ms다.
- good에서는 작았다(p50 −82…+50 ms).
- limited 고부하에서 dag_reopt는 p50 +1.5 s, p95 +6.9 s였다. dag_static은 p50 +34 ms, p95 +224 ms였다.
- 비용 모델은 현재 regime 정보만 가진다. 입력은 8개 입력 median compute, 4 KB/1 MB × 3 probe, 1 s queue poll + EWMA 0.3이다.
- 관측된 큰 잔차는 이 비용 모델이 해당 과부하 상태의 종단 지연을 정확히 예측하지 못했음을 보여준다. queue·relay 포화 각각의 인과 기여는 분리하지 않았다.
- 관측 나이는 p50 0.3–0.7 s, max 1.7 s였다.
- poll 오류는 good에서 0이었고, 무너진 limited cell에서 30–73 / ~270이었다.

**계획 전환**
- `plan_switches`는 관측된 배치 변경 수가 아니다. raw row에서 시간순 전환을 따로 셌다.
- dag_static·fixed·task_fastest는 0이었다.
- dag_reopt는 최대 31(b0 limited-high), greedy는 최대 30(b1 good-high)이었다.
- 관측 기반 계획 churn이 과부하에서 손실을 낸다는 설명과 맞는다. 다만 인과는 확인하지 않았다(추가 검증 과제).

**해석의 한계**
- greedy_comm도 task별·worker별 측정 compute, 네트워크, queue를 쓴다.
- 따라서 task_fastest와 greedy의 차이를 네트워크 하나로 돌릴 수 없다. queue와 online EWMA도 다르다.
- greedy는 국소 heuristic이고, dag_*는 144개 계획 전체를 열거한다.
- 열거 자체나 이종성·네트워크 고려 자체는 기여가 아니다.

## 감사와 정리

**정리**
- 두 run 모두 소유 객체 8개(Pod 7 + ConfigMap 1)를 생성 UID로만 삭제했다.
- `cleanup_remaining 0`이고, label 조회에서도 남은 자원이 없었다.
- edge kubelet stream·host 변경·privileged·preemption·tunnel은 쓰지 않았다.

**protected 감사: 두 run 모두 `partial`**
- pilot: 비소유 Pod 6개의 이전 UID가 사라졌으며, 일부 서비스에서 04:03–04:11Z의 새 ReplicaSet revision을 확인했다. 모든 소실을 rollout으로 단정하지 않으며, 변경 주체는 확인하지 않았다.
- compare: 비소유 Pod 3개의 이전 UID가 사라졌고, dev0007의 기존 Pod 6개는 Running 상태를 유지한 채 Ready=False에서 True로 바뀌었다.
- 클러스터가 바뀌지 않았다고 주장하지 않는다.

## 남은 일

- 품질 미달 상태다. 독립 평가 자료가 없다.
- cell 70 이후 limited-high b1 group은 수정 범위가 정해진 뒤 새 run id로만 재수집한다.
  - 수정 후보: worker 메모리 상한 또는 bytes 기반 admission.
  - 이번에는 재시도·소스 수정을 하지 않았다.
- block 수와 동결 부하는 탐색용이다.
  - λ*가 검열되었으므로 good의 포화 지점은 측정되지 않았다.
- Kubernetes/Koordinator·DRL·논문 이름 기준선은 미구현이다.
