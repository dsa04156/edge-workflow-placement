# AnchorDL 비교 준비 점검과 재현 계약

점검일: 2026-10-16. **현재 단계는 비교 준비 점검이며 AnchorDL 구현·성능 비교는 아직 수행하지 않았다.** [공식 초록 검토](ieee-11662529-review.md), [원문 탐색 기록](anchordl-source-acquisition.md), [동결 소스·로그 점검 JSON](../../artifacts/anchordl-readiness/audit.json)을 근거로 한다.

## 1. 확인된 선행과 현재 판단

AnchorDL은 데이터 전송과 목적지의 모델 가중치 상주 여부에 따른 로딩 비용을 함께 다루며, 의존 Task의 후속 비용을 고려하는 look-ahead를 제안한다. 따라서 이 결합 자체나 단순 greedy 보완을 우리 연구의 신규성으로 삼을 수 없다. 정확한 수식·깊이·cache 정책은 초록으로 재구성하지 않는다. [고려대학교 공식 초록](https://pure.korea.ac.kr/en/publications/anchordl-dual-locality-based-scheduling-for-serverless-inference-/)

공식 [Crossref DOI 메타데이터](https://api.crossref.org/works/10.1109%2FCLOUD72782.2026.00057)의 `resource.primary.URL`은 `https://ieeexplore.ieee.org/document/11662529/`다. 사용자 제공 문서번호와 DOI의 직접 연결을 확인했으며 [원응답](../../artifacts/anchordl-readiness/crossref.json)을 보존했다. **논문 식별은 해결됐지만 본문·공식 코드는 아직 확보하지 못했다.**

현재 E2/E3로 확인할 수 있는 것은 실제 입력·준비 상태에 따른 배치 비용의 변화다. 최신 비용·품질·상태 정보를 받은 AnchorDL이 실패한다는 결론은 아직 낼 수 없다.

## 2. 우리 실행기에서 먼저 맞춰야 할 의미

과거 실행은 당시 ConfigMap으로 확인했다. E2는 [workers.json의 ConfigMap](../../artifacts/e2-dynamic/workers.json), E3는 [configmap.json](../../artifacts/e3-input-replication/configmap.json)이 기준이다. 현재 작업 파일이 바뀌어도 이 근거를 대체하지 않는다.

| 점검 항목 | 실제 구현·관측 | 비교에 필요한 처리 |
|---|---|---|
| 모델 준비 상태 | `model_ready`는 해당 `ModelRuntime`의 추론 closure 유무다. `generation`은 준비 횟수다. 모델별 메모리 상주량·용량·cache eviction 상태 표가 아니다. | 이를 AnchorDL의 가중치 메모리 상주 상태와 같은 변수로 바로 치환하지 않는다. 원문의 상태 정의부터 대조한다. |
| 객체 해제 범위 | 양쪽 동결 실행기는 runtime 선택 전에 ORT CPU reference session을 만들며 이를 명시적으로 해제하는 코드가 없다. NPU eviction은 서비스 closure를 해제하고 Core/plugin·driver/OS cache는 유지한다. 실제 잔존 가중치 bytes는 측정하지 않았다. | 현재 비용은 **서비스 객체 재준비 비용**이다. 가중치 전체 부재·장치 reset·완전 cold 비용으로 이름을 바꾸지 않는다. |
| 준비 비용의 내용 | 로컬 모델 파일 read, NPU compile, InferRequest 생성, 고정 dog warmup2회를 측정한다. SDK·Pod 시작·모델 다운로드는 요청의 준비 비용에서 제외된다. | 원문의 model loading 경계와 겹치는 항목·추가 항목·제외 항목을 분리한다. 전체 준비 비용을 원문 로딩 변수에 넣으면 변형 여부를 명시한다. |
| look-ahead의 단위 | E2 `ready_dag`/`ready_greedy`는 준비 추정값을 **4요청**에 나눈다. 작은 배치 공간의 정확 열거를 사용한다. | 요청 간 상각과 DAG 의존성 look-ahead는 구분한다. H=4를 AnchorDL 깊이·알고리즘으로 부르지 않는다. |
| DAG·모델 종류 | 세 Task chain이며 실제 모델 추론은 T2 한 단계다. T1/T3는 CPU 전·후처리다. | 여러 추론 단계·모델별 cache 경쟁·fan-in/fan-out의 효과를 이 실행으로 검증했다고 쓰지 않는다. 확장 DAG는 원문 계약과 실행 검증 뒤 선정한다. |
| 프로파일 범위 | E2는 dog 입력에서 source2별 Task6칸·ingress2칸·간선별4칸과 준비5회로 보정했다. E3는8입력/측정960회지만 allARM/allNPU-server 두 배치만 실측했다. | E3 결과를 본 뒤 입력별 최적 후보 표로 만들어 같은 E3를 새 정책 평가에 재사용하지 않는다. 혼합8배치의 입력별 최신 프로파일도 확보된 것이 아니다. |
| 전송 비용 | `rpc_next_ms`에는 하위 Task 실행과 응답이 포함된다. 이를 빼서 만든 잔여값에도 HTTP·직렬화·응답 처리 등이 남는다. local call에도 잔여값이 있다. | 잔여값은 해당 공통 실행 경로의 관측 비용으로 표기한다. 순수 전송시간·bytes/bandwidth와 동일시하거나 하위 실행을 다시 더하지 않는다. |
| 품질 정보 | E3 NPU Top-1은480/480, Top-5 집합은420/480 일치다. 차이는 camera의 마지막 class다. CPU 예측은 정답 라벨이 아니다. | 응용의 품질 요구와 후보 검증 절차를 평가 전에 정한다. 완료 뒤 불일치 입력을 제외하거나, 사후 관측을 사전 품질 지식으로 제공하지 않는다. |
| 실행·부하 | 전체 계획을 요청 시작 전에 선택해 적용한다. 제한 주기의 순차 요청이며 동시 큐·장기 cache 용량 경쟁을 평가하지 않았다. | 원문의 function별 결정 시점·동시성·대기/용량 모델을 확인한다. 요청 경계 실험을 실행 중 상태 이전으로 부르지 않는다. |

위 내용은 우리 구현의 적용 경계다. AnchorDL의 해당 기능 부재나 실패를 뜻하지 않는다. 점검은 E2 평가1,380회·보정112회와 E3 전체1,344회·측정960회의 로그 및 동결 source hash를 대조했으며 새 클러스터 측정은 추가하지 않았다.

## 3. 본문에서 추출해야 할 최소 계약

각 항목에 논문 쪽수/식·알고리즘 번호와 코드 경로/commit을 붙인다. 코드가 없고 원문으로 결정할 수 없는 세부는 명시적 가정으로 남긴다. 현재 아래 항목은 모두 **미확인**이다.

| 계약 | 확인할 내용 |
|---|---|
| 목적함수 | 전송·로딩·계산·대기 중 실제 포함 항목, 단위·정규화·가중치, 최적화 대상 |
| 결정 절차 | Task 방문 순서, 후보 생성/필터, 전체 계획 또는 개별 function 결정, 동률 처리 |
| look-ahead | DAG 방향·깊이·후속 후보의 평가 방법, 분기/합류의 합·최댓값 등 집계, cache 상태 갱신 가정 |
| 모델 상태 | cache 단위·키·위치, 메모리 한계·eviction 규칙, 모델 공유/복제와 초기 배치 |
| 로딩 비용 | 다운로드·파일 read·장치 전송·compile·warmup의 포함 여부, 중첩·동시 로딩 처리 |
| 환경·관측 | 장치/runtime 이질성, 네트워크 비용 모델, 프로파일 획득/갱신 시각, 미래정보 사용 여부 |
| 실행 의미 | 재최적화 trigger, 실행 중 요청·상태 처리, 큐·경합·용량·실패/재시도 |
| 평가 기준선 | 초록에 언급된 세 baseline의 정확한 정의·설정과 greedy joint-cost ablation |
| 재현 자산 | 원문 버전, 코드 commit·라이선스, dependency lock, 모델·trace·seed·metric 정의 |

본문이 확보되면 NPU/runtime/compile/동적 전환을 **명시적 지원 / 명시적 범위 밖 / 설명 불충분**으로 나눈다. 초록에서 언급이 없다는 이유로 범위 밖 판정을 내리지 않는다.

## 4. 동일 정보 비교를 위한 공통 입력 계약 — 제안

다음은 주 비교를 위한 제안 계약이며 완성된 데이터셋·논문 adapter는 아니다. 그중 기존3Task의 관측→정책→HTTP 연결은 [공통 PolicyRunner](../experiments/common-policy-runner.md)로 구현·로컬 CPU 검증했다. 입력/profile·후보·service readiness·receipt의 일부를 지원하며 아래의 모델별 cache/queue·runtime/품질 증거 전체를 구현한 것은 아니다. 원문의 의미를 확인한 뒤 논문 adapter를 확정한다.

- **DAG·입력:** Task/간선 ID, 모델·artifact hash/버전, 알려진 입력의 크기·schema·발생 위치. 실행 뒤에만 알 수 있는 출력 크기·class·시간은 결정 입력에 넣지 않는다. 필요한 예측값은 사전 보정의 출처와 오차를 붙인다.
- **후보:** Task–노드별 runtime/연산/자료형/품질 검증 결과와 유효 시각. 정보가 없으면 `unknown`으로 기록하며 실행 성공을 가정하지 않는다. 모든 정책에 같은 후보 필터를 적용한 비교임을 명시한다.
- **관측 상태:** 모델/artifact/실행 객체/장치 cache의 서로 다른 상태, 모델별 위치, resource/queue 용량, 관측 시각·generation. 관측할 수 없는 항목을 ready bool에서 추정해 채우지 않는다.
- **예측 비용:** Task 실행, 공통 경로 전달, 준비 단계별 비용과 중첩 가정, 표본 수·profile version. 정책들이 같은 snapshot을 읽게 하되 정책별로 어떤 필드를 실제 쓰는지 남긴다.
- **결정 경계:** 현재 요청·현재까지의 이력만 전달한다. 평가 구간의 미래 source 일정·eviction 일정·실측 최선 배치를 차단한다. snapshot ID와 결정 시작/종료·선택·실제 적용 계획을 기록한다.
- **평가:** 관측·정책 계산·적용·준비·실행·재시도 비용을 포함한 종단 지연과 유효 완료 수를 보존한다. 정책이 만든 cache/queue는 실제 궤적으로 평가하며 E3의 두 anchor 관측을 골라 온라인 실행을 대체하지 않는다.

원 알고리즘을 보존한 재현과 새 정보를 활용하도록 바꾼 확장을 구분한다. 입력 연결·단위 변환만으로 보존할 수 없는 비용식/상태/후보/결정 시점 변경은 **AnchorDL 기반 변형**으로 표기하고 변경점과 각각의 결과를 남긴다. 공통 후보 필터가 제공하는 이점도 알고리즘 자체의 호환성 기능으로 계산하지 않는다.

## 5. 실행으로 넘어가는 순서

1. 원문 PDF 또는 저자 공식 코드에서 §3의 계약을 추출한다. 코드가 없어도 본문으로 충분한 부분은 알고리즘 기반 재구현으로 진행할 수 있으나, 해결되지 않은 선택은 가정으로 공개한다.
2. 그 계약과 공통 실행기의 차이를 표로 고정한다. 원 계약 검증과 이종 runtime 확장 평가를 구분하고 품질·프로파일 보정/평가 입력·비용 경계를 수집 전에 정한다.
3. 승인된 실제 HTTP 경계에서 상태 관측→정책 결정→계획 적용·receipt 연결을 검증한다. 공통 연결부의 로컬 CPU17개 회귀·별도8시도 뒤 [E2 실측 표 연결](../experiments/measured-policy-bridge.md)에서 전체22개 회귀와 실제 ARM/NPU의 동일 snapshot60결정·대표10배치 적용을 확인했다. 원문 정책의 cache miss/후속 의존성을 포함한 작은 검산 사례와 제공 예제는 추가 검증해야 한다.
4. 동일 관측 snapshot의 Task-fastest·greedy·AnchorDL·기존 재최적화/안정화를 실제 실행으로 비교한다. 결과가 같으면 같은 배치를 독립 정책 표본으로 중복 해석하지 않는다. kube-scheduler·Koordinator·DRL의 필수 본 비교도 별도로 완료해야 한다.

현재 즉시 필요한 외부 입력은 **AnchorDL 원문 PDF 또는 원문·공식 구현의 접근 가능한 URL**이다. 비용식·look-ahead·cache·baseline 절만 확보되면 그 범위의 계약 추출부터 이어갈 수 있다. 확보 전에는 추정 알고리즘을 AnchorDL 재현으로 추가하지 않는다.

초기 준비 점검은 문서와 기존 산출물을 대상으로 했다. [당시 검증 기록](../../artifacts/anchordl-readiness/local-validation.json)에서 Markdown8개·로컬 링크58개와 증거253파일의 SHA256, Crossref 원응답의 문서번호 연결을 확인했다. 이후 공통 연결부·E2 실측 표 adapter의 구현과 runtime 검증은 각각의 실행 문서를 따른다.
