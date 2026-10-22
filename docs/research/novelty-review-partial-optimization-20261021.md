# 부분 재최적화·자원 점유자 선택의 신규성 검토

검토일: 2026-10-21. 대상: [정적 배치 후보](static-placement-neighborhood-candidate.md). 범위: 제한된 원문 대조이며 체계적 문헌고찰이나 신규성 부재의 완전한 증명은 아니다. 구현·실험은 하지 않았다.

## 판단

**부분집합 밖의 배치를 고정하고 안에서 정확히 재최적화하는 골격은 기존 LNS/fix-and-optimize이다. 자원 점유자를 함께 선택하는 규칙도 machine reassignment의 Target Move Search(TMS)에 매우 가까운 직접 선행이 있다.** 따라서 현재 정의만으로 새 알고리즘의 신규성을 주장하기 어렵다. 임계 경로와 runtime 호환성에 맞춘 구체적인 선택 규칙의 추가 기여 가능성은 남지만, 그 규칙이 아직 충분히 명세되거나 검증되지 않았다.

## 근거표

| 선행 및 확인 수준 | 직접 확인한 내용 | 후보와 겹침·차이 |
|---|---|---|
| Pisinger & Ropke, **Large Neighborhood Search**, Handbook of Metaheuristics 2판, 2010, pp.399–420. [DTU 저자 원고](https://backend.orbit.dtu.dk/ws/files/5293785/Pisinger.pdf), [대학 서지](https://orbit.dtu.dk/en/publications/large-neighborhood-search-2/). 본문 §2, 원고 pp.9–10 확인. 해당 장은 LNS 제안자의 최초 논문이 아닌 LNS/ALNS 연구자들의 체계 설명이다. | 현재 값의 일부를 고정하고 나머지를 최적화하는 fix/optimize 해석을 명시한다. 최적 repair와 휴리스틱 repair, MIP/CP 사용, 개선만 수용하는 방식, 제거 크기 확대도 설명한다. | \(\mathcal N_S(p)\)와 정확 repair는 이 틀의 직접적인 특수화다. 작은 \(S\) 선택이나 정확 solver 사용 자체는 새 원리가 아니다. |
| Brandt, Speck & Völker, **Constraint-based large neighborhood search for machine reassignment**, Annals of Operations Research 242, 63–91 (2016), 온라인 2014-12-24. [출판사·DOI](https://doi.org/10.1007/s10479-014-1772-6), [저자 업로드 원문](https://www.researchgate.net/publication/281935613_Constraint-based_large_neighborhood_search_for_machine_reassignment). §4.1, §4.2.2, §4.3.3, 원고 pp.8,12,16–17 확인. | TMS는 비용이 큰 process와 목적 machine을 정하고, 목적 machine의 점유 process 7개(7개 이하면 전부)를 함께 선택하여 CP로 재배치한다. 대상 process의 목적지 이동을 강제한다. 일반 탐색은 첫 개선에서 종료한다. 의존 process를 선택 집합에 추가하는 규칙도 있다. | **좋은 목적지의 자원을 비우기 위해 점유자를 공동 변경한다는 메커니즘이 직접 겹친다.** 차이는 load/balance/move cost와 서비스 의존 제약 대 현재의 DAG 최장 경로·전송·runtime 후보다. 임계 경로 seed, 정확 최적 repair, 여러 목적지로의 연쇄 선택까지 동일하다고 단정하지 않는다. |
| Danna, Rothberg & Le Pape, **Exploring relaxation induced neighborhoods to improve MIP solutions**, Mathematical Programming 102, 71–90 (2005), 온라인 2004-05-21. [출판사 초록·DOI](https://doi.org/10.1007/s10107-004-0518-7), [SCIP 공식 구현 설명](https://scipopt.org/doc/html/heur__rins_8h.php). 원 논문 본문은 확보하지 못함. | 출판사 초록은 연속완화로 neighborhood를 정하고 MIP 부분문제로 탐색한다고 설명한다. SCIP RINS는 incumbent와 LP 해의 값이 같은 변수를 고정한다고 명시한다. | 변수 고정+부분문제 풀이가 겹친다. **현재 후보는 LP 합의로 집합을 정하지 않으므로 RINS 그 자체는 아니다.** SCIP 문서를 원 논문 본문 검증으로 대신하지 않는다. |
| Fischetti & Lodi, **Local branching**, Mathematical Programming 98, 23–47 (2003). [출판사·DOI](https://doi.org/10.1007/s10107-003-0395-5), [Lodi의 MIC2003 공식 튜토리얼](https://site.unibo.it/operations-research/en/research/library-of-codes-and-instances-1/mic2003-lb.pdf/@@download/file/mic2003-lb.pdf), PDF 23–24번째 페이지, 식 (7),(8). 원 논문은 초록, 수식은 저자 튜토리얼에서 확인. | incumbent와의 Hamming 거리 제한을 추가하여 MIP solver로 이웃을 탐색한다. 원 방법은 외부 분기 체계도 포함한다. | 변경할 특정 집합을 미리 고정하는 현재 후보와 다르다. 모든 크기 \(k\) 이하 집합의 합집합은 task 변경거리 \(k\) 이웃과 같아진다. 선택된 몇 개 집합만 푸는 방법을 전체 local branching과 동일하다고 하면 부정확하다. |
| Helber & Sahling, **A fix-and-optimize approach for the multi-level capacitated lot sizing problem**, International Journal of Production Economics 123(2),247–256 (2010). [출판사 초록](https://www.sciencedirect.com/science/article/abs/pii/S0925527309003107), [대학 서지·DOI](https://research.uni-hannover.de/en/publications/a-fix-and-optimize-approach-for-the-multi-level-capacitated-lot-s/), DOI 10.1016/j.ijpe.2009.08.022. | 저자 초록은 소수 binary setup 변수만 풀고 나머지는 이전 해의 값으로 고정하며, real 변수는 전체 최적화한다고 명시한다. | 문제 분야와 분할 단위가 다르지만 부분 정수변수 고정·재최적화 골격은 명확히 선행한다. 본문 미확보로 세부 분할 규칙과 후보의 동등성은 판단하지 않는다. |

## 정확한 수학적 대응

후보는 \(S\) 밖의 task 위치를 고정한다.

\[
\mathcal N_S(p)=\{q\in\mathcal F:q_i=p_i\text{ for }i\notin S\}.
\]

이는 task 배치를 변수로 쓰는 LNS에서 \(S\)를 destroy하고 정확 repair하는 경우다. 선택 함수 \(S=g(p,\text{DAG},\text{capacity},\text{runtime})\)가 실제 연구 대상이며 위 수식 자체가 새로운 최적화 원리는 아니다.

현재 후보를 이진 one-hot 변수 \(x_{in}=1[p_i=n]\)로 쓰면 한 task의 위치 변경은 정확히 두 binary 값을 바꾼다. 따라서

\[
\bigcup_{|S|\le k}\mathcal N_S(p)
=\left\{q\in\mathcal F:\sum_i\mathbf1[q_i\ne p_i]\le k\right\}
=\left\{x(q):\|x(q)-x(p)\|_1\le2k\right\}.
\]

이는 **전체 k-task 이웃과 local-branching 거리 제약 사이의 이 문서의 수학적 대응**이다. 목적함수의 MIP 선형화나 solver 실행을 수행했다는 뜻은 아니다. 특정 \(S\) 하나는 이 거리 이웃의 부분집합일 뿐이다.

유한 후보에서 엄격 개선은 유한 횟수의 배치 갱신을 보장한다. 그러나 후보 생성기가 같은 실패 집합을 무한히 되풀이할 수 있다면 탐색 루프의 종료는 별도 stop 조건에 달린다. \(m^k\)는 한 집합의 단순 열거 상한이며, 집합 생성·평가·반복 비용이나 전역 최적성 보장을 주지 않는다. 두 성질 모두 독립적인 신규성 근거가 아니다.

## 아직 남는 기여 후보와 판정 조건

남는 질문은 단순히 blocker를 넣는가가 아니라, **DAG의 병목 전환과 다차원 자원 부족을 함께 반영하여 어떤 seed·목적지·점유자·추가 연쇄를 선택하는가**다. TMS에 DAG 비용을 연결한 변형과 먼저 비교해야 한다. TMS 원 논문이 현재 DAG 목적을 지원하지 않는다고 해서 그 선택 원리가 새로워지는 것은 아니다.

검증을 시작하려면 \(g\)를 재현 가능하게 정의해야 한다: 임계/준임계 기준, 목적지 순위, 필요한 해제 자원량, 점유자 선택·연쇄 확장·tie-break·중복 제거·정지 조건. 같은 크기·시간 예산의 random LNS, 비용 기반 선택, TMS식 점유자 선택, 순환 교환/가변 이웃과 대조하고, 임계 정보와 blocker 정보의 추가 이득을 분리해야 한다. 별도 DAG-specific 규칙과 이득이 확인되기 전의 적절한 명칭은 **병목·자원 정보를 이용한 LNS 설계 후보**다.

## 접근 한계

TMS는 저자 Felix Brandt가 올린 원고 텍스트를 읽었으며 publisher 유료 PDF를 읽은 것은 아니다. 원고 페이지와 최종 저널 페이지는 다르다. LNS 장은 DTU 원문 PDF를 읽었다. RINS 원 논문은 출판사 초록, 알고리즘의 변수 고정 방식은 SCIP 공식 문서로만 확인했다. Local branching 원 논문 본문 대신 원저자의 공식 튜토리얼로 거리 수식을 확인했다. Fix-and-optimize는 검색 도구에 노출된 출판사 초록과 대학 서지를 확인했으며 직접 publisher open은403, EconStor 원고는 bot challenge로 차단되어 본문을 확인하지 못했다. 이 한계를 넘어선 세부 주장이나 성능 수치를 사용하지 않았다.
