## 구현 역할과 인증

- 기본 역할은 Astra의 설계·수용 기준·최종 검증, Claude Opus 5.5의 구현·테스트·디버깅이다. 현재 Codex 모델이 다르면 이를 알리고 임의로 변경하지 않는다.
- Claude 구현 위임은 기존 공식 Claude Code 구독 로그인만 사용한다. API 종량제나 다른 공급자·모델로 자동 전환하지 않는다.
- 호스트는 허용 파일·검증 기준을 전달하고 실제 모델·인증 경로·도구 오류를 확인한다. 기존 변경과 동결 실험 자료를 보존하며 최종 검증과 사용자 요청 범위의 Git 작업을 담당한다.

## 프로젝트 목적

이 폴더는 이종 엣지 AI 환경에서 실제 AI 워크플로의 배치 정책을 비교하고, 네트워크·모델 실행환경이 변할 때 기존 방법의 한계가 드러나는 조건을 검증하는 연구 공간이다.

현재 연구 주제는 **이종 엣지 AI 환경에서 실행 호환성과 데이터 이동 비용을 고려한 동적 워크플로우 배치 최적화**이다.

영문 제목: **Runtime- and Data-Aware Dynamic Workflow Placement for Heterogeneous Edge AI Systems**.

핵심 질문은 **개별 Task마다 가장 빠른 실행 가능 노드를 선택하는 것과 DAG 전체를 고려해 실행 위치를 결정하는 것 사이에 실제로 얼마나 차이가 나는가?**이다. 이어서 네트워크·모델 실행환경의 변화가 기존 DAG 배치 정책에 어떤 손실을 만들고, 기존 재최적화·단순 보완으로도 손실이 남는지 확인한다.

문서 진입점은 `README.md`, 문제 정의는 `docs/research/problem-definition.md`, 첫 비교 실험 계획은 `docs/research/workflow-placement-experiment-plan.md`를 따른다. 실제 환경은 `docs/experiments/e0-kubeedge.md`, 전달 프로파일은 `docs/experiments/e0-rpc-profile.md`, 정적 배치는 `docs/experiments/e1-dag.md`, 입력 위치·준비 상태는 `docs/experiments/e2-dynamic.md`, 입력 다양화·새 배포는 `docs/experiments/e3-input-replication.md`를 확인한다. 기존 KubeEdge 클러스터를 우선 사용하고, 자원 등록과 특정 모델의 실행 성공을 구분한다.

- 최적화에 고려하는 변수를 늘리는 것 자체를 연구 기여로 삼지 않는다.
- 기존 방법이 실패하거나 성능이 저하되는 구체적인 조건을 먼저 확인하고, 그 원인에 필요한 범위에서만 새로운 방법을 설계한다.
- 첫 비교 실험은 Kubernetes 환경의 3~5개 Task로 구성된 실제 AI 추론 워크플로를 대상으로 한다. E0의 SqueezeNet CPU fixture와 검증된 backend는 실행 결과 문서를 따른다. 주 비교의 DAG·모델·장비 부분집합, 실행 도구와 DRL 구현은 아직 확정하지 않았다.
- 센서 입력은 엣지 디바이스에서 발생한다. 주 실험의 source는 엣지로 고정하고, 센서 위치와 T1 실행 위치를 구분한다. 서버 source 실험은 별도 진단으로만 보고하며 엣지 source와 균등 집계한 값을 주 연구 결과로 사용하지 않는다. 현재 JPEG 재생은 엣지 Pod에서 발생시킨 입력이며 실제 센서 스트림 수집을 검증한 것은 아니다.
- kube-scheduler, Koordinator, 기존 DAG 최적화, DRL 정책을 비교한다. Task별 fastest-feasible 정책을 별도로 두어 DAG 공동 결정의 효과를 분리한다.
- DRL은 비교 대상이다. 실험 전부터 새 DRL/GNN 알고리즘의 필요성이나 우월성을 전제하지 않는다.

## 연구의 출발점과 검증 과제

다음은 연구의 출발점이다. 논문별 확인 수준과 출처는 연구 문서에 기록하고, 원문 범위 밖의 미지원·실패를 단정하지 않는다.

이미 연구된 영역으로 보는 출발점:

