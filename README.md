# 이종 엣지 AI 워크플로 배치 연구

## 2026-10-19 공유 WAN·open-loop 부하 재실험 완료

[실제 부하 실험 결과](docs/experiments/topology-profile-load.md): 고정 엣지 source에서 실제 이미지·음성과 ResNet18/Wav2Vec2/MUTAN을 사용해 33조건·606요청을 수집했다. 성공 597건, deadline 시간 초과 3건, 동시 실행 상한 거절 6건을 모두 보존했다. 2Mbps·1rps에서 서버 집중은 33/40 성공·성공 평균94.74초, 혼합은 40/40 성공·평균1.014초였다. 100Mbps 저부하에서는 서버 집중이 더 빨랐다. 성공 출력 품질 불일치0, 소유 리소스 잔여0이며, 공개 파일의 엣지 재생·단일 배포 진단이다. 실제 센서 수집이나 새 정책 우월성 검증은 아니다.

## 2026-10-19 실제 모델 분기·합류 실행 완료

[실제 실행 결과](docs/experiments/topology-profile-smoke.md): Pi2대의 이미지·음성 파일 재생과 ResNet18/wav2vec2/MUTAN을 연결했다. CPU reference60쌍, 두 클러스터 배포24workflow/72receipt에서 실행 오류·reference 대비 최종 답변/Top5순서 불일치0을 확인했다. 엣지 분기 고정 평균1237.69ms, 실제 default-scheduler 서버 배치723.43ms는 순차 smoke 기술 통계이며 제안 정책의 개선 결과가 아니다. 소유 Pod/ConfigMap0개, 보호 Running194개 상태 변화0을 확인했다. 아래 PREPARED 인계 기록은 당시 상태로 보존한다.

## 2026-10-19: DAG·토폴로지·노드 성능 프로파일 실험 인계

현재 사용자 지정 실험 축은 **고정 센서의 DAG를 네트워크 토폴로지와 작업×노드 성능 프로파일로 배치**하는 정적 비교다. 기존 동적 연구·실측 기록은 아래에 보존한다.

- [실제 시스템 실험 절차와 준비 도구](docs/experiments/topology-profile-system.md): 프로파일링 → 동결 → 실제 default-scheduler/성분 제거/제안 배치 → 평가. **PREPARED, 새 클러스터 실행 미실시.**
- [토폴로지·성능 성분 제거 시뮬레이션](experiments/topology-profile-20261019/REPORT.md):144조건×12시드. 자원 점수 비교는 Kubernetes 근사이며 실제 kube-scheduler가 아니다.
- [준비 도구](experiments/real-topology-profile/): 계획/Pod 명세 생성, 노드별 연산 프로파일, 스냅샷, 실패 포함 집계, 로컬 테스트. 실제 모델 adapter와 분기·합류 데이터 경로는 실험실 연결이 필요하다.


**이종 엣지 AI 환경에서 실행 호환성과 데이터 이동 비용을 고려한 동적 워크플로우 배치 최적화**

**Runtime- and Data-Aware Dynamic Workflow Placement for Heterogeneous Edge AI Systems**

핵심 질문은 **개별 Task마다 가장 빠른 노드를 선택하는 것과 DAG 전체를 고려해 실행 위치를 결정하는 것 사이에 실제로 얼마나 차이가 나는가?**이다.

AnchorDL의 데이터·모델 비용과 look-ahead, JellyBean·Atlas의 공동 최적화를 출발점으로 삼아, 네트워크·모델 실행환경이 동적으로 변할 때 기존 DAG 정책의 적용 경계와 실제 손실을 확인한다. AnchorDL은 공식 초록 확인 단계로 본문/코드는 아직 확보하지 못했다. 새 스케줄링 알고리즘이나 DRL의 필요성은 같은 정보를 제공한 비교 실험 결과를 보고 판단한다.

## 최신 결과: 센서 입력을 엣지에 고정

[실제 네트워크 제약 실험](docs/experiments/edge-network-placement.md)을 세 번 새 Pod 배포에서 완료했다. 총1,340회 실행(보정864/평가476, 평가 측정340)에서 실패·CPU reference Top5 순서 불일치0을 확인했다.

- 큰 입력·20Mbps: Task-fastest239.12ms → 현재 비용표의 DAG63.05ms(73.63% 감소).
- 중간 입력·20Mbps: native 고정 DAG112.25ms → 갱신 DAG51.75ms(53.89% 감소).
- 중간 입력·50Mbps: 갱신 이득의 방향이 배포마다 달랐다. 작은 입력·native에서는 Task-fastest가 DAG보다 약간 빨랐다.
- 저대역폭에서는 단순 greedy도 DAG와 같은 엣지 배치를 선택했다. 새 알고리즘의 필요성은 아직 입증되지 않았다.

