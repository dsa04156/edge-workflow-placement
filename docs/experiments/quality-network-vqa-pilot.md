# 품질·네트워크 VQA 파일럿 — 설계와 실제 수행 명령

작성일: 2026-10-23. 명세: `.scratch/quality-network-pilot/spec.md`. 구현: `experiments/quality-network-vqa/`.

| 수준 | 상태 |
|---|---|
| 아이디어/설계 | 이 문서와 `run_pilot.py --dry-run`의 동결 설계 |
| 코드 존재 | 공식 VQA 채점 포팅, ledger→goodput/품질 집계, calibration-only 계획 선택, 수집기 CLI |
| 관련 테스트 통과 | 초기 40개 + 엣지 제어 개정 후 영향 모듈 `test_run_pilot` 10개(로컬, stdlib unittest). 아래 로그 경로 |
| 실제 클러스터 실행 | **미실행**. live 게이트 BLOCKED(독립 리뷰 미실행, 2026-10-23). 새 클러스터 추론·성능 수집 없음, 배포한 실험 자원 0 |
| 실장비 성능/공식 수용 | 없음 |

## 1. 서비스와 주장 범위

- 서비스: 공개 VQA1 validation 이미지 + JellyBean 음성 질문 재생. 이미지→ResNet18(448, no-att 특징) ∥ 음성→wav2vec2-base-960h → MUTAN(no-att, ResNet18) 결합. 영상 스트림·실제 센서 수집이 아니다. 입력은 엣지 Pod(e0 이미지, e1 음성)에서 발생한다.
- 첫 파일럿은 **공식 VQA 품질과 2.0초 deadline goodput을 함께 재는 측정 기반 구축과 공유 WAN의 네트워크 효과 진단**이다. 부하·큐 반영 온라인 최적화, goodput 최적 정책, 근접 시스템 전체 재현, 통계적 우월성·장기 안정성을 주장하지 않는다. 1배포·1블록.
- 자산: `artifacts/topology-profile-assets/20261019T091725Z/selected-data/pairs.json`(seed20261019, 이미지 비중복 calibration20/evaluation40). **과거 파일럿에서 이미 평가한 fixture이며 미관측 holdout이 아니다.** 재선별 없음. Pod corpus는 `artifacts/topology-profile-live/load-pairs.json`(raw RGB/PCM 경로 추가본)이며 수집기는 두 파일의 question/split 순서와 human_answers 동일성을 시작 시 검사한다.
- 모델: `experiments/real-topology-live/model_adapter.py`와 동결 asset, CPU FP32·torch thread 1. 후보는 ARM Pi e0/e1 + x86 c의 CPU뿐이며 가속기는 검증이 없어 후보가 아니다.

## 2. 품질

- 채점: GT-Vision-Lab/VQA commit `a013f0043c1e2cdc995922dfe257f7149aa9af06`의 `PythonEvaluationTools/vqaEvaluation/vqaEval.py`(sha256 `f08edfca…bec8`, BSD-2)를 `upstream/`에 원본 그대로 보존(`upstream/PROVENANCE.json`). `vqa_eval.py`가 Python3 포팅이다.
  - 유지: 개행/탭/strip, human answer 10개가 모두 같지 않을 때만 punctuation·숫자/관사/축약 정규화, answer dict 단위 leave-one-out 10회 평균, `min(1, matches/3)`. 1/2/3/4명 일치 → 0.3/0.6/0.9/1.0. `periodStrip.sub`의 세 번째 인자(re.UNICODE=count 32)도 그대로 둔다.
  - 동작 차이(명시): 상류는 Python2라 unicode 답에 str 정규식의 `\d`가 ASCII 숫자만 맞는다. 포팅은 `re.ASCII`로 이를 유지한다. Python3에서 상류 파일을 그대로 실행한 oracle은 Unicode 숫자도 `\d`로 본다(ASCII 숫자에서는 동일). 문항 값은 반올림하지 않은 0..1이다(상류는 보고용으로 100·acc를 소수 2자리 반올림).
  - 독립 oracle: `vqa_eval.upstream_oracle`가 상류 파일을 hash 확인 후 print문만 메모리에서 무력화해 실제 실행한다. 실제 60쌍에서 모델 답·모든 human 답과 변형 철자(대문자+마침표, 관사, 물음표)에 대해 포팅과 값이 같다(테스트).
  - 정답 결측·10개 아님·answer_id 중복, 중복 question_id/답, 미지 question_id는 거부한다. 단순 `min(matches/3)` 대체는 쓰지 않는다.