- 이기종 연산 자원·네트워크·QoS의 공동 최적화
- DNN 분할·오프로딩
- 그래프 기반 배치
- 동적 스케줄링
- GNN/RL 기반 자원 관리
- JellyBean·Atlas의 워크플로 전체 모델 선택·배치 공동 최적화. DAG 전체를 고려한다는 사실 자체를 새 기여로 삼지 않는다.
- AnchorDL의 데이터 전송·모델 상주/로딩 joint cost와 look-ahead 배치도 근접 선행으로 대조한다. 공식 초록 확인과 본문/코드 재현을 구분하며, 본문 미확보 상태에서 NPU·runtime·동적 전환 미지원으로 단정하지 않는다. 검토 상태는 `docs/research/ieee-11662529-review.md`, 실행기 의미·동일 정보 비교 계약은 `docs/research/anchordl-reproduction-contract.md`를 따른다.
- Kubernetes 계열 DRL의 이종 GPU/NPU 배치. EECRL 등의 직접 선행이 있으므로 'NPU 고려 연구가 없다'는 전제를 사용하지 않는다.

추가 검증이 필요한 핵심 질문:

- 어떤 중간 데이터 크기·네트워크 조건에서 Task별 빠른 노드 선택보다 DAG 공동 배치가 유리하며, 차이가 거의 없는가?
- 네트워크 변화·런타임 가용성·모델 준비 상태 변화에서 기존 정책의 실행 가능성과 종단 지연·SLO가 어떻게 달라지는가?
- 최신 관측값을 제공한 기존 DAG 재최적화와 단순 hysteresis를 적용해도 남는 손실이 있는가?
- 전송·준비·재배치·관측·최적화 비용을 포함하면 동적 정책의 이익이 유지되는가?

이 질문들이 기존 연구에서 해결되지 않았다는 점이나 제안 방법의 우월성은 아직 확정된 사실이 아니다. 반대 근거와 이점이 없는 조건도 함께 조사한다.

## 연구 작업 원칙

