# 배치 결정을 위한 선택적 프로파일링 — 합성 검증 (synthetic-v1)

설계 원본: [`docs/research/decision-focused-placement-design-20261021.md`](../../docs/research/decision-focused-placement-design-20261021.md) v0.1.
이 디렉터리는 그 설계의 **아이디어 기반 합성 baseline**이다. Wang–Boutilier/Boutilier 등 원문 구현의 재현이 아니며, 클러스터 측정·실제 종단 지연·신규성·성능 우월성을 주장하지 않는다. 의존성은 Python 3 표준 라이브러리뿐이다.

## 파일과 공개 경계

| 파일 | 역할 | 공개 seam |
|---|---|---|
| `regret.py` | 비용모형 `L(p;θ)=max_r(a·θ+b)`, pairwise/전체 regret 상한, Fraction 정확 LP | (1) 비용모형+전체 regret 결과 |
| `p75.py` | DKW error spending 순차 p75 구간 | (2) 표본→순차 p75 구간 |
| `synthetic.py` | arm별 seeded stream, 4개 acquisition 규칙, 공통 종료 runner | (3) 정책 실행→선택/인증/비용/종료 JSON |
| `fixtures.py` | 최초 실행 전에 고정한 `synthetic-v1` 설정·fixture | — |
| `run_synthetic.py` | benchmark CLI. 기존 output이 있으면 거부(exit 2) | (3) |
| `test_*.py` | 세 seam에 대한 unittest | — |

```bash
rtk proxy python3 -m unittest discover -s experiments/decision-focused-placement -p 'test_*.py' -v
rtk proxy python3 experiments/decision-focused-placement/run_synthetic.py --output artifacts/decision-focused-placement/synthetic-v1
# 독립 검토 1차 수정 후 같은 config·fixture 재실행 (최초 synthetic-v1 은 보존)
rtk proxy python3 experiments/decision-focused-placement/run_synthetic.py --output artifacts/decision-focused-placement/synthetic-v1-reviewfix
# 2차 검토 P2(미사용 무한 primitive 의 contrast crash) 수정 후 최종 소스로 재실행 (앞의 두 디렉터리 보존)
rtk proxy python3 experiments/decision-focused-placement/run_synthetic.py --output artifacts/decision-focused-placement/synthetic-v1-final
```

`--seeds N`, `--max-probes K`는 tempfile 검증용 override이며 결과 JSON의 `overrides`에 기록된다.

## 정확 solver의 범위 (`regret.py`)

- 입력 전제: 실행 가능 배치 집합 F와 각 배치의 경로 계수는 **외부에서 동결·검증된 것**으로 받는다. 이 모듈은 실행 가능성을 검사하지 않고 비용만 다룬다. 계수 `a≥0`, 같은 차원, 중복 ID 금지, 빈 F 금지를 검사한다.
- 간단 상한: `L(b;u)−L(q;l)`. `a≥0`이므로 box 전체에서 유효하다. 무한 상한 성분이 쓰이면 `+∞`.
- 정확 LP: 고정 (p,q)와 p의 경로 r마다 `max z s.t. z ≤ (a_pr−a_qs)·θ + b_pr−b_qs ∀s, θ∈box`. 변수 (θ,z)의 d+1개 활성 제약 조합을 모두 열거하고, Fraction Gauss–Jordan으로 풀어 모든 제약 만족 여부를 정확히 검사한다. D(p,q)는 r별 최댓값이다.
- **왜 vertex 열거로 정확한가:** 실행 가능 영역은 z 방향 아래로 unbounded이지만, θ가 유한 box(compact)에 있으므로 line을 포함하지 않는(pointed) 다면체이고 목적 z는 `max_θ min_s f_s(θ)`로 위에서 유계이다. 이 두 조건에서 LP 최적값은 vertex에서 달성된다. 이 근거(θ compactness)와 "모든 조합을 끝까지 열거했다"는 구현 조건을 **둘 다** 만족할 때만 `lp_exact`로 기록한다. 최적점이 box 내부(경로 교차)에 있을 수 있으므로 box 꼭짓점 목적 평가로 대체하지 않는다(`test_regret.InteriorMaximum`).
- 상한 cap: `LP_DIMENSION_CAP=3` primitive (fixture는 최대 2), 한 쌍(b의 모든 경로 r 합계)의 활성 조합 수 `|Π_b|·C(2d+|Π_q|, d+1) ≤ lp_combination_cap`(기본 20000; 초과면 LP를 시작하지 않고 `simple_lp_combination_cap`), planner deadline(64 조합마다 확인). 한도 초과·deadline·vertex 미발견·무한 box면 해당 쌍은 **간단 상한을 유지**하고 (b·q 어느 쪽도 쓰지 않는 좌표의 무한 상한은 계수 0이므로 하한에 고정해 LP에서 제외) `lp_complete=false`. maximization의 feasible primal 값을 상한으로 쓰지 않는다.
- 전체 regret: 추천 후보 `b=argmin L(·;u)`(동률은 입력 ID 순서), 모든 q에 대해 간단 상한 → ε 초과 쌍만 LP 정밀화. q=b는 정확히 0. 빈 box는 `empty_uncertainty_set`, 무한 상한은 `unbounded`로 미인증.
- 출력 float 변환은 상한을 바깥(위) 방향으로 반올림한다. 내부 계산은 정확 유리수이다.

