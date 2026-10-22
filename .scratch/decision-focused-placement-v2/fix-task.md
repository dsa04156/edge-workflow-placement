# v2 Spec P2 경계 오류 최소 수정

## 목표·소유권
review-spec.md의 유일한 P2를 수정한다. 당신은 별도 Claude Opus5.5 Developer이며 이전 Developer의 user-owned 터미널을 재사용하지 않는다. 기존 다른 작업을 되돌리지 않는다.

허용: policies_v2.py, test_synthetic_v2.py, README.md의 해당 규칙 설명, `.scratch/decision-focused-placement-v2/fix-report.md`, 신규 `artifacts/decision-focused-placement/synthetic-v2-reviewfix/` 결과만. 다른 소스·명세·기존 artifact 금지. 기존 코드 고정점은 before-fix/.

## 확정 동작
공통 좌표 중 상한이 유한한 좌표는 다른 좌표의 구간이 무한이어도 측정 대상에서 제외한다. 사용 좌표 중 비공통 좌표와 상한이 무한인 공통 좌표를 원래 순서로 순환한다. 상한 무한인 공통 좌표는 원래 runner 인증 gate 때문에 측정을 허용한다(모형/통계/종료 규칙을 바꾸지 않는다). 유한 공통 좌표까지 함께 복원하지 않는다. targets없음 fallback은 기존 tiny finite fixture의0-probe인증과 호환되게 유지한다.

최소 red→green 테스트: (1) review 재현 z support유한,y support미상 => ['y','y'], 비용2; (2) 유한 공통 w,무한 공통 z,비공통 y 혼합에서 w는 제외하고 z,y를 순환. 기존 무한 공통 좌표 테스트도 통과해야 한다. tdd 스킬 및 승인된 run_policy 공개 seam 재사용.

## 검증
1. `rtk proxy python3 -m unittest discover -s experiments/decision-focused-placement -p 'test_*.py'`.
2. fixture/config/seeds를 절대 바꾸지 않고 기존 v2 CLI로 신규 output `artifacts/decision-focused-placement/synthetic-v2-reviewfix`에750회 실행한다. 기존 v2를 덮어쓰지 않는다.
3. v2 최초 결과와 수정본 runs의 모든 비시간 필드 일치, source hashes·원시 기록 확인. 결과가 달라지면 숨기지 말고 원인을 보고한다. max_probes/seeds override 사용 금지.
4. 실제 모델/구독 로그인 경로와 red→green 결과, 변경 경로, 알려진 오류를 fix-report.md에 기록하고 주입된 Orca CLI lifecycle로 worker_done.

## 실행 규칙
모든 셸은 `rtk`/`rtk proxy`. 절대 glob 기반 삭제를 하지 않는다. 임시 파일은 정리할 필요 없다. 출력은 명시적으로 할당한 저장소 내 경로만, 필요하면 Python subprocess로 stdout/stderr를 capture하여 허용 신규 파일에 기록한다. 임의 redirect나 부모 경로 이동 금지. 커밋/push/클러스터/의존성/설정 변경 금지. 공식 claude.ai firstParty 구독만; 대체 모델·API 금지.
