# 독립 검토 1차 수정 보고

2026-10-21. 계약: `fix-task.md`. 입력: `implementation-task.md`, `review-spec.md`(4건), `review-standards.md`(1건).
레벨: **코드 존재 + 관련 테스트 통과 + 합성 benchmark 재실행**. 클러스터·실측·공식 수용 아님.

## 모델·인증 확인

- `claude auth status`: `authMethod=claude.ai`, `apiProvider=firstParty`, `subscriptionType=max`.
- `ANTHROPIC_*` / `CLAUDE_CODE_USE_*` 환경 변수 이름 0개(API key·base URL·Bedrock/Vertex override 없음).
- 이 세션 transcript(`~/.claude/projects/-home-jinuk-codex-work-scheduler/f85f89b3-….jsonl`)의 assistant `model` 필드 75개 모두 `claude-opus-5-5`(보고 작성 직전 집계).

## 절차 편차 (사실 기록)

초기 몇 개 셸 호출(작업 문서 `cat`, 인증 확인, 원본 소스 사본 저장, 최초 test_p75 red 실행 전의 heredoc 편집 2회 — test_p75.py 추가와 p75.py 수정, regret.py cap 수정)을 `rtk proxy` 없이 `cd … && python3`로 실행했다. Coordinator 지적(`msg_bd621a315026`) 이후 모든 셸 호출을 `rtk proxy`로 감쌌고 파일 편집은 Edit/Write 또는 `rtk proxy python3` heredoc로 했다. 편차 호출은 읽기 또는 소유 파일 편집뿐이었다.

## 변경 파일 (소유 범위만)

- `experiments/decision-focused-placement/p75.py`, `regret.py`, `synthetic.py`, `run_synthetic.py`, `README.md`
- `experiments/decision-focused-placement/test_p75.py`, `test_regret.py`, `test_synthetic.py`
- 신규 결과 `artifacts/decision-focused-placement/synthetic-v1-reviewfix/results.json`
- 이 보고서
- `fixtures.py`는 변경 없음(sha256 `e710c25d…ad21`, 원본과 동일). AGENTS.md·docs/agents/domain.md의 기존 수정은 다른 작업자 것이며 건드리지 않았다.

## 결함별 수정 (TDD red → green)

기준선: 33 tests OK. 최종: **50 tests OK** (`rtk proxy python3 -m unittest discover -s experiments/decision-focused-placement -p 'test_*.py' -v`, 0.15초).