## 통계 구성의 범위 (`p75.py`)

- 전제: primitive별 i.i.d. 표본, 고정 환경, **알려진 support**(합성 fixture에서는 지정 가능). 시간 상관·drift·간섭은 다루지 않는다.
- n마다 `α_n=δ/(d·n·(n+1))`, `e_n=sqrt(log(2/α_n)/(2n))`, band `[Q̂(.75−e_n), Q̂(.75+e_n)]`; Q̂는 generalized inverse(⌈t·n⌉번째 순서통계량). 범위 밖 인수는 support 경계(상한 미지정이면 +∞). 모든 n의 band를 교집합한다.
- `e_n`, `⌈t·n⌉`은 **float 계산**이고 `INDEX_TOL=1e-9`만큼 넓히는 방향으로 처리한다. 이 단계는 exact rational 보장이 아니며, 정확 보장은 구간이 주어진 뒤의 optimizer에만 해당한다.
- 상태: `ok`, `infinite`(미인증·round-robin), `empty`(빈 교집합, 검증 실패), `censored`(검열 표본이 하나라도 있으면 v0.1에서는 certifiable=false, 구간은 support로 되돌림). support 밖 표본은 오류.
- 입력 검증(오류 `ValueError`, 상태를 바꾸기 전에 거부): support 하한은 유한, 상한은 유한·`None`·`+inf`만(NaN/−∞ 금지), `lo>hi`인 빈 support, 정수가 아니거나 1 미만인 `d`, `(0,1)` 밖 또는 NaN `δ`, 비유한(NaN/±∞)·비수치 비검열 표본. 거부된 표본은 `n`을 올리지 않는다. 표본 누적 후의 빈 교집합은 오류가 아니라 `empty`(미인증)이다.
- 관측 최댓값을 support 상한으로 쓰지 않는다. 점 비용(경험 p75)은 무한 구간 단계의 예산 소진 시 `point_estimate` 선택에만 쓰며 인증에 쓰지 않는다.

## 측정 선택 규칙과 공통 종료 (`synthetic.py`)

