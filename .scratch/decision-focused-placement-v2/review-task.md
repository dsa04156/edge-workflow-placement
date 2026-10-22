# 독립 검토 계약

두 독립 Orca Codex gpt-6-astra 워커가 각각 Spec / Standards를 검토한다. 구현 파일은 수정하지 않는다. 다른 작업자가 같은 저장소에서 작업 중이며 기존 변경을 되돌리지 않는다. 담당 보고 파일만 쓴다. 셸은 rtk 또는 rtk proxy. 커밋·네트워크 설치·클러스터 작업 금지.

## 고정점과 범위
이번 수정 전 실제 파일: `.scratch/decision-focused-placement-v2/before/`. 현재 `experiments/decision-focused-placement/`와 비교한다. 명령 `rtk proxy diff -ru --exclude=__pycache__ .scratch/decision-focused-placement-v2/before experiments/decision-focused-placement`의 exit1은 차이가 있다는 뜻이다. 신규 v2 파일은 전체 검토한다. 기존 모듈이 HEAD에 없으므로 HEAD diff는 이번 변경을 나타내지 않는다. 이번 작업 커밋은 없다.

명세 `.scratch/decision-focused-placement-v2/spec.md`, 구현 보고 `developer-report.md`, 결과 `artifacts/decision-focused-placement/synthetic-v2/results.json`. 기존 `synthetic-v1-final/results.json`은 호환성 근거이며 변경 금지다. 필요하면 tempfile 출력으로 검증하되 기존 결과를 덮어쓰지 않는다.

## Spec 축
명세에서 누락/부분 구현, 잘못 구현, 요청하지 않은 확장을 검사한다. 각 finding에 명세 문구·파일/줄·재현 근거·영향·우선순위를 기록한다. 특히 전역 공통항 판별, 모든 배치를 포함한 인증, original vs swap stream 일치, fallback/동률 예외, evaluator truth 접근 차단, 비용 비교의 joint-certified 분모, v1 호환성, source hash·raw 기록·overwrite를 확인한다. 보고 `.scratch/decision-focused-placement-v2/review-spec.md`, 본문400단어 이내. 결함 없으면 검사 근거와 잔여 한계를 구분한다.

## Standards 축
AGENTS.md 및 사용자 제공 상위 공통 지침, docs/agents/{issue-tracker,domain}.md, 명세 금지 범위와 보존 규칙, 모듈 README를 적용한다. GLOSSARY.md는 현재 없으며 빈 문서를 만들지 않는다. 자동 도구가 잡는 스타일 문제를 중복 보고하지 않는다. 각 finding은 실제 diff hunk·근거 지침·영향을 포함한다. 아래 smell은 판단 보조이며 프로젝트 지침이 우선한다.

- Mysterious Name: 의미가 불명확한 이름 → 의미를 드러내는 이름.
- Duplicated Code: 같은 논리 중복 → 필요한 공통 로직 추출.
- Feature Envy: 다른 객체 내부에 과하게 의존 → 책임 이동.
- Data Clumps: 함께 이동하는 값 묶음 → 필요한 경우 객체화.
- Primitive Obsession: 도메인 의미가 원시값에 숨음 → 필요한 경우 타입화.
- Repeated Switches: 반복 분기 → 적절한 dispatch/map.
- Shotgun Surgery: 한 변경이 여러 곳에 흩어짐 → 응집.
- Divergent Change: 무관한 이유로 같은 모듈 변경 → 책임 분리.
- Speculative Generality: 요구 없는 추상화 → 제거.
- Message Chains: 깊은 접근 연쇄 → 지식 숨김.
- Middle Man: 의미 없는 위임 → 단순화.
- Refused Bequest: 상속 계약 위반 → 적합한 조합.

보고 `.scratch/decision-focused-placement-v2/review-standards.md`,400단어 이내. 강제 지침 위반과 smell 판단은 구분한다. 중복 모듈 재작성보다 작은 backward-compatible 확장이 명세상 우선임을 고려한다.

완료 시 메시지 확인 후 자기 Task/Dispatch의 worker_done 전송. 실제 model 증거를 보고하고 완료 뒤 유휴 상태로 끝낸다. 발견 결함을 직접 수정하지 않는다.
