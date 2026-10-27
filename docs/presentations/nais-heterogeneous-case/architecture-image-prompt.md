# 시스템 아키텍처 이미지 생성 기록

- 생성 방식: 내장 `image_gen` 도구.
- 용도: NAIS 면접 발표에 사용할 시스템 아키텍처 이미지.
- 근거: [실제 구현 상세 설명](architecture.md).
- 구성: 엣지 generator와 배치 정책 → 5 Task VQA DAG → 3개 물리 노드·4개 worker. 실험 제어·관측 경로를 구분하고, Mobilint는 별도 검증 영역으로 표시한다.
- 이미지의 논리 DAG는 특정 고정 배치를 뜻하지 않는다. 144는 검증된 작업별 실행 후보의 조합 수다.

정확한 생성 프롬프트는 `architecture-image-prompt.txt`에 보존한다.
