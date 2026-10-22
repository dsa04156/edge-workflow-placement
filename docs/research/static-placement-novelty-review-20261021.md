# 정적 배치 후보의 신규성 검토

검토일: 2026-10-21. 대상은 [병목·자원 점유자 기반 부분 배치 탐색 후보](static-placement-neighborhood-candidate.md)다. 문헌과 수학적 구조를 대조했으며 구현·성능 재현·새 클러스터 실험은 하지 않았다. 연구 전체의 가치나 논문 게재 가능성을 판정하는 문서가 아니다.

## 판정

**현재 정의된 후보를 독립적인 새 알고리즘으로 주장할 근거는 부족하다. 기존 LNS에 임계 경로와 자원 점유 정보를 이용하는 설계 후보로 분류하는 것이 타당하다.**

앞선 대화에서 미검증 기여 후보로 남겼던 “목적지의 자원 점유자를 함께 선택한다”에도 직접 선행이 확인됐다. 목표 함수나 실행 계약이 다른 논문들을 완전히 같은 알고리즘이라고 단정하지는 않는다. 그러나 후보의 일반적인 구성과 수식, 3-cycle 설명만으로 신규성을 뒷받침할 수는 없다.

이 검토는 집중된 선행 조사이며 완결된 systematic review가 아니다. 후보의 seed·목적지·점유자 선택 함수와 탐색 예산 배분이 아직 구체화되지 않아, 그 미정 부분의 신규성은 판정할 수 없다.

## 주장별 대조

| 후보의 주장 | 확인한 선행 | 판정 |
|---|---|---|
| 임계 경로·작은 slack에 탐색 집중 | Wong et al. 2009 §III-C; Ding et al. 2022 §IV-B/C | 이미 존재 |
| 목적지의 자원 점유자를 함께 재배치 | Brandt et al. TMS §4.3.3 | 가장 직접적인 중복 |
| 일부 변수 고정 후 선택 변수 최적화 | LNS의 fix/optimize 해석, 정확 repair | 기존 방법의 특수화 |
| 두 작업 교환을 넘어 연쇄·순환 이동 | Yagiura et al. 2004/2006; Ahuja et al. 2000 | 이미 존재 |
| 이종 워크플로의 용량을 지키며 교환하고 전체 makespan 평가 | HetPart2, 2023 §4.2.1 | 직접 워크플로 선행 |
| edge DAG에서 critical/slack 집합만 풀고 나머지 위치 고정 | PAC-LNS, 2026 §4.4 식(43), Algorithm 2 | 결합한 형태에도 근접 선행 |
| 정체 시 변경 집합·크기 확대 | VNS 및 LNS | 확대 자체는 기여 근거 아님 |
| 상주 슬롯·CPU·메모리 합계 제약 | GAP 및 machine reassignment의 정적 누적 용량 | 시간 스케줄과 다르지만 새로운 제약 원리는 아님 |

## 판정을 바꾼 직접 근거

### Target Move Search

