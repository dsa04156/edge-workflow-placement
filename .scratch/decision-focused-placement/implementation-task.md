# 합성 검증 구현 작업

## 목표

설계 v0.1을 작은 합성 문제에서 실행하고, 측정 선택의 이점과 불리한 조건을 재현한다. 연구 성능·신규성 입증으로 표현하지 않는다.

## 범위와 소유권

작업 디렉터리 `/home/jinuk/codex-work/scheduler`. Developer는 새 `experiments/decision-focused-placement/` 디렉터리의 Python 모듈, unittest 테스트, README만 소유한다. 결과는 새 `artifacts/decision-focused-placement/synthetic-v1/`에 저장한다. 구현·테스트 보고서는 `.scratch/decision-focused-placement/developer-report.md`에 작성한다. 다른 작업자도 있으므로 기존 변경을 되돌리지 않는다. 기존 runner, AGENTS.md, 동결 artifact, 연구 설계 문서를 수정하지 않는다.

## 설계 결정

- 원본: `docs/research/decision-focused-placement-design-20261021.md`. 연구 메모들도 읽을 수 있다.
- 이번에는 Python 표준 라이브러리만 사용한다. SciPy/NumPy/pytest 설치는 하지 않는다. 검증용 작은 유한 box, 명시적 feasible 배치 및 경로를 입력받는다. runtime 검증은 외부에서 동결했다는 계약이며 합성으로 실제 검증을 주장하지 않는다.
- LP는 저차원(최대 2 primitive인 fixture, 정식 dimension cap 문서화)에서 Fraction 유리수 vertex enumeration으로 독립적 정확 검증을 구현할 수 있다. 변수 θ와 z, box 제약과 z≤path-difference 제약. 활성 제약 조합의 정확 선형 시스템을 풀고 모든 제약 만족 여부를 검사한다. maximization primal을 일반적으로 upper로 쓰지 말 것: 전체 vertex 열거 완료인 bounded/nonempty LP에 한해서 exact maximum이라고 한다. 제한·실패·중단이면 conservative box upper로 반환한다. 최종 모든 challenger 검증 필수.
- 공유상쇄 예: p=z+10,q=z+12,z∈[0,100], R(p)=0. 경로전환 예: p=max(z+10,30),q=max(z+12,20),z∈[0,40], D(p,q)=10,D(q,p)=2. q의 maximum difference는 구간 내부에서도 생길 수 있으므로 단순 box vertex 목적 평가만으로 LP 대체 금지.
- public seams: (1) 비용모형+전체 regret 결과, (2) 표본→순차 p75 구간, (3) 정책 실행→선택/인증/비용/종료 JSON. 사용자 승인한 spec 수용 기준의 관측 경계다. 내부 함수 호출 횟수나 구현을 그대로 복제한 oracle 테스트는 하지 않는다.
- p75 구간은 DKW error spending 및 generalized inverse, 알려진 support(합성 fixture에는 합법적으로 지정 가능)를 사용한다. 초기 무한 구간·빈 교집합·검열·예산 소진은 미인증. 실수 DKW 통계 계산과 exact rational optimizer의 인증 범위를 구별한다. censored 입력은 이 버전에서는 인증 false. 점 비용과 신뢰구간을 혼동하지 않는다.
- acquisition은 uniform round robin, width/cost, incumbent-witness widest interval, contrast-width/cost 네 가지. witness 규칙은 incumbent/competitor에 등장하는 항목 중 widest interval(공유상쇄를 임의 추가하지 않음). 모든 정책 동일 전체-regret stop. contrast는 midpoint의 1-coordinate 경로전환을 반영하고 매5번째 round-robin, allzero시 round-robin. 초기무한은 round-robin. probe cost는 과거 관측 또는 명시된 동일 초기 cost metadata만 사용; 참 θ와 미래 sample은 정책에 공개하지 않는다.
- per-arm seeded independent sample streams를 정책들이 공유하되 각 정책이 요청한 표본만 공개. fixture의 distribution/support/true population p75는 평가자에게만 제공. θ가 알려진 bounded uniform 또는 명시된 discrete distribution으로 구성. deterministic known-interval 검증과 noisy DKW end-to-end를 분리해 보고.
- 최소 fixture: shared additive uncertainty, critical-path switch, near tie, unequal probe cost, no advantage case. 최소 5 seed의 작은 smoke 비교를 실행. 대규모 반복/과도한 LP 금지. 인증률·참 proxy gap·probe count·simulated probe cost·실제 planner wall time·remaining upper·종료이유를 JSON 기록. simulated probe 시간은 실제 클러스터 절감으로 부르지 않는다.
- budget은 probe count 및 planner wall time 경계를 명시. LP 내부 큰 조합 탐색도 정해진 한도로 중단 가능해야 한다. unfinished LP는 안전 상한 유지. 총 run 시간을 합리적으로 제한한다.
- 순차구간이 느려 인증이 안 되는 결과도 그대로 보고한다. 성능이 좋아 보이도록 예산/fixture를 사후 변경하지 않는다. 최초 정한 benchmark config를 결과에 포함한다.

## 수용 기준과 검증 명령

- 명령: `rtk proxy python3 -m unittest discover -s experiments/decision-focused-placement -p 'test_*.py' -v`.
- 실행 CLI는 `rtk proxy python3 experiments/decision-focused-placement/run_synthetic.py --output artifacts/decision-focused-placement/synthetic-v1`로 만든다. 기존 output에 덮어쓰지 말고 최초 실행만 저장, 추가 검증은 tempfile 사용.
- TDD 스킬 `/home/jinuk/.agents/skills/tdd/SKILL.md`의 red→green을 한 기능씩 적용하고 실패·통과 증거를 report에 기록. 수용 기준/공개 경계는 위에 확정되어 있다.
- 수학 예제의 literal 정답, invalid/censored/empty/unbounded/budget/identical-plan 동작, JSON 결과를 테스트한다. 알려진 참값은 평가와 oracle에만 사용한다.
- 정확 solver의 dimension/vertex cap, feasibility 전제, 통계 가정, 구현상 보장 범위를 README에 적는다. 원문 재현이 아니라 아이디어 기반 baseline이다.
- worker 실행 모델 `claude-opus-5-5`, 인증 `claude.ai`+`firstParty`를 확인. 환경 변수 이름만 확인하고 API/provider override가 있으면 작업을 멈추고 보고. 모델 전환이나 API 우회 금지.
- 최종 report에 변경 파일, 테스트 결과, benchmark 경향/음성 결과, 모델/인증 확인 근거를 제공하고 Orca injected preamble의 worker_done으로 완료한다.

## 금지

커밋·push·merge·배포·클러스터 접근·의존성 설치·기존 artifact 덮어쓰기·다른 모델로 대체·추가 구현 워커 생성 금지. 셸 명령은 rtk 또는 rtk proxy로 실행한다.
