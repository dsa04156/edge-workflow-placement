# Domain Docs

이 프로젝트는 단일 컨텍스트(single-context) 구조를 사용한다. 엔지니어링 스킬은 다음 규칙으로 도메인 문서를 읽는다.

## Before exploring, read these

- 루트 `GLOSSARY.md`에서 관련 용어와 정의를 읽는다.
- `docs/adr/`에서 작업 영역과 관련된 결정 기록을 읽는다.
- 향후 루트에 `GLOSSARY-MAP.md`가 생기면 해당 파일을 먼저 읽고, 작업에 관련된 컨텍스트의 용어집과 ADR을 확인한다.

문서가 없으면 별도 안내나 생성 제안 없이 작업을 진행한다. `/domain-modeling`은 용어나 결정이 실제로 확정될 때 필요한 문서를 생성한다. 빈 용어집이나 ADR을 미리 만들지 않는다.

## File structure

아래는 문서가 생성될 때 사용할 경로이며, 파일의 현재 존재 여부를 나타내지 않는다.

```text
/
├── GLOSSARY.md
└── docs/
    └── adr/
        └── NNNN-<decision-slug>.md
```

## Use the glossary's vocabulary

이슈 제목, 연구 가설, 설계 제안, 테스트 이름에서 도메인 개념을 표현할 때는 `GLOSSARY.md`의 용어를 사용한다. 용어집에서 피하도록 정한 동의어를 임의로 사용하지 않는다.

필요한 개념이 아직 정의되지 않았다면 기존 용어로 표현 가능한지 먼저 확인하고, 실제 정의가 필요한 경우 `/domain-modeling`의 검토 대상으로 남긴다. 가설을 확정된 정의나 결정으로 기록하지 않는다.

## Flag ADR conflicts

기존 ADR과 충돌하는 제안은 해당 ADR 번호, 충돌 내용, 재검토 근거를 명시한다. 기존 결정을 조용히 덮어쓰지 않는다.

Semantica 조회·기록 규칙은 루트 `AGENTS.md`를 따른다. ADR은 문서로 설명할 설계 결정과 근거를 담고, Semantica에는 확정된 결정의 요약과 관련 문서 경로를 기록해 연결한다. 대화 전문을 중복 저장하지 않는다.