Brandt, Speck, Völker의 논문은 온라인 2014-12-24, 최종 권호는 2016년이다. §4.3.3은 비용이 큰 process의 목표 machine과 그곳의 점유 process를 함께 골라 CP로 재배치한다. 후보의 blocker 포함 원리가 이미 있다. 차이는 원문의 load/balance/move 목적·첫 개선 repair와 우리 후보의 DAG 최장 경로·정확 repair다. [저자 원문](https://www.researchgate.net/publication/281935613_Constraint-based_large_neighborhood_search_for_machine_reassignment), [DOI](https://doi.org/10.1007/s10479-014-1772-6).

### PAC-LNS

Jia와 Zhang의 *Budget-Constrained Edge Orchestration of DAG-Based Sensor-Data Workflows with Multicast Result Dissemination*은 2026-10-08 공개됐다. **PMC 본문 §4.4, 식(43)–(49), Algorithm 2**에서 critical/low-slack 작업의 위치를 풀고 외부 위치를 고정한 repair를 확인했다. 대상은 edge DAG이며 전체 시간을 다시 계산한다. repair는 Algorithm 1의 휴리스틱 재구성으로 정확 최적화가 아니다. 용량은 식(16)–(17)의 실행 시간별 점유이고, 최종 multicast와 비용 예산을 함께 다뤄 현재 상주 배치와 구별된다. [공개 본문](https://pmc.ncbi.nlm.nih.gov/articles/PMC13667616/), [DOI](https://doi.org/10.3390/s26196348).

출판사 직접 접근은 웹 도구429/직접 요청403이었다. PubMed XML의 PMCID를 통해 공식 PMC 본문을 확보했으므로 최종 확인 수준은 초록이 아니라 본문이다. 논문의 성능 수치는 재현하지 않았으며 이번 판정에 사용하지 않았다.

### 연쇄 교환과 용량

Yagiura et al. 2006 §3.1은 이전 작업의 자리를 다음 작업이 이어받는 chain과 cyclic 형태를 정의한다. GAP의 제약도 agent별 정적 누적 자원 제약이다. 따라서 연쇄 이동이나 상주 용량 자체로 차별화할 수 없다. DAG의 경로 max·배치 의존 통신은 원문의 합산 목적과 다르지만, 목적 변경만으로 새로운 탐색 원리가 되지는 않는다. [저자 원문](https://leeds-faculty.colorado.edu/glover/article%20-%20Ejection%20Chains%20%26%20Path%20Relinking%20for%20GAP%20YagiuraIbarakiGloverEJOR2006.pdf).

## 수학적 구조의 판정

실행 가능 집합을 F, 현재 배치를 p라 할 때, 후보는 S 밖의 위치를 고정하고 S 안의 배치를 정확히 최적화한다. 이는 S를 풀고 나머지를 고정하는 LNS의 정확 repair에 해당한다. 이 대응은 후보의 수학적 해석이며 선행 구현을 실행했다는 뜻은 아니다. [LNS 저자 설명 §2](https://backend.orbit.dtu.dk/ws/files/5293785/Pisinger.pdf).

모든 크기 k 이하 집합을 탐색하면 현재 배치와 다른 작업 수가 k 이하인 전체 거리 이웃이 된다. 작업-노드 one-hot 표현에서는 변경 작업마다 두 비트가 바뀌므로 binary 거리 한도는 2k다. 선택된 몇 개 집합만 푸는 경우는 그 일부다. 정확 해법과 한 집합의 m^k 열거 상한은 새로운 원리가 아니다. [부분 최적화 검토](novelty-review-partial-optimization-20261021.md)에 수식과 local branching의 차이를 기록했다.

엄격 개선은 고정 유한 비용모형에서 **수용된 갱신 횟수**를 유한하게 한다. 실패한 집합을 계속 생성하는 루프까지 종료시키려면 유한 시간/반복 예산 또는 별도 종료 조건이 필요하다. 제한된 집합 탐색의 종료는 전역 최적성이나 전체 k-이웃의 국소 최적성을 뜻하지 않는다.

앞서 제시한 통신0·단일 chain의 3-cycle 예제는 합산 assignment 문제로 축약된다. 2-swap의 한계를 설명하지만 DAG 특유의 새로운 구조를 입증하지 않는다. [최신 실측](../experiments/budget-completion-20261021.md)에서 1-swap과 정확 열거가 같은 배치를 선택한 사실 역시 더 복잡한 방법의 필요성을 지지하지 않는다.

## 남는 차이와 다음 판단 기준

현재 후보와 선행 사이에는 일반 DAG의 경로 경쟁, 배치 의존 통신, 사전 검증된 runtime 후보, 상주 admission, 정확 부분문제 풀이 등의 계약 차이가 있다. **차이가 존재한다는 사실과 그 차이를 해결하는 새 기법이 있다는 주장을 분리한다.** 기존 요소의 결합도 연구 기여가 될 수 있지만 특정한 새 규칙·성질·효과가 있어야 한다.

후보를 계속 연구한다면 다음이 최소 요구사항이다.

1. 선택 함수의 seed·목적지·부족 자원량·점유자·연쇄 확장·동률·예산·종료 규칙을 명시한다. “중요한 작업을 고른다”는 서술은 알고리즘 명세가 아니다.
2. 기존 TMS의 점유자 선택과 PAC-LNS식 critical/slack 집합 선택을 **같은 정적 배치 목적·실행 가능 계약에 옮긴 기준선**으로 비교한다. 변경된 목적과 repair를 사용하는 경우 원 논문 재현이라고 부르지 않는다.
3. random LNS, critical-only, TMS식 선택, cyclic exchange, 반복 2-swap과 동일 정보·solver·초기해·후보 평가 수 및 wall-clock 예산으로 비교한다. exact solver를 후보에만 제공하는 비교는 선택 규칙의 효과를 분리하지 못한다.
4. 실제 차별성은 새 후보 선정·안전한 가지치기·증분 비용 계산의 구체적 성질과 효율에서 확인한다. 적용 가능한 정리나 반례의 조건을 명시하고 실측/오프라인 결과를 구분한다.
5. 일반 방법의 변형이 같은 비용으로 손실을 없애면 현 후보를 독립 알고리즘 기여로 추진할 근거를 낮춘다. 현재 단계에서는 구현 전에 이 차별 규칙을 정의하는 것이 우선이다.

이번 검토의 처리는 **신규 알고리즘 주장 보류, 기존 방법 기반 후보로 유지**다. 연구 범위·제목을 변경하거나 새 알고리즘 개발을 승인한 결정은 아니다.

## 근거 문서와 접근 범위

- [직접 DAG 선행 검토](novelty-review-dag-placement-20261021.md): Wong2009, HDATS2022, HetPart2 및 인접 문헌. 본문/초록 구분.
- [부분 최적화 검토](novelty-review-partial-optimization-20261021.md): LNS, TMS, RINS, local branching, fix-and-optimize. 원문 접근 제한 명시.
- [용량·연쇄 교환 검토](novelty-review-capacity-chains-20261021.md): ejection chain, cyclic/path exchange, 다자원 GAP. 다자원 GAP 세부 원문 미확보.
- AnchorDL의 본문·공식 코드 미확보 상태는 [기존 재현 계약](anchordl-reproduction-contract.md)을 유지한다. 미확보를 미지원으로 해석하지 않는다.

검색은 일반 방법, DAG/메모리/critical path, edge workflow/LNS의 세 축으로 분리했다. 최종 판정은 저자·출판사·공식 아카이브의 방법 절과 수식에 근거한다. 코드나 실험 artifact는 수정하지 않았다.
