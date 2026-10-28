# live-report — un-collect-r3 실제 이기종 불안정 네트워크 수집·분석 (task_6a17e77beab5 / ctx_0539822f9512)

작성: Claude Code, 2026-10-27. commit/push 안 함. 구현 파일, 동결 plan, 예산, trace, 순서표는 바꾸지 않았다. 중첩 worker와 리뷰도 없다.

## 1. 결과 수준

| 수준 | 상태 |
|---|---|
| 실제 수집 (실장비 CPU / Jetson GPU / Intel NPU) | **36/36 cell 수집 완료.** skip 0, fault 0, fence 0, 수집 시 timing_invalid 0 |
| 오프라인 분석 | rc 0, stderr 0 B. timing valid 36/36, comparable 36/36 |
| UID 소유 cleanup | 8/8 deleted, 남은 객체 0, `cleanup_verified=true`. protected_changes `[]`, audit complete. 수집 후 읽기 전용 kubectl 확인에서도 `un-collect-r3` 객체 0 |
| 품질 | **diagnostic.** Qmin 0.40 미달(재사용 60쌍 eval 0.355). 품질 적격 성공이나 독립 holdout이 아니다 |
| 통계 | 탐색적 2 block. 유의성·capacity 주장 없음 |
| 미포함 | 실제 센서 수집, 물리 링크 변경, 공식 수용 |

## 2. 모델·인증 (비밀값 제외)

- `claude auth status` 안전 필드: loggedIn=True, authMethod=claude.ai, apiProvider=firstParty, subscriptionType=max.
- `ANTHROPIC_*` / `CLAUDE_CODE_USE_*` 환경변수는 없다(이름만 검사).
- 이 세션 transcript `~/.claude/projects/-home-jinuk-codex-work-scheduler/9c5940d5-64f1-46b3-bebe-f1178323f98b.jsonl`: assistant `message.model`은 `claude-opus-5-5`만 있다(검사 시점 13건, 다른 모델 0).
- lifecycle: `/home/jinuk/.orca/cli/bin/orca` 1.4.235.

## 3. 사전 검증

1. release 결속: `release-collect-r3.json` == `coordinator-release.json.release_binding` == 새 dry-run `release_binding` (True).
   - plan `ee0aba54…c1e7`, shipped `e358c10d…4bc1`, budgets `56dd2a43…85f9`, traces `cec8c6d4…7fb3`, schedule `00eb908d…dc40`
2. dry-run rc 0, drift `[]`, stderr 0 B (`live-r3/dry-run.json`).
3. 현재 가용성(읽기 전용, 11:20Z): `live-r3/preflight-refresh-20261027T1120Z/{nodes,pods}.json`, stderr 보존.
   - dev0003, dev0009, ser0002: Ready, 압박 없음, unschedulable false
   - dev0009 `nvidia.com/gpu.shared` 2: 사용 Pod 없음
   - ser0002 `npu.intel.com/accel` 1: 사용 Pod 없음
   - ser0002의 별도 `nvidia.com/gpu` 1은 `edgeai/edgeai-scenario-gpu-…`가 사용 중이다. coordinator preflight와 같고, 이 plan은 쓰지 않는다
   - 기존 `un-collect` Pod는 없었다
4. coordinator 안내 이후 기존 진입점 `experiments/quality-network-baselines/capture.py`도 실행했다.
   - 출력: `live-r3/preflight-capture-during-prep/`, rc 0, stderr 0 B
   - 수집 준비 중에 찍었으므로 run 자신의 Pod가 포함된다
5. 수집 후 dry-run: rc 0, drift `[]`, binding 동일(`live-r3/dry-run-after.json`). 실행 중 소스는 바뀌지 않았다.

## 4. 실행 명령과 시간

