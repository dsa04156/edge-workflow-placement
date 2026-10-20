# 01 오프라인 greedy-swap-move 선택기와 CLI
Status: ready-for-human

명세: [spec.md](../spec.md)

## 완료 조건
- 공개 선택기 `greedy_swap_move(profile)`와 5정책 계획 `plan(profile, server)`.
- 손계산 3노드 사례(교환 뒤에도 유익한 한 작업 이동), 미자격/용량 거부, 결정적 동률, 입력 비변경, 개선 없음 테스트.
- CLI provenance, 배타적 생성, 잘못된 profile/server에서 출력 없음 테스트.
- 보존된 `wan-20261020t082011` profile이 있으면 사후 진단(`fresh-single-move.json`) 선택 재현 확인.
- 기존 관련 unittest 회귀, 한국어 문서(실제 명령·한계·다음 실측 관문).

## Comments
- 2026-10-20 구현: 선택기·`plan`·CLI와 테스트9건 추가. 관련13/live 전체32/prepare7 통과.
  `wan-20261020t082011` b0 두 profile에서 기존4정책 배치와 사후 진단 selected/대리 비용/고유 후보 수 재현.
  실환경 검증 없음. 문서: `docs/experiments/swap-move-baseline.md`. 남은 일: 실측 설계 동결과 수집기/감사기
  5정책 계약 갱신(후속 작업).
