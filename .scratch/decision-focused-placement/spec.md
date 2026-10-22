# 선택적 프로파일링 연구 명세

상태: 설계 v0.1 및 첫 합성 구현·검증 완료. 52개 테스트 통과, 120개 합성 실행. 실측·신규성 입증 없음. [결과 보고](../../docs/research/decision-focused-placement-synthetic-20261021.md).

단일 설계 원본: [배치 결정을 위한 선택적 프로파일링](../../docs/research/decision-focused-placement-design-20261021.md).

## 구현 범위

기존 클러스터와 동결 artifact를 변경하지 않는 `experiments/decision-focused-placement/` 합성 검증 모듈. 실제 실행 명령은 해당 디렉터리 README에 기록했다. 후속 알고리즘 변경은 이 실험의 음성 결과를 바탕으로 별도 설계한다.

## 수용 기준

- 유한 배치·경로 입력에서 feasibility와 proxy 비용을 분리한다.
- 공유 파라미터 pairwise regret LP를 독립적인 작은 사례와 대조한다. maximization 하한을 인증에 쓰지 않는다.
- 공통 가산 비용 상쇄, 경로 전환, 빈 구간, 무한 구간, timeout, 예산 소진을 처리한다.
- 모든 경쟁 배치를 검증하고 미완료 계산의 유효 상한을 유지한다.
- uniform, width/cost, witness-gap, 제안 contrast-width 규칙을 같은 데이터 접근·종료 조건으로 비교한다.
- 측정·계획 시간과 proxy 인증을 기록하고 실제 종단 보장으로 표시하지 않는다.
- 구현 및 테스트는 지정 Claude 워커, 독립 검토는 지정 Codex 워커가 담당한다. Orca 가이드·모델·인증 기능 확인이 선행되어야 한다.

## 미해결 연구 질문

Wj 기반 측정 순서가 기존 witness 규칙보다 나은 조건, 순차 p75 구간의 측정 부담, 공유 비용 가정의 실측 타당성, 모형 오차가 실제 배치 순위에 미치는 영향. 이 질문이 해결되기 전 신규성 또는 성능 우월성을 확정하지 않는다.
