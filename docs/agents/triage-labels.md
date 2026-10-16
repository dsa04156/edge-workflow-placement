# Triage Labels

다섯 가지 표준 triage 역할을 아래 문자열로 사용한다. 로컬 Markdown 이슈에서는 파일 상단의 `Status:`에 해당 값을 기록한다.

| 표준 역할 | 프로젝트 라벨 | 의미 |
| --- | --- | --- |
| `needs-triage` | `needs-triage` | 담당자가 이슈를 검토하고 분류해야 함 |
| `needs-info` | `needs-info` | 진행에 필요한 추가 정보 대기 |
| `ready-for-agent` | `ready-for-agent` | 명세가 충분하며 에이전트가 수행 가능 |
| `ready-for-human` | `ready-for-human` | 사람이 직접 수행해야 함 |
| `wontfix` | `wontfix` | 처리하지 않기로 결정 |

스킬이 특정 역할의 라벨 적용을 요구하면 위 프로젝트 라벨을 사용한다. 라벨 이름을 바꿀 때는 이 표의 프로젝트 라벨 열을 수정한다.

`/wayfinder` 티켓의 `open` / `claimed` / `resolved` 작업 상태는 이 역할 매핑의 대상이 아니다. 해당 규칙은 [issue-tracker.md](issue-tracker.md)를 따른다.
