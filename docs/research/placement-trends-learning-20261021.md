# 2025–2026 의사결정 중심 측정·학습 및 solver 결합 동향

확인일: 2026-10-21. 상태: 1차 자료 기반 설계 참고이며 방법 채택·구현·클러스터 검증은 아니다. 범위: 최근 학습 기반 최적화 중 현재 정적 배치 연구와 실제로 연결되는 방향. 아래 5건은 초록뿐 아니라 해당 방법 절을 확인했다. 2024까지 검색했지만 더 직접적인 2025–2026 자료를 우선했다. 최신 전체 문헌을 망라했다는 뜻은 아니다.

## 현재 프로젝트에 대한 판단

3Task·실행 가능한 19배치라는 현재 문제에서는 정확 열거를 학습 정책으로 대체할 근거가 없다. 학습할 대상이 필요하다면 배치 자체보다 **어떤 프로파일을 추가로 측정하면 배치 선택의 불확실성이 줄어드는가**를 먼저 검토하는 편이 타당하다. 그러나 이것도 이미 활발한 선행 연구가 있다. 결정 중심 측정 또는 불확실성 인증이라는 명칭 자체를 신규성으로 삼을 수 없다.

아래 근거에서 읽히는 공통 방향은 (1) 예측오차보다 최종 결정 손실을 평가하고, (2) 측정·학습 비용을 고려하며, (3) 학습에 실행 가능성/최적성 판단 전체를 맡기지 않고 solver를 남기는 것이다. 이 해석은 본 프로젝트를 위한 종합 판단이며 특정 논문의 직접 주장과 구분한다.

## 확인한 1차 자료

### 1. OPO: Making Decision-Focused Data Acquisition Decisions

