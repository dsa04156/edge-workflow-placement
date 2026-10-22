# 개발자 보고서 — 합성 검증 synthetic-v1

작성일: 2026-10-21. 작성: Claude 구현 워커(Orca task `task_b8c656e84035`, dispatch `ctx_40f26e97b317`). Git HEAD 기준 `43f9d110db4b85bd487214910653efbde481da83`. 커밋·push 없음.

**확인 수준:** 코드 존재 + 관련 unittest 통과 + 합성 benchmark 1회 실행. 실제 클러스터 측정, 실제 종단 성능, 공식 수용은 해당 없음. 이 결과는 아이디어 기반 합성 baseline이며 신규성이나 성능 우월성을 입증하지 않는다.

## 1. 모델·인증 확인

- `claude auth status`: `loggedIn=true`, `authMethod="claude.ai"`, `apiProvider="firstParty"`, `subscriptionType="max"`. credential 값은 출력·기록하지 않았다.
- 환경 변수는 이름만 확인했다. `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `ANTHROPIC_MODEL`, `CLAUDE_CODE_USE_BEDROCK/VERTEX/FOUNDRY` 같은 API/provider override는 없다. 관련 없는 `S2_API_KEY`(Semantic Scholar)만 있다.
- 실제 모델: 현재 세션 transcript `~/.claude/projects/-home-jinuk-codex-work-scheduler/f96b1315-aa5a-4fab-b332-6ebf7140a3d6.jsonl`(`CLAUDE_CODE_SESSION_ID`와 일치)의 assistant `"model"` 필드는 46건 모두 `claude-opus-5-5`이고 다른 값은 없다(작업 중반 시점에 확인). 시스템 프롬프트의 모델 ID도 `claude-opus-5-5`이다. 모델 전환, API 우회, 추가 워커 생성은 하지 않았다.

## 2. 변경 파일 (모두 신규 파일, 기존 파일 수정 없음)

- `experiments/decision-focused-placement/`: `regret.py`, `p75.py`, `synthetic.py`, `fixtures.py`, `run_synthetic.py`, `test_regret.py`, `test_p75.py`, `test_synthetic.py`, `README.md`
- `artifacts/decision-focused-placement/synthetic-v1/results.json`: 최초 1회 실행분. sha256 `b3849eba2f77cfd61d6baff09f03b18de8514bb186d1e01b4bcdd476ccb5d2a6`.
- `.scratch/decision-focused-placement/developer-report.md`(이 문서)

`AGENTS.md`의 `M` 표시와 기타 untracked 문서는 작업 시작 전부터 있었고 건드리지 않았다. 실행으로 생긴 `__pycache__`는 내 디렉터리에서만 지웠다.

## 3. TDD 기록 (red → green)

모든 명령은 `rtk proxy python3 -m unittest discover -s experiments/decision-focused-placement -p 'test_*.py'`이다.

| slice | seam | red 증거 | green |
|---|---|---|---|
| 1 공유상쇄 regret | (1) | `ModuleNotFoundError: No module named 'regret'` (errors=1) | 3 tests OK |
| 2 경로전환·내부 최대·edge case | (1) | 1 failure `AssertionError: 'p' != 'q'` | 테스트 수정 후 14 OK |
| 3 순차 p75 | (2) | `ModuleNotFoundError: No module named 'p75'` | 21 OK |
| 4 정책 실행 JSON | (3) | `ModuleNotFoundError: No module named 'synthetic'` | 정책 테스트 통과, CLI 1 failure |
| 5 CLI | (3) | `can't open file .../run_synthetic.py` (failures=1) | 33 OK |

솔직한 편차:
- slice가 skill 권장보다 크다. 한 cycle에서 여러 테스트를 추가했다.
- slice 2의 11개 테스트 중 10개는 slice 1에서 이미 구현한 정확 LP 때문에 처음부터 통과했다. 이들은 엄밀한 red가 아니라 특성 확인이다.
- slice 2의 실패는 코드가 아니라 **내 기대값의 오류**였다. 경로전환 예에서 설계 규칙 `b=argmin L(·;u)`는 `L(p;u)=50 < L(q;u)=52` 때문에 p를 고르고 `R(p)=10`이다. ε=2에서는 `R(q)=2`인 q가 인증 가능하지만 추천 규칙이 q를 고르지 않는다. 테스트는 이 동작을 literal로 고정했다(`test_default_candidate_rule_picks_p_although_q_certifies`).

최종: `Ran 33 tests in 0.107s — OK` (`-v`, README 작성 후 재실행).

검증 범위: 설계 수학 예제의 literal(98/0/−2/2, 10/2), 내부 최대(1D −5, 2D −1, box 꼭짓점은 과소평가), invalid 입력, 빈 box, 무한 box, LP 조합 cap·deadline 초과 시 간단 상한 유지, 동일 배치, DKW literal(n=200: e=0.189014, band (113,188)), 무한 support, 검열, 빈 교집합, support 밖 표본, 정책별 첫 probe, round-robin, 예산·planner time 소진, fallback, 재현성, CLI의 덮어쓰기 거부. 참값은 평가자 계산과 테스트 oracle에만 쓰고 정책에는 공개하지 않는다.

## 4. Benchmark (최초 실행, 사후 변경 없음)

설정(`fixtures.py`, 결과 JSON의 `config`에 포함): ε=1, δ=0.05, batch 20, max_probes 150, planner 예산 10s/run, 초기 cost metadata 1.0, contrast round-robin 주기 5, LP 조합 cap 20000, seed 0–4, 전체 900s 상한. fixture 6개 × 정책 4개 × seed 5개 = 120 run, 생략 0. 전체 3.43s, run당 planner 최대 0.11s. 모든 run에서 `lp_complete=true`.

