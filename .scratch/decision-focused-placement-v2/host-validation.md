# v2 호스트 검증

2026-10-21. 구현 완료 후 독립 실행.

- `rtk proxy python3 -m unittest discover -s experiments/decision-focused-placement -p 'test_*.py'`: **68/68 통과**,11.820초. 기존52개 및 신규16개. 기존120회 v1 원시 기록과 비시간 필드 호환성 검증 포함.
- strict JSON 파싱,750개 fixture/policy/seed 고유 조합, 누락0, overrides없음 확인.
- 결과에 기록한 Python 소스12개 hash가 현재 소스와 일치. 수정 금지된 기존 artifact3개는 사전 hash와 모두 일치.
- probe 기록55,925개: 각 run의 횟수, 표본 수, arm 순서, 비용 합계와 일치. 인증된 모든 run의 참 proxy gap≤ε. planner 예산 종료0.
- 종료446 certified /254 budget_probes /50 censored_uncertified. 이 합계는 label-swap 대응을 포함하므로 독립 반복 수나 전체 환경 성공률로 해석하지 않는다.
- 결과 SHA256: `d20a941d64230ae290ba4f48f538965a40d89139735af1a67732baf52519f2bb`.
- 실제 diff: synthetic.py는 optional policy_factory 및 stream_key 추가3곳, 나머지 기존 구현 보존. 신규 모듈·CLI·테스트와 README 추가.
- 임계경로에서 contrast−witness 비용 차이는 seed10–19 순으로6,8,8,3,8,8,3,10,6,6. 세 배치 p최적 사례는1,1,1,3,1,1,3,0,2,1. 두 비교 모두 각 정책10/10 인증하므로 비용 비교 분모가 같다.
- 실제 Developer transcript assistant 모델은 claude-opus-5-5로 확인. 독립 리뷰 모델은 해당 rollout turn_context로 추가 확인.

## 절차 편차와 자원 소유권

Developer의 rtk 누락, glob 기반 임시 디렉터리 삭제, 저장소 밖 stdout redirect 사고는 developer-report.md에 기록됐다. 호스트는 broad cleanup을 지시하지 않았으며 발견 즉시 중단 지침을 전달했다. 작업자는 전달 전에 실행되었다고 보고했다. 기존 연구 artifact 보존과 결과·소스 일치는 직접 검증했지만, glob 삭제 대상의 다른 사용자 파일 포함 여부까지 입증한 것은 아니다. 새 설정/fixture로 결과를 재수집해 사고를 숨기지 않았다.

Orca는 Developer 터미널에 `ownershipState=user_owned`, `releaseState=retained`, `retainedReason=user_takeover`를 보고했다. 이를 존중해 호스트가 터미널을 닫거나 재사용하지 않았다. Developer의 유효한 worker_done(Task task_baa9790ca9e3 / Dispatch ctx_ad9377d74395)을 확인하고 delivery를 처리했다. 독립 리뷰는 별도 신규 워커다.

## 경계 수정 후 최종 검증

- 호스트 전체71/71 테스트 통과(12.517초).
- synthetic-v2-final/results.json:750회 모든 비시간 run 필드가 최초 v2와 동일(원시 표본·rule label 포함). fixture/config 동일, overrides없음,750완료/누락0.
- 최종 소스12개 hash 및 기존 v1 동결3개 hash 확인. 최종 SHA256 `0e4fbeaebf296fdd6c9c328783b88104fe09cb93365d1282d01ec8fc318db3ae`.
- 중간 synthetic-v2-reviewfix는 rule label만 달랐고 보존했다. 이후 호환 label 복구로 최종본에 이 차이도 없다. 기존 결과를 수정해 맞춘 것이 아니라 별도 실행이다.
- 수정 Developer 실제 Claude transcript 모델 claude-opus-5-5 확인.

## 검토 종료

- 독립 Spec2차: 기존 P2 해결, 새 구현 결함0. Standards2차: 새 코드/보존 결함0. 양쪽 동일 README P3 한 건은 Claude가 문서만 정정하고 호스트가 조건문과 직접 대조했다. Python source hash 불변 확인, 테스트 재실행 불필요.
- 두 차례 독립 검토 한도를 지켰다. 마지막 문구 수정은 호스트 검증이며3차 독립 승인이라고 주장하지 않는다.
- 모든7개 작업자 worker_done 확인;6개 release, 최초 Developer는 사용자 takeover retained 상태 유지. reclaimable0.