- CPU reference 일치(`quality_ok`, 기존 코드)와 정답 품질은 분리한다. 기존 `load_trace`의 `quality_ok`/`deadline_error`는 CPU 일치·10초 취소 표시일 뿐이며 `metrics.py`는 이를 `cpu_reference_mismatch`/`cancel_timeout_exceeded` 개수로만 보고한다.
- 탐색용 품질 문턱 Qmin=0.40(운영 요구·논문 수용 기준 아님).
- **기존 reference 출력의 오프라인 재채점(새 추론 아님)**: `reference-v1/results.jsonl`(단일 프로세스 CPU 순차)의 top-1 답에 공식 채점 → calibration **0.515**(20/20 응답), evaluation **0.355**(40/40 응답). calibration gate는 통과 조건이지만 **evaluation 집단은 지연이 0이어도 Qmin 미달**이다. 따라서 evaluation phase의 goodput은 숫자와 실패한 품질 gate를 따로 표시해야 하며, 문턱을 낮추거나 입력을 제외하지 않는다. 증거: `artifacts/quality-network-vqa/reference-quality-20261023T061045Z/score.json`.

## 3. SLO·부하·네트워크

- SLO 2.0초: 예정 발생 시각(scheduled origin)부터 최종 결과 반환 + 기존 fusion ack 경계까지(`load_trace`의 `finished_s`). `latency <= 2000 ms`면 on-time(경계 포함). 시스템 취소 timeout은 10초(`load_trace` `deadline_s`)로 분리하고 config에 `deadline_semantics`로 기록한다. 늦게 끝난 정상 출력의 품질은 보존하고 late로 집계한다.
- goodput = on-time 반환 요청 수 / (count/rate). 모든 유입(드롭·오류·timeout·missing terminal)을 분모에 둔다. timeout = client 측 `TimeoutError`/`timed out` + 서버 deadline 만료(HTTP 504, `http_runtime`의 queue/실행/forward 만료); 다른 HTTP 코드(500/429/502 등)는 error. 기존 `load_trace`는 `repr(error)`만 남기므로, 이 run의 소유 ConfigMap에서만 `http_runtime.py` 사본의 `__main__` guard 앞에 pilot adapter(`vqa_http_adapter.py`) 2줄을 넣는다(디스크 원본 무변경). 감싼 `request`는 HTTP 오류 시 본문(≤64 KB)을 읽어 바깥→안쪽 상태 코드 사슬을 담은 `HTTPChainError`를 던지고, 기존 500 wrapper(edge_client handler, http_runtime Handler)의 `repr(error)`가 사슬을 전달해 terminal row에 남는다. `metrics.failure_evidence`는 가장 안쪽 코드가 504이고 모든 wrapper가 500/504일 때만 timeout(`nested_server_deadline_http_504`)으로 보고, 메시지 문구나 경과 시간은 쓰지 않는다. adapter가 없는 legacy repr은 바깥 코드만으로 판정한다. 서버 `invoke`의 future 대기 만료(`TimeoutError()`를 500으로 감쌈)는 504가 아니므로 error로 남는다. 품질은 반환 coverage 평균과 전체 유입 zero-impute 평균을 모두 표시한다. phase 품질 gate 통계량은 전체 유입 zero-impute 평균 ≥ Qmin이며 gate 실패 시 `quality_qualified_goodput_rps=null`로 숫자와 분리된다.
- open-loop 0.5/1.0 rps, phase마다 calibration20 또는 evaluation40을 각 1회, offset=i/rate, max_inflight 32. source e0/e1 고정. raw RGB/PCM만(압축 혼합 없음); decode는 배포 전 offline이며 clock 밖이다.
- 네트워크: `run_load.Session`의 소유 gateway Pod(c)에서 zone/방향별 공유 cap 100/2 Mbps, 방향별 delay 20 ms. 호스트 링크 shaping·cloud relay/tunnel 없음. `network_preflight`/`guard`/`shape`/`drain` 재사용.

