# 독립 검토 계약

작업 디렉터리: `/home/jinuk/codex-work/scheduler`. 각 검토자는 자신에게 배정된 한 축만 검토한다. 다른 작업자가 있으므로 기존 변경을 보존한다. 구현·테스트·artifact는 수정 금지. 지정 report 파일만 생성한다. 셸은 rtk/rtk proxy. 새 워커·커밋·push·클러스터·의존성 설치 금지.

## 기준점과 실제 변경

기준 HEAD `43f9d110db4b85bd487214910653efbde481da83`. 이번 구현은 전부 새 `experiments/decision-focused-placement/` 아래에 있고 아직 untracked다. `git diff BASE...HEAD`만 읽으면 빈 diff가 되므로 각 신규 텍스트 파일에 `git diff --no-index /dev/null <file>`를 실행하거나 전체 파일을 읽어 실제 변경을 검토한다. `__pycache__`는 제외한다. 기존 dirty/untracked 변경을 이번 구현으로 세지 않는다. 새 커밋 없음.

원 명세: `.scratch/decision-focused-placement/implementation-task.md`, 연구 설계: `docs/research/decision-focused-placement-design-20261021.md`. 결과: `artifacts/decision-focused-placement/synthetic-v1/results.json`. Developer 보고: `.scratch/decision-focused-placement/developer-report.md`.

검증 명령: `rtk proxy python3 -m unittest discover -s experiments/decision-focused-placement -p 'test_*.py' -v`. 필요하면 임시 디렉터리만 사용해 CLI 재현. 기존 결과 덮어쓰기 금지. 초점은 정확성·인증 오용·실험 공정성·재현성이며 사소한 스타일 선호는 제외.

## Standards 축

AGENTS.md 및 해당 하위 AGENTS, README의 계약을 확인한다. 해당 경로에 적용되는 CONTRIBUTING/CODING_STANDARDS가 있으면 확인한다. 문서화된 위반과 판단에 따른 smell을 구분한다. 도구가 이미 잡는 포맷은 제외.

Smell 기준(모두 판단 사항이며 프로젝트 명시 규칙이 우선):
- Mysterious Name: 이름이 의도를 감춤 → 명료한 이름.
- Duplicated Code: 같은 논리 반복 → 필요한 공통 함수.
- Feature Envy: 다른 객체 내부 자료에 과도 의존 → 책임 이동.
- Data Clumps: 항상 함께 전달하는 자료 묶음 → 의미 있는 묶음.
- Primitive Obsession: 도메인 개념을 원시값으로 흩뜨림 → 적절한 타입.
- Repeated Switches: 같은 분기가 반복 → 한 매핑이나 필요한 다형성.
- Shotgun Surgery: 한 변화가 여러 파일을 요구 → 관련 책임 모음.
- Divergent Change: 한 파일에 무관한 책임 → 적절한 분리.
- Speculative Generality: 요구하지 않은 추상화 → 제거.
- Message Chains: 내부 탐색 체인 → 의도를 가진 인터페이스.
- Middle Man: 단순 전달만 하는 계층 → 제거 검토.
- Refused Bequest: 상속 계약을 무시 → 조합 검토.

보고 `.scratch/decision-focused-placement/review-standards.md`: 파일·라인, 근거 규칙, 재현 또는 영향, 최소 수정. 400단어 이내. 결함 없으면 명시. 구현 파일을 수정하지 않는다.

## Spec 축

원 명세의 미구현/부분 구현, 범위 초과, 잘못 구현된 요구를 보고한다. 특히 전체 competitor 인증, exact vertex completion/fallback, confidence semantics, 관측 정보 누출, 예산 종료, invalid/timeout handling, 결과 재현성을 확인한다. 자체 oracle 예제를 사용하고 기존 테스트 통과만으로 승인하지 않는다.

보고 `.scratch/decision-focused-placement/review-spec.md`: 파일·라인, 명세 근거, 재현·영향·최소 수정. 400단어 이내. 결함 없으면 명시. 구현 파일을 수정하지 않는다.

## 완료

실제 모델 확인이 가능하면 보고에 기록한다. 요청 모델만 보고 실제 모델이라고 주장하지 않는다. 라이브 Orca preamble의 worker_done을 정확히 한 번 전송하고 종료한다. 보고서는 검토 결과이지 수정 권한 부여가 아니다.
