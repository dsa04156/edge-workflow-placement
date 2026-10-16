# 고정 엣지 센서의 전달 조건별 배치와 오래된 계획의 손실

Status: ready-for-agent

## Material Passport

- Origin Skill: experimental-design; academic-research-suite / experiment-agent
- Origin Mode: run
- Origin Date: 2026-10-16
- Verification Status: UNVERIFIED
- Version Label: edge_network_placement_v1

## 연구 질문과 완료 판단

센서는 ARM 엣지에 고정한다. Task별 최소 연산시간과 데이터 인지 greedy/DAG 배치의 차이를 실제 노드 간 전달 비용으로 설명할 수 있는가? 전달 조건이 바뀔 때 기본 조건의 DAG 계획을 유지하면 손실이 생기며, 현재 조건의 같은 비용표를 받은 기존 DAG 열거만으로 그 손실을 줄이는가?

의미 있는 결과의 기준은 특정 방법의 승리가 아니라 **세 독립 Pod 배포 구간에서 입력별 선택·전체 지연·원본/중간 데이터 전송량과 배치 전환 경계를 정량화**하는 것이다. 같은 물리 host/date/cache이므로 독립 환경 재현으로 부르지 않는다. 새 알고리즘 필요성이나 논문 구현 대비 우월성은 이 실험만으로 확정하지 않는다. 정책 선택이 같거나 이득이 없는 조건도 전부 보고한다. 첫 완전한 사전 고정 격자 뒤에는 누락/불안정한 질문만 별도 명세로 후속하며 좋은 결과가 나올 때까지 표본을 임의 추가하지 않는다.

## 사전 고정 설계

- source=arm 고정. 입력은 기존 corpus의 rocket(작은 JPEG), chelsea(중간), hubble_deep_field(큰 JPEG)다. 실제 센서 대신 엣지 Pod의 파일 재생을 사용한다. T1/T3 CPU, T2 ARM CPU 또는 Intel NPU. 모델·hash·CPU reference·실행기 버전 유지.
- 네트워크 조건은 native, cap50, cap20. native는 실측 경로이고 100Mbps라고 가정하지 않는다. cap50/cap20은 실험 Pod의 eth0 egress에만 TBF를 설치한다. 원격 노드의 실험 Pod IP와 Service IP만 u32로 필터링하고 같은 노드·cache·운영 workload는 기본 band로 통과시킨다. 세 Pod(ARM client, ARM worker, NPU worker)에 별도 NET_ADMIN helper를 둔다. hostNetwork/hostPID/host NIC 변경은 없다. 대역폭 제한은 방향별·Pod별이며 공유 WAN 전체의 공정 큐를 모사하지 않는다. burst16000 bytes, queue limit2000000 bytes, 손실/지연 주입 없음.
- netem rate 설정 실패는 artifacts/edge-link-preflight에 보존했다. TBF 별도 실제 HTTP preflight는 artifacts/edge-link-preflight-tbf에 보존한다. pyroute2 0.9.4 wheel SHA 고정. 모니터링·관측 제어 트래픽도 원격 목적지에 해당하면 제한된다.
- worker2/client2를 순차3회 새 배포. 각 배포에서 네트워크3조건 × 입력3 × 전체8배치 × (warmup1 sweep+측정3 sweep)=288회 보정한다. regime 순서, sweep 내 입력 순서, 입력 내 배치 순서를 experimental-design assign_factorial_runs로 배정한다. 기본 seed20261301+block*1000, 평가 seed20261351+block*1000. 정확한 배정표는 첫 측정 전 저장한다.
- 모든 조건 보정 후 입력별 T2 품질 후보를 조건 전체에서 공동 판정한다. warmup 포함 모든 출력의 CPU-reference Top5 순서 일치를 요구한다. 불일치 입력을 버리지 않는다. 보정 protocol 오류/빈 후보는 중단하고 보존한다. 예상한 두 후보 모두 통과하지 않으면 이번 완전 격자를 강행하지 않는다.
- regime별 task/ingress/edge 중앙값 비용표를 동결한다. 정책은 task_fastest, data_greedy, current_dag, fixed_native_dag. 앞의 세 정책은 동일 현재 조건 비용표·후보를 받으며, fixed_native_dag는 native 보정에서 고른 계획을 유지한다. 이는 정보 갱신 효과의 대조다. 모든 계획은 평가 전에 계산·동결하고 선택시간은 별도 보고한다.
- 평가 순서는 보정과 독립 배정한다. regime별 입력별 고유 선택계획을 warmup2+측정5 sweep 실행한다. 같은 계획은 관측을 공유한다. 평가 전 baseline/candidate/반복 수는 결과에 따라 바꾸지 않는다. 각 regime의 실제 qdisc 설정/읽기 결과와 단계 전후 counter를 보관한다. 최신 비용표는 사전 보정된 현재 regime의 표를 제공한 이상적인 정보 조건이며 실제 온라인 대역폭 추정/관측 지연 해결을 주장하지 않는다.
- 요청 최소 주기0.25초, 오류/초과/극단값 보존. 주 지표 total_ms와 유효 완료. workflow·관측·state 확인·실제 입력/중간 payload bytes·전달 잔여를 분리한다. 준비·보정 획득·정책 계산·qdisc 적용 시간은 따로 보고한다. per-request total은 계획 조회이며 재배치 deploy 비용이나 온라인 최적화 비용을 포함했다고 주장하지 않는다.
- 배포·입력·regime별 평균/중앙값/SD/range와 paired 정책 차이를 보고한다. 같은 계획의 공유 관측은 독립 표본이 아니다. p값/CI/p95/SLO 우위·동등성 주장은 하지 않는다. 현재3Task chain의 열거는 AnchorDL/JellyBean/Atlas 재현이 아니며 kube-scheduler/Koordinator/DRL 본 비교는 별도다.
- 종료 시 자기 label/이름의 리소스만 정리하고 보호 workload3개의 Ready와 읽기 전용 EdgeCore 상태를 확인한다. 실패한 실행은 덮어쓰지 않는다. 원자료 artifacts/edge-network-placement에 보존한다.

## 검증 경계

기존 합의된 실제 HTTP 경계를 사용한다. 새로운 배치 알고리즘 없이 기존 정책을 그대로 적용한다. 네트워크 제어는 실제 두 노드 Pod의 hash 검증 HTTP payload 전송으로 native→50→20→native 효과와 복귀를 검증했다. 전송 지연을 계산값으로 더하거나 sleep으로 대체하지 않는다. Context7은 quota 초과였으며 공식 pyroute2 문서·고정 wheel 소스·GitHub 구현으로 API를 확인했다.
