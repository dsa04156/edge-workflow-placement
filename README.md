# 품질·네트워크 인지형 이기종 엣지 AI 파이프라인 오케스트레이션

## 2026-10-26 실측 결과

[Mobilint 실추론 DAG 파일럿](docs/experiments/mobilint-dag-pilot.md): Raspberry Pi의 전처리·후처리와 ser0004의 Mobilint NPU 추론을 연결하고, 같은 원본 모델의 CPU FP32 경로와 비교했다. 총 530/530회 성공. ImageNetV2 200장 기준 종단 지연 중앙값은 Mobilint 19.94ms / CPU 128.80ms, Top-1 정확도는 60.0% / 59.5%다. 단일 순차 실행 진단이며, 배치 정책·goodput 개선이나 품질 우월성을 뜻하지 않는다.

[CPU·Jetson GPU·Intel NPU DAG 비교 결과](docs/experiments/quality-network-baseline-results.md): pilot 36개 구간과 비교 70개 구간을 수집했다. 비교의 마지막 구간은 CPU OOM 영향을 받았고 2개 구간은 미실행이다. 품질 0.355 < 기준 0.40으로 모든 수치는 진단이며, Mobilint 추론은 이 비교에 포함되지 않았다. [공개 증거와 원시 자료 범위](artifacts/quality-network-baselines/publication-20261026/README.md).


**Quality- and Network-Aware Orchestration of Multi-Stage AI Inference on Heterogeneous Edge Nodes**

## 2026-10-23 연구 방향 전환

**AI 서비스가 요구하는 품질을 지키면서, 여러 엣지 장치의 서로 다른 연산 능력과 네트워크를 활용해 더 많은 요청을 제시간에 처리한다.**

이종 노드·품질·네트워크·DAG를 유지하고, 연구의 초점을 실제 다단계 AI 서비스의 실행 시스템으로 옮긴다. 대표 평가 후보는 영상 분석과 음성 인식의 결과를 결합하는 멀티모달 서비스다. 주 지표는 **서비스 품질 기준을 충족한 조건의 초당 기한 내 완료 요청 수(goodput)**이며, 최종 품질·지연·실패·드롭·coverage를 함께 보고한다.

[문제 정의](docs/research/problem-definition.md) · [현재 실험 계획](docs/research/quality-network-experiment-plan.md) · [이기종 실행 설계](docs/research/quality-network-heterogeneous-design.md) · [전체 장비 감사](docs/research/quality-network-heterogeneous-capability-audit.md) · [근접 선행 대조](docs/research/quality-network-orchestration-related-work.md)

본 실험은 **CPU·GPU·NPU를 포함한 이기종 실행**을 대상으로 한다. 보유 장비 전체의 모델·런타임별 실행 가능성을 확인하고 실제 가속기 실행·최종 서비스 품질을 검증한 조합으로 DAG 배치·네트워크·goodput을 비교한다. CPU 전용으로 범위를 축소하지 않는다.

[이기종 실행 구현·검증 기록](docs/experiments/quality-network-heterogeneous.md): 실행 `qnh-20261023t100710`에서 **Intel NPU 영상 → Jetson GPU 음성 → CPU 결합의 실제 혼합 요청 60/60건**을 완료했다. Jetson dev0009의 영상·음성·결합도 각각 60/60건 실행됐고 CUDA 출력·모델 hash·요청·Pod UID를 검증했다. NPU 영상은 60/60건, 별도 음성 probe는 27건 뒤 프로세스 종료로 33건이 실패했다. 5개 구성 모두 최종 평가 품질 0.355 < 0.40으로 본 goodput 비교는 아직 수행하지 않았다. 소유 자원 잔여 0개를 확인했으며, 보호 workload 감사는 별도 완료 Pod 소실로 partial이다. 전체 12개 노드의 CPU·GPU·NPU 검증 목록과 미확보 SDK·점유·장애·실행 실패 상태를 유지하고, 이전 실패 실행과 원본 자료도 보존한다.

이미지·음성 질문 → 결합 응답 VQA용 [CPU 진단 구현](docs/experiments/quality-network-vqa-pilot.md)은 보존한다. 2026-10-23 실행 시도 `qnv-20261023t073121`은 준비 Pod가 배정되지 않아 **추론 0건**으로 종료했고 소유 자원은 정리했다. 같은 시각 제어면 장애 징후를 관측했으나 인과관계는 확정하지 않았다. 기존 reference 출력의 공식 VQA 재채점은 보정 0.515, 평가 0.355로 탐색용 품질 문턱 0.40에 미달했다. 이는 새 추론 결과나 이기종 본 실험 성과가 아니다. 기존 CPU reference 일치와 정답 기반 서비스 품질을 구분하고, 아래 과거 실험의 조건과 한계를 보존한다.