1. **p75 입력 검증** (review-spec #1). Red: `P75InvalidInput` 4개 FAIL. 수정: `SequentialP75.__init__`이 support 하한 유한, 상한 유한/`None`/`+inf`만 허용(NaN·−∞ 거부), `lo>hi` 빈 support 오류, `d`는 bool 아닌 정수 ≥1, `δ∈(0,1)`(NaN 거부). `add()`는 비유한·비수치 비검열 표본과 support 밖 표본을 **n 증가 전에** 거부. 표본 누적 후의 빈 교집합은 기존대로 `empty`(미인증). 테스트는 `sequential_p75`/`SequentialP75` 공개 결과만 검사. Green: 13/13.
2. **전체 run planner 예산** (review-spec #2). 먼저 `run_policy(clock=…)`·`total_regret(clock=…)` 측정 경계 주입을 동작 보존 리팩터로 추가(45 green 유지). Red: 3개(0 예산 one-plan 인증, regret 계산 중 소진 후 인증, 소진 후 유료 probe 기록 부재). 첫 버전의 "계산 중 소진" 테스트는 LP deadline abort로 우연히 통과해 잘못된 근거였으므로, simple 상한만으로 인증되는 fixture(p=x, q=y+50)로 바꿔 진짜 red를 확인했다. 수정: checkpoint (1) 시작 전 예산 ≤0 → `budget_planner_time`(regret 미계산), (2) 구간·regret 계산 후 **인증 판정 전**, (3) acquisition 후 **유료 probe 전**. 판정 순서 빈 구간→검열→planner 예산→인증→probe 예산. 테스트는 호출마다 1초 진행하는 `StepClock`만 쓰고 정책 private 함수는 mock하지 않았다. 각 probe 기록의 `planner_time_used_before_s < budget`를 40개 예산 값에서 검사. CPU 작업 자체는 LP deadline(64 조합마다) 외에는 선점하지 않아 마지막 계산 1회만큼 초과 가능함을 README에 명시.
3. **무한 구간 인증 gate** (review-spec #3). Red: one-plan·사용 primitive 무한 support가 `certified`; 그리고 **추가 발견** — 어떤 배치도 쓰지 않는 primitive가 무한이면 regret LP가 `simple_unbounded_box`로 떨어져 SHARED 상쇄 fixture가 150 probe 후 미인증. 수정: runner는 `reg.certified`이고 **사용 primitive에 `infinite`가 없을 때만** 인증. `pairwise_regret_upper`는 p·q 모두 계수 0인 좌표의 무한 상한을 하한에 고정(LP 값 불변)하고 LP를 수행. 하위 solver의 one-plan zero regret(무한 box 포함)은 그대로 타당함을 `test_regret`에 명시. 추가 테스트: one-plan 무한→`budget_probes` 미인증, one-plan 유한→0 probe 인증, 불필요 무한 primitive는 4개 정책 모두 0 probe 인증, 불필요 무한 좌표에서 `lp_exact` −2.
4. **원시 기록·JSON 계약·source hash** (review-spec #4). Red: 5 errors. 수정: `probe_records[]`에 `index/arm/rule/batch`(관측값, `null`=timeout 검열)/`censored_positions`/`simulated_cost`/`cost_kind="simulated"`/`planner_time_used_before_s`. 비유한 수치는 `null` + 형제 `*_status`(`finite|infinite|not_computed`): `regret_upper(_status)`, `arms[].hi(_status)`, 결정적 검사 `upper(_status)`, summary `regret_upper_median(_status)` 및 `regret_upper_not_computed_runs`. CLI는 `allow_nan=False`로 쓰고 `json_contract`, `source_files`(디렉터리 `.py` 8개 sha256)를 결과에 포함. 기존 `claim_boundary` 유지. CLI 테스트는 `parse_constant`로 Infinity/NaN token을 실패 처리하고 hash를 재계산해 대조.
5. **LP 조합 cap 쌍 단위** (review-standards). Red: review 예제 cap=3에서 `lp_exact`. 수정: 쌍 전체 `|Π_b|·C(2d+|Π_q|, d+1)`를 사전 계산해 cap 초과면 LP를 시작하지 않고 `simple_lp_combination_cap`; 경로별 검사는 제거. 테스트: cap=3 → simple 9, cap=6 → `lp_exact` 9.

## 검증

- tempfile CLI smoke: `--seeds 2 --max-probes 10` exit 0, 재실행은 덮어쓰기 거부 exit 2 (scratchpad, 정리 대상).
- 정식 재실행: `rtk proxy python3 experiments/decision-focused-placement/run_synthetic.py --output artifacts/decision-focused-placement/synthetic-v1-reviewfix` → 120/120 run, skipped 0, 총 3.52초. sha256 `472a5ade21d4d0a58677e2f2edaf8aedecb08f01ce7b3c053c3c3ded5180e1c2` (7.7MB, probe 기록 9,941개).
- 최초 결과 보존: `synthetic-v1/results.json` sha256 `b3849eba2f77cfd61d6baff09f03b18de8514bb186d1e01b4bcdd476ccb5d2a6` 수정 전후 동일.
- config·fixture·ε·δ·예산·seed 변경 없음.

## benchmark 비교 (최초 vs reviewfix)

새 필드(`probe_records`, `*_status`, `regret_upper_not_computed_runs`, `json_contract`, `source_files`)와 planner 시간을 제외하고 120개 run 전 필드(selected, basis, certified, termination, regret_upper, lp_complete, true_proxy_gap, probe_count/samples, simulated_probe_cost, arms, probe_sequence/rules), 결정적 검사, summary 비시간 지표를 대조: **차이 0건**. 모든 run의 `regret_upper_status=finite`. 추가로 각 run의 probe 기록 합계가 sequence·rules·simulated cost·arm n/검열 수와 일치함을 확인.

차이가 없는 이유(예상대로): 정식 fixture는 모두 유한 support라 #3 gate와 불필요 좌표 처리가 발동하지 않고, 유효 표본만 있어 #1이 무관하며, planner 예산 10초 대비 run당 수 ms라 #2 checkpoint가 발동하지 않고, d≤2·경로≤2라 쌍 조합 수 ≤40으로 #5 cap(20000)에 걸리지 않는다. 즉 이 benchmark 결과의 경향(최초 developer-report의 인증률·음성 결과)은 그대로이며, 수정은 경계 입력의 계약 위반을 막는 것이다.

## 남은 제한

- planner 예산은 checkpoint 방식: regret/LP 한 번의 CPU 계산은 선점하지 않으며(LP deadline 64 조합마다 제외) 예산을 그만큼 넘길 수 있다.
- p75 DKW 계산은 여전히 float(`INDEX_TOL`)이며 exact rational 보장은 optimizer에 한정.
- 검열 표본은 v0.1처럼 즉시 미인증. 결과 JSON이 7.7MB로 커졌다(raw batch 보존 대가).
- 독립 재검토는 받지 않았다. 커밋·push·클러스터 실행 없음. 합성 proxy 검증일 뿐 실제 클러스터 절감·신규성 주장 아님.