입력은 ARM 엣지 Pod의 JPEG 재생이며 실제 센서 스트림 검증은 아니다. 현재 비용표는 regime별 사전 보정 정보다. 다음 연구 질문은 온라인 관측 지연·보정 획득 비용·변화 지속시간을 포함해 기존 DAG 재최적화와 hysteresis 이후에도 손실이 남는지다. 논문 구현·kube-scheduler·Koordinator·DRL 본 비교는 남아 있다.

## 현재 작업 범위

- Kubernetes에서 3~5개 Task의 실제 AI 추론 워크플로를 구성한다.
- kube-scheduler·Koordinator·기존 DAG 최적화·DRL 정책을 비교한다.
- Task별 fastest-feasible, 기존 DAG 재최적화, 단순 보완 정책을 두어 차이의 원인을 분리한다.
- 종단 지연·SLO 내 완료율과 데이터 이동·모델 준비·전환·제어 비용을 측정한다.

**E2까지 확인:** KubeEdge CPU·서버 GPU/NPU fixture, [576요청 전달 프로파일](docs/experiments/e0-rpc-profile.md), [세 Task·64배치 정적 검증](docs/experiments/e1-dag.md) 뒤 [입력 위치·NPU 준비 상태 변화 파일럿](docs/experiments/e2-dynamic.md)을 완료했다. 동적 평가 1,380회에서 객체 eviction 조건의 평균 지연은 고정 배치107.08ms, 준비 비용을 포함한 DAG 재배치48.16ms였다. 단순 greedy와 hysteresis도 같은 배치 궤적을 골라 DAG만의 추가 이득이나 새 알고리즘 필요성은 확인하지 못했다. 단일 이미지·공유 worker 결과였으며 EdgeCore와 eth0의 100Mbps 협상값을 확인했다.

**입력 다양화 재현:** [8입력·3회 새 worker 배포](docs/experiments/e3-input-replication.md)에서 전체1,344/측정960회와4,032 stage receipt를 검증했다. 입력별 배치 순위가 달라 dog 비용표의 적용 경계를 확인했다. NPU는 Top-1 480/480 일치였지만 camera의 Top-5가 달라 집합 일치는420/480이었다. 같은 host/날짜·driver cache를 공유하는 재배포로 완전히 독립인 환경이나 정답 accuracy 검증은 아니다. AnchorDL 직접 대조·새 알고리즘 필요성은 미확정이며, 실제 링크/호환성 변경·p95/SLO·네 필수 정책군 비교가 남았다.

**다음 비교 준비:** [AnchorDL 재현 계약](docs/research/anchordl-reproduction-contract.md)에서 동결 소스와 기존 로그를 점검했다. 현재 객체 readiness는 모델 가중치 상주 정보가 아니며, 요청4회 비용 상각도 논문의 DAG look-ahead와 구분해야 한다. Crossref로 DOI와 IEEE 문서번호 연결은 확인했다. 비용식·cache·baseline을 확인할 원문/공식 코드는 아직 필요하다.

**공통 정책 연결:** [관측→정책→HTTP 실행 연결부](docs/experiments/common-policy-runner.md)를 구현하고 전체17개 회귀를 통과했다. 로컬 CPU worker2프로세스에서 읽기 전용 snapshot, 후보/입력 profile 검사, 결정 후 상태 변경 거부와 cold→warm을 확인했다. 비용표는 interface 검증용 synthetic 값이며 새 정책·AnchorDL·클러스터 성능 비교는 아니다.

**실측 표·클러스터 연결:** [E2 비용표 연결 검증](docs/experiments/measured-policy-bridge.md)에서는 같은 snapshot의 여섯 정책 결정60건을 확인하고 고유 대표 배치10+준비2회를 ARM 엣지/Intel NPU 서버에서 실행했다. receipt36개·실제NPU8회·객체 재준비3회를 대조했고 전체22개 회귀를 통과했다. 과거 비용표의 결정/적용 호환성 검증이며 최신 profile·성능 우월성 비교는 아니다.

**최신 보정·동결 평가 완료:** [8입력·3새 배포의 정적 비교](docs/experiments/fresh-placement.md)에서 보정1,536회 후 품질 후보·비용표·계획을 동결하고 평가679회/측정485회·receipt6,645개를 검증했다. camera는 입력을 유지하고 NPU 추론 후보만 사전 제외했다. ARM 엣지 source만 집계한 평균 total은 Task-fastest67.24ms, greedy53.23ms, DAG50.00ms였다. 원 보고서의 양쪽 source 균등 집계는 역사적 진단으로만 유지하며 주 센서 연구 결과로 사용하지 않는다. greedy와 DAG는48조건 중35개에서 같은 배치, 다른13개 중2개에서 DAG의 관측 평균이 더 높았다. 작은 warm3Task chain의 기술 통계이며 AnchorDL 실패·새 알고리즘 필요성은 미확정이다. [비용 경계 후속](docs/experiments/placement-cost-diagnostic.md)960회를 완료했고, state 비용 보완이 선택계획을 바꾼 조건은0/6이었다. 이후 위의 고정 엣지·실제 네트워크 제약 실험으로 확장했다. 첫 시도의 [idle 연결 오류·수정](docs/experiments/transport-idle-diagnosis.md)은 별도 보존했고 v2와 합치지 않았다.

