# 신규성 검토: 자원 점유 연쇄와 다중 작업 교환

검토일: 2026-10-21. 범위: [정적 배치 후보](static-placement-neighborhood-candidate.md)의 자원 점유자 추적·공동 변경 부분. 알고리즘 구현, 클러스터 실행 또는 성능 재현은 하지 않았다. 원문 3편과 추가 직접 선행의 공식 초록을 구분했다. 문헌 전체의 완전 검색이나 신규성 인증은 아니다.

## 판정

**“A를 옮기려면 B가 자리를 비우고, B의 이동을 위해 C도 바꾼다”는 메커니즘은 ejection chain 및 cyclic/path exchange의 기존 핵심이다. 2-swap 국소 최적점을 3-cycle로 벗어나는 설명만으로 알고리즘 신규성을 주장할 수 없다.** 아래 직접 원문으로 확인했다.

반면 현재 후보는 DAG 최장 경로 비용으로 임계/준임계 작업을 골라 다차원 자원 점유자 집합 S를 만들고, S 밖은 고정한 정확 부분문제를 푼다. 선행의 합산 비용 목적과 다르다. 구체적인 seed·점유자·목적지·분기·중단 규칙이 미정이므로 후보 전체가 기존 특정 알고리즘과 동일하다고 확정할 수도 없다. 현 단계의 정확한 표현은 **기존 연쇄 교환/부분 탐색을 DAG 배치에 적용하려는 미완성 설계**다.

## 직접 선행과 확인 수준

### 1. Yagiura, Ibaraki, Glover (2004), An Ejection Chain Approach for the Generalized Assignment Problem