## 2026-10-22 무거운 모델 재실측 (YOLO11m·mpnet)

[무거운 모델 재실측](docs/experiments/multi-edge-sensors-heavy-20261022.md): 같은 엣지 source·정책에서 V=YOLO11m, T=all-mpnet-base-v2로 교체. 64/64 성공·품질 통과·잔여0. E2E 약 290–330ms로 V compute가 지배하며 exact/greedy/task-fastest/server-fixed 차이는 0–2%(SD 이내)로 DAG 공동 배치 이득이 사라졌다. kube-default는 V를 느린 ser0004에 두어 w1/w3에서 약 45–48% 느렸다(프로파일 기반 정책 일반의 이득). cam-e8 RTSP probe 9건 404, dev0006은 capacity로 V/T 후보 제외.

## 2026-10-22 다중 엣지 센서 입력 배치 실측

[다중 엣지 RTSP·텍스트 소스 파일럿](docs/experiments/multi-edge-sensors-20261022.md): Ready9 노드, 엣지 영상3(RTSP)·텍스트2 source, YOLO11n‖MiniLM→fusion DAG, CPU FP32 ONNX. 68/68 성공·품질 통과·잔여0. exact 중앙값은 task-fastest 대비 3.3–5.6%, kube-default 대비 6.4–10.0% 낮지만 n=3·차이가 SD 규모이고 평균 기준 w2는 역전, greedy-network와 거의 동일하다. 동결 PNG 입력 비교이며 실시간 RTSP E2E가 아니다.

## 2026-10-21 기본 스케줄러 비교 소규모 실측

[6정책 소규모 비교](docs/experiments/scheduling-baseline-smoke-20261021.md): 품질108/108, 평가9/9 성공. 10Mbps에서는 모두 서버 배치, 2Mbps에서는 기본 스케줄러의 서버 배치1,427.91ms 대비 프로파일 기반 엣지 혼합934.51ms였다. 단순 greedy도 같은 배치를 골라 새 알고리즘 고유 이득은 없다. 마지막 네트워크 제어 exec EOF와 보호 workload 변화로 전체 감사는 **partial**이며 소유 자원 정리는 잔여0·오류0이다. 조건·배치당3입력의 진단값으로만 보고한다.

## 2026-10-20 단순 보완 기준선 고정

[greedy 교환 후 한 작업 이동](docs/experiments/swap-move-baseline.md)을 오프라인
선택기와 계획 CLI로 구현했다. 기존 동결 정책 소스를 보존하고, 후보 수와
greedy 포함/제외 선택시간·구현 hash를 기록한다. 보존된 두 비용표의 사후
진단 선택을 재현했으며, 단순 보완에도 잔여가 남는 손계산 사례를 테스트했다.
출력은 `execution_ready=false`다. 새 실측 결과가 아니며 제어 연결 검증과
5정책 수집기·감사기 연결은 후속 작업이다.

## 2026-10-20 완주 재시도: 새 보정·첫 블록 완료, 제어 연결 중단

[새 부분 결과와 단순 보완 대조](docs/experiments/budget-completion-attempt.md):
585건 품질 검증 뒤10/2Mbps 첫 블록24/24평가 성공.10Mbps에서 greedy1099.28ms,
정확 열거872.51ms(20.63% 감소), 단순 교환994.45ms(9.54% 감소)였다.
2Mbps는 모두 같은 배치였다. 그러나 **교환 후 작업 하나 이동이라는 사후
비용표 진단도 정확 열거와 같은 배치를 찾았다.** 새 알고리즘 필요성은 미확정이다.
복구 확인의 연속exec EOF로 두 번째 블록은 미실시이며, 후속 제어 진단도
중단됐다. 두 시도 모두 소유 리소스 잔여0. 기존 부분 관측과 합산하지 않는다.

## 2026-10-20 자동 배치 비교와 서버 슬롯 제한 부분 결과