- 문제 발견 → 선행 연구와의 대조 → 재현 가능한 사례 → 해결 방법 설계 → 비교 검증 순서로 진행한다.
- 사실, 사용자 제공 전제, 연구 가설, 실험 결과를 구분해 기록한다. 기술 조사는 `research` 스킬을 사용하고, 논문 원문·공식 문서·소스 코드 등 1차 자료를 우선한다.
- 배치 문제를 기록할 때는 DAG·Task 입출력·중간 데이터 크기·노드별 실행시간·실행 호환성·모델 준비 상태·네트워크 변화 시점 및 관측 결과를 남긴다.
- 런타임 호환성은 모델 형식의 지원 여부만으로 단정하지 않는다. 해당 사례에서 필요한 연산자, 자료형, 컴파일 산출물 및 상태 이전 가능성을 근거로 확인한다.
- 가속기별 파생 그래프·컴파일 산출물의 hash와 준비 경로를 원본 모델과 연결한다. 가중치 보존과 단일 입력 smoke 통과는 전체 데이터셋의 품질 동등성을 뜻하지 않는다. 진단용 패키지 설치시간을 운영 배포 비용이나 가속기 간 성능 차이로 일반화하지 않는다.
- CPU reference와의 Top-1/Top-5 집합/순서/score 오차를 구분한다. protocol·hash 통과와 수치 품질을 분리하며 불일치 입력을 결과 확인 뒤 제외하지 않는다. 새 Pod 배포가 동일 host·driver/OS cache까지 독립으로 만든다고 가정하지 않는다.
- 같은 DAG 실행기·데이터 경로·입력·품질·자원·실행 가능 후보를 사용해 배치 정책을 비교한다. 모델 선택 효과와 배치 효과, 관측 지연과 정책의 한계, 초기 배치와 재배치 효과를 분리한다.
- DAG 공동 결정 효과를 평가할 때는 복수 Task의 위치가 실제로 달라질 수 있어야 한다. 선택한 배치가 같으면 같은 관측치를 중복 실행/독립 표본으로 세지 않는다. 작은 DAG의 정확 열거는 논문 구현 재현이나 새 알고리즘으로 부르지 않는다.
- Task 데이터 bytes와 관측 trace·HTTP header를 구분한다. 상위 RPC에 포함된 하위 실행을 중복 합산하지 않는다. 통신 잔여 시간을 순수 네트워크 시간으로, NIC 협상값을 실효 throughput으로 단정하지 않는다. source가 바뀌면 과거 검증은 동결 manifest를 기준으로 읽는다.
- 기존 DAG 정책은 정적 계획뿐 아니라 최신 프로파일로 재최적화하는 버전도 비교한다. 원 구현 재현과 아이디어를 옮긴 구현을 구분하며, Koordinator의 사용 플러그인·descheduler 설정과 DRL 학습/추론 비용을 기록한다.
- 중간 데이터 전송과 모델 산출물·상태 전송을 구분한다. 재컴파일·로딩·서비스 중단·재시도·최적화 비용을 포함하며, 중첩 실행 시간을 단순 합산하거나 비용을 중복 계산하지 않는다.
- 서비스 객체 해제·재준비, OpenVINO 파일 cache 비활성, 드라이버/OS cache 초기화와 장치 reset을 구분한다. 입력 발생 위치 변화는 실제 데이터 경로 변화이며 물리 링크 대역폭·지연 변화 실험으로 부르지 않는다.
- 서비스 객체 readiness와 모델 가중치 메모리 상주를 같은 상태로 가정하지 않는다. 준비 비용의 요청 간 상각과 DAG 의존성 look-ahead를 구분하고, 원문과 비용 경계·결정 단위를 바꾼 경우 기반 변형으로 명시한다.
- 정상 실행 구간의 개선과 배포·전환 비용을 포함한 전체 관측 구간의 순이익을 구분한다. 성능 지표, 관측 기간, 반복 측정 방법 및 실질적인 이점의 판단 기준은 실험 전에 정한다.
- 구현이나 실행 환경이 추가되면 실제로 확인한 설치·실행·검증 명령을 문서화한다. 현재 존재하지 않는 코드 구조나 테스트 명령을 가정하지 않는다.
- 기존3Task 정책 연결은 `experiments/e0/policy_runner.py`와 `docs/experiments/common-policy-runner.md`를 따른다. 각 `run()`은 새 관측이므로 동일 형식과 동일 상태 비교를 구분한다. 로컬 CPU HTTP/synthetic 비용표 검증을 클러스터 정책 성능·NPU 검증으로 세지 않는다.
- E2 실측 표·정책 연결은 `experiments/e0/measured_policy_bridge.py`와 `docs/experiments/measured-policy-bridge.md`를 따른다. 같은 snapshot의 결정 대조·대표 배치 적용과 정책별 장기 실행/성능 비교를 구분한다. 과거 profile·초기 hysteresis 상태를 최신 보정·전체 변화 궤적으로 해석하지 않는다.
- 최신 경로의 정적 비교는 `experiments/e0/fresh_placement.py`와 `docs/experiments/fresh-placement.md`를 따른다. 보정→입력별 품질 후보/비용표/계획 동결→별도 평가 순서를 지키고, 같은 계획은 관측을 공유한다. warm 고정 배치·선택시간 별도 측정을 동적 온라인 최적화 비교로 해석하지 않는다. 연결 오류로 중단한 첫 시도는 별도 보존하며 v2의 보정·평가에 합치지 않는다. runtime 소스가 바뀌면 새로 보정한다.
- workflow 비용만 최소화한 계획을 관측·선택 위치의 state_validation까지 포함한 total_ms의 최적해로 부르지 않는다. fresh v2의 hubble 예측 순위 변화·chelsea total 역전은 보정 안정성과 비용 경계의 검증 과제다. 같은 정보의 단순 보완으로 해소 가능한지 확인하기 전에 선행 방법의 실패나 새 알고리즘 필요성으로 단정하지 않는다.

- 고정 엣지·실제 전달 제약의 최신 결과는 `docs/experiments/edge-network-placement.md`다. 현재 regime의 사전 보정 표를 제공한 이상 정보 실험과 온라인 관측/재배치 전체를 구분한다. next step은 관측 지연·획득 비용·변화 지속시간 대조이며 큰 Task-fastest 손실만으로 새 알고리즘 필요성을 주장하지 않는다.
- 새 수집은 phase 시작/종료 event와 timeout 부분 stdout/stderr를 보존한다. Git이 mtime을 보존하지 않으므로 분석에서 checkout 시각을 실행 순서의 근거로 쓰지 않는다. 기존 artifact/동결 소스를 덮어쓰지 않는다.

## Agent skills

### Issue tracker

이슈와 명세는 `.scratch/<feature-slug>/` 아래의 로컬 Markdown 파일로 관리한다. 상세 규칙은 `docs/agents/issue-tracker.md`를 따른다.

### Triage labels

기본 라벨 `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`를 사용한다. 역할 매핑은 `docs/agents/triage-labels.md`를 따른다.

### Domain docs

단일 컨텍스트(single-context) 구조로 루트 `GLOSSARY.md`와 `docs/adr/`를 사용한다. 읽기 및 생성 규칙은 `docs/agents/domain.md`를 따른다.