## 4. 정책·비용표

- 계획 공간: vision ∈ {v-edge(e0), v-cloud(c)}, speech ∈ {s-edge(e1), s-cloud(c)}, fusion = c 고정. 자유도 2인 제약된 DAG, 계획 4개. 4개 operator가 한 배포에 모두 떠 있고 정책은 경로(endpoint)만 바꾼다. 용량은 이 배포의 operator 예약 슬롯이다.
- 비용표(`vqa_plan.build_profile`): calibration20만으로 network 조건마다 새로 측정·동결. compute = 분기 qualification receipt와 fusion calibration receipt의 p75, 전송 = 워크플로와 같은 경로로 in-Pod payload POST(`/sink`)의 p75(엣지 출발은 source Pod `/transfer_profile` — 재사용 handler가 `target_url`에 `/sink`를 붙이므로 endpoint base URL만 전달, 서버 출발은 서버 Pod 내부 loop), 반환 = fusion 답 JSON proxy + control proxy. 순수 단방향 네트워크 시간이 아니며 큐/경합을 예측하지 않는다.
- 선택기(같은 동결 profile): task-fastest(compute만, multi-edge `taskwise_fastest`와 같은 규칙), greedy-network(`placement_policies.sequential`), dag-exact(`prepare.plan topology_profile`, 4계획 정확 열거 — 새 알고리즘·논문 재현 아님), dag-exact-network-blind(같은 열거, `profile_only`로 선택에서만 통신 0, 실행 경로는 유지), empirical calibration oracle(조건별 calibration trace의 on-time 최대, 동률은 median 지연 — 측정 기반 선택이며 greedy/exact와 구분).
- 품질 필터: calibration 출력의 계획별 공식 품질(반환 출력 평균, 조건 내 두 rate 합산) ≥ Qmin인 계획만 선택. 각 선택기와 oracle의 필터 없는 선택은 별도 evaluation alias `<정책>+no-quality-filter`로 schedule에 들어가며, 필터 정책이 quality-rejected/infeasible이어도 유지된다(조건마다 evaluation phase가 최소 1개, 0건 completed 거부). 단일 고정 모델이므로 차이가 없으면 "없음"으로 보고한다. 낮은 부하 비용으로 고른 고정 배치는 goodput 최적 정책이 아니다.
- 같은 조건에서 같은 계획을 고른 정책·alias는 한 evaluation phase 관측을 공유한다(조건 간 공유 없음). alias는 4계획 밖의 배치를 만들 수 없어 dry-run 상한(조건당 4, 총 16 phase)은 변하지 않는다. 조건/고유 계획 순서는 seed 20261023으로 사전 셔플.
- 미구현(명시): 실제 kube-scheduler 기준선(모든 계획을 한 배포에 고정 배치하므로), 온라인·부하 반영 재최적화, 반복 블록, 가속기, 압축 표현.

## 5. 수집 절차와 증거(`run_pilot.py --execute`, 미실행)

보호 Pod 스냅샷(투영) → 재사용 run label 거부 → create-only 배포(API 반환 UID 기록) → 실행 Pod UID/노드 확인 → network preflight → 60쌍×4 분기 후보 raw CPU-reference qualification(`Session.qualify`, 제외 없음) → fusion calibration → **calibration 공식 품질 gate(Qmin)**, 미달이면 `quality-infeasible.json`만 남기고 부하 phase 없음 → guard → network 조건별 전송 profile + 4계획×rate calibration trace → profiles/plans/oracle 동결(`plans-frozen.json`) → 이 시점 이후에만 evaluation 라벨 사용 → 고유 계획 evaluation → 복구 → UID precondition 삭제(`multi-edge-sensors/collect.cleanup`).