[자동 배치 첫 비교](docs/experiments/owned-hardware-placement-pilot.md)는54/54평가 성공으로 완료했다. [서버 슬롯2개 후속 비교](docs/experiments/greedy-joint-budget.md)는10Mbps 첫 블록18/18평가 후 제어 exec EOF로 중단됐다. 평균 greedy1088.86ms, 정확 열거905.97ms(16.80% 감소), 단순 교환957.34ms(12.08% 감소). 2Mbps 대조·두 번째 블록·마지막 복구 확인은 미완료이며, 소유 리소스 정리는 잔여0이다. 단순 교환도 차이를 상당 부분 줄여 새 알고리즘 필요성이나 동적 순이익을 입증한 결과는 아니다.

## 2026-10-19 공유 WAN·open-loop 부하 재실험 완료

[실제 부하 실험 결과](docs/experiments/topology-profile-load.md): 고정 엣지 source에서 실제 이미지·음성과 ResNet18/Wav2Vec2/MUTAN을 사용해 33조건·606요청을 수집했다. 성공 597건, deadline 시간 초과 3건, 동시 실행 상한 거절 6건을 모두 보존했다. 2Mbps·1rps에서 서버 집중은 33/40 성공·성공 평균94.74초, 혼합은 40/40 성공·평균1.014초였다. 100Mbps 저부하에서는 서버 집중이 더 빨랐다. 성공 출력 품질 불일치0, 소유 리소스 잔여0이며, 공개 파일의 엣지 재생·단일 배포 진단이다. 실제 센서 수집이나 새 정책 우월성 검증은 아니다.

## 2026-10-19 실제 모델 분기·합류 실행 완료

[실제 실행 결과](docs/experiments/topology-profile-smoke.md): Pi2대의 이미지·음성 파일 재생과 ResNet18/wav2vec2/MUTAN을 연결했다. CPU reference60쌍, 두 클러스터 배포24workflow/72receipt에서 실행 오류·reference 대비 최종 답변/Top5순서 불일치0을 확인했다. 엣지 분기 고정 평균1237.69ms, 실제 default-scheduler 서버 배치723.43ms는 순차 smoke 기술 통계이며 제안 정책의 개선 결과가 아니다. 소유 Pod/ConfigMap0개, 보호 Running194개 상태 변화0을 확인했다. 아래 PREPARED 인계 기록은 당시 상태로 보존한다.

## 2026-10-19: DAG·토폴로지·노드 성능 프로파일 실험 인계

현재 사용자 지정 실험 축은 **고정 센서의 DAG를 네트워크 토폴로지와 작업×노드 성능 프로파일로 배치**하는 정적 비교다. 기존 동적 연구·실측 기록은 아래에 보존한다.

- [실제 시스템 실험 절차와 준비 도구](docs/experiments/topology-profile-system.md): 프로파일링 → 동결 → 실제 default-scheduler/성분 제거/제안 배치 → 평가. **PREPARED, 새 클러스터 실행 미실시.**
- [토폴로지·성능 성분 제거 시뮬레이션](experiments/topology-profile-20261019/REPORT.md):144조건×12시드. 자원 점수 비교는 Kubernetes 근사이며 실제 kube-scheduler가 아니다.
- [준비 도구](experiments/real-topology-profile/): 계획/Pod 명세 생성, 노드별 연산 프로파일, 스냅샷, 실패 포함 집계, 로컬 테스트. 실제 모델 adapter와 분기·합류 데이터 경로는 실험실 연결이 필요하다.


## Git 실험의 부하 조건 재분석

[네트워크·요청 부하 재분석과 수정 계획](docs/experiments/topology-profile-load-reanalysis.md): 원 프로파일의 decoded image/PCM과 smoke의 압축 JPEG/FLAC 입력 경계 차이를 확인했다. 공유 WAN100/10/2Mbps·지연·open-loop 요청 부하와 serving queue를 구분해 다음 실험을 준비한다. 원자료 분석과 로컬 protocol 진단이며 새 클러스터 부하 성능 결과는 아니다.

## 2026-10-19 실제 클러스터 적용 계획

후속 [실제 모델 실행 결과](docs/experiments/topology-profile-smoke.md)는 현재 폴더에 통합되어 있다. 두 배포24workflow에서 실행 오류·CPU reference 대비 답변/Top5순서 불일치0, 소유 Pod/ConfigMap0개를 확인했다. 6정책·네트워크 제약 본 비교는 후속이다.

