# 개발 보고 — 센서 작업 전체의 서버 선택 (GPU vs Mobilint NPU), 2026-10-29

Task `task_e566a747c5a1` / dispatch `ctx_fc6d96ad3dfd`. 범위: 코드 수정, 오프라인 테스트, 서명되지 않은 release draft, dry-run.
**live 실행, 클러스터 변경, Git 작업, 외부 다운로드는 하지 않았다.**

## 1. 모델·인증 증거 (확인한 값만 기록)

| 항목 | 값 | 근거 |
|---|---|---|
| authMethod | `claude.ai` | `claude auth status` |
| apiProvider | `firstParty` | `claude auth status` |
| subscriptionType | `max` | `claude auth status` |
| model | `claude-opus-5-5` (Claude Opus 5.5) | 세션 시스템 정보; `worker-start.json`의 launch.requested/effective 모두 `claude-opus-5-5` |
| 환경변수 override | `ANTHROPIC_*`, `CLAUDE_CODE_USE_*`, `CLAUDE_MODEL` 키 없음 | 키 이름만 확인, 값은 출력하지 않음 |

모델·공급자 대체는 없었다. 요청된 `tdd` skill은 이 세션의 skill 목록에 없어 불러오지 못했다. 대신 테스트를 먼저 작성하고(`test_whole_server.py`), 그 테스트에 맞춰 구현했다.

## 2. 변경 파일 (모두 `staged/experiments/gpu-mobilint-placement/` 아래)

| 파일 | 변경 (이전 GB10 native 원본 대비 diff 줄 수) |
|---|---|
| `chain_design.py` | 76 — `PLACEMENTS=('GGG','NNN')`, `host_of()`(혼합 배치 거부), `REGIMES=('relay','npu_slow2')`, `SHAPED_HOSTS=('N',)`, 정책 4종, `MAX_MAIN_JOBS=4`, `cells()`의 혼합 배치/4건 초과 거부, two-flow 예산 삭제, design에 `decision_unit` 추가 |
| `chain_policy.py` | 164 — `fixed_gpu`/`fixed_npu`/`compute_only`/`compute_network`; 비용표 전체 검증(누락·bool·비숫자·NaN/inf·≤0이면 `PolicyError`); tie 표시; probe 6개(S→서버, 같은 서버 내부 HTTP); 서버 간 probe 거부; `probe_evidence()` |
| `chain_generator.py` | 56 — `/trace`는 GGG/NNN만 받음; `/probes`는 시작 전에 S→서버 또는 같은 서버만 허용; `/two_flow` 삭제; profile에서 다른 서버 출력을 입력으로 쓰는 경우 제거(같은 서버 출력만 사용) |
| `chain_live.py` | 176 — `apply_network`: `cloud=[N IP]`, `edge=[]` + `network_problems()` 검사(spec echo, netns, qdisc tree), ips/problems 저장; GGG·NNN 각각 qualification(앞뒤 qdisc snapshot); 새 `route_problems()`; main job마다 snapshot과 `route-<label>.json`(기록만 하고 중단하지 않음); two-flow 삭제; `--repeat-count`/`--measurement-blocks`는 1만 허용; `REPO` 주석 경로 수정 |
| `chain_analyze.py` | 99 — 설명문을 whole-server 결정으로 수정, LIMITATIONS 교체, 유의성 요약 비활성화(`comparisons=[]`), `observed_choice`, `route_verified`, `shaped_band_bytes_delta`, policies.csv의 processing/network/tie 열 |
| `test_whole_server.py` | 신규, 18 tests |
| `test_chain.py`, `test_chain_r1.py`, `test_chain_live.py`, `test_chain_loopback.py`, `test_staged_native.py` | 오래된 설계를 가정한 assertion을 변환함 (§5) |
| `chain_stages.py`, `chain_worker.py` | 변경 없음 (byte-identical) |

