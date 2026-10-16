# Kubernetes의 DRL 이종 가속기 스케줄링: NPU 선행연구 확인

검색·검토일: 2026-10-16. 원문 확인 범위는 아래에 구분했다. 성능 수치의 재현·상호 비교는 수행하지 않았다.

현재 연구 주제는 **이종 엣지 AI 환경에서 실행 호환성과 데이터 이동 비용을 고려한 동적 워크플로우 배치 최적화**다. 이 문서는 DRL·NPU 선행의 존재와 적용 범위를 확인하는 배경 자료다. DRL은 [첫 비교 실험](workflow-placement-experiment-plan.md)의 비교군이며, 채택할 새 알고리즘으로 확정하지 않는다. DAG/데이터 의존성을 다루는지와 현재 워크플로에 옮길 때 바뀌는 계약을 구현 선정 시 추가 확인한다. [현재 문제 정의](problem-definition.md)

## 결론

**있다.** 특히 EECRL은 DQN, Kubernetes 계열 오케스트레이션, GPU와 실제 Ascend NPU를 함께 다룬 직접 선행이다. 따라서 'Kubernetes에서 DRL로 NPU/이종 자원을 스케줄링한 연구가 없다'는 전제로 연구 공백을 설정하면 안 된다. 다만 kube-scheduler의 일반적인 native framework plugin과, Kubernetes/K3S/KubeEdge·Rancher를 결합한 맞춤 시스템은 구분해야 한다. [EECRL §4·5.2·6.1 원문](https://www.cs.newpaltz.edu/~lik/publications/Wangbo-Shen-CC-2025)

## 조건을 나눈 근거표

| 연구·확인 수준 | 정책과 state/action/reward | 장치·NPU 근거 | Kubernetes 구현·실험 범위 |
|---|---|---|---|
| **EECRL**, Shen et al., *Reinforcement learning-based task scheduling for heterogeneous computing in end-edge-cloud environment*, Cluster Computing 28:179 (2025), DOI `10.1007/s10586-024-04828-2`. **저자 공개 원문 확인** | §5.2 DQN·replay·target network. 상태는 자원/작업 행렬·시계, 행동은 cluster/node/실행방법 배정 또는 대기. 보상은 대기 penalty와 완료·대기시간 개선. | §6.1 cloud Tesla T4, edge Atlas 200 DK의 Davinci/Ascend 310 NPU, end Raspberry Pi. **NPU 단순 언급이 아닌 실장비 testbed**. | §4·6.1 Kubernetes/K3S/KubeEdge·Rancher와 API 배포, npu-smi 수집. device별 image 사전 준비·방식별 runtime 입력. 범용 runtime/state 변환 검증과는 구분. [§3–6.1](https://www.cs.newpaltz.edu/~lik/publications/Wangbo-Shen-CC-2025) |
| **Large-scale Machine Learning Cluster Scheduling via Multi-agent Graph Reinforcement Learning**, Zhao & Wu, IEEE TNSM, DOI `10.1109/TNSM.2021.3139607`. **저자 공개 accepted manuscript 확인** | §IV hierarchical GNN+multi-agent actor–critic. 상태는 topology·GPU group·작업의 graph embedding. 행동은 worker/PS를 GPU group에 배치하거나 다른 scheduler로 전달. 보상은 정규화 학습 진행량. | GPU group/서버 구조·PCIe·네트워크 간섭 고려. **이 원문에서 NPU 실험 확인 안 됨**. | §VI offline simulation 학습·trace 실험, §VII 실제 Kubernetes/Python scheduler가 API로 상태를 읽고 배치. GPU testbed 포함. '에이전트 학습에 GPU만 사용'한 사례와 다름. [§IV·VI·VII](https://i2.cs.hku.hk/~cwu/papers/xyzhao-tnsm22.pdf) |
| **DRL-MLS**, *A reinforcement learning-based approach for scheduling ML training tasks in heterogeneous Kubernetes clusters*, FGCS 182 (2026), 108459, DOI `10.1016/j.future.2026.108459`. **출판사 초록·서론·section preview 확인, 전체 설계/평가 절 미열람** | 개선 DQN·delayed reward·action mask·간섭 reward를 서론에 명시. 입력 자원은 CPU·memory·가용 GPU memory·GPU type, 동작은 node 배치. 정확한 전체 state·reward 식은 **미확인**. | GPU 이종성은 명시. **NPU 지원·실장비 여부는 확인 못 함**. | 출판사 본문 preview는 GPU device plugin 통합·Resource Monitor·실제 9-node Kubernetes 및 추가 simulation 평가를 기술한다. GPU 세부 모델·전체 재현 계약은 추가 확인 필요. [출판사 자료](https://www.sciencedirect.com/science/article/abs/pii/S0167739X26000932) |
| **PPO-LRT**, *Optimization of Task-Scheduling Strategy in Edge Kubernetes Clusters Based on Deep Reinforcement Learning*, Mathematics 11(20), 4269 (2023). **출판사 본문 검색 인덱스의 §3.3 확인; 직접 전체 HTML 열람 실패** | PPO에 least-response-time 보상 결합. CPU load·memory·Pod CPU/memory 요구가 상태, node와 배포/제거가 행동. 부하 불균형·응답시간·과도한 자원사용을 보상에 반영. | CPU/memory 중심의 heterogeneous node 구성. **검토 근거만으로 GPU/NPU-aware로 분류하지 않음**. | Kubernetes API와 HTTP/S로 연결하는 extender 방식이 출판사 본문에 명시됨. VM 기반 cluster 설명은 있으나 가속기 실험 근거는 확보하지 못함. [출판사 §3.3](https://www.mdpi.com/2227-7390/11/20/4269) |

DRL-MLS와 PPO-LRT는 원문 접근 수준이 낮으므로 세부 구현·실험 주장에는 위 제한을 유지한다. 검색 결과를 전체 논문 검증으로 바꾸어 표기하지 않았다. EECRL과 TNSM 논문은 실제 정책·행동·실험 절을 확인했다.

## 우리 연구에서 달라야 할 질문

다음은 신규성 결론이 아닌 **후속 검증 질문**이다.

- 이미 실행 가능한 image/model 조합을 배정하는 문제와, 새 model/runtime 조합을 실제로 준비·검증하는 문제의 경계는 어디인가?
- 신규 작업 배정과 진행 중 요청/학습 상태의 이동은 서로 다른 행동이다. 원 정책을 확장하지 않은 채 후자를 해결 못 한다고 평가하지 않는다.
- 호환성 검사·artifact 생성·loading·상태 변환 비용을 같은 DRL 정책에 입력하는 것만으로 문제가 해결되는가? 그렇다면 새로운 최적화보다 관측·실행 계층의 개선으로 기여를 한정할 수 있다.
- 서로 다른 NPU/GPU runtime에서 실행 가능하다는 판단과 실제 QoS가 어긋나는 최소 사례를 재현할 수 있는가?

EECRL 원문이 명시한 device별 실행 준비와 runtime 정보를 적용한 조건에서도 추가 문제가 남는지 확인해야 한다. migration·compile이라는 검색어가 없다는 이유만으로 기능 부재를 확정하지 않는다.

## 운영 기준선과 검색 한계

HAMi와 Volcano 같은 Kubernetes 가속기 관리/배치 체계는 DRL 여부와 별개로 비교할 운영 계층이다. 여러 vendor 장치를 할당·공유하는 기능은 기존에 있으므로, 'NPU를 Kubernetes에서 쓸 수 있다' 자체를 새 기여로 삼지 않는다. 이 문서에서는 이들을 DRL 연구로 분류하지 않았다. [HAMi v2.6 FAQ](https://project-hami.io/docs/v2.6.0/faq), [Volcano 공식 소개](https://volcano.sh/docs/v1.10.0/home/introduction/)

검색어는 Kubernetes/DQN/PPO/DRL와 heterogeneous/GPU/NPU/Ascend를 조합했다. GPU를 RL 학습에 사용한 것과 GPU를 스케줄링 대상으로 삼은 것을 구별했다. 이번 검토는 전수조사가 아니며, 특정 장치·후속 버전·artifact 성숙도는 독립 재현하지 않았다. 최종 논문에서는 검토한 버전과 deployment 경계를 고정해야 한다.
