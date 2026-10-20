# greedy-swap-move 오프라인 사전 등록 기준선
Status: ready-for-agent

Question: 서버 슬롯2 동결 비용표에서 사후 진단으로만 확인한 "greedy 한 번 교환 후 최대 한 작업 이동"을
새 실측 전에 고정된 결정 규칙·코드·hash로 사전 등록할 수 있는가? 성능 주장이 아니라 다음 실측의 비교 대상 고정이다.

## 결정 규칙 (동결)
1. greedy = 기존 `placement_policies.sequential(profile)`. 같은 profile에서 기존 `select_budget_policies`의 greedy와 같아야 한다.
2. 교환 = 기존 `budget_placement.simple_alternatives`의 greedy-one-pair-swap과 같은 규칙. greedy 키 순서의 모든 작업 쌍
   1회 교환, `validate_placement` 통과 후보와 greedy 중 `(topology_profile 비용, sorted(placement.items()))` 최소.
   기존 greedy-swap 배치와 다르면 실패한다.
3. 이동 = 교환 결과에서 작업 하나의 노드를 다른 후보로 바꾸는 후보와 무이동(교환 결과)을 같은 키로 비교해 1회 선택.
   반복·수렴 탐색 없음. 비용 동률에서 무이동보다 사전순이 작은 이동이 선택될 수 있다(기존 사후 진단과 같은 키).
4. 후보 수는 단계별 생성 제안, 미자격·용량 거부, 검증 통과 고유 배치 수(기준 배치 포함)로 기록한다.

## 경계
- 새 파일만: `experiments/real-topology-live/swap_move_baseline.py`, 테스트, 문서. 기존 `budget_placement.py`,
  `placement_policies.py`, `analyze_single_move.py`, `run_budget_placement.py`, verify/report 경로는 바꾸지 않는다.
- 시간: greedy, 교환, 이동을 각각 측정하고 repair(교환+이동)와 total(greedy 포함)을 분리한다. greedy(기존 sequential)는
  feasibility 확인을 위해 전체 조합을 열거하므로 total 시간을 확장 가능한 greedy의 비용으로 부르지 않는다. repair 단계는
  전체 조합을 열거하지 않는다.
- 출력은 오프라인 계획: `execution_ready=false`, contract/provenance, profile 파일·정규화 hash, 구현 소스 hash.
  기존 수집기·validator(4정책 집합 계약)를 5정책으로 호출하지 않는다.
- CLI: 단일 profile JSON + server. 측정 provenance·서버 노드·admission_budget 서버 일치 검사, 실패 시 출력 파일 없음,
  기존 파일 덮어쓰기 금지.
- 기존 동결 snapshot은 호환성(사후 선택 재현) 확인에만 쓰고 관측을 합치거나 성능을 주장하지 않는다.

## Next live gate
Pi E1 exec 제어 경로 검증 후, 새 보정·사전 고정한 설계 문서에서 5정책 집합과 수집기/감사기 계약을 함께 갱신하는
후속 작업. 이번 구현·검증 범위에는 실측을 포함하지 않는다.