이 디렉터리 밖에서 만든 파일은 `release/release-draft.json`, `release/plan.json`, `release/dry-run.out.json`, 그리고 이 보고서뿐이다.
- 형제 디렉터리 `gpu-mobilint-vlm`, `quality-network-*`, `real-topology-live`(`net_control.py` 포함), `multi-edge-sensors`의 소스는 `gpu-spark-dag-20261029` 원본과 동일하다(`diff -rq`로 확인).
- 테스트 실행으로 생긴 `__pycache__`는 삭제했다.
- `frozen/`, `AGENTS.md`, docs, artifacts는 건드리지 않았다.

## 3. 설계 반영 요약

- **결정 단위:** GGG 또는 NNN, 즉 작업 전체를 서버 한 곳에 둔다. 혼합 배치는 세 군데에서 거부한다.
  - 정책: `parts`/`decide`의 `PolicyError`.
  - 스케줄: `D.cells`/`host_of`의 `ValueError`.
  - generator 진입점: `start_trace`/`start_probes`의 `ValueError`, 요청 전송 전.
- **qualification도 혼합 route를 쓰지 않는다:** 조건마다 GGG와 NNN을 실행한다.
- **비용:**
  - `processing(h) = T1 + local(h,1) + T2 + local(h,2) + T3`. 같은 서버 안의 stage 간 HTTP 비용을 포함한다.
  - `network(h) = link(S,h,0)`. 실제 NPY payload를 요청과 같은 gateway route로 보낸 probe의 RTT이며, HTTP/relay 처리 시간을 포함한다.
  - `compute_only`는 processing이 최소인 서버를, `compute_network`는 processing+network가 최소인 서버를 고른다.
  - `fixed_gpu`는 resource-eligible GPU-first 고정 규칙이다. **kube-scheduler라고 부르지 않는다.**
  - `fixed_npu`는 reference다.
  - 같은 서버를 고른 정책들은 관측 1건을 공유한다(alias).
- **네트워크:**
  - 두 조건 모두 S→G와 S→N 요청이 같은 owned gateway를 지난다(S는 edge zone, 서버들은 cloud zone).
  - `relay`: qdisc 없음.
  - `npu_slow2`: 수정하지 않은 `net_control.py`에 `{action:'shape', mbps:2, delay_ms:0, cloud:[N IP], edge:[]}`를 넘긴다. tbf는 N 목적지에만 걸리고, G와 source 응답은 default band(1:3)로 나가 shaping되지 않는다.
- **우회 방지 qualification:** 각 서버 job에 대해 다음을 확인한다.
  - 응답이 정상이고, 첫 route가 `gateway`이며, 두 hop이 같은 서버 안의 `direct`다.
  - receipt가 모두 한 Pod에서 나왔다.
  - gateway counter에는 해당 서버만 1건 있고 byte 수가 payload 이상이다.
  - shaped band(tbf, parent `1:1`)의 byte 증가량이 `npu_slow2`의 N이면 payload 이상, 그 밖의 경우(G, 또는 relay)이면 payload 미만이다.
- **실제 qdisc 구조와 대조:** 기존 artifact `artifacts/quality-network-baselines/qnb-compare-r2/net-0{1,2}-*.json`과 맞춰 봤다. unshaped root는 `noqueue 0:`이고 stats가 있다. shaped는 prio `1:`, tbf `10:`(parent `1:1`), tbf `20:`(parent `1:2`)이다.
- **유지한 것:** identity/device/model/input hash 증명, CPU fallback 금지, UID cleanup, drain/barrier/deadline, release provenance, 시작 시 occupancy 거부(`G.check_occupancy`, `preflight_problems` "held by"). 선점은 하지 않는다.
- **release:** `dependency_hashes()`가 동적으로 로드되는 `G.__file__`(`experiments/gpu-mobilint-vlm/live.py`)을 계속 포함한다. 테스트로 확인했다.

## 4. 실행한 명령과 결과

모든 테스트는 기존 numpy env에서 실행했다(`PY=.scratch/gpu-mobilint-vlm-20261028/env/mblt-x86/bin/python`).

```bash
cd .scratch/sensor-server-placement-20261029/staged/experiments/gpu-mobilint-placement
PYTHONPATH=.:../quality-network-baselines:../quality-network-heterogeneous:../real-topology-live \
  $PY -m unittest test_whole_server test_chain test_chain_r1 test_chain_live test_chain_loopback test_staged_native
# -> Ran 65 tests in 11.3s, OK   (변경 전 원본 기준선: 51 tests OK)
```