Egon Peršak, Miguel F. Anjos. arXiv v1, **2025-04-21**, preprint. [본문](https://arxiv.org/html/2504.15062v1).

**확인 범위:** §2.1 식(1)–(2), §2.4, §3, §4. 과거 context/parameter 관측으로 제한된 데이터 취득의 선형 surrogate 목적을 학습한다. 취득→예측→최종 최적화의 두 implicit layer를 연결하고 최종 결정 성능으로 취득을 조정한다. 취득 제약은 사전에 주어진다. 평가 사례는 drone reconnaissance가 shortest-path 비용 예측 입력을 제공하는 문제다.

**관련성:** “측정 예산 아래 downstream 결정에 유리한 정보를 고른다”는 발상은 이미 직접 선행이다. 여기의 취득 변수는 학습된 고정 전략이며, noisy latency를 순차 측정하여 특정 배치의 종료 인증을 만드는 계약과 동일하지 않다. OPO의 과거 관측·훈련 요구를 현재의 소수 반복 CPU 측정만으로 충족한다고 가정하지 않는다.

### 2. Decision-Focused Sequential Experimental Design: A Directional Uncertainty-Guided Approach

Beichen Wan, Mo Liu, Paul Grigas, Zuo-Jun Max Shen. arXiv v1, **2026-02-05**, preprint. [본문](https://arxiv.org/html/2602.05340v1).

**확인 범위:** §1.1, §2.2 Algorithm 1, §2.3–2.4, §3.1. Downstream 선형 최적화의 cost-vector 방향 차이를 불확실성으로 삼는다. 현재 predictor confidence set 안의 정규화된 예측 차이로 다음 실험 분포를 정하고, importance-weighted SPO loss로 학습·집합 제거를 진행한다. 유한 hypothesis class 및 risk-gap 등 명시한 가정 아래 보장과 stopping 비교를 제시한다.

**관련성:** 결정에 무관한 큰 예측 분산보다 결정 방향의 불확실성을 보자는 가장 직접적인 최신 근거다. 동시에 “결정 민감 순차 측정”의 신규성 반대 근거다. 선형 objective에 대한 정규화 논리·risk 가정을 DAG의 max-path 비용에 그대로 옮기면 안 된다. 본문의 predictor-set 불확실성은 임의 반복 latency CI와 같은 객체가 아니다.

### 3. Data Informativeness in Linear Optimization under Uncertainty

Omar Bennouna, Amine Bennouna, Saurabh Amin, Asuman Ozdaglar. arXiv 최초 제출 **2026-02-17**, preprint. [서지](https://arxiv.org/abs/2602.15365), [전체 PDF](https://arxiv.org/pdf/2602.15365).

**확인 범위:** §2 Definition 1/Proposition 2, §3.3 Theorem 3, §4, Appendix D Proposition 10. 주 모형은 사전 uncertainty set와 선형 query 결과로 최적 결정 집합을 복원할 수 있는 sufficient dataset을 정의한다. 정보를 구별할 수 없는 비용벡터가 같은 최적 결정을 내리는지가 기준이다. 핵심 설정은 noiseless·비적응 취득이며 combinatorial/noisy 확장도 명시돼 있다. 따라서 “선형 문제라 이산 결정이나 noise를 다루지 않는다”는 주장은 틀리다.

**관련성:** 배치 결정을 확정하는 데 필요한 최소 프로파일이라는 관점과 매우 가깝다. 적응적으로 재측정하는 경로, 공유되는 프로파일 오차와 DAG max-path, 실제 관측시간/불일치 모델의 처리가 차이 후보이나, 확장만으로 신규성을 단정할 수 없다. 본문에 noisy 확장이 있으므로 그 보장까지 대조해야 한다.

### 4. BALANS: Multi-Armed Bandits-based Adaptive Large Neighborhood Search for Mixed-Integer Programming Problems

Junyang Cai, Serdar Kadioğlu, Bistra Dilkina. **IJCAI 2025**, pp.2566–2574. [공식 본문](https://www.ijcai.org/proceedings/2025/0286.pdf).

**확인 범위:** §3–3.2, pp.2568–2569. 초기 MIP 풀이로 쉬운 instance를 먼저 걸러내고, 어려운 instance에 여러 destroy operator를 bandit으로 고른다. repair는 MIP solver가 수행하며 incumbent과 LP relaxation 정보를 재사용한다. 과거 대규모 offline 훈련을 요구하지 않고 현재 instance의 탐색 결과로 operator 선택을 갱신한다.

**관련성:** 최신 ML 최적화가 반드시 큰 신경망·DRL 훈련을 요구하지 않는 반대 근거다. 하지만 이 bandit은 **solver neighborhood**를 선택하므로 실측 프로파일 취득 bandit으로 동일시하지 않는다. 19개 배치에서 적용해야 할 이유는 약하다. 정확 풀이가 실제 병목이 되는 확장 문제라면 초기 baseline 후보가 된다.

### 5. BTBS-LNS: Binarized-Tightening, Branch and Search on Learning LNS Policies for MIP

Hao Yuan et al. **ICLR 2025**. [공식 본문](https://proceedings.iclr.cc/paper_files/paper/2025/file/7eca17ef54789b0663cab421f2e9dbf5-Paper-Conference.pdf).

**확인 범위:** §3, Algorithm 2/3, §4.3 및 §4.5. Tripartite graph와 learned LNS를 사용하되 잘못 고정한 변수를 다시 풀기 위한 branching policy를 추가한다. 학습 label은 best-known 해 또는 local branching 결과에서 수집하고 최종 sub-MIP는 solver로 푼다. 작은→큰 instance generalization, 다양한 MIP instance cross-validation을 실험한다.

**관련성:** graph-guidance+solver는 실제 최신 방법이다. 동시에 학습이 중요한 변수를 놓칠 수 있어 복구 경로가 필요하다는 근거다. 해당 generalization 실험은 새 runtime/장치/네트워크 OOD 보장이 아니다. 이 결과를 3Task 엣지 배치·제한된 CPU 실측에서 학습 우월성의 근거로 전이할 수 없다. 학습·label 생성·solver 시간까지 포함해야 한다.

## 학습을 붙이기 전 최소 조건 — 프로젝트 설계 판단

문헌에서 모든 문제에 통하는 최소 sample 수를 얻을 수는 없다. “N개 이상이면 학습 가능”이라는 숫자를 임의로 정하지 않는다. 현재 동일 상태 반복은 noise 추정에 도움이 되지만 다양한 DAG/runtime의 독립 훈련 instance를 대신하지 않는다.

- 관측 계약: profile ID, task-node/runtime/hash, 입력 특성, source, 연산·전송 항목, 측정 시간·순서와 timeout을 함께 기록한다. 비용표를 공유하는 여러 배치를 독립 학습 표본으로 세지 않는다.
- 검증 분할: 같은 profile snapshot 파생 배치가 train/test 양쪽에 섞이지 않게 한다. 장치·runtime·DAG/입력·날짜 블록 중 무엇에 일반화할지 먼저 정하고 그 단위 holdout을 둔다.
- 최소 비교: uniform acquisition, 최대 uncertainty acquisition, 모델 없는 decision-gap 기반 측정, full profiling+exact enumeration. 이 단순 방법보다 개선된 learning curve가 확인되기 전 큰 학습 모델을 채택하지 않는다.
- 비용: 측정 초·준비/로딩·모델 훈련·inference·계획시간을 각각 보고한다. 요청 수가 정해지지 않았으면 학습비 상각을 임의로 가정하지 않는다.
- fallback: learned score는 측정 순서나 solver warm start에만 영향을 주도록 제한할 수 있다. Runtime 실행 가능성은 실제 검증된 후보 집합에서 판단한다. 낯선 context 또는 검증 실패에서는 추가 측정과 정확 열거로 돌아간다.

## 구간·인증 설계에 필요한 한계

확실한 비용 구간이 존재한다면, 모든 후보의 가능한 비용 범위로 현재 배치가 허용 손실 이내임을 확인하는 것은 학습 predictor 없이도 가능하다. 단, **구간이 현실을 포함한다는 가정**과 **그 구간 안에서 모델을 최적화했다는 사실**은 다르다. 개별 고정시점 CI를 반복 확인해 처음 조건이 맞는 시점에 멈추면 자동으로 순차 유효한 인증이 되지 않는다. Adaptive sampling과 stopping에 유효한 통계 구성, 다중 비교, 시간 상관/공유 부하 및 모델 불일치 처리가 필요하다.

학습 추정치에 기반한 구간만으로 OOD에도 성립하는 인증을 주장하지 않는다. 실측 workflow와 합성 비용식 사이 잔차를 별도로 확인하고, 인증 대상을 비용모형의 기대지연으로 할지 실제 관측 지연/SLO로 할지 먼저 고정해야 한다. 이론 보장과 testbed 측정 성공을 구분한다.

이번 문헌은 **탐색 알고리즘을 크게 바꾸기보다 결정에 필요한 측정과 그 신뢰성을 연구할 방향**을 지지한다. 동시에 OPO·IWSD-DU·Data Informativeness가 이미 그 방향의 핵심 개념을 다루므로, 새로운 이름이나 neural module 추가를 기여로 삼는 것은 피해야 한다.
