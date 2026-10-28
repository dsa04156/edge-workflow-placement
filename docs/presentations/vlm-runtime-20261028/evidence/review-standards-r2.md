# Standards review — final R2

**PASS — R1 P2 수정 확인. 검토 범위 내 미해결 결함 없음.**

실제 모델은 현재 세션 `01a18224-9ec2-7ba0-8fb4-6c88e3695a17`의 최신 `turn_context.model=gpt-6-astra`로 확인했다. 두 현재 파일의 SHA-256은 `review-r2-hashes.json`과 일치한다. `review-r1/` 사본은 R1 해시와 일치하며, 사본→현재 파일로 재생성한 diff가 `review-r2.diff`와 정확히 같다.

## 문서화된 규칙

`AGENTS.md:19`의 “전체 유입 요청을 기준으로 … 시간 초과·미완료 … 함께 보고한다”와 R1의 요청 중단 보존 요구를 기준으로 확인했다.

- `experiments/gpu-mobilint-vlm/benchmark_live.py:203`: 호출 전에 측정 식별자·시작시각을 확보하고, 호출/기록 중 예외에 대해 `interrupted`, 결과 불확실성, 부분 경과시간과 수신한 응답을 보존한 후 예외를 다시 전달한다.
- 같은 파일 `:187`: 이미 기록된 동일 measurement를 확인해 append 완료 직후 중단 시 중복 행을 방지한다.
- 같은 파일 `:250`: interrupted를 failed 및 unattempted와 분리하여 전체·arm별 집계에 반영한다. 기존 sequence 검사·첫 실패 중단·UID cleanup 경로는 유지된다.
- `test_benchmark_live.py:411`, `:433`: 호출 중 중단, 응답 후 기록 전/후 중단을 검증하며 후속 호출 없음, 합계 80건, 미실행 77건, 예외 전파와 cleanup을 확인한다.

`logs/test_benchmark_live-fix-r1.log`에서 **12 tests / 8.387s / OK**를 확인했다. 새로운 구체적 우려가 없어 테스트를 중복 실행하지 않았다.

## 판단성 코드 냄새

추가 지적 없음. 수정은 P2와 관련 회귀 테스트에 한정된다.

남는 경계: 개발 보고서가 명시한 기록 복구 중 재중단·부분 write 복구는 검증되지 않았다. 본 PASS는 해당 내구성을 보증하지 않는다. 실제 클러스터·가속기 성능도 검증하지 않았다. 분석기는 새 interrupted 항목을 반영하고, 실행 전 collector hash가 변경된 release를 재생성해야 한다. 구현·클러스터 변경 없음.
