# 호스트 검증 기록

2026-10-21, 최초 구현 기준. 독립 검토·수정의 최종 상태는 후속 기록을 따른다.

- 호스트가 `rtk proxy python3 -m unittest discover -s experiments/decision-focused-placement -p 'test_*.py' -v`를 재실행: 33개 통과(0.116초).
- 최초 결과 `results.json`: 120개 실행, 생략 0. 인증된 실행에서 참 proxy gap이 ε를 넘은 사례 0. 이 횟수로 통계적 실패확률 0.05를 검증했다고 주장하지 않는다.
- 임시 디렉터리로 전체 CLI 재실행: 120/120개 실행 기록이 planner wall time을 제외하고 일치. 임시 출력은 정리했고 최초 artifact는 유지했다.
- 최초 결과 sha256: `b3849eba2f77cfd61d6baff09f03b18de8514bb186d1e01b4bcdd476ccb5d2a6`.
- Claude 세션 transcript를 직접 검사: assistant 모델 필드 72개 모두 `claude-opus-5-5`.
- 독립 Spec reviewer: Orca `ctx_0e8864b11780`, Standards reviewer: `ctx_662364d2f5bc`. 두 launch의 requested/effective 모델 `gpt-6-astra`. 검토 완료와 실제 실행 모델 증거는 후속 기록한다.

## 수정본 호스트 재검증

- 동일 unittest 명령 50/50 통과(0.156초).
- 수정본 `synthetic-v1-reviewfix/results.json`은 strict JSON 파싱 통과. source_files의 Python 파일 8개 SHA256이 현재 파일과 일치.
- 120개 run의 probe_records 수·batch 표본 수·비용 합·검열 위치·probe 직전 예산 잔여를 검증했다. 인증된 실행의 참 proxy gap 초과 없음.
- 수정본 전체를 tempfile에 재실행: 원시 batch까지 포함한 120개 run의 모든 비시간 필드 일치. 최초 결과의 기존 비시간 run 필드도 모두 그대로다.
- 수정본 SHA256 `472a5ade21d4d0a58677e2f2edaf8aedecb08f01ce7b3c053c3c3ded5180e1c2`. 최초 결과 hash 유지 확인.
- 수정 Developer transcript의 assistant model 86건 모두 `claude-opus-5-5` 직접 확인.
- 첫 두 reviewer의 개별 Codex rollout에서 `turn_context.model=gpt-6-astra` 확인. 요청값만으로 실제 모델을 추정하지 않았다.
- 수정 중 Orca transcript reader의 `transcript_parse_failed` 1회, Claude Pre/PostToolUse hook의 `ECONNREFUSED 127.0.0.1:3002`를 관측했다. 설정을 변경하지 않았으며 terminal source로 작업 상태를 확인했다. 최종 파일·hash·실행은 호스트가 별도로 재검증했다. 오류를 숨기거나 hook 복구를 완료했다고 주장하지 않는다.
- Developer의 초기 일부 셸 명령에 rtk wrapper 누락이 있었다. 두 차례 준수를 지시했고 수정 report에 편차가 기록돼 있다. 이 절차 편차를 완전 준수로 보고하지 않는다.
- 최종 독립 재검토: Spec `ctx_6a85fac5b6c5`, Standards `ctx_f639d54cc3c2`, 완료. 각 개별 rollout의 `turn_context.model=gpt-6-astra` 직접 확인.

## 마지막 경계 수정과 수용

- 2차 Standards 잔여0, Spec은 기존4건 해결 및 미사용 무한 좌표의 contrast acquisition crash 1건 발견.
- 마지막 Developer `ctx_ffb3e6ce2dc4`가 witness 쌍의 계수가 모두0인 좌표를 finite lower bound에 고정했다. 호스트가 해당 실제 코드를 읽고 값 불변의 수학적 이유를 확인했다.
- 호스트 unittest 최종 **52/52 통과(0.151초)**. 원 재현은 실제 probe가 필요한 public runner 테스트로 포함됐다. 보조 테스트 하나는 내부 contrast 함수에 직접 접근하므로 공개 경계 원칙의 편차가 있으나 public runner 회귀도 함께 통과했다.
- 최종 JSON의 120개 run과 raw batch는 중간 수정본과 시간 필드 외 모두 일치. config·fixture 불변, 소스8개 hash 현재 파일과 일치.
- 최종 artifact SHA256 `a08d88c2a51156a83c489aee0b606f298af59b5cb4be8a5de4b10cb4df3b844b`.
- 마지막 Claude transcript의 실제 model도 `claude-opus-5-5`임을 호스트가 직접 확인했다.
- 7개 Orca dispatch 모두 명시적 worker_done 확인 후 release. reclaimable worker0 확인. 독립 검토 라운드는2회이며 마지막 수정은 호스트 검증으로 마무리했다.
- 수용 범위: 첫 합성 검증 프로토타입. 실측 성능·통계 실패확률 실증·신규성·일반 우월성에 대한 승인은 아니다.