`test_whole_server.py`(18 tests)가 확인하는 항목:
- 설계 상수가 GGG/NNN만 허용하고, 정책·스케줄·generator 세 경계 모두 혼합 배치를 거부한다. generator는 이 경우 아무 요청도 보내지 않는다.
- probe는 S→서버와 같은 서버 내부만 있고, 서버 간 probe는 거부된다.
- 고정 fixture에서 processing만 볼 때와 processing+network를 볼 때 선택이 실제로 뒤집힌다.
  - relay: 둘 다 NNN.
  - npu_slow2: `compute_only`는 NNN(724), `compute_network`는 GGG(970 vs 4124).
- 비용을 거울처럼 바꾸면(G↔N) 선택도 바뀐다. 즉 특정 노드를 하드코딩한 선호가 없다.
- tie는 표시된다.
- 누락이나 잘못된 비용은 네 정책 모두에서 fail-closed다.
- alias를 공유하고 main job은 최대 4건이며, CLI의 repeat/blocks가 1이 아니면 거부한다.
- 두 조건 모두 같은 relay를 쓰고, net_control 대상은 N만이다(`cloud=[N]`, `edge=[]`). 잘못된 qdisc 상태는 거부한다.
- probe 증거는 실제 bytes와 유도된 throughput을 기록한다.
- qualification은 GGG와 NNN만 실행하며, 혼합·우회·잘못된 band를 거부한다.
- release가 실행 코드 전부와 `gpu-mobilint-vlm/live.py`를 포함한다.
- analyzer 문구가 whole-server 결정임을 밝히고, 유의성 검정을 하지 않는다.

frozen 자산 검증(읽기 전용, 수정 없음):
- `frozen/assets-manifest.json`의 106개 파일(9,955,125,218 B)과 입력 4개의 sha256/size가 모두 일치한다.
- `frozen/contract.json` = manifest `contract_sha256` = source(`gpu-spark-prep-20261029/frozen/contract.json`) = `4b902ef0…97fb`.
- manifest sha는 `371880695d…90aa`다.

오프라인 CLI(kubectl 미호출 경로; `rtk proxy` 사용):
```bash
L=staged/experiments/gpu-mobilint-placement/chain_live.py
$PY $L --plan                > release/plan.json            # rc 0
$PY $L --print-release-draft > release/release-draft.json   # rc 0, approved_by=null (서명 없음)
# 임시 사본(scratchpad)에 approved_by='TEMPORARY-DRY-RUN-TEST-MARKER-not-a-live-authorization'를 넣고:
$PY $L --dry-run --release <임시 사본>   # {"release":"ok","design_sha256":"0c87f0ad…fbb5","code_files":36}; 사본은 바로 삭제
$PY $L --dry-run --release release/release-draft.json      # 거부: "release needs approved_by" (no kubectl was run)
```
release draft 요약:
- design_sha256 `0c87f0ad7f65ebb2cbae8b3b4d0ecb3ff2082a33286cbdb5aaab8325b8b1fbb5`, code 36개.
- `experiments/gpu-mobilint-vlm/live.py` sha256 `a6719d17…b1c9`.
- regimes `relay`/`npu_slow2`, placements GGG/NNN, max_main_jobs 4.
- 파일 sha256: release-draft.json `9aa275cc…1eeb`, plan.json `e4afd44b…168e`.

**임시 marker는 live 승인이 아니다. 최종 release 승인은 독립 리뷰 뒤 coordinator가 한다.**

## 5. 오래된 assertion 변환 기록 (조용히 끄지 않음)