## 문서 안내

| 문서 | 역할 |
|---|---|
| [고정 엣지·전달 제약 비교](docs/experiments/edge-network-placement.md) | 3배포·3입력·3네트워크 조건, 계획 전환과 차이/반례·1,340실행 |
| [비용 경계 후속 진단](docs/experiments/placement-cost-diagnostic.md) | 두 입력960실행, paired anchor·state 비용과 제한 |
| [프로젝트 지침](AGENTS.md) | 연구 범위와 에이전트 작업 원칙 |
| [문제 정의](docs/research/problem-definition.md) | 현재 제목·RQ·제약·비용 경계의 기준 문서 |
| [Task 데이터 전달·EdgeCore 관측](docs/experiments/e0-transport.md) | 네 Service 경로의 실행·무결성·시간과 엣지 제어 경로 관측 |
| [GPU/NPU 실행 검증](docs/experiments/e0-accelerators.md) | 서버 GPU·NPU의 실제 추론, 파생 그래프·준비 비용과 검증 한계 |
| [엣지–가속기 전달 프로파일](docs/experiments/e0-rpc-profile.md) | TCP 조건별 576요청, T2 위치 선택의 holdout 검증 |
| [세 Task 공동 배치 파일럿](docs/experiments/e1-dag.md) | 64배치 보정, B2/C1/정확 열거 비교, EdgeCore·링크 관측 |
| [입력 위치·NPU 준비 상태 변화](docs/experiments/e2-dynamic.md) | 1,380회 동적 평가, 준비 비용 포함 재배치·greedy·hysteresis 비교와 한계 |
| [입력 다양화·새 배포 재현](docs/experiments/e3-input-replication.md) | 8입력/3배포/960측정, 입력별 순위와 NPU Top-5 차이·프로파일 전이 경계 |
| [공통 정책 연결 검증](docs/experiments/common-policy-runner.md) | 읽기 전용 관측·후보/profile 검사·상태 재확인·실제 HTTP와 오류 기록,17개 회귀 |
| [실측 비용표·KubeEdge 연결 검증](docs/experiments/measured-policy-bridge.md) | E2 원 정책 재사용·동일 snapshot60결정·대표10배치 적용·22개 회귀 |
| [최신 보정표·품질 후보 동결 비교](docs/experiments/fresh-placement.md) | 3새 배포·평가679/측정485회, Task-fastest/greedy/DAG의 차이와 역전 사례·비용 경계 |
| [idle 전달 연결 오류 진단](docs/experiments/transport-idle-diagnosis.md) | 첫 실패 보존·중첩 연결 최소 재현·POST 전 연결 교체·회귀 |
| [E0 실행 결과](docs/experiments/e0-kubeedge.md) | 실제 환경 인벤토리·CPU 실행 로그·관측 문제·다음 검증 |
| [첫 비교 실험 계획](docs/research/workflow-placement-experiment-plan.md) | 실제 DAG 후보·기준선·변화 시나리오·측정·진행 조건 |
| [기존 방안 조사](docs/research/heterogeneous-resource-orchestration.md) | 넓은 자원 오케스트레이션 문헌 배경과 출처 |
| [선행연구 비판](docs/research/heterogeneous-resource-orchestration-critique.md) | 기존 가정·범위와 반증 실험; 현재 주제 연결 절 포함 |
| [AnchorDL 근접 선행 대조](docs/research/ieee-11662529-review.md) | 공식 초록의 data/model joint cost·look-ahead, 본문/코드 미확보와 비교 계약 |
| [AnchorDL 비교 준비·재현 계약](docs/research/anchordl-reproduction-contract.md) | 실행기 의미 차이·동일 정보 비교 조건·원문 필요 항목; [탐색 기록](docs/research/anchordl-source-acquisition.md) |
| [Kubernetes DRL·이종 가속기 조사](docs/research/kubernetes-drl-heterogeneous-accelerators.md) | NPU 포함 선행 근거와 확인 수준 |

이전 조사 문서는 출처와 검증 이력을 보존한다. 현재 실험 우선순위는 문제 정의와 첫 비교 실험 계획을 따른다. 로컬 Markdown 이슈와 도메인 문서 운영 규칙은 [docs/agents](docs/agents/domain.md)에 있다.