- 보존: phase start/end(`run_baseline_comparison.phase`), kubectl call별 stdout/stderr/event(timeout 부분 출력 포함), collector·재사용 source 사본과 hash, spec 사본, 실제 Pod manifest/UID/노드, receipt의 model/input/output hash, phase별 raw qdisc·backlog·relay counter, drain ledger, cleanup(remaining/errors/preserved/unresolved), 보호 Pod 전후 diff. 보호 workload 변화가 있거나 정리가 검증되지 않으면 `run-status.json`의 `audit=partial`.
- **소유권 확인(fix-r1)**: `VqaSession.deploy`는 `run_load.Session.deploy`와 같은 객체·순서이되, cache·gateway·worker 각 Pod의 생성 UID와 의도 노드를 첫 쓰기 전에 API GET으로 확인한다(`owned_state`). cache는 기록 identity에 포함되고, asset upload는 직접 subprocess가 아니라 guarded/logged `call` seam(`*-cache-upload.*`)으로 간다. 이후 모든 kubelet stream(exec·logs 포함, `collect_pod_logs`와 상속 `ownership.cleanup`의 logs-final까지)은 공통 `VqaSession.call` seam에서 매번 새 API GET으로 생성 UID와 의도 서버 노드를 확인한 뒤에만 kubectl로 간다. 엣지·미기록·없음·불일치 대상은 fail closed: logs는 `*.skipped.json`, 다른 verb는 `*.refused.json`. 같은 이름 교체·엣지 노드 오배치 Pod에는 upload/peer file/shaping이 가지 않는다. 확인과 exec 사이의 짧은 TOCTOU 창은 남는다.
- **엣지 제어 경계(2026-10-23 개정, `.scratch/quality-network-pilot/edge-control-proposal.md`)**: e0/e1은 unpatched KubeEdge v1.23.0이라(`docs/experiments/edgecore-crash-fix.md`의 stream ReadChan panic·재시작 볼륨 해제) 엣지 노드 Pod에 대한 kubelet stream(exec/attach/logs/cp/port-forward/top/debug)을 쓰지 않는다. `VqaSession.call`이 kubectl 호출 전에 거부하며 엣지 logs는 명시적으로 skip한다. 엣지 Pod에는 netctl sidecar가 없다. guard는 서버 zone operator Pod(v-cloud/s-cloud/fusion)에서만 모든 엣지 Pod IP를 blackhole하고, 서버측 exec 증명과 source `/transfer_profile` HTTP 증명(직접 실패·gateway 성공, guard 전 직접 성공)으로 확인한다. v-edge/s-edge는 직접 probe하지 않고 같은 서버측 규칙으로 덮는다(한계). network preflight의 엣지측 capacity/latency도 source HTTP로 측정하며 capacity 분모는 두 동시 요청을 감싼 collector monotonic 구간, 문턱은 불변. 수집기는 노드·EdgeCore 재시작이나 호스트 조작을 발행하지 않으며 노드/Pod 상태 변화는 API로 관찰만 한다(인과 주장 없음). 엣지 stdout·emptyDir trace 파일은 Pod 삭제 시 소실되며 엣지 증거는 HTTP health·trace_status 이벤트·receipt이다.

## 6. 실제 수행 명령과 결과

아래는 명령 본문을 기록한 것이다. 실제 셸 실행에는 저장소 지침에 따라 `rtk` 또는 `rtk proxy`를 사용했다.

