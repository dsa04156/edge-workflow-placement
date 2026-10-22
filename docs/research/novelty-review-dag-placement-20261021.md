# 정적 DAG 배치 후보의 신규성 검토 — 직접 선행

검토일: 2026-10-21. 대상: [정적 배치 후보](static-placement-neighborhood-candidate.md)의 임계/준임계 Task 선정, 자원 점유자 포함, 제한된 공동 재배치 및 전체 경로 비용 평가. 이번 문서는 직접 DAG/워크플로 선행 담당 조사이며, 일반 LNS·ejection chain 전체 조사와 합쳐 판단해야 한다. 구현·성능 재현은 하지 않았다.

## 판정

**현재 서술만으로 새 알고리즘의 신규성을 인정하기 어렵다.** 임계 경로 중심 후보 축소, 이종 실행 후보, 메모리 실행 가능성, 교환 후 전체 makespan 평가, 국소 탐색 정체를 벗어나는 perturbation이 직접 선행에 이미 존재한다. 아래 세 논문이 후보와 완전히 같은 문제·절차라는 뜻은 아니다. 특히 현 프로젝트의 상주 서비스 admission 용량을 시간 스케줄의 실행 중 자원 점유와 구분해야 한다. 이 차이는 문제 계약 차이이며 곧바로 알고리즘 신규성의 증거는 아니다.

이번 확인만으로 “임계 경로+자원 점유자+k-Task 정확 재할당”의 완전 동일 선행을 찾았다고 말할 수 없다. 반대로 정확히 같은 명칭의 선행을 찾지 못했다는 것도 신규성 근거가 아니다. 후보 선택 규칙과 그 이점을 더 정밀하게 정의해야 한다.

## 1. Wong et al. (2009): critical-path Tabu Search

