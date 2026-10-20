# 보유 장비 실험 계획 Git 인계
Status: ready-for-agent

## 요구
사용자가 승인한 S1~S5 시나리오를 GitHub에 게시한다. 실제 장비 실행이나 자동 배치 구현 요청으로 확대하지 않는다.

## 산출물
[실험 계획](../../docs/experiments/owned-hardware-scenarios-20261020.md)

## 결정
- E0/E1/C 먼저, J0/J1 실행 검증 후 확장.
- source는 엣지, F 배치도 선택 가능, 동일 모델/품질/자원/실행기.
- native 대조·공유 WAN·source 위치·이기종 성능·부하를 분리.
- 프로파일과 평가 분리, block별 반복·무작위 순서, 같은 배치 관측 공유.
- 과거 결과/코드를 변경하지 않는 문서 전용 feature branch.
- 새 자동 배치/HEFT 연결 및 실장비 실험은 미완료.

## 검증 기록
원격 main 49fb86908a6823f18eba98489f346bcc7db8e75c의 AGENTS.md와 tree, 로컬 issue/domain 규칙을 확인했다. 해당 원격 tree에 GLOSSARY.md, docs/adr/, 추가 AGENTS.md는 없었다.
실장비 근거는 experiment/topology-profile-live의 4dc7550 고정 문서를 확인했다.
문서 내 상대 링크/필수 조건을 검토했다. 실행 코드를 변경하지 않으므로 런타임 테스트는 수행하지 않는다.
Semantica는 제공되지 않아 미기록.
