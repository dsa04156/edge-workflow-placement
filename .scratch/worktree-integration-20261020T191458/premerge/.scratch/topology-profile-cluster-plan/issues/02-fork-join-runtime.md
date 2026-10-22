# 02 고정 엣지 source와 직접 전송 fork/join 실행기

Status: ready-for-agent
Blocked by: 01 (실제 모델 통합; 합성 프로토콜 검증은 선행 가능)

## 작업

source agent, stage worker, fusion join table과 bounded queue를 구현한다. E0 단일 monotonic E2E, E1 로컬 음성 발생, stage→fusion 직접 전송, 3stage receipt·payload hash·Pod UID, 오류/timeout 보존을 구현한다. 기존 phase_capture·연결 회귀 패턴만 재사용한다.

## 완료 조건

중복/역순/누락/늦은 branch·실패·timeout 회귀, 정상 attempt 1회 합류, 실제 source/전송 검증, CPU reference와 분산 출력 대조. 모델 통합 완료 전에는 protocol-only로 표시한다.