| fixture | 정책 | 인증률 | probe 평균 | sim 비용 평균 | 참 gap 최대 |
|---|---|---|---|---|---|
| shared_additive | round_robin | 1.0 | 40.8 | 102.0 | 0 |
| | width_cost | 1.0 | 55.2 | 159.6 | 0 |
| | witness | **0.0** | 150 | 591.0 | **3.0** |
| | contrast | 1.0 | **22.6** | **29.2** | 0 |
| critical_path_switch | round_robin | 0.2 | 144.2 | 144.2 | 0 |
| | width_cost | 1.0 | 101.6 | 101.6 | 0 |
| | witness | 1.0 | 101.6 | 101.6 | 0 |
| | contrast | 1.0 | 110.0 | 110.0 | 0 |
| near_tie | 전부 | 0.0 | 150 | 150 | 0 |
| unequal_probe_cost | round_robin | 0.6 | 145.2 | 800.4 | 0 |
| | width_cost | **0.0** | 150 | 159.0 | **5.0** |
| | witness | 0.4 | 146.2 | 796.0 | 0 |
| | contrast | **0.0** | 150 | 294.0 | 0 |
| no_advantage | 전부 | 1.0 | 16.0–16.4 | 16.0–16.4 | 0 |
| timeout_censoring | 전부 | 0.0 (censored_uncertified) | 1.4 | 1.4 | 0 |

감사: 평가자 기준으로 uncensored 최종 구간 204/204개가 참 p75를 포함했다(검열 arm 20개는 해당 없음). 5 seed 결과이므로 실패확률 δ를 검증한 것은 아니다.

### 경향과 음성 결과

- **이점이 보인 조건(shared_additive):** 공통 z가 상쇄되고 y만 결정에 필요하다. contrast는 z를 거의 probe하지 않아 simulated 비용 29.2로 round-robin 102.0보다 적었다.
- **witness 실패:** 같은 fixture에서 witness는 가장 넓은 z만 계속 probe해 예산을 모두 썼다. 5/5 미인증이었고, 추천 규칙이 q를 골라 참 gap 3 > ε였다.
- **contrast가 최선이 아닌 조건(critical_path_switch):** 경로전환 fixture에서는 width_cost/witness(101.6)보다 contrast(110.0)가 probe를 더 썼다. round-robin은 1/5만 인증했다. 이산 x의 p75 상한 band는 40에서 줄지 않으므로, 인증은 z의 하한을 올리는 데 의존한다.
- **비용 정규화의 기아(unequal_probe_cost):** 필요한 x의 probe가 10배 비싸면 width_cost와 contrast가 y만 probe한다. width_cost는 x를 1회만 probe했고, `b=argmin L(·;u)`가 잘못된 q를 골라 5/5에서 참 gap 5 > ε였다. contrast는 5번째마다 하는 round-robin 덕분에 x를 16회 probe해 올바른 p를 선택했지만, 상한 약 4.2로 미인증이었다. 비용이 큰 round-robin(800)과 witness(796)만 일부 인증했다. 즉 "싼 측정 우선"은 이 조건에서 인증 실패를 만들고, 1/5 round-robin으로는 예산 안에서 회복되지 않았다.
- **near_tie:** 참 차이 0.5 < ε이지만 DKW 구간이 충분히 좁아지지 않아 모든 정책이 미인증이었다. 참 gap은 0이지만 인증되지 않았다. 순차 구간의 측정 부담이 그대로 드러난 결과다.
- **no_advantage:** 대칭 조건에서는 네 정책이 사실상 같았다(16.0–16.4 probe).
- **timeout_censoring:** v0.1 규칙대로 검열 관측 즉시 미인증으로 종료한다. 검열 처리 구성이 없어 이 조건에서는 어떤 정책도 인증할 수 없다.
- **planner 시간:** 2-arm, 2-배치 fixture에서 run당 최대 0.11s다. 측정 비용 대비 계획 비용이 문제가 되는 규모는 이번 fixture로 검증하지 않았다.

해석 경계: 하나의 fixture에서 contrast가 이점을 보였고, 나머지에서는 동률이거나 열세이거나 실패였다. 이 결과는 "공유 비용 상쇄 + 결정에 무관한 넓은 항목" 조건의 시연일 뿐, 규칙의 일반적 우월성이 아니다. fixture는 실행 전에 고정했지만 설계자가 직접 만든 사례이므로 선택 편향 가능성이 있다. simulated 비용은 클러스터 절감이 아니다.

## 5. 설계에 대한 발견 (권고, 미구현)

1. `b=argmin L(·;u)`(비관적 상한 기준)는 인증 가능한 다른 배치를 놓칠 수 있다(경로전환 literal). 또 한쪽 구간만 좁혀지면 잘못된 배치를 고른다(unequal_probe_cost, shared_additive/witness). 후보 여러 개의 R 상한을 비교하는 방식은 다음 버전의 검토 대상이다.
2. 비용 정규화 규칙에는 필요한 고비용 항목의 기아를 막는 장치가 더 필요할 수 있다. 다만 주기를 바꾸려면 synthetic-v1이 아닌 새 버전으로 사전 등록해야 한다.
3. DKW 순차 구간은 연속 분포에서도 n≈104 이전에는 상한이 support에 머문다(d=1, δ=.05). 이산 분포의 jump에서는 상한이 줄지 않는다.

## 6. 남은 일·제한

- 검열 처리 confidence 구성, 3개 이상 배치/큰 F에서의 LP 비용, 간섭·모형 편향 fixture, 기존 interval-split minimax 규칙 비교는 구현하지 않았다.
- 독립 Codex 검토는 아직 수행되지 않았다.
