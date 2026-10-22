# 정적 배치 재설계의 수학·선행 검토: 선택적 프로파일과 regret 상한

검토일: 2026-10-21. 상태: 설계 검토, 구현·실측·신규성 입증 없음. 이 문서는 동결된 실행 가능 집합에서 배치 전 측정을 선택하는 제안만 다룬다. 운영 중 동적 재배치 보장은 아니다.

## 판정

불확실성 집합을 줄이면서 결정에 필요한 정보만 측정하고, 최악 regret가 허용치 이하가 되면 멈추는 발상은 이미 **minimax-regret elicitation 및 pure exploration**의 핵심이다. 이번에 직접 확인한 고전은 pairwise common-parameter regret, LP 계산, incumbent/witness 기반 query, interval split에 따른 worst-successor regret까지 포함한다. 따라서 이 조합을 처음 제시한 알고리즘이나 새로운 수학 정리로 표현하면 안 된다.

DAG max-of-paths, 공유 파라미터, noisy 비용 측정, 계획·측정 비용을 함께 다루는 구체 적용에는 연구할 여지가 있다. 그러나 이것이 미해결인지와 효율적 해법의 신규성은 추가 검토·실험이 필요하다.

## 1. 목적과 측정 계약

실행 가능 배치 집합 \(\mathcal F\)는 유한하며 사전에 고정한다. runtime, input quality, 메모리/CPU/상주 슬롯 feasibility는 비용 추정과 별도로 검증해야 한다. 미검증 backend를 비용이 싸다는 이유로 후보에 포함하면 아래 보장은 성립하지 않는다.

파라미터 \(\theta\)는 명시된 실험 조건에서의 primitive cost 모집단 p75이다. 기존 profile의 p75 estimand를 유지하며, mean으로 바꾸려면 별도 실험·프로파일 버전으로 명시해야 한다. 각 DAG 경로는 비용 성분을 중복 없이 사용하여

\[
L(p;\theta)=\max_{r\in\Pi_p}(a_{pr}^{\mathsf T}\theta+b_{pr})
\]

로 표현한다. 비음수 시간 성분에 대한 \(a_{pr}\ge0\), 고정 source/sink, 입력 bytes, runtime 상태 및 공유 파라미터 인덱스를 명시한다. primitive mean, p75, 별도 run total 평균을 같은 estimand로 섞지 않는다. 예측기는 primitive p75를 조합한 최장경로 proxy다.

**primitive p75의 합·최장경로는 실제 종단 지연의 p75와 일반적으로 다르다.** 두 독립 branch가 각각 75% 확률로 0, 25% 확률로 1이라면 각 primitive p75는 generalized-inverse 정의에서 0이지만, max branch의 0 확률은 0.5625이므로 종단 p75는 1이다. 평균으로 바꿔도 \(\max\mathbb E X\neq\mathbb E\max X\) 문제가 남는다. 따라서 인증 대상은 primitive-p75 최장경로 proxy의 gap이며 실제 end-to-end mean/p75/SLO가 아니다. 이 예시는 자체 수학 계산이다.

## 2. 가장 단순한 인증은 맞지만 보수적이다

동시에 모든 측정시점에서 참 파라미터를 포함하는 사건

\[
\mathcal E=\{\theta^*\in U_t\ \forall t\},\qquad \Pr(\mathcal E)\ge1-\delta
\]

를 가정한다. 비어 있지 않은 집합에서

\[
\ell_t(p)=\inf_{\theta\in U_t}L(p;\theta),\quad
u_t(p)=\sup_{\theta\in U_t}L(p;\theta)
\]

를 계산하면 어떤 feasible 추천 \(\hat p\)에 대해서도 사건 \(\mathcal E\) 위에서

\[
L(\hat p;\theta^*)-\min_{q\in\mathcal F}L(q;\theta^*)
\le u_t(\hat p)-\min_{q\in\mathcal F}\ell_t(q).
\]

따라서 우변이 \(\epsilon\) 이하면 조건부 proxy \(\epsilon\)-optimality를 말할 수 있다. 이는 상·하한 정의를 잇는 기본 부등식이며 새 정리가 아니다. nested 집합은 이 유효성에 필수는 아니지만 상한 단조 축소에 도움이 된다.

직사각형 \(U_t=[l_t,u_t]\)이고 위 비음수 단조성이 맞으면 \(\ell_t(p)=L(p;l_t)\), \(u_t(p)=L(p;u_t)\). 일반 상관 polytope나 부호가 있는 계수에 이를 그대로 적용하지 않는다. 공유 성분을 양쪽에서 서로 다른 값으로 극대·극소화하므로 매우 보수적이다. 후보가 하나여도 비용구간 폭이 크면 우변이 양수가 된다.

## 3. 같은 파라미터를 쓰는 regret가 더 직접적이다

