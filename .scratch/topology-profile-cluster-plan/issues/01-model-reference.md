# 01 모델 자산·입력과 CPU reference 고정

Status: ready-for-agent

## 작업

JellyBean 고정 commit의 ResNet18 no-att, wav2vec2-base-960h, 대응 MUTAN checkpoint와 config/vocabulary/answer mapping/skipthought 자산을 확보한다. 실제 paired image/audio의 보정·평가 목록과 hash를 고정한다. CPU reference 후 amd64/arm64에서 task별 품질·RSS·준비/실행시간을 확인하고 dependency lock·호환 patch·이미지 digest를 기록한다.

## 완료 조건

모든 필수 자산 확보와 출처 확인, 숫자로 동결된 feature 허용오차, transcript/answer 기준, task×node 후보표. vision/speech 각각 최소2후보. 미확보 자산은 명시하며 임의 모델로 대체하지 않는다. synthetic 성공은 모델 성공에 포함하지 않는다.

## 근거

[공식 소스 검토](../../../docs/research/topology-profile-system-source-check.md), [계획 G1/G2](../../../docs/experiments/topology-profile-cluster-plan.md).

## Comments

2026-10-19 실행: 공식 자산 확보·재구성 vocab·CPU reference60/60 성공. 별도 작업트리 experiment/topology-profile-live에서 ARM6입력 후보 probe와 두 배포24workflow를 검증했다. 원 훈련 vocab의 독립 hash·ARM전체60입력 및6정책 본 비교는 미검증이다. [실행 결과](/home/jinuk/codex-work/scheduler-topology-profile-live/docs/experiments/topology-profile-smoke.md).
