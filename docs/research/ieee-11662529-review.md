# AnchorDL 검토 상태 — 사용자 제공 IEEE 링크 후속

확인일: 2026-10-16. **공식 서지·초록과 DOI↔IEEE 문서번호 연결은 확인했고, 논문 전체 원문·코드는 아직 확보하지 못했다.** 초기 번호 검색만으로 식별하지 못한 상태는 사용자 제공 제목과 후속 Crossref 원응답으로 갱신되었다.

## 식별된 논문

- **AnchorDL: Dual-Locality-based Scheduling for Serverless Inference Workflows**. Min Chang Kim, Hyungjun Kim, Hokun Park, Minjae Kang, Hwamin Lee, Heonchang Yu. IEEE CLOUD 2026, pp.443–453. DOI: [10.1109/CLOUD72782.2026.00057](https://doi.org/10.1109/CLOUD72782.2026.00057). [고려대학교 공식 서지·초록](https://pure.korea.ac.kr/en/publications/anchordl-dual-locality-based-scheduling-for-serverless-inference-/)
- 저자 연구실의 [논문 목록](https://ds.korea.ac.kr/publications)과 [IEEE CLOUD 공식 프로그램](https://services.conferences.computer.org/2026/cloud-program/)에서도 제목·저자가 일치한다. 연구실 [공식 저장소의 발표 기록](https://github.com/KU-Cloud/ku-cloud.github.io/blob/9deefe31fefa57a3aa42ef16a452576d7e24605d/_posts/2026-07-15-CLOUD-2026-1.md)도 확인했다.
- 사용자 제공 [IEEE arnumber=11662529 PDF](https://ieeexplore.ieee.org/stamp/stamp.jsp?arnumber=11662529) 및 [문서 페이지](https://ieeexplore.ieee.org/document/11662529)는 web 도구에서 접근되지 않았다. 후속 [Crossref DOI exact API](https://api.crossref.org/works/10.1109%2FCLOUD72782.2026.00057)의 `resource.primary.URL`이 `https://ieeexplore.ieee.org/document/11662529/`임을 확인하여 **arnumber와 DOI의 직접 연결은 해결했다.** [원응답](../../artifacts/anchordl-readiness/crossref.json)은 본문이 아닌 서지 metadata다.

## 초록에서 확인한 범위

- dependent function의 동시 배치에 따른 **데이터 전송 비용**과 목적지 메모리에 model weights가 상주하는지에 따른 **모델 로딩 비용**을 함께 다룬다. 인접 의존성의 후속 비용을 보는 joint-cost look-ahead 배치를 제안한다. [공식 초록](https://pure.korea.ac.kr/en/publications/anchordl-dual-locality-based-scheduling-for-serverless-inference-/)
- trace 기반 workload의 chain·fan-in·fan-out에서 세 baseline과 비교하고 greedy joint-cost 대비 look-ahead 효과를 보고한다. 이는 **저자 초록의 평가 주장**이며 원문 실험 구성·수식·통계·코드를 독립 검증한 것은 아니다. 수치 성능을 현 E2/E3로 전이하지 않는다. [공식 초록](https://pure.korea.ac.kr/en/publications/anchordl-dual-locality-based-scheduling-for-serverless-inference-/)

## 현 연구와의 대조 — 초록 확인 수준

| 항목 | 확인한 사실 또는 현재 판단 |
|---|---|
| 데이터 위치+모델 상주 상태 | **직접 겹친다.** 이를 함께 고려하는 것 자체를 새 기여로 삼을 수 없다. 근거: 위 공식 초록. |
| 다단계 workflow 배치 | chain·fan-in·fan-out 평가가 명시되어, 현재 3Task chain보다 구조적으로 넓은 평가를 이미 주장한다. 근거: 위 공식 초록. |
| NPU·이종 runtime 호환성 | 초록만으로 실제 장치·연산·artifact 지원 조건을 판정할 수 없다. **미지원 단정 금지.** |
| 모델 준비·실행 상태 | memory-resident weights/load 비용은 확인된다. NPU compile/request lifecycle·실행 중 state 이전까지 포함하는지는 원문 확인 과제다. |
| 동적 전환 비용 | per-placement 비용과 look-ahead는 확인된다. eviction·재컴파일·재배치·모델 버전 전환의 구체 처리 여부는 원문 확인 과제다. |
| 비교 실험 설계 | **연구적 판단:** 동일 상태·비용 관측 정보와 look-ahead를 제공한 AnchorDL 계열 기준선 없이 새 알고리즘의 공백·우월성을 판단할 수 없다. |

## 원문·코드 탐색과 남은 일

- 제목·DOI·저자명과 PDF/preprint/GitHub 조합을 검색했다. 공식 연구실 사이트 및 [논문 목록 소스](https://github.com/KU-Cloud/ku-cloud.github.io/blob/9deefe31fefa57a3aa42ef16a452576d7e24605d/_data/publications-ci.yaml)에는 해당 서지는 있었으나 이 항목의 공개 PDF·코드 URL은 확보하지 못했다. GitHub repository 이름 `AnchorDL` 검색도 결과가 없었다. **코드 비공개·원문 부재의 증명은 아니다.**
- 공식 서지 검색 결과에서 초록·DOI를 읽었으나 직접 페이지 재열람은 `Processing` 응답으로 바뀌기도 했다. IEEE 본문 접근도 계속 제한되어 원문을 읽었다고 기록하지 않는다. 차단·저작권 접근은 우회하지 않았다.
- [후속 공개 경로 조사](anchordl-source-acquisition.md)와 [재현 계약·실행기 의미 점검](anchordl-reproduction-contract.md)을 완료했다. 현재 서비스 객체 readiness와 가중치 메모리 상주, E2의 요청4회 비용 상각과 DAG look-ahead는 구분해야 한다. 다음 입력은 원문의 비용식·look-ahead 범위·모델 cache 정책·실제 testbed·baseline 정의다. 현재 E2/E3는 구체 환경의 현상 재현으로 유지하고, **AnchorDL을 근접 선행으로 대조한 뒤에도 남는 실패 조건**이 있는지를 검증한다.