[토폴로지·성능 프로파일 DAG 실행 계획](docs/experiments/topology-profile-cluster-plan.md)은 새 준비 브랜치 `e110d8b`를 분석하고 현재 KubeEdge 장비에 적용할 단계와 통과 조건을 정리한다. Pi2대+x86서버 CPU로 모델·직접 전달·합류를 검증한 뒤 실제 default-scheduler와 성분 제거/단순 기준선을 비교한다. **계획·읽기 전용 점검 완료이며 새 AI 실측 결과는 아니다.** [모델·공식 소스 확인](docs/research/topology-profile-system-source-check.md).

## 이전 배치 연구의 실험 이력

아래 결과와 후속 과제는 당시 실험 범위의 기록이다. 현재 연구 목표와 우선순위는 상단의 2026-10-23 방향 전환 및 문제 정의를 따른다.

## 최신 결과: 센서 입력을 엣지에 고정

[실제 네트워크 제약 실험](docs/experiments/edge-network-placement.md)을 세 번 새 Pod 배포에서 완료했다. 총1,340회 실행(보정864/평가476, 평가 측정340)에서 실패·CPU reference Top5 순서 불일치0을 확인했다.

- 큰 입력·20Mbps: Task-fastest239.12ms → 현재 비용표의 DAG63.05ms(73.63% 감소).
- 중간 입력·20Mbps: native 고정 DAG112.25ms → 갱신 DAG51.75ms(53.89% 감소).
- 중간 입력·50Mbps: 갱신 이득의 방향이 배포마다 달랐다. 작은 입력·native에서는 Task-fastest가 DAG보다 약간 빨랐다.
- 저대역폭에서는 단순 greedy도 DAG와 같은 엣지 배치를 선택했다. 새 알고리즘의 필요성은 아직 입증되지 않았다.

입력은 ARM 엣지 Pod의 JPEG 재생이며 실제 센서 스트림 검증은 아니다. 현재 비용표는 regime별 사전 보정 정보다. 다음 연구 질문은 온라인 관측 지연·보정 획득 비용·변화 지속시간을 포함해 기존 DAG 재최적화와 hysteresis 이후에도 손실이 남는지다. 논문 구현·kube-scheduler·Koordinator·DRL 본 비교는 남아 있다.

## 이전 방향의 작업 범위와 검증 이력

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
| [현재 실험 계획](docs/research/quality-network-experiment-plan.md) | 서비스 품질 계약·고정 모델 실측·goodput·성분 제거·근접 선행 비교 |
| [근접 선행 대조](docs/research/quality-network-orchestration-related-work.md) | LMEdge·OctopInf·JellyBean·Atlas의 범위와 확인 수준 |
| [이전 비교 실험 계획](docs/research/workflow-placement-experiment-plan.md) | 이전 DAG 배치 연구의 계획·검증 이력 |
| [기존 방안 조사](docs/research/heterogeneous-resource-orchestration.md) | 넓은 자원 오케스트레이션 문헌 배경과 출처 |
| [선행연구 비판](docs/research/heterogeneous-resource-orchestration-critique.md) | 기존 가정·범위와 반증 실험; 현재 주제 연결 절 포함 |
| [AnchorDL 근접 선행 대조](docs/research/ieee-11662529-review.md) | 공식 초록의 data/model joint cost·look-ahead, 본문/코드 미확보와 비교 계약 |
| [AnchorDL 비교 준비·재현 계약](docs/research/anchordl-reproduction-contract.md) | 실행기 의미 차이·동일 정보 비교 조건·원문 필요 항목; [탐색 기록](docs/research/anchordl-source-acquisition.md) |
| [Kubernetes DRL·이종 가속기 조사](docs/research/kubernetes-drl-heterogeneous-accelerators.md) | NPU 포함 선행 근거와 확인 수준 |

이전 조사 문서는 출처와 검증 이력을 보존한다. 현재 실험 우선순위는 문제 정의와 품질·네트워크 인지 실험 계획을 따른다. 로컬 Markdown 이슈와 도메인 문서 운영 규칙은 [docs/agents](docs/agents/domain.md)에 있다.


- [보유 장비 자동 배치 파일럿 결과](docs/experiments/owned-hardware-placement-pilot.md): Pi2대+CPU 서버, 압축 입력4조건·고유 배치9개·54/54성공.

- [Sense HAT 실제 데이터 배치 진단](docs/experiments/sensehat-placement-20261021.md): 실제 온도·습도·기압,3배치36회 실행. 엣지 중앙값4.21ms; 외부 Pod 변화로 전체 검증은 미통과.