**접근 수준:** 저자 Shyh-Hao Kuo가 공개한 전체 본문. [저자 공개 본문](https://www.researchgate.net/publication/221041472_A_Tabu_Search_for_the_Heterogeneous_DAG_Scheduling_Problem), [DOI 10.1109/ICPADS.2009.127](https://doi.org/10.1109/ICPADS.2009.127). 제목: *A Tabu Search for the Heterogeneous DAG Scheduling Problem*.

**정확 위치:** §II, §II-A 식(1)–(5), §III-C1–C4, §III-D.

§II는 task/processor별 실행시간을 두며 실행 불가 조합을 무한 비용으로 나타낸다. 데이터 크기와 processor 간 전송률로 통신 비용을 계산한다. 각 processor는 한 번에 하나의 Task를 실행한다. §II-A의 slack은 makespan에서 top-level과 bottom-level을 뺀 값이다. §III-C1은 임계 경로 Task를 실행 가능한 큐 위치로 옮기고, §III-C2/C3은 대상 processor를 slack 또는 weighted slack으로 축소한다. 이동 평가는 새 disjunctive graph 전체 makespan이다. §III-D의 tabu/aspiration은 단순 엄격 개선과 다른 수용 규칙이다.

**겹치는 연산:** 임계 경로로 search 집중, slack 기반 후보 축소, compatibility 후보, 이동 뒤 전체 DAG 비용 평가.

**실제 차이:** 원문 결정변수는 processor 배정과 큐 내 순서이며 시간별 processor 직렬 실행이 제약이다. 현 후보는 상주 서비스 위치와 정적 CPU/메모리/슬롯 admission을 다룬다. 본문의 move는 task 하나의 큐 삽입이며, 현 후보의 선택된 k개 변수 정확 공동 최적화와 같다고 단정하지 않는다.

**판정:** “임계 경로만 보고 탐색 비용을 줄인다”와 “호환성까지 고려한다”는 독립 기여로 부적절하다.

## 2. Ding et al. (2022): HDATS의 임계 Task·메모리 연동 탐색

**접근 수준:** 저자 arXiv 원문 HTML 및 v1 서지. [원문](https://arxiv.org/html/2206.05268), [v1 서지](https://arxiv.org/abs/2206.05268v1). 제목: *A heuristic method for data allocation and task scheduling on heterogeneous multiprocessor systems under memory constraints*. 이번 확인으로 학술지 게재 상태를 가정하지 않는다.

**정확 위치:** §III-A/B, §IV-B Algorithm 2, §IV-C Algorithm 3.

§III는 이종 distributed shared memory 시스템에서 task-core 배정·실행순서·data-memory 할당을 함께 다루며 makespan을 최소화한다. §IV-B Algorithm 2는 critical task에 N7 및 core-change 후보를 만들고 approximate 평가 상위 K개를 정확 makespan으로 재평가한 뒤 메모리를 재할당한다. 이 K는 공동 변경 Task 수가 아니라 정확 재평가 후보 수다. §IV-C는 critical path에 자주 등장하는 데이터부터 빠른 메모리에 배치하고 용량을 확인한다. 배정 뒤 임계 경로가 바뀔 수 있어 R/Q/slack을 다시 계산한다.

**겹치는 연산:** critical task/blocks, 연산과 데이터 이동의 결합, 메모리 용량, 후보 축소 뒤 정확 평가, 변경 후 임계 경로 재계산.

**실제 차이:** 메모리는 데이터 생애주기별 점유를 확인하고 사용 종료 후 해제한다. 현재의 상주 모델/서비스 메모리 합계 admission과 다르다. core 변경 뒤 greedy memory update이며, 자원 점유 Task들을 골라 고정된 k개 위치를 정확 공동 최적화하는 절차라고 읽으면 안 된다. 원문의 정확 평가는 현재 모델 내부 makespan 계산이지 실측 exact latency 보장이 아니다.

**판정:** “criticality+capacity+정확 평가”의 단순 조합은 이미 매우 근접한 선행이 있다. 데이터 생애주기와 상주 서비스 비용 경계를 보존해 비교해야 한다.

## 3. Kulagina, Meyerhenke, Benoit (2023): HetPart2

**접근 수준:** 출판사 Open Access 전체 본문. 최초 공개일 2023-07-06. [원문/DOI 10.1002/cpe.7842](https://onlinelibrary.wiley.com/doi/full/10.1002/cpe.7842). 제목: *Mapping tree-shaped workflows on systems with different memory sizes and processor speeds*.

**정확 위치:** §3.1–3.3 식(1), §4.2.1 Algorithm 4, §4.2.2 Algorithms 5–6.

§3은 tree를 subtree로 나누고 processor에 배치한다. processor는 한 번에 하나의 Task를 실행하며 network bandwidth는 동질적이다. subtree 실행순서의 최소 메모리가 배치 processor 메모리에 들어가야 한다. 식(1)의 makespan은 입력 전송+subtree 실행시간 합+후속 subtree makespan 최대값이다. §4.2.1의 SwapUntilBest는 메모리상 실행 가능한 모든 쌍 교환을 평가하고 makespan 개선이 최대인 교환을 반복한다. §4.2.2는 ILS 방식의 perturbation 후 hill-climbing을 다시 한다.

**겹치는 연산:** 용량 때문에 제한되는 이종 위치 교환, 전체 경로 비용 재평가, best-improvement, 국소 최적점 탈출.

**실제 차이:** tree와 이미 정해진 subtree partition의 processor 교환이 대상이다. 현 후보는 일반 DAG의 Task별 위치와 task-node별 runtime 후보·상주 CPU/메모리/슬롯, 비동질적 전송비용을 둔다. 원문 메모리는 subtree traversal peak이며 상주 서비스 메모리 합과 다르다. 서로 다른 문제 계약을 이유로 원 방법이 실패한다고 단정할 수 없다.

**판정:** 현재 실험에서 효과를 보인 2-swap을 새 방법으로 제시하기 어렵게 하는 가장 직접적인 현대 워크플로 선행이다. k 증가만으로 이 선행과 일반 VNS를 넘어선다고 볼 수 없다.

## 4. 추가 탐색 결과와 확인 한계

- **AILS (2022):** [출판사 초록·소개](https://www.sciencedirect.com/science/article/pii/S0957417422002809), DOI 10.1016/j.eswa.2022.116824. 예산 제약 아래 workflow makespan 최소화, HEFT 초기해, resource-pool 및 task-order perturbation, intensification을 확인했다. 전체 §4 본문은 이번에 확보하지 않아 세부 이웃이 현 후보와 같다고 판단하지 않는다. ILS를 workflow에 적용하는 것 자체가 새롭다는 주장은 배제한다.
- **PAC-LNS (2026):** 후속 조사에서 [PMC 전체 본문](https://pmc.ncbi.nlm.nih.gov/articles/PMC13667616/)을 확인했다. §4.4 식(43)은 선택 집합 R 밖의 배치를 고정하고 안의 배치를 해제하며, 식(44)은 low-slack Task를 후보로 삼는다. Algorithm 2는 Algorithm 1의 휴리스틱으로 전체 schedule을 재구성한다. 이는 정확 부분문제 solver가 아니다. 용량 제약 식(16)–(17)은 시간별 실행 점유이므로 현재의 상주 서비스 admission과 구분한다. 상세 대조와 통합 판정은 [중앙 신규성 검토](static-placement-novelty-review-20261021.md)를 따른다.
- **JellyBean/Atlas:** [기존 후보 문서](static-placement-neighborhood-candidate.md)의 확인 수준을 유지한다. 전체 워크플로 배치 문제 자체의 선행이며 위 세 논문보다 특정 local move의 직접 증거는 약하다.
- **AnchorDL:** [기존 검토](ieee-11662529-review.md)와 [재현 계약](anchordl-reproduction-contract.md) 기준 본문/공식 코드 미확보 상태를 유지한다. 초록의 joint-cost look-ahead를 근거로 정확한 neighborhood를 추정하지 않는다.

검색은 DAG/critical/neighborhood/memory, workflow/fog/VNS, workflow/edge/LNS, workflow/ILS 및 직접 제목 검색을 사용했다. 초록에 지나지 않는 자료와 본문으로 확인한 연산을 구분했고, 관련 키워드만 맞는 GA·RL 논문은 직접 중복 근거에 포함하지 않았다. 포괄적·완결된 systematic review 또는 재현 연구는 아니다.

## 후보를 유지하려면 필요한 차이

새 이름보다 **선택 규칙의 정확한 정의**가 먼저다. 병목 Task의 목표 노드에서 어느 점유자를 포함하며, 여러 자원이 동시에 부족할 때 최소 이동 집합을 어떻게 고르고, 다른 노드의 연쇄 점유까지 어디까지 추적하는지 명시해야 한다. 임계/준임계 기준과 k 증가 규칙도 고정해야 한다. 이 규칙이 일반 LNS/ejection chain의 그대로인 적용인지 별도 대조해야 한다.

평가는 최소한 memory-feasible 2-swap, cyclic exchange, critical-path local search, 무작위 같은 크기 부분집합+동일 exact solver, VNS/LNS와 동일 정보·계산 예산으로 비교한다. small DAG는 exact gap, 큰 DAG는 wall-clock 및 후보 평가 수를 함께 보고한다. 원 목적함수/실행 계약을 바꾼 baseline은 원 구현 재현으로 부르지 않는다. 점유자 포함 ablation의 추가 이득이 없거나 동일 예산의 일반 탐색이 손실을 해소하면 현 후보의 독립 알고리즘 기여는 약하다.
