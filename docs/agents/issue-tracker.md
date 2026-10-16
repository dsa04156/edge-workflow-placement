# Issue tracker: Local Markdown

이 프로젝트의 이슈와 명세는 `.scratch/` 아래의 Markdown 파일로 관리한다.

## Conventions

- 기능 또는 연구 주제별 디렉터리: `.scratch/<feature-slug>/`
- 명세: `.scratch/<feature-slug>/spec.md`
- 개별 이슈: `.scratch/<feature-slug>/issues/<NN>-<slug>.md`. 번호는 `01`부터 시작하며, 여러 티켓을 하나의 파일에 합치지 않는다.
- 일반 이슈의 분류 상태는 파일 상단의 `Status:` 줄에 기록한다. 값은 [triage-labels.md](triage-labels.md)의 역할 문자열을 사용한다.
- 이슈에 필요한 의견과 결정 요약은 파일 하단의 `## Comments`에 추가한다. 대화 전문은 복사하지 않는다.
- 디렉터리와 파일은 실제 명세나 이슈가 생길 때 생성한다.

## When a skill says "publish to the issue tracker"

위 경로에 해당 명세 또는 개별 이슈 파일을 작성한다. 이 프로젝트의 이슈 발행에는 `gh`나 `glab`을 사용하지 않는다.

## When a skill says "fetch the relevant ticket"

사용자가 지정한 경로의 파일을 읽는다. 번호만 주어진 경우 관련 주제의 `issues/`에서 찾고, 같은 번호가 여러 주제에 있으면 주제를 확인한다.

## Wayfinding operations

`/wayfinder`를 사용할 때는 다음 규칙을 적용한다. 이 흐름의 `Status:`는 일반 triage 라벨과 별개의 작업 상태다.

- 지도: `.scratch/<effort>/map.md`. Notes / Decisions-so-far / Fog를 기록한다.
- 자식 티켓: `.scratch/<effort>/issues/NN-<slug>.md`. 번호는 `01`부터 시작하고 본문에 조사할 질문을 기록한다.
- 유형: 파일 상단에 `Type: research`, `prototype`, `grilling`, `task` 중 하나를 기록한다.
- 상태: 새 티켓은 `Status: open`, 작업 시작 시 `Status: claimed`, 해결 시 `Status: resolved`로 기록한다.
- 의존성: `Blocked by: NN, NN`으로 기록한다. 나열된 티켓이 모두 `resolved`이면 차단이 해제된다.
- 다음 작업: `open`이고 차단되지 않은 티켓 중 번호가 가장 작은 것을 선택한다.
- 작업 시작: 실제 작업 전에 `Status: claimed`를 저장한다.
- 해결: `## Answer`에 답과 근거를 추가하고 `Status: resolved`로 바꾼 뒤, 지도 Decisions-so-far에 요약과 티켓 링크를 추가한다.