\[
D_t(p,q)=\sup_{\theta\in U_t}\{L(p;\theta)-L(q;\theta)\},
\quad R_t(p)=\max_{q\in\mathcal F}D_t(p,q),
\quad G_t=\min_{p\in\mathcal F}R_t(p).
\]

\(q=p\)를 포함하므로 \(R_t(p)\ge0\). 추천 \(\hat p\)에 대한 실제 proxy gap은 \(R_t(\hat p)\) 이하이며

\[
R_t(p)\le u_t(p)-\min_q\ell_t(q).
\]

모든 배치에 동일하게 더해지는 불확실 성분 \(h(\theta)\)는 차이에서 정확히 상쇄된다. 상관 여부와 무관하게 같은 물리량은 같은 파라미터로 표현해야 한다. 다만 서로 다른 시간·동시부하 조건의 RPC를 같은 파라미터로 묶는 것은 별도 검증이 필요한 가정이다.

\(U_{t+1}\subseteq U_t\)이고 둘 다 비어 있지 않으면 각 \(R_t(p)\) 및 \(G_t\)는 증가하지 않는다. \(U_t=\varnothing\)이면 참값 포함 사건이 깨졌거나 구현·모형 문제가 드러난 것이므로 이를 0-regret 인증으로 처리하지 않는다.

### 작은 DAG에서 pairwise regret의 정확 계산

\(U_t\)가 compact polytope이면 다음 식이 맞다.

\[
D_t(p,q)=\max_{r\in\Pi_p}\max_{\theta,z}z
\quad\text{s.t.}\quad
z\le(a_{pr}-a_{qs})^\mathsf T\theta+b_{pr}-b_{qs}
\quad\forall s\in\Pi_q,\ \theta\in U_t.
\]

유한 max와 sup를 교환하고 \(a_r^T\theta-\max_s a_s^T\theta=\min_s(a_r-a_s)^T\theta\)를 쓰면 된다. 고정 r당 LP이다. 수식 변형 자체를 새 정리로 삼지 않는다.

전체 pairwise 계산은 대략 \(|\mathcal F|^2\max_p|\Pi_p|\) LP가 필요하며, 각 LP에 challenger 경로 수만큼 제약이 있다. 경로 수와 배치 수는 지수적일 수 있다. challenger 경로 제약의 위반 여부는 해당 θ에서 longest-path 계산으로 찾을 수 있지만, 이를 실제 빠른 알고리즘·확장성 보장으로 부르려면 구현과 평가가 필요하다. 작은 DAG에는 명시 열거가 검증하기 쉽다.

### 최적화 오차의 방향

인증에는 \(R_t(\hat p)\)의 **유효한 상한**이 필요하다. maximization LP의 feasible primal 값이나 일부 challenger만 검사한 값은 하한이므로 인증에 쓰면 안 된다. solver dual bound와 수치 허용오차를 반영한다. 추천 p를 전역 min-regret로 풀지 않아도 그 p의 전체 R 상한이 ε 이하면 인증은 가능하다. 반대로 \(\min_q\ell_t(q)\)를 휴리스틱 후보의 비용으로 대체하면 최소값을 과대평가하므로 단순 gap 인증이 잘못될 수 있다.

## 4. acquisition은 명시하되 예상과 보장을 분리한다

재현 가능한 초기 기준선으로 다음을 사용할 수 있다. 이것은 기존 minimax-regret elicitation의 변형이며 새 기법이라고 주장하지 않는다.

1. 전체 feasible 집합에 대해 G와 추천 p를 구한다.
2. 측정 가능한 primitive j의 구간을 midpoint에서 두 closed half로 나눠 \(U_{j,0},U_{j,1}\)를 만든다. 빈 child는 제외한다. 남은 child들이 U를 덮어야 한다.
3. query cost \(c_j>0\)에 대해

\[
A_j=\frac{G(U)-\max_{h\in\{0,1\}}G(U_{j,h})}{c_j}
\]

를 계산하고 최대값 j를 다음 측정 대상으로 선택한다. 비용 정의와 batch 크기, 동률 순서를 고정한다.
4. 정해진 비율의 측정은 가능한 primitive를 순환 방문하는 탐색으로 배정한다. 모든 A가 0인 정체에서도 측정을 진행할 수 있다. fixed finite budget 내에서 수렴을 보장한다는 뜻은 아니다.

이 score는 **가상의 구간 축소가 일어났을 때**의 최악 regret 개선이다. noisy 측정 결과로 얻는 실제 confidence interval이 두 half 중 어디에도 포함되지 않을 수 있으므로 실제 한 번 측정의 보장된 이익이 아니고, 확률모형을 적분한 expected VOI도 아니다. interval split만을 보고 latency query 한 번으로 구간이 반으로 줄어든다고 가정하지 않는다.

