# 독립 검토 1차 수정

작업 경로 `/home/jinuk/codex-work/scheduler`. 원 구현 계약 `implementation-task.md`와 두 독립 보고 `review-spec.md`, `review-standards.md`를 읽고 재현 후 최소 수정한다. 소유권은 기존 새 `experiments/decision-focused-placement/`와 새 결과 디렉터리 `artifacts/decision-focused-placement/synthetic-v1-reviewfix/`, `.scratch/decision-focused-placement/fix-report.md`만이다. 다른 작업자의 변경과 최초 synthetic-v1 결과는 보존한다.

## 확정 수정

1. p75 public boundary에서 support 정렬·NaN·잘못된 차원/확률과 nonfinite uncensored sample을 검사. 잘못된 표본은 카운트를 올리기 전에 거부. 알려지지 않은 upper는 None/+inf로 허용하지만 NaN/−inf는 허용하지 않는다. empty-support는 오류, 사후 empty intersection은 미인증. 테스트는 공개 함수 결과로 검증.
2. 전체 run planner budget: 최초0이면 미인증 종료. interval/LP 계산 후 인증 판정 전에도 예산 확인. acquisition 후에는 다시 확인하여 소진 시 유료 probe를 실행하지 않는다. 타이밍 테스트는 clock boundary를 조절하고 정책 private 함수 mock은 피한다. CPU 연산 자체의 완전 선점은 요구하지 않으며 안전 checkpoint 한계를 문서화한다.
3. 사용되는 primitive에 infinite interval이 있으면 runner 인증 금지(하위 수학 solver의 one-plan zero regret 자체는 타당). 불필요한 primitive와 single-plan 처리 기준을 명시적으로 테스트한다.
4. 관측 batch·검열 표식·probe별 실제 관측 simulated cost를 반환/저장. 가상 비용이라는 표시 유지. JSON에 Infinity/NaN token을 쓰지 않도록 비유한 수치의 명시 표현(null+status 등)을 계약화하고 strict allow_nan=False로 출력. 재현 가능한 source file hashes도 결과 manifest에 포함한다. 기존 결과는 변경하지 않는다.
5. LP combination cap은 pair 전체의 모든 r 경로 활성 조합 합계에 적용. 전체가 cap초과이면 simple bound 유지. 1차 review의 cap=3 예제를 테스트한다.

정상 benchmark fixture/ε/δ/예산/seed는 그대로 두고 수정 코드로 새 synthetic-v1-reviewfix 디렉터리에 120 run 실행. 최초 결과와 기존 비시간 핵심 지표 비교(새 raw records/hash 필드는 제외) 및 예상 차이가 있다면 이유를 보고한다. 최초 artifact hash 보존 확인.

## 검증/완료

TDD red→green은 결함 하나씩. `rtk proxy python3 -m unittest discover -s experiments/decision-focused-placement -p 'test_*.py' -v` 및 tempfile CLI smoke. 최종 수정 report에는 실제 model/auth 확인, 변경 파일, 추가 테스트, benchmark 비교, 남은 제한을 기록한다. Claude Opus 5.5 공식 구독만, API/provider override 금지. 셸은 rtk/rtk proxy. 커밋·push·배포·클러스터·의존성 추가·새 worker 금지. 구현 외 원 설계·AGENTS·초기 review 보고서는 수정하지 않는다. 주어진 Orca worker_done으로 완료한다.