```bash
# 공식 evaluator 원본 고정(2026-10-23)
git ls-remote https://github.com/GT-Vision-Lab/VQA HEAD   # a013f0043c1e2cdc995922dfe257f7149aa9af06
C=a013f0043c1e2cdc995922dfe257f7149aa9af06
curl -sfL --max-time 30 -o vqaEval.py https://raw.githubusercontent.com/GT-Vision-Lab/VQA/$C/PythonEvaluationTools/vqaEvaluation/vqaEval.py
curl -sfL --max-time 30 -o license.txt https://raw.githubusercontent.com/GT-Vision-Lab/VQA/$C/license.txt
curl -sfL --max-time 30 -o README.md https://raw.githubusercontent.com/GT-Vision-Lab/VQA/$C/README.md

# 기존 reference 품질
python3 experiments/quality-network-vqa/vqa_eval.py score \
  --pairs artifacts/topology-profile-assets/20261019T091725Z/selected-data/pairs.json \
  --answers artifacts/topology-profile-live/reference-v1/results.jsonl \
  --out artifacts/quality-network-vqa/reference-quality-20261023T061045Z/score.json
# -> calibration answered_mean 0.515 (20/20), evaluation 0.355 (40/40)

# 수집기 CLI (클러스터 호출 없음)
python3 experiments/quality-network-vqa/run_pilot.py --help     > artifacts/quality-network-vqa/dryrun-20261023T062145Z/help.stdout
python3 experiments/quality-network-vqa/run_pilot.py --dry-run  > artifacts/quality-network-vqa/dryrun-20261023T062145Z/design.json
# -> calibration trace phase 16(320 요청), evaluation 고유 계획 phase 4..16(160..640 요청),
#    qualification 240 + fusion 20, 전송 POST 400, 예상 2111..6299 s(상수 기반 계획 추정, 측정 아님)
#    조건 순서: net2-r1, net100-r0.5, net100-r1, net2-r0.5

# 관련 테스트(로컬)
cd experiments/quality-network-vqa
PYTHONDONTWRITEBYTECODE=1 python3 -W ignore -m unittest -v test_vqa_eval test_metrics test_vqa_plan test_run_pilot
# -> Ran 40 tests, OK. 로그 .scratch/quality-network-pilot/test-20261023T062158Z.log
```

- `test_run_pilot`는 테스트 전용 `fake_rtk.py`(클러스터 아님)로 `--execute`의 배포 실패 경로를 공개 CLI로 구동한다: 자신이 만든 Pod만 UID precondition으로 삭제, 같은 이름의 교체된 객체 보존, 재사용 run label이면 create 전 거부, 보호 Pod 변화 시 audit partial.
- 배포 이후의 live 경로(preflight·qualification·trace·동결·evaluation)는 로컬에서 실행 검증하지 않았다. 보조 확인으로 실제 `load_trace.run_trace` ledger(원격 호출만 stub)→`metrics`, in-Pod 전송 코드의 로컬 sink 실행(code 인자 58 KB), calibration payload hash를 확인했다(`.scratch/quality-network-pilot/glue-check.log`, 커밋 대상 테스트 아님).

## 7. 감독 하 live 실행 전 확인할 위험

1. evaluation reference 품질 0.355 < Qmin: evaluation phase의 품질 gate는 지연과 무관하게 실패가 예상된다. coordinator는 문턱·자료를 유지한 진단 실행으로 한정했다. 동일 품질 조건에서의 개선 주장에는 사용할 수 없다.
2. raw 이미지 0.64–1.23 MB: 2 Mbps에서 v-cloud 계획은 이미지 업로드만 2.5–4.9초라 SLO 2.0초를 만족할 수 없고 1 rps에서는 유입 bytes가 cap을 넘어 과부하가 된다(진단 목적상 의도된 조건). drain이 길어질 수 있다.
3. 구형 EdgeCore stream 문제를 피하도록 엣지 Pod의 kubelet stream을 차단하고 서버측 guard·source HTTP preflight로 변경했다(5절). 이 새 경로는 로컬 테스트만 통과했으며 독립 리뷰와 실제 클러스터 검증이 남아 있다.
4. 계획 품질은 calibration trace의 반환 출력으로 정한다(10초 취소 안에 반환되지 않은 요청은 품질 분모에서 빠지고 coverage로 따로 남는다).
5. 실행 명령(감독 dispatch에서만): `python3 experiments/quality-network-vqa/run_pilot.py --execute` (결과 `artifacts/quality-network-vqa/qnv-<UTC>/`).