전체 rescore가 비싸면 incumbent와 regret witness의 관련 비용만 대상으로 widest interval을 고르는 기준선을 둔다. 이 역시 아래 AIJ 2006 §5.3과 직접 겹친다. 그런 축소는 query 선택을 위한 휴리스틱일 뿐 최종 전체-regret 검증을 축소하지 않는다. 측정 수뿐 아니라 acquisition 계산·LP·실험 준비 시간을 함께 보고한다.

## 5. 통계 보장의 필수 조건과 fallback

- 매번의 일반 95% CI를 보고 적응적으로 대상을 고르고 멈추면 전체 95% 보장은 자동으로 유지되지 않는다. arm와 sample-count 모두에 대해 simultaneous/time-uniform coverage가 필요하다. confidence sequence 또는 사전 error spending을 쓴다.
- p75를 유지하는 명시 계약 예시: d개의 primitive별 고정-regime i.i.d. 표본으로 empirical CDF \(\widehat F_{j,n}\)를 만든다. \(\alpha_{j,n}=\delta/[d n(n+1)]\), \(e_{j,n}=\sqrt{\log(2/\alpha_{j,n})/(2n)}\)를 쓰는 DKW band가 각 sample count에서 유효하다는 조건이면 arm·n union bound로 전체 실패확률 ≤δ이다. generalized inverse \(\widehat Q(\tau)=\inf\{x:\widehat F(x)\ge\tau\}\)를 써서 \([\widehat Q(.75-e),\widehat Q(.75+e)]\)로 p75를 감싼다. 아래 확률이 0 이하이면 알려진 lower support(비음수 latency는0), 위 확률이 1 이상이면 알려진 upper support 또는 +∞로 보수적으로 확장한다. 누적 교집합은 nested U를 만든다. 이는 조건부 충분조건 예시이며 실제 RPC에 가정을 확인하지 않고 적용하지 않는다.
- DKW quantile interval은 **보간 quantile이 아닌 empirical generalized inverse/order statistic**으로 만든다. atoms가 있는 분포도 위 정의로 다루며 q의 convention을 저장한다. 알려진 finite upper support H는 DKW 자체에 필수가 아니다. \(.75+e<1\)이면 unbounded support에서도 empirical quantile upper가 유한할 수 있다. 초기 구간에서만 +∞가 필요할 수 있다. 따라서 H가 없다고 언제나 finite certificate가 불가능하다고 말하면 안 된다.
- 관측 최댓값을 알려진 H로 취급하지 않는다. timeout으로 cap한다면 실패·timeout을 포함하는 capped-latency p75와 실패율을 사전에 정의하고 uncensored latency와 구분한다. 성공 샘플만 남기는 것은 다른 estimand이며 검열 관측으로부터 실제 tail을 복구했다는 보장을 주지 않는다.
- primitive 간 통계적 상관 자체가 union bound를 깨지는 않는다. 각 marginal confidence sequence의 조건이 맞아야 한다. 시간상 autocorrelation, shared-server drift, 선택시점에 의존하는 평균, 동시전송 경합은 고정 θ 가정과 marginal coverage를 깨뜨릴 수 있다. 시간블록·sentinel·배정 무작위화는 진단/설계 대책이지 보장의 자동 복구가 아니다.
- 공동 시간 block의 관측은 독립 반복 수로 중복 계산하지 않는다. compute와 nested RPC를 분리 관측하지 못하면 같은 합계를 설명하는 θ가 여러 개일 수 있다. identifiability와 관측모형을 먼저 확인한다. underidentified 방향을 표본 수만 늘려 식별했다고 가정하지 않는다.
- fixed budget B 또는 시간한도에 도달하면 현재 추천, 남은 upper regret, 표본 수, 종료이유를 반환한다. R 상한이 ε를 넘으면 **미인증**으로 표기한다. best-so-far 추천은 가능하지만 원하는 정확도로 인증됐다고 쓰지 않는다.
- 참 primitive p75의 추정오차와 surrogate model discrepancy는 구별한다. 실제 목표 T에 대해 모든 후보에서 \(|T(p)-L(p;\theta^*)|\le\eta\)를 별도로 입증했다면 실제 gap ≤ε+2η로 옮길 수 있다. 일부 holdout 배치의 평균 residual만으로 전 후보의 η bound가 생기지 않는다.

## 6. 직접 확인한 선행

### 핵심 직접 선행: Wang & Boutilier (2003)