- 서지: INFORMS Journal on Computing 16(2), 133–151. [DOI](https://doi.org/10.1287/ijoc.1030.0036), [저자 대학 서버 원문](https://leeds-faculty.colorado.edu/glover/335%20-%20An%20Ejection%20Chain%20Approach%20for%20the%20Generalized%20Assignment%20Problem.pdf).
- 접근: 본문 확인. §2 p134 식(1); §3.2 pp135–137; 특히 §3.2.1의 Lagrangian 비용과 §3.2.2 LONG_CHAIN.
- 메커니즘: 작업을 제거해 빈자리를 만든 reference structure에 다른 작업을 이동하고 마지막에 free job을 넣어 완성한다. shift, double shift, long chain을 구분하며 자원 여유와 Lagrangian 상대 비용으로 후보를 제한한다. 따라서 연쇄 이동뿐 아니라 자원·비용 정보를 이용한 후보 축소도 이미 있다.
- 차이: 목적은 \(\sum_{ij}c_{ij}x_{ij}\), 제약은 agent별 \(\sum_j a_{ij}x_{ij}\le b_i\). 시간축 자원 재사용을 표현한 DAG 실행 스케줄이 아니다. 이 정적 누적 용량 구조는 현재 상주 자원 예산과 가까우므로 “상주 슬롯이라 기존 연쇄 교환과 무관하다”는 주장은 부적절하다. 원문 알고리즘은 infeasible 영역을 벌점으로 탐색하고 tabu를 쓰지만 현재 후보는 feasible strict improvement만 수용한다. DAG 통신·경로 max 목적을 이 논문이 직접 해결했다고 해석하지 않는다.

### 2. Yagiura, Ibaraki, Glover (2006), A path relinking approach with ejection chains for the generalized assignment problem

- 서지: European Journal of Operational Research 169(2), 548–569. [DOI](https://doi.org/10.1016/j.ejor.2004.08.015), [저자 대학 서버 원문](https://leeds-faculty.colorado.edu/glover/article%20-%20Ejection%20Chains%20%26%20Path%20Relinking%20for%20GAP%20YagiuraIbarakiGloverEJOR2006.pdf).
- 접근: 본문 확인. §3.1 pp550–552, 특히 p551의 ejection-chain 정의.
- 메커니즘: 여러 작업을 동시에 변경하며 \(\sigma'(j_r)=\sigma(j_{r-1})\), 즉 앞 작업이 비운 agent로 다음 작업을 옮긴다. 첫 작업도 마지막 작업의 이전 agent로 가면 cyclic이다. shift와 swap을 각각 길이1 chain과 길이2 cyclic chain으로 포함한다고 명시한다. Lagrangian 기반으로 double shift와 long-chain 후보 수를 줄인다.
- 판정: “점유 관계를 따라 여러 작업을 묶는다”는 제안에 가장 명료한 직접 대조 정의다. 다만 좋은 해 사이 path relinking, 벌점 조정, GAP 합산 비용을 쓰므로 현재 후보의 전체 정책과 동일하지 않다. 이 논문 자체가 chain neighborhood의 원 출처를 위 2004 논문으로 밝힌다.

### 3. Ahuja, Orlin, Sharma (2000), Very large-scale neighborhood search

- 서지: International Transactions in Operational Research 7, 301–317. [출판사 원문 정보](https://doi.org/10.1111/j.1475-3995.2000.tb00201.x), [공저자 Dushyant Sharma 업로드 본문](https://www.researchgate.net/publication/228580743_Very_large-scale_neighborhood_search).
- 접근: 저자 업로드 본문 텍스트 확인. §3 p303; §3.1 p304의 cyclic/path exchange 정의; §3.3 improvement graph. 웹의 최초 온라인 표시와 권호 출판연도를 혼동하지 않고 2000으로 인용한다.
- 메커니즘: 서로 다른 부분집합에서 원소를 순환 이동시키며 two-exchange가 길이2의 특수 경우임을 명시한다. 각 부분집합의 feasibility를 유지하는 cyclic/path exchange와 improvement graph로 큰 이웃을 암묵 탐색한다. 원소가 한 집합으로 들어가면서 기존 원소가 나오는 arc 해석은 점유자 교체와 직접 대응한다.
- 차이: 일반 partitioning 틀과 capacitated minimum spanning tree 실증이다. 현재 DAG 최장 경로 목적과 같은 공식의 실증이 아니다. 다만 교환 크기 확대·연쇄 관계·큰 neighborhood의 선택적 탐색이라는 일반 논거는 이미 선행에 포함된다.

### 4. Yagiura, Iwasaki, Ibaraki, Glover (2004), A very large-scale neighborhood search algorithm for the multi-resource generalized assignment problem

- 서지: Discrete Optimization 1(1), 87–98. [공식 초록](https://doi.org/10.1016/j.disopt.2004.03.005), [저자 소속기관 보고서 목록 2004-003](https://www.amp.i.kyoto-u.ac.jp/tecrep/TR2004.html).
- 접근: **공식 초록과 저자기관 서지 확인만 완료**. CiteSeer PDF는 HTTP403, 기관 목록의 오래된 PS 링크도 웹 도구로 열리지 않아 정확한 내부 수식·쪽별 동작은 이번에 확인하지 못했다.
- 확인된 범위: multi-resource GAP의 VLSN, improvement graph 탐색, feasible/infeasible 방문 균형을 위한 적응 파라미터를 공식 초록이 명시한다. 따라서 CPU·메모리·슬롯처럼 자원 차원을 늘리는 것만으로 선행과 구별한다고 주장해서는 안 된다. DAG max 목적이나 현재 subset 선정 규칙을 이미 포함하는지는 이 접근 수준으로 판정하지 않는다.

## 현재 3-cycle 예제의 의미

후보 문서의 A→B→C, 통신0, X/Y/Z 각 슬롯1 예제는 단일 경로이므로 목적이 단순히 \(c_A(p_A)+c_B(p_B)+c_C(p_C)\)이다. 이는 일반 DAG의 경로 간 max 경쟁·통신 상호작용 없이 용량1 할당 문제로 축약된다. 15→3의 3-cycle과 26의 pair swap 수치는 이 예제의 자체 계산이며 위 논문의 동일 수치 예제를 인용한 것이 아니다.

따라서 예제는 이웃 포함 관계 \(\mathcal N_{2\text{-swap}}\subset\mathcal N_{\text{cyclic}}\)와 작은 이웃 국소 최적성의 한계를 보여준다. 새로운 순환 교환이나 DAG 고유의 난점을 발견한 증거는 아니다. 실제 다중 경로·통신·자원 상호작용을 포함한 상황에서 이미 알려진 cyclic exchange가 충분한지부터 비교해야 한다.

## 아직 남는 연구 여지와 최소 검증

논문별 목적 차이는 적용을 위한 작업이 필요하다는 뜻이지, 자동으로 신규성을 뜻하지 않는다. 검토를 계속하려면 다음을 먼저 확정해야 한다.

1. 자원 부족 벡터와 DAG slack을 이용해 **어떤 점유자를 어떤 순서로 선택하는지** 명확한 식·의사코드를 정의한다. 여러 작은 작업을 비워야 하는 경우와 분기 연쇄도 처리 범위를 정한다.
2. 알려진 ejection/cyclic-chain 후보를 동일 DAG 전체 비용으로 평가하는 기준선을 둔다. 원문 그대로의 재현과 목적·feasibility를 바꾼 adapted baseline을 구분한다.
3. 같은 부분집합 크기·후보 평가 수 및 wall-clock 예산에서 무작위/임계작업만/점유자만/결합 선택을 비교한다. 계획시간을 포함하지 않은 실행 지연 우위로 탐색 효율을 주장하지 않는다.
4. 이득이 있다면 차별점은 연쇄 이동의 존재보다 **DAG 구조를 사용한 후보 선정·안전한 가지치기·효율적인 정확 비용 갱신의 구체적 성질**에 둔다. 그런 구체 규칙의 기존 연구 중복은 추가 조사 대상이다.

최종 결론: 현재의 광범위한 신규성 주장은 지지되지 않는다. 특히 자원 점유 연쇄·3-cycle은 직접 선행과 겹친다. 아직 정의하지 않은 DAG 전용 subset 규칙이 새롭다는 결론도 현재는 내릴 수 없다.