## 8. 2026-10-23 live dispatch(task_e00bf53e8fdd) 상태 — 미실행

- **live 게이트 BLOCKED**: 독립 리뷰(Astra)가 Codex 시작 시 hook 신뢰 승인 대기로 실행되지 않았다. 독립 리뷰를 거친 상태도, 운영 준비 상태도 아니다. `--execute`는 실행하지 않았고 새 클러스터 추론·성능 수집은 없다.
- 읽기 전용 preflight(`kubectl get nodes`, `describe node`, `get pods`): e0 `etri-dev0003-raspi5`, e1 `etri-dev0002-raspi5`, c `etri-ser0002-cgnmsb` 모두 Ready. e0/e1은 unpatched KubeEdge v1.23.0. 요청 기준 여유는 e0 약 3.2 CPU/7.6 GiB, e1 약 3.0 CPU/15.3 GiB, c 약 21.5 CPU/26 GiB로 설계에 충분하다. `topology-profile-lab`에는 이 실험과 무관한 `diag-ec-fleet-20261022-*` Pod 5개(Error/Terminating)가 있으며 건드리지 않았다.
- 엣지 stream 회피 개정은 5절과 `.scratch/quality-network-pilot/edge-control-proposal.md`에 있다(coordinator 수정 조건부 승인 후 구현). 신규 guard/preflight 메서드는 로컬에서 실행할 수 없어 live에서 처음 실행된다.
- 실험 자원: `kubectl get pods,configmaps -A -l experiment=quality-network-vqa` 결과 0개. 이 dispatch에서 create/delete/shape 호출 없음.
- 테스트: 초기 40개 OK(`.scratch/quality-network-pilot/test-20261023T062158Z.log`), 개정 후 영향 모듈 `test_run_pilot` 10개 OK(`.scratch/quality-network-pilot/test-run_pilot-edge-20261023T063306Z.log`).
- 재개 명령(독립 리뷰 통과와 coordinator 승인 후에만, 저장소 루트에서):
  `rtk proxy python3 experiments/quality-network-vqa/run_pilot.py --execute --run-id qnv-<UTC yyyymmddthhmmss>` → `artifacts/quality-network-vqa/qnv-…/`

## 9. 2026-10-23 round-1 리뷰 결함 수정(fix-r1) — live 미실행

- 결함 4건(전송 URL `/sink/sink`, unfiltered ablation 미실행, HTTP 504 미집계, deploy 쓰기 전 소유권 미확인)을 신규 모듈 안에서만 수정했다. 기존 `real-topology-live`/`multi-edge-sensors` source는 변경하지 않았다. 상세·diff 범위·한계는 `.scratch/quality-network-pilot/fix-r1-report.md`.
- 테스트(로컬, 클러스터 호출 없음): 영향 모듈 `test_metrics test_vqa_plan test_run_pilot` 43개 OK, returncode 0(`.scratch/quality-network-pilot/test-fix-r1-green.log`). 같은 신규 테스트를 리뷰 시점 코드(hash 일치)에 돌린 red: 11개 실패, 기존 32개 통과(`.scratch/quality-network-pilot/test-fix-r1-red-prefix-code-final.log`).
- dry-run(`dry-run-fix-r1.log`): evaluation 고유 계획 phase 4..16, 160..640 요청, 예상 2111..6299 s 불변; reference calibration 0.515 / evaluation 0.355, Qmin 0.40 불변. live는 여전히 quality-infeasible 진단이다. 실행별 소유권 GET(exec마다 1회, 약 80회 예상)은 추정 상수에 포함하지 않았다.

## 10. 2026-10-23 round-2 리뷰 결함 수정(fix-r2) — live 미실행