[Incremental Utility Elicitation with the Minimax Regret Decision Criterion, 저자 PDF](https://www.cs.toronto.edu/~cebly/Papers/minimax.pdf). 본문 확인: PDF p2 §2.1 pairwise max regret의 LP 및 모든 결정 쌍 계산; pp3–4 §3의 직사각형 불확실성 및 query 전략. §3.2는 정확히 \(MMR(C)-\max\{MMR_{yes},MMR_{no}\}\)를 최대화한다. §3.3은 모든 query의 worst-case 개선이 0이어서 멈추는 문제도 논의한다. 그러므로 regret 인증+구간 분할 획득규칙은 기존이다. 사용자 utility의 응답 제약과 noisy latency 표본·DAG max 목적은 다르다.

### 구조적 직접 선행: Boutilier et al. (2006)

[Constraint-based Optimization and Utility Elicitation using the Minimax Decision Criterion, 저자 preprint](https://webdocs.cs.ualberta.ca/~daes/papers/aij06.pdf), [출판 DOI](https://doi.org/10.1016/j.artint.2006.02.003). 본문 확인: preprint printed pp20–21 §4.4 witness constraint generation과 상·하한; printed p36 §5.3 Current Solution Strategy. incumbent와 adversarial witness에 등장하는 파라미터 중 가장 큰 gap의 midpoint를 질의한다. 시간제한으로 min-regret 최적화가 끝나지 않아도 추천 해의 전체 max regret를 계산하여 upper bound로 쓸 수 있다고 설명한다. **witness 중심 선택이나 anytime certificate도 일반적 발상으로는 신규성이 아니다.** 목적은 generalized additive utility이며 DAG max-of-paths와 같다고 주장하지 않는다.

### 통계 근거: Howard et al. (2021)

[Time-uniform, nonparametric, nonasymptotic confidence sequences](https://arxiv.org/pdf/1810.08240), [출판 DOI](https://doi.org/10.1214/20-AOS1991). 본문 확인: 열람 버전 arXiv v9(2022), pp1–3 정의(1) 및 P1–P4. 모든 시점의 동시 coverage와 임의 중단의 관계를 설명한다. 일반 fixed-sample CI와 confidence sequence의 차이를 직접 뒷받침한다. 이 문헌을 인용하는 것만으로 실제 latency의 boundedness/conditional-mean 조건이 충족되는 것은 아니다.

### 최근 직접 대조: Xiong et al. (AISTATS 2024)

[A/B Testing and Best-arm Identification for Linear Bandits with Robustness to Non-stationarity, 공식 본문](https://proceedings.mlr.press/v238/xiong24a/xiong24a.pdf). 본문 확인: p1 문제정의는 fixed budget 및 \(\arg\max_x x^T\sum_t\theta_t\), P1-RAGE. 적응적 선택에서 비정상 환경을 별도 문제로 다뤄야 한다는 최근 대조다. 정적 θ/고정 confidence인 이번 계약과 다르며, 미래의 배치 성능이나 일반 DAG max 목적에 그대로 보장을 옮길 수 없다. 최신이라는 이유로 더 직접적인 2003/2006 minimax 선행을 생략하지 않는다.

### 추가 근접 문헌과 적용 경계

[Huang et al., IJCAI 2018 COCI 본문](https://www.microsoft.com/en-us/research/wp-content/uploads/2018/05/ijcai18_CPE-CS.pdf): §2 p2는 reward의 **additive separability**를 가정하고, Algorithm1/§3 p3은 confidence set에서 최적 결정이 바뀔 수 있는 primitive만 후보로 선택한다. 일반 DAG max-of-paths는 그 separability가 자동으로 성립하지 않는다. 따라서 decision-sensitive sampling은 선행이지만 COCI sample-complexity를 이번 문제에 그대로 인용하지 않는다.

[Du, Kuroki, Chen, NeurIPS 2021 CPE-B 공식 원문](https://proceedings.neurips.cc/paper/2021/file/c92a10324374fac681719d63979d00fe-Paper.pdf): 확인한 초록·도입은 base-arm subset의 최소 평균을 bottleneck reward로 정의하고 fixed-confidence/fixed-budget를 다룬다. 이는 비선형 목적의 pure exploration도 이미 존재함을 보여준다. min-of-base-means와 DAG max-of-path-sums의 동일성은 확인되지 않았다.

## 권고 명세

일단 **“동일 파라미터 기반 regret 검증을 붙인 선택적 사전 프로파일링 기준선”**으로 정의한다. 작은 검증 DAG에서는 전체 feasible 배치와 모든 경로를 사용해 exact pairwise regret를 계산하고, acquisition은 기존 regret-elicitation 변형임을 명시한다. 불확실성 구간의 경험적 안정성 확인과 정식 time-uniform coverage는 결과표에서 구분한다.

가능한 기여를 찾으려면 공유 파라미터·임계경로 전환을 이용해 regret 계산 또는 필요한 측정을 줄이는 **구체적 규칙과 계산량/표본복잡도/실측 이득**을 보이고, minimax elicitation·COCI/CPE-B 계열 적용 기준선과 비교해야 한다. 현재 단계에는 그런 새 규칙·증명·성과가 아직 없다.
