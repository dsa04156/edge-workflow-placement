# 최소 비교 구현·검토·실측 진행

- 사용자 후속 요청: 구현하고 실험은 많이 하지 않는다. 조건별 소수 요청의 기존 실험 namespace 내 실행으로 해석하며 별도 반복 승인 질문을 하지 않는다.
- 축소 범위: 1 block, 10/2Mbps +20ms, calibration 첫4/evaluation 첫3, 고유 배치당 warmup1+평가3,0.1rps. 최대48 workflow(보정/품질 검증 제외). 통계적 우월성·최대 처리량 주장 없음.
- Orca runtime1.4.235의 orchestration 및 coordinator-loop 가이드를 확인했다.
- Run run_8b12c8e83119, Developer task_f9537f289609 / dispatch ctx_a0cb067a726a. requested/effective claude-opus-5-5, 실제 transcript 모델은 완료 시 확인.
- Claude 인증: loggedIn, authMethod=claude.ai, apiProvider=firstParty, Max. 호스트 provider override 변수 없음. 자격 증명 값은 저장하지 않는다.
- 호스트 세부 Astra 모델 식별은 미확인으로 고지했으며 모델 변경 없음.
- 구현 소유권은 새 collector/validator/tests/developer-report에 한정. 기존 추적 파일 hash는 before-tracked-hashes.json에 보존. 과거 실험 자료·다른 변경 보존.
- 읽기 전용 preflight: dev0003/dev0002/ser0002 Ready, topology-profile-lab Pod0. ser0002에 ebpf-clock-check-heart-20261021/server2 Running이므로 공유 환경 교란 가능성을 결과에 기록.
- 구현은 claude-implementer, 공개 collector/validator 경계의 기존 TDD 계약, 독립 검토는 code-review의 Spec/Standards 두 축을 적용한다. 새 외부 API·라이브러리는 추가하지 않아 기존 구현/문서 근거를 재사용한다.

## 구현·검토 완료

- Claude Opus 5.5 구현 및 두 차례 수정 완료. 최종 수정 worker `task_20bc392e13fa` / `ctx_af79936dcfd4`의 실제 transcript 모델도 `claude-opus-5-5` 확인.
- 독립 Spec/Standards 검토 2회 완료. reviewer 실제 rollout은 `gpt-6-astra`. 최종 잔여 P2(전체 Pod 자원 조회의 env 원문 저장)는 Claude가 수정하고 호스트가 실제 diff 및 신규 테스트 33개 통과를 확인했다. 세 번째 독립 검토는 수행하지 않았다.
- 최초 구현은 test-first 순서를 지키지 못했고 이후 mutation 재현을 수행했다. 최종 수정은 신규 실패 테스트 후 수정했다. 일부 worker의 bare shell 및 중복 worker_done 전달이 있어 절차 전체의 완전 준수로 보고하지 않는다.
- 완료 accounting: released 6, user_takeover retained 1, reclaimable 0. 보존 터미널은 사용자 소유로 유지.
- 마지막 release에서 하위명령 `orchestration` 누락으로 Unknown command가 발생했다. matched guide 재확인 후 올바른 `orchestration worker-release`로 정상 해제. 마지막 ACK가 release보다 먼저 처리된 순서 이탈을 기록한다.
- 실측 실행: `rtk proxy python3 -u experiments/real-topology-live/run_baseline_comparison.py --execute`. stdout/stderr 및 시작·종료/종료코드는 이 폴더 live.*에 보존한다. 새 run은 `wan-20261021t094522`.

## 2026-10-21 19:12 KST 재개

- 사용자의 이어서 진행 요청으로 기존 실행을 점검했다. `wan-20261021t094522` 수집 프로세스가 없고 qualification 종료·완료·실패·정리 기록이 없어 중단으로 분류했다. 마지막 완료 event는 09:53:12 UTC이며 종료 원인은 확인하지 못했다.
- 기존 원자료는 변경하지 않았다. 별도 `recovery-20261021T101145Z/`에서 기존 run-id 자원만 기존 Session.close로 정리했다. 잔여0, 오류0. 원 실행의 감사는 partial이며 보정/평가 완료로 취급하지 않는다.
- baseline-provenance에 기록된 수집기와 의존 소스 hash가 현재 파일과 모두 일치했다. 기존 검토·테스트 근거를 재사용하며 구현 변경은 하지 않았다.
- 새 실행 `wan-20261021t101203`을 동일 축소 설계로 새 보정부터 시작했다. wrapper 시작/종료 및 stdout/stderr는 `live-retry-20261021T101203Z.*`로 별도 보존한다. 완료·감사 결과는 종료 후 기록한다.

## 재개 결과와 Opus 진단 완료

- wan-20261021t101203은19:12–19:28 KST 실행. 품질108/108, 평가9/9 성공 뒤 최종 차단 해제의 exec EOF3회로 종료코드1. 전체 감사 partial, 소유 자원 잔여0/정리 오류0. 보호 workload7개 변화. 결과 문서는 docs/experiments/scheduling-baseline-smoke-20261021.md.
- 진단에 diagnosing-bugs/claude-implementer 및 Orca1.4.235 orchestration/coordinator-loop를 적용했다. Run run_fed2b80e7886, 진단 task_d19f8ac5982b/ctx_369781a5c9bc 성공, 보고서 정정 task_77ef7a7ce7c6/ctx_1f7c83a81961 성공. 공식 claude.ai/firstParty Max, 실제 transcript model claude-opus-5-5 확인. 모델/provider 대체 없음.
- Opus의 bounded 읽기 전용 진단은 cloudcore의 같은 노드10.36초 터널 단절과 세 exec 거절을 확인. 근본 단절 원인은 미확정. replay8개 검사를 호스트도 통과. 초기 보고의 전부 오차0/인과 단정/mtime 시각을 원자료와 대조해 Opus에 정정시켰고 최종 문구를 확인했다. 구현 변경과 추가 실측 없음.
- 절차 이력: coordinator의 첫 orchestration send는 --subject 누락으로 거절됐고 가이드를 읽어 올바른 dispatch 주소/subject로 전달했다. worker 초기 일부 보조 셸 명령의 rtk 미사용은 진단 보고서에 기록했다.
- 워커 완료 메시지2건 수용. 동일 터미널 재사용 뒤 runtime은 원 자원을 user_takeover/user_owned, 새 자원을 external_terminal로 분류했다. worker-release 결과 retained/external_terminal이므로 강제 종료하지 않았다. reclaimable 자원 없음 확인 후 delivery ACK 완료. 사용자 소유 터미널은 보존한다.