- 네 규칙 모두 같은 데이터 접근(요청한 arm의 다음 batch만 공개)·같은 전체-regret 종료·같은 예산을 쓴다. 참 분포·참 p75·참 probe 비용은 정책에 공개되지 않고, 비용 추정은 과거 관측 평균 또는 모든 arm에 동일한 `initial_cost_metadata`이다.
  - `round_robin`: 사용되는 arm을 ID 순서로 순환.
  - `width_cost`: `(u_j−l_j)/ĉ_j`.
  - `witness`: b와 최대 pairwise 상한 경쟁자 q에 등장하는 arm 중 가장 넓은 구간(비용 미반영, 공통 항목을 따로 제거하지 않음).
  - `contrast`: midpoint에서 1-좌표 차이 `g_j=L(b)−L(q)`의 변동폭 `W_j`(양 끝점과 b·q 각 배치 내부 경로 교차점에서 평가) / ĉ_j. 매 5번째 probe와 모든 점수 0일 때 round-robin. **휴리스틱**이며 EVOI가 아니다. b·q 어느 쪽도 쓰지 않는 좌표(세 번째 배치만 쓰는 경우 포함)는 계수 0이므로 하한(유한 상수)에 고정해 midpoint 산술에서 제외하고 `W_j=0`이다(상한이 +∞여도 crash 없음).
  - 공통: 사용 arm 중 무한 구간이 있으면 round-robin.
- 인증 gate: 전체 regret 상한 ≤ ε **이고** 사용되는 primitive에 `infinite` 구간이 없어야 `certified`. 하위 solver의 one-plan regret 0(무한 box 포함)은 수학적으로 타당하지만 runner는 인증하지 않고 probe를 계속하거나 예산 종료한다. 어떤 배치도 쓰지 않는 primitive의 무한 구간은 gate에서 제외한다.
- planner 예산 checkpoint: (1) 시작 전 예산 ≤0이면 regret 계산 없이 `budget_planner_time`, (2) 구간·regret 계산 후 **인증 판정 전**, (3) acquisition 후 **유료 probe 실행 전**. 소진되면 probe를 실행하지 않는다. 계산 중 CPU 작업 자체는 LP deadline(64 조합마다) 외에는 선점하지 않으므로 마지막 계산 한 번만큼 예산을 넘길 수 있다(`planner_wall_time_s ≥ 예산`으로 기록). `run_policy(clock=...)`는 이 측정 경계를 결정적 시계로 바꾸는 테스트용 seam이다.
- 종료: `certified`, `budget_probes`, `budget_planner_time`, `empty_interval`(선택 없음), `censored_uncertified`(검열 관측 즉시 종료). 판정 순서: 빈 구간 → 검열 → planner 예산 → 인증 → probe 예산.
- 결과 기록: `probe_records[]`에 probe별 `index/arm/rule/batch`(관측값, `null`=timeout 검열)/`censored_positions`/`simulated_cost`/`cost_kind="simulated"`/`planner_time_used_before_s`를 보존한다.
- JSON 계약: 결과는 `allow_nan=False`로 쓴다(Infinity/NaN token 없음). 비유한 상한은 `null`과 형제 `*_status`(`finite|infinite|not_computed`)로 표현한다: `regret_upper(_status)`, `arms[].hi(_status)`, 결정적 검사의 `upper(_status)`, summary의 `regret_upper_median(_status)`. CLI 결과에는 `source_files`(이 디렉터리 `.py` sha256)와 `json_contract`를 포함한다.
- 선택: regret 상한이 유한하면 `b`; 무한이면 관측이 있을 때 경험 p75 점 추정, 없으면 fixture의 사전 지정 fallback.
- 표본 stream은 `Random(f"{seed}/{fixture}/{arm}")`로 정책들이 공유한다. probe 비용은 fixture에 지정한 **simulated 값**이고, `planner_wall_time_s`만 실제로 측정한 계획 시간이다(표본 생성 제외). simulated probe 비용 절감을 클러스터 측정 절감으로 부르지 않는다.

## 보장하지 않는 것

proxy 인증(`proxy_certified`)은 primitive p75 조합 최장경로 비용모형 안의 선택 손실 상한이며, 실제 종단 지연의 평균/p75 인증이 아니다. 추천 규칙 `b=argmin L(·;u)`는 minimax-regret 배치가 아니므로 다른 배치가 인증 가능해도 인증하지 못할 수 있다(`test_regret.CriticalPathSwitch`). 순환 측정도 유한 예산 내 인증을 보장하지 않는다. 결과 해석은 `.scratch/decision-focused-placement/developer-report.md`.