- (P1) failure path 로그 stream: 교체/엣지 cache와 교체 gateway에 `kubectl logs`가 가던 경로를 공통 stream seam의 fresh UID+서버 노드 확인으로 차단(5절). (P2) nested 504: 소유 ConfigMap 주입 adapter로 상태 코드 사슬을 terminal ledger까지 보존(3절).
- 테스트: `test_metrics test_run_pilot` 32개 OK, returncode 0(`.scratch/quality-network-pilot/test-fix-r2-iter1.log`); 같은 테스트의 수정 전 red는 returncode 1, failures 6 + errors 2(`test-fix-r2-red.log`). `test_vqa_plan`/`test_vqa_eval`은 대상 코드 무변경이라 재실행하지 않았다. 실제 handler 사슬 원시 출력: `chain-probe-fix-r2.log`. dry-run 불변(`dry-run-fix-r2.log`). 상세 `.scratch/quality-network-pilot/fix-r2-report.md`.

## 11. 2026-10-23 live 시도 `qnv-20261023t073121` — 실패(배포 단계, 추론 0건)

- 명령(저장소 루트, gate release 후): `rtk proxy python3 .scratch/quality-network-pilot/run_logged.py .scratch/quality-network-pilot/live-qnv-20261023t073121.console.log . <hash 대상> python3 experiments/quality-network-vqa/run_pilot.py --execute --run-id qnv-20261023t073121` → returncode 1(07:31:27–07:33:05 UTC). 코드 hash run_pilot `a3ccf51d…`, metrics `8f07ba6f…`, vqa_http_adapter `8a9ac13d…`, vqa_plan `5c6f4b25…`.
- 진행: preflight(run label 재사용 없음) ok → deploy에서 cache Pod 생성(UID `789e079c-6908-4440-af62-253b38bb1690`) 후 `kubectl wait --for=condition=Ready --timeout=90s` 시간 초과. 정리 직전 API 관찰: `phase=Pending`, `nodeName=null`, conditions 없음(스케줄러가 이 Pod를 처리하지 않음). network preflight·qualification·quality gate·calibration·evaluation phase는 **실행되지 않았다**. 새 추론·품질·goodput 결과 없음(0 요청); 0.515/0.355는 여전히 과거 reference 오프라인 재채점일 뿐이다.
- 같은 시간대 관찰(인과 미증명): kube-system 이벤트에 kube-apiserver liveness/readiness HTTP 500, etcd readyz timeout, kube-controller-manager·kube-scheduler liveness 실패와 재시작이 기록되었고, `lease/kube-scheduler`는 holder 비어 있음·마지막 갱신 07:30:07Z(07:33:52Z 확인), controller-manager 갱신 07:29:57Z였다. 이후 07:35:51Z에 새 holder로 갱신. 수집기는 control plane·호스트 조작을 하지 않았다. Pod가 스케줄되지 않은 직접 원인이 이 장애라는 것은 시간 일치에 근거한 추정이며 증명되지 않았다.
- 소유권·정리: cache 1개만 생성, UID precondition 삭제 `deleted`, `cleanup_verified=true`, remaining 0, errors 없음. 최종 읽기 전용 확인에서 `experiment=quality-network-vqa` 객체 0개. 엣지 stream 0건: cache logs 3회는 fresh 소유권 확인(node=None)으로 skip(`0009/0011/0015-*.skipped.json`), 엣지 Pod는 생성 전. `audit=partial`: 보호 Pod 253개 중 다른 namespace의 4개(edgeai-hawkeye-safety, my-ekuiper-0, kueue controller, runtime-operator)가 장애 시간대에 readiness가 바뀌었다(관찰만, 인과 주장 없음).
- 증거: `artifacts/quality-network-vqa/qnv-20261023t073121/`(run-status, phase 이벤트, call별 stdout/stderr/event, cleanup, protected-diff, collector-source), `.scratch/quality-network-pilot/live-qnv-20261023t073121.console.log`, `-controlplane-events.log`, `-controlplane-pods.log`, `-final-status.log`, 보고 `live-qnv-20261023t073121-report.md`.
- 결정(coordinator): 재시도하지 않는다. CPU-only 범위는 수정 예정이며 다음 작업은 이 워크로드를 실제 GPU/NPU에서 qualification한다. 이 시도는 재실행·덮어쓰기 없이 보존한다.
