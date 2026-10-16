# AnchorDL 원문·공식 코드 확보 및 재현 준비 상태

조사일: **2026-10-16**. 주 스킬 `research`의 배경 조사다. 이전 IEEE 페이지·제목 검색은 반복을 줄이고, DOI exact metadata·arXiv API·기관 저장소 검색·연구실 공개 저장소를 추가 확인했다. 외부 연락·로그인·권한/차단 우회는 하지 않았다.

**결과: DOI와 사용자 제공 IEEE 문서 번호의 연결은 확인했다. 본문 PDF·accepted manuscript·공식 실행 코드는 아직 확보하지 못했다.** 검색 실패는 자료가 존재하지 않는다는 증거로 쓰지 않는다.

## 새로 확보한 원자료

- 공식 [Crossref DOI exact API](https://api.crossref.org/works/10.1109%2FCLOUD72782.2026.00057)를 Python `urllib.request.urlopen`으로 조회하여 **HTTP 200** 응답을 실제 받았다. 조회 시각: `2026-10-16T09:13:53.942962+00:00`.
- 응답의 `message.title[0]`은 `AnchorDL: Dual-Locality-based Scheduling for Serverless Inference Workflows`, `message.DOI`는 `10.1109/cloud72782.2026.00057`, **`message.resource.primary.URL`은 `https://ieeexplore.ieee.org/document/11662529/`**이다. 따라서 기존 미확인 사항인 **DOI↔arnumber 11662529** 연결이 출판사 등록 metadata로 확인됐다. [해당 API](https://api.crossref.org/works/10.1109%2FCLOUD72782.2026.00057)
- 가공하지 않은 응답 bytes를 [crossref.json](../../artifacts/anchordl-readiness/crossref.json)에 보존했다. **7,591 bytes**, SHA256 **`434f7afb67bd78309658b9836d230e1aabb7afda95daabc05560d4587c8c9865`**. metadata를 획득한 것이며 논문 본문 획득이 아니다.
- Crossref의 `message.link[0]`에는 `http://xplorestaging.ieee.org/ielx8/11661598/11662351/11662529.pdf?arnumber=11662529`가 있지만, `content-version=vor`, **`intended-application=similarity-checking`**로 등록되어 있다. 이를 일반 공개 원문이나 OA 허가로 간주하지 않았으며 staging 주소에 PDF 다운로드를 시도하지 않았다. [동일 원자료](https://api.crossref.org/works/10.1109%2FCLOUD72782.2026.00057)

## 추가 공개 경로의 실제 확인 수준

| 경로 / 조회 URL | 사용 도구와 실제 결과 | 획득 여부·한계 |
|---|---|---|
| [arXiv API `all:AnchorDL`](https://export.arxiv.org/api/query?search_query=all:AnchorDL&start=0&max_results=5) | `rtk proxy python`·urllib: HTTP 200, Atom `totalResults=0` | 이 검색의 matching entry는 없었다. 다른 제목·후속 등록 가능성까지 배제하지 않는다. |
| [arXiv 검색 UI](https://arxiv.org/search/?query=AnchorDL&searchtype=all&abstracts=show&order=-announced_date_first&size=50) | web open: `Internal Error` | HTML을 획득하지 못했다. API 결과와 접근 실패를 구분한다. |
| Korea University [ScholarWorks](https://scholarworks.korea.ac.kr/)·[dCollection](https://dcollection.korea.ac.kr/) 대상 `"AnchorDL" site:...` 검색 | web search, 그리고 제목+`manuscript`·정확 DOI+`preprint` 검색 | 해당 기관 원문 item URL을 식별하지 못했다. 기관 내부 catalog 전체를 조사했다는 뜻은 아니다. |
| [저자 연구실 회원 페이지](https://ds.korea.ac.kr/members) | web open/find | 저자 소속을 확인했으나 여기서 manuscript·개인 artifact 링크를 확보하지 못했다. 연락은 하지 않았다. |
| [공식 연구실 저장소 root](https://github.com/KU-Cloud/ku-cloud.github.io/tree/9deefe31fefa57a3aa42ef16a452576d7e24605d) | GitHub MCP `get_file_contents`, 공개 REST contents | `_publications`·`assets`·`_data`를 식별했다. 서지·발표 기록은 기존 조사에서 확보한 상태다. |
| [공식 website 전체 tree API](https://api.github.com/repos/KU-Cloud/ku-cloud.github.io/git/trees/9deefe31fefa57a3aa42ef16a452576d7e24605d?recursive=1) | urllib: HTTP 200, `truncated=false` | 이 commit의 파일 경로에서 `anchor`·`.pdf`·`manuscript` 문자열 matching은 없었다. 파일명 검사이며 모든 파일 내용·과거 history·외부 저장소를 부정하지 않는다. |
| [KU-Cloud public repos API](https://api.github.com/orgs/KU-Cloud/repos?type=public&per_page=100) | urllib: HTTP 200, 공개 저장소 7개 metadata 확인 | 이 목록에서 AnchorDL artifact로 식별할 저장소·homepage를 찾지 못했다. 다른 개인 계정·다른 이름의 코드 존재 여부는 미확인이다. |
| [공식 website `_publications`](https://api.github.com/repos/KU-Cloud/ku-cloud.github.io/contents/_publications?ref=9deefe31fefa57a3aa42ef16a452576d7e24605d)·[`_data`](https://api.github.com/repos/KU-Cloud/ku-cloud.github.io/contents/_data?ref=9deefe31fefa57a3aa42ef16a452576d7e24605d) | urllib: HTTP 200, 목록 확인 | 서지 생성 구조를 확인했다. 기존 AnchorDL 서지 항목에 공개 PDF/code URL을 확보하지 못한 상태가 이어진다. |
| [IEEE CLOUD 공식 프로그램](https://services.conferences.computer.org/2026/cloud-program/) | web find | `CLD_REG_116`의 제목·저자를 확인했다. 이 번호는 IEEE 문서 arnumber와 다른 프로그램 ID다. 본문은 확보하지 못했다. |

## 지금 확인 가능한 계약과 재현 전에 필요한 정보

논문 서지와 초록은 [고려대학교 공식 Pure](https://pure.korea.ac.kr/en/publications/anchordl-dual-locality-based-scheduling-for-serverless-inference-/)에서 확인했다. 아래 “미확인”은 미지원·결함을 뜻하지 않는다.

| 계약 항목 | 현재 근거 | 원문·코드에서 확보할 최소 정보 |
|---|---|---|
| 정책 입력·목적 | 데이터 이동과 memory-resident weights에 따른 모델 loading 비용을 함께 고려 | 두 비용의 측정식·단위·상태 관측 시점·예측 오차 처리·실행/queue 비용 포함 여부 |
| look-ahead | 인접 의존성의 후속 비용을 보는 항이 있다는 초록 수준 | 정확 수식·horizon·분기/합류 집계·tie-break·ready-task 순서·복잡도 |
| model locality 상태 | 목적지 메모리에 필요한 weights가 있는지 사용 | cache key·용량·eviction·warm 판정·동시 요청 처리·모델 준비 API·compile/loading 구간 |
| 비교 대상 | 세 baseline 및 greedy joint-cost 비교를 초록이 명시 | 세 baseline의 이름·수식·설정·동일 정보 접근 여부·look-ahead ablation·scheduler overhead |
| 평가 workload | trace 기반 chain·fan-in·fan-out 평가를 초록이 명시 | 실제 trace/seed·도착률·DAG·payload·모델·장치/runtime·warm/reset 절차·반복 통계 |
| 공개 구현·환경 | 공식 실행 코드·본문은 미확보 | artifact URL·license·commit·의존성·실행 명령·원 결과 재현 범위 |

## 재현을 위한 프로젝트 판단

- 현재 정보만으로 AnchorDL 이름을 붙인 구현을 만들면 비용식·horizon을 임의로 채우게 된다. 그런 구현을 원 논문 재현이나 논문 자체의 성능으로 보고하지 않는다. 필요한 경우 **초록에 근거한 별도 joint-cost/look-ahead 운영 기준선**으로 명명하고 가정을 공개한다.
- 원문 확보 후에는 동일 상태·비용 정보와 동일 자원·QoS·관측 구간을 제공하여 비교한다. 실제 데이터 이동, 모델 준비, 전환 중 손실, 정책 계산 시간을 포함한 전체 비용을 측정한다. 이 조건은 **우리 재현 계약 제안**이며 논문이 이미 채택했는지는 미확인이다.
- E2/E3의 실제 환경 재현은 유지할 수 있지만, 데이터 위치+모델 준비 비용을 함께 보았다는 사실만으로 새 연구 공백을 주장하지 않는다. AnchorDL의 원문과 같은 정보를 제공한 look-ahead 기준선에 대조한 뒤 남는 실패 조건을 확인한다.
- 추가 공개 경로 조사는 여기서 종료한다. 다음 진전 조건은 합법적으로 접근 가능한 본문/accepted manuscript/artifact의 실제 링크 또는 사용자가 가진 자료다. 이번 작업에서 새 알고리즘·논문 결과 재현·본문 검토를 완료했다고 주장하지 않는다.