```sh
rtk proxy bash -c 'PYTHONDONTWRITEBYTECODE=1 python3 experiments/unstable-network/unstable.py execute --plan artifacts/unstable-network/plan-20261027T111532Z --run-id un-collect-r3 --release .scratch/unstable-network-20261027/release-collect-r3.json > .scratch/unstable-network-20261027/live-r3/execute.stdout 2> .scratch/unstable-network-20261027/live-r3/execute.stderr; echo $? > .scratch/unstable-network-20261027/live-r3/execute.rc'
rtk proxy bash -c 'PYTHONDONTWRITEBYTECODE=1 python3 experiments/unstable-network/unstable.py analyze --plan artifacts/unstable-network/plan-20261027T111532Z --run artifacts/unstable-network/un-collect-r3 --out artifacts/unstable-network/un-collect-r3-analysis.json > .scratch/unstable-network-20261027/live-r3/analyze.stdout 2> .scratch/unstable-network-20261027/live-r3/analyze.stderr'
```

**execute**
- 시각: 11:20:35.87Z → 12:14:38.80Z (54 min 3 s), rc 0
- stdout/stderr: 0 B / 0 B. 수집기는 모든 기록을 run 디렉터리에 쓴다
- wall budget: 10,800 s 중 3,242.9 s 사용, 7,557 s 남음

**phase**
- preflight ok
- cache ok (49.8 s, assets.tar 3.99 GB)
- pods ok (1.2 s)
- readiness ok (604.4 s)
- profile ok (65.1 s, `measured:un-collect-r3:good`)
- probes: unshaped-direct-control ok, good ok
- trace cell 36개 모두 ok

**analyze**: rc 0, stderr 0 B. 출력 `artifacts/unstable-network/un-collect-r3-analysis.json`(새 파일, sha256 `bcabbe38…a4ac`).

**모니터링**
- Orca heartbeat 약 5분 간격: `live-r3/heartbeats.log`
- 파일 기반 monitor: fault / skip / deadline / 비정상 phase를 감시했다. 이벤트 0
- 중간 개입·재시작·조건 변경은 없다

## 5. 산출물

- run: `artifacts/unstable-network/un-collect-r3/` (1,134 파일, 63 MB; `assets.tar` 별도 3.99 GB)
  - `collection.json` `6c27f179…89dc`
  - `cleanup.json` `20365301…434a`
  - `run-status.json` `e445fd5c…a5e0`
- 분석: `artifacts/unstable-network/un-collect-r3-analysis.json`
- 실행 증거: `.scratch/unstable-network-20261027/live-r3/`
  - dry-run 전후, preflight-refresh, capture, execute/analyze stdout/stderr/rc/시각, heartbeats, post-cleanup-check
  - `summarize.py`(읽기 전용 보고 도우미, 검토 파이프라인 아님), `summary.json`, `summary-tables.txt`

## 6. 관측 결과 (실제 데이터; 진단 품질, 탐색적 2 block)

**전체 cohort**
- 예정 도착: 3,462건
- 결과: ok 2,929, late_ok 265, timeout 81, admission_drop 187
- invalid 0, foreign row 0, terminal status 없는 row 0

**trace×정책, 2 block 합계·평균** ([b0, b1])

| trace | policy | 요청 | ok/late/timeout/drop | in-deadline | p95 ms | 품질(timely, 0 대입) | 계획 전환 |
|---|---|---|---|---|---|---|---|
| stable | 6개 정책 모두 | 190씩 | 190/0/0/0 | 1.000 | 246(task_fastest)–673(no_network) | 0.361 | 0–22 |
| slow | dag_static | 215 | 215/0/0/0 | 1.000 | 276 | 0.337 | 0 |
| slow | greedy_comm | 215 | 206/6/0/3 | 0.956 [0.913, 1.0] | 1395 | 0.316 | [11, 12] |
| slow | dag_reopt | 215 | 168/28/9/10 | 0.786 [0.903, 0.670] | 3757 | 0.258 | [6, 17] |
| slow | dag_reopt_hysteresis | 215 | 155/24/8/28 | 0.724 [0.796, 0.652] | 6109 | 0.219 | [6, 4] |
| slow | task_fastest | 215 | 150/47/0/18 | 0.698 [0.709, 0.688] | 5829 | 0.224 | 0 |
| slow | dag_reopt_no_network | 215 | 136/25/15/39 | 0.632 [0.612, 0.652] | 6302 | 0.209 | [16, 20] |
| burst | dag_static | 172 | 172/0/0/0 | 1.000 | 339 | 0.348 | 0 |
| burst | dag_reopt_hysteresis | 172 | 161/11/0/0 | 0.935 [0.989, 0.882] | 3825 [848, 6802] | 0.318 | [5, 4] |
| burst | dag_reopt | 172 | 143/13/9/7 | 0.831 [0.828, 0.835] | 3259 | 0.300 | [12, 21] |
| burst | greedy_comm | 172 | 100/37/10/25 | 0.579 [0.770, 0.388] | 3095 [375, 5814] | 0.189 | [12, 20] |
| burst | task_fastest | 172 | 94/44/11/23 | 0.546 [0.575, 0.518] | 6605 | 0.178 | 0 |
| burst | dag_reopt_no_network | 172 | 89/30/19/34 | 0.517 [0.540, 0.494] | 7944 | 0.167 | [14, 9] |

