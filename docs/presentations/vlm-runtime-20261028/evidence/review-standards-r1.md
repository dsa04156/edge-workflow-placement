# Standards review — R1

**결과: 수정 필요 — P2 1건.** 실제 모델은 현재 세션 `01a18224-9ec2-7ba0-8fb4-6c88e3695a17`의 `turn_context.model=gpt-6-astra`로 확인했다.

검토 범위는 `/dev/null` 기준 신규 `benchmark_live.py`, `test_benchmark_live.py` 두 파일이다. 현재 SHA-256 두 값 모두 `review-hashes.json`과 일치하며, 현재 파일로 재생성한 unified diff도 `review.diff`와 정확히 일치했다. `AGENTS.md`, `../AGENTS.md`, spec/plan, 개발 보고서와 테스트 로그 및 재사용한 live/collect/worker/HTTP 경로를 확인했다.

## 문서화된 규칙 위반

- **P2 — `experiments/gpu-mobilint-vlm/benchmark_live.py:193` (집계: 234–240): 중단된 실제 요청을 미실행으로 기록한다.** `AGENTS.md:19`는 “전체 유입 요청을 기준으로 완료율·실패·드롭·시간 초과·미완료…를 함께 보고한다”고 규정한다. 이번 spec도 “첫 실패에서 중단하고 failure/unattempted/partial timing을 보존한다”고 요구한다. `live.call_arm()` 호출이 시작된 뒤 `KeyboardInterrupt`가 발생하면 ledger append까지 도달하지 않는다. summary는 ledger 길이만으로 unattempted를 계산하므로 해당 요청의 measurement_id·시작시각·부분 경과시간이 사라지고 미실행으로 분류된다. 기존 loopback Harness에서 원래 call_arm이 응답을 받은 직후 interrupt를 발생시키는 표적 probe로 **worker 호출 1건, completed=0, failed=0, unattempted=80, stopped_at=[], ledger 없음, cleanup_verified=true**를 재현했다. 호출 전 진행 중 measurement 상태를 보존하고, BaseException 경로에서 해당 요청을 interrupted/unknown으로 기록하여 아직 시작하지 않은 79건과 분리하라. 호출 중 중단 회귀 테스트도 추가하라.

## 판단성 코드 냄새

별도 지적 없음. cleanup 복제는 기존 소스 변경 금지와 inline 기존 구현이라는 제약을 고려해 지적하지 않았다.

개발 로그는 mock 테스트 10/10 통과이며 중복 실행하지 않았다. 추가 probe는 위 중단 회계 경로만 검증했다. 구현·클러스터는 수정하지 않았으며, mock 결과로 실제 가속기 실행·성능을 주장하지 않는다.