| 기존 테스트 | 오래된 전제 | 처리 |
|---|---|---|
| `test_chain.Policies.*` | 5개 DAG 정책, 혼합 8배치, link 0 허용 | whole-server 4정책으로 손 계산 재작성. NNN 총합 397/3637은 이전 손 계산값과 같음 |
| `test_chain.Schedule` | 3 regime × 8×8 혼합 스케줄 | relay/npu_slow2 4 cell, 재현성, label 고유성 |
| `test_chain.Analyzer.test_denominator_quantiles_and_paired_test` | GNN vs NNN paired sign-flip | `test_denominator_singletons_and_no_significance`: 분모·not_sent·timeout 무지연·singleton note·`comparisons==[]`. `sign_flip`/`holm`의 단위 테스트는 유지(라이브러리 코드는 남아 있으나 쓰이지 않음) |
| `test_chain_r1.FrozenReplyShape` | GNG/NGN에 실제 frozen receipt | 같은 실제 receipt를 GGG/NNN(같은 Pod, direct hop)로 변환. 위·변조 검출 assertion은 모두 유지 |
| `test_chain_r1.RouteQualification` | 이전 `route_problems(relay=)`, GNG | qualify 중단 테스트는 새 signature로 변환. 세부 route 검사는 `test_whole_server.Qualification`로 이동 |
| `test_chain_r1.NetworkTransitions`/`IntegrityAbort`/`OutcomeAccounting` | native/shaped2, GNN | 새 regime과 GGG/NNN으로 변환 |
| `test_chain_live.test_two_flow_verdict` | 공유 병목 two-flow 검사 | **삭제**. 명세에 따라 two-flow 기능 자체를 삭제했음 |
| `test_chain_loopback` GNG 3곳 | 혼합 chain | NNN(S→N gateway, 같은 서버 hop)으로 변환. generator가 혼합 trace를 거부하므로 |
| `test_staged_native.NativeSchedule` | native 단일 regime | alias 테스트는 4 cell로 변환. `test_native_network_spec…`는 `test_whole_server.Network`로 대체 |

## 6. 남은 한계와 열린 사항

1. **live 실행 안 함.** NPU를 다른 실험이 쓰고 있다(coordinator가 가용성 관리 중). 시작 시 occupancy 거부가 그대로 동작하며 선점하지 않는다.
2. **u32 filter 덤프를 해석하지 않았다.** `network-*.json`에는 raw filter/qdisc/route/link와 netns를 저장한다. 다만 "N만 shaping된다"는 판정은 qualification 중 tbf byte 증가량이라는 실제 거동 증거로 한다. filter 구조 문자열을 파싱해 단정하지 않는다.
3. **main job의 route 판정은 기록만 하고 중단하지 않는다**(`route-<label>.json`, 분석의 `route_verified`). qualification만 fail-closed다.
4. **calibration 비용:** 조건마다 GGG와 NNN qualification이 실행되므로 VLM 실행 4회가 추가된다. 이는 main job 4건과 따로 센다. probe는 1회 측정이라 RTT 잡음에 그대로 노출된다.
5. **profile 변경:** 이제 같은 서버에서 나온 PNG/VLM 출력만 다음 단계 입력으로 쓴다. 이전 방식(다른 서버 출력도 입력으로 사용)보다 profile 호출이 줄고, 비용은 해당 서버 자신의 경로 기준이 된다.
6. **유도 throughput:** `effective_app_mbps`는 실제 bytes를 probe RTT로 나눈 값이다. HTTP/relay 처리와 sink 응답을 포함하므로 링크 용량이나 설정한 2 Mbit/s로 읽으면 안 된다.
7. **tie:** tie가 나면 G를 먼저 택하고 `tie=True`로 표시한다. 고정 fixture에는 tie가 없다. 실측에서 tie가 나면 결과를 해석할 때 따로 밝혀야 한다.
8. **analyzer는 release hash에 묶이지 않는다.** live 경로가 로드하는 실행 코드가 아니고 실행 뒤에 쓰는 도구이기 때문이다. 필요하면 coordinator가 별도 hash를 기록해야 한다.
9. **선택을 강제하지 않았다.** 실측에서 `compute_only`와 `compute_network`가 같은 서버를 고르면 그대로 보고한다(`observed_choice.network_changed_choice=false`).
10. **이전 GNG/GNN DAG pilot은 별도 동결 근거다.** 이번 목표의 수용 근거로 쓰지 않는다. 품질 평가는 범위 밖이며, BF16과 W4V8의 정밀도 차이는 그대로 둔다.