**해석과 반례**
- **dag_static의 1.000은 적응 효과가 아니다.**
  - 초기 good profile에서 고른 고정 계획은 전부 엣지다: image/audio_preprocess는 cpu-e0(dev0003), vision/speech/fusion은 Jetson dev0009.
  - 그래서 shaping이 걸린 edge↔server 경로로 데이터가 지나가지 않는다. 그 경로의 gateway relay 본문은 cell당 약 4 kB였고, 관측 poll로 보인다.
  - 이 실험은 edge↔edge LAN을 shaping하지 않는다. 따라서 이 결과는 "열화 경로를 쓰지 않는 엣지 단독 계획이 deadline 안에 가능했다"는 범위 조건의 결과다. 일반적인 우월성은 아니다.
- 관측 기반 재최적화 정책(dag_reopt, hysteresis, greedy_comm)은 관측에 따라 server CPU/NPU 계획으로 전환했다. 그 뒤 열화 phase에서 손실이 났다.
- 통신 ablation `dag_reopt_no_network`(선택 비용에서 전송비 0)는 slow와 burst 모두에서 가장 낮았다. dag_reopt 대비 −0.154 / −0.314다. 네트워크 비용을 반영하면 나아진다는 방향과 맞지만 2 block 탐색 결과다.
- block 간 차이가 크다. 예: greedy_comm burst 0.770 대 0.388, hysteresis burst p95 848 대 6802 ms.
- stable에서는 정책 간 차이가 지연에만 있었다.

**phase cohort** (명목 구간 기준, b0/b1 평균)

slow 열화 [20, 40):
- dag_static 1.00, greedy_comm 0.92, dag_reopt 0.60, hysteresis 0.26, task_fastest 0.15, no_network 0.01

burst:
- [10, 20) lossy: static 1.00, hysteresis 0.79, greedy 0.70, reopt 0.44, task_fastest 0.10, no_network 0.00
- [20, 28) narrow: static 1.00, hysteresis 0.79, reopt 0.40, 나머지 0.00
- [28, 30) outage: **cell당 도착 1건뿐이다.** 해석하지 않는다
- [40, 50) 재열화: static / reopt / hysteresis 1.00, greedy 0.50, no_network / task_fastest 0.04
- `boundary_uncertain`: 전체 2건

**회복** (slow B40, burst B30·B50): 72개 경계 모두 `recovered`.
- 첫 회복 시간은 같은 block 안에서 정책 간 거의 같다. 정책이 같은 도착 cohort를 공유하고, 경계 직후 첫 도착이 대부분 바로 in-deadline이기 때문이다(0.05–2.39 s). 따라서 이 값으로 정책을 구별할 수 없다.
- 10 s 창 in-deadline 비율은 정책마다 다르다. 예: b1 burst B30에서 greedy_comm 0.23, task_fastest / no_network 0.88, 나머지 1.0.

## 7. 네트워크·timing·장치 증거

**timing**
- 36/36 valid
- 최대 총 불확실성 122.6 ms (허용 250 ms 이하)
- 최대 정렬 상한 113.9 ms

**netctl change**
- 성공 96 / 실패 0 (burst 6×12 + slow 2×12)
- 최대 적용 지연 0.76 ms, 최대 호출 8.9 ms
- 모든 cell에서 window `completed`
- final teardown: `emergency=false`, `torn_down`

**실제 qdisc 카운터** (owned gateway netns, 12 cell 합)
- 열화 phase에서 backlog와 drop이 관측된다
  - burst lossy: drop 1,240, 끝 backlog 최대 133,883 B
  - burst narrow: drop 1,739
  - burst outage: drop 1,322
  - slow 열화: drop 3,345, 끝 backlog 최대 149,258 B
