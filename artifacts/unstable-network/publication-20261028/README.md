# 2026-10-27–28 불안정 네트워크 실험 공개 기록

## 실제 수집 결과

- 동결 계획: `artifacts/unstable-network/plan-20261027T111532Z`; 실행: `un-collect-r3`.
- CPU / Jetson GPU / Intel NPU의 실제 5-task DAG, 6개 정책 × 3개 네트워크 trace × 2개 block = 36개 cell.
- 전체 유입 3,462건: 기한 내 완료 2,929건, 늦은 완료 265건, timeout 81건, admission drop 187건. 누락된 terminal 행 없음.
- `dag_static`은 고정 all-edge 배치로 577/577건을 2초 안에 완료했다. 이 배치의 task 데이터 경로에는 제약을 준 edge-server WAN이 포함되지 않는다.
- 조건: 1.6 rps, cell당 60초, deadline 2초, cancel 10초, max_inflight 8. 2개 탐색적 block이며 통계적 우월성이나 포화 처리 용량을 입증하지 않는다.
- 당시 실험의 품질 상태는 diagnostic이었다. 이후 품질 최적화를 제외한 연구 방향과 과거 실측 조건을 구분한다.

## 후속 정책의 검증 상태

`dag_deadline_guard`는 동결 anchor 배치의 예측 지연이 deadline의 75% 이내면 유지하고, 초과하면 기존 DAG 재최적화를 호출하는 독립 프로토타입이다. 실행 가능성만 후보 조건으로 사용한다.

- 원래 3,462개 관측 중 결정 재구성 가능 2,680건, service_ms 기록 부족 595건, snapshot 없음 187건.
- 재구성된 결정 중 anchor 유지 2,662건, 이동 18건.
- 이것은 기존 관측에서의 결정 감사다. 새 정책을 실행한 성능·goodput·성공률 결과가 아니다.
- 독립 R2 리뷰 Spec/Standards PASS, 39개 테스트 통과. 실제 generator 연결과 새 클러스터 성능 비교는 아직 실행하지 않았다.

## 포함 범위와 재현

실험 코드·테스트·문서, 동결 JSON/CSV 계획, 요청별 ledger, 수집·네트워크 제어·정리 기록, 분석 결과, 두 버전의 후속 정책 감사 결과 및 선택한 검증 보고서를 포함한다. `evidence.json`은 공개 준비 당시 원본 산출물의 경로·크기·SHA-256과 포함 여부를 기록한다.

다운로드 가능한 `jellybean.tar`, 원시 Pod/HTTP stdout·stderr, 인터프리터 cache와 임시 리뷰 작업 디렉터리는 원래 위치에 보존하고 이 커밋에서 제외했다. 일부 과거 문서의 링크는 이러한 로컬 자료를 가리킬 수 있다. 실제 수집 재현에는 동결 계획에 명시된 클러스터와 별도 모델·입력 자산이 필요하다.

- [실측 조건과 결과](../../../docs/experiments/unstable-network-20261027.md)
- [후속 정책과 관측 감사](../../../docs/experiments/deadline-network-20261028.md)
- [실측 분석 JSON](../un-collect-r3-analysis.json)
- [최종 감사 JSON](../../deadline-network/audit-un-collect-r3-20261028T020451Z/audit.json)

공개 준비 중 기존 실험 소스 18개 SHA-256이 보존 기록과 일치함을 확인했다. 클러스터 실험을 다시 실행하지 않았다.