- **good / stable 구간에도 tbf drop이 있다.** stable 12 cell 합 4,389건이다. 100 Mbps tbf 자체가 burst 시 drop하므로 "good"은 무손실이 아니다.
- 설정값은 실효 throughput이 아니다.

**장치 receipt** (전 ledger, 모든 receipt run_id = un-collect-r3)

| stage | 장치(receipt 수) |
|---|---|
| vision | openvino-npu NPU @ser0002 (1,585), cuda-torch cuda:0 @dev0009 (1,546), cpu @dev0003 (62), cpu @ser0002 (1) |
| speech | cuda @dev0009 (3,113), cpu @ser0002 (94), cpu @dev0003 (1) |
| fusion | cuda @dev0009 (4,976), cpu @ser0002 (1,412) |
| image_preprocess | cpu @dev0003 (2,166), cpu @ser0002 (1,097) |
| audio_preprocess | cpu @dev0003 (1,644), cpu @ser0002 (1,586) |

**model hash** (receipt 값 = plan `model_provenance` 기대값)
- vision `f37072fd…`, NPU derived ONNX `9be0337c…`(1,585/1,585)
- speech `8aa76ab2…`
- fusion `2674d468…`
- audio_preprocess `b225d617…`

**Pod UID** (receipt = cleanup의 생성 UID)
- dev0003 `c7b0910f…`
- dev0009 `a370e216…`
- ser0002 `f91dd2d9…`(cpu-c), `3794152b…`(npu-c)

**관측 poll 오류**
- burst cell당 3–23회, slow 0–7회, stable 0회
- stale fallback이 burst / slow에서 실제로 쓰였다(예: b1 burst dag_reopt 14회)
- 오류는 빈 큐로 해석하지 않았다

## 8. cleanup

- 생성: Pod 7(cache, cpu-c, npu-c, cpu-e0, jgpu-dev0009-cu130, gateway, generator) + ConfigMap 1
- 모두 UID precondition으로 deleted. unresolved_creation / preserved_not_ours / foreign_with_run_label / errors는 모두 비었다
- 엣지 노드 Pod 3개(cpu-e0, jgpu, generator)의 final logs는 설계대로 skip했다(EdgeCore stream 경계, kubectl 미호출)
- 수집 후 읽기 전용 확인(`live-r3/post-cleanup-check.txt`): `un-collect-r3` 객체 0
  - namespace에 남은 `diag-ec-fleet-20261022-*` Pod 5개와 ConfigMap은 5일 전 객체다. 이 run과 무관하므로 건드리지 않았다

## 9. 편차와 남은 제한

**shell wrapper 편차**: 몇 개 보조 shell 단계가 `rtk` 없이 실행되었다.
- 같은 bash 호출 안의 `mkdir -p`, `date -u` 시각 기록, `cat`/`tail`/`ls`/`wc` 읽기
- `orca orchestration check | python3` 1회
- 수집기·분석기·kubectl·lifecycle send는 모두 `rtk proxy`였다

**측정 범위의 한계**
- shaping 범위는 gateway Pod netns의 edge↔server 경로뿐이다. edge↔edge LAN과 물리 링크는 바뀌지 않았다. 실제 엣지 네트워크의 대표 분포라고 주장하지 않는다.
- netem 손실 실현은 packet 단위로 재현되지 않는다. 설정 수준만 동결했다.
- 2 block 탐색적 실험이다. 정책 순위의 block 간 변동이 크고 유의성은 없다. outage phase는 cell당 1건이다.
- 품질은 diagnostic이다(reused60 0.355 < 0.40). 품질 적격 비교나 독립 holdout이 아니다.
- 입력은 재사용 60쌍 파일이다. 실제 센서 획득이 아니다.

**해석상 주의**
- dag_static 결과는 엣지 단독 계획이 존재한다는 이 토폴로지의 성질에 의존한다. 다른 입력 크기나 토폴로지에서 같은 결과를 기대할 근거는 없다.
- readiness가 604 s 걸렸다. 수집에는 영향이 없었고 예산 안이다.
