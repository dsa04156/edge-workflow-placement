# 정적 배치를 위한 선택적 측정: 2024–2026 동향과 신규성 위험

검토일 2026-10-21. 연구 메모이며 구현·실험 결과가 아니다. 범위는 **고정 DAG·고정 모델·고정 엣지 source에서, 배치를 선택하기 전에 어떤 성능 항목을 얼마나 측정할지 결정**하는 문제다. 동적 재배치로 범위를 확대하지 않는다. runtime 실행 가능성·품질 통과는 필수 사전 조건이며 선택적 성능 측정의 이름으로 생략할 수 없다.

## 결론

**프로파일을 적게 수집해 좋은 배치를 선택하는 방향은 현재 작은 DAG에서 검토할 만하지만, 이 방향 자체와 정보 가치/측정 비용 기반 종료는 이미 확립된 연구다.** 새 알고리즘이라고 부르기 전, 기존 cost-aware BAI/BO를 적용한 기준선으로 해소되지 않는 문제가 있어야 한다. 원시 task·전송 측정 하나가 여러 배치의 순위에 공유되는 구조, 비선형 최장 경로, 실제 probe 비용과 상관·bias를 다루는 구체적 기여만 후보로 남는다. 이 역시 신규성 확인 완료가 아니다.

## 직접 대조할 1차 자료

| 자료·확인 수준 | 실제 방법 | 현재 설계에 주는 제약 |
|---|---|---|
| **CherryPick**, Alipourfard et al., NSDI2017 pp.469–482. [USENIX 원문](https://www.usenix.org/system/files/conference/nsdi17/nsdi17-alipourfard.pdf). §2.1, §3.5 원문 확인. | BO로 클라우드 설정을 순차 평가하고 좋은 설정을 구별할 정도의 모델 정확도를 목표로 한다. 실행시간 제약을 만족할 확률로 EI를 가중하고 종료 기준을 둔다. 반복 workload에서 검색 비용을 상각한다. | 전체 비용표를 정확하게 만들지 않고 결정에 충분할 만큼만 측정한다는 발상, BO 프로파일링, 검색 비용 상각은 신규성이 아니다. VM 설정 탐색이며 현재 DAG primitive 측정과 동일한 관측 모델은 아니다. |
| **Cost Aware Best Arm Identification**, Kanarios, Zhang & Ying, RLJ4 pp.1533–1545/RLC2024. [공식 PDF](https://rlj.cs.umass.edu/2024/papers/RLJ_RLC_2024_193.pdf), [공식 서지](https://rlj.org/papers/kanarios2024cost). §2–4 확인. | 구현에서 좋은 arm을 선택하되 테스트의 누적 비용을 최소화한다. CTAS는 cost-aware track-and-stop, CO는 계산이 간단한 대안이다. 고정 confidence, 독립 reward/cost 관측·분포 가정이 있다. | 측정 비용과 최종 성능을 분리하는 것은 직접 선행한다. 우리 profiling 시간과 관측 latency가 같은 실행에서 함께 나오면 독립성 가정을 자동 적용할 수 없다. 19개 배치 각각을 arm으로 하는 baseline은 비교적 명확하다. |
| **Cost-aware Bayesian Optimization via the Pandora’s Box Gittins Index**, Xie, Astudillo, Frazier, Scully & Terenin, NeurIPS2024. [공식 원문](https://papers.neurips.cc/paper_files/paper/2024/file/d14c355d5e88cff437a6303d2d716252-Paper-Conference.pdf). §2, §3.2–3.3 pp.2–6 확인. | 평가 비용을 고려하는 acquisition과 종료를 Pandora’s Box/Gittins index로 구성한다. 독립 box에서는 Bayes 최적성이 성립한다. 일반 correlated BO에서는 posterior 갱신 후 상관을 무시한 선택 규칙으로 근사한다. | 정보 가치/비용, 비용을 고려한 종료, 비용 단위 환산 자체는 기여가 아니다. 공유 primitive가 만드는 배치 간 상관이 강하면 독립 box 정리를 그대로 주장할 수 없다. |
| **Balancing Performance and Costs in Best Arm Identification**, Harding & Kandasamy, NeurIPS2025. [공식 원문](https://papers.nips.cc/paper_files/paper/2025/file/b0dfe8236981ba84b4d98a7ed0426f0b-Paper-Conference.pdf), [DOI](https://doi.org/10.52202/085713-4080). §1.1 식(1), §1.2 및 DBCARE 설명 pp.2–3 확인. | 추천 arm의 simple regret 또는 오선택 확률과 누적 sampling cost의 합을 최소화한다. DBCARE는 surviving arms를 동일하게 샘플링하고 confidence 기반 제거 뒤 남은 집합 크기에 따라 예산을 조정·조기 종료한다. | 배치 손실과 측정 비용을 합친 목적함수·가까운 후보를 끝까지 구별하지 않는 결정도 직접 선행한다. 본문 기본 모델은 알려진 일정한 샘플 비용, 독립 sub-Gaussian 관측 및 알려진 noise bound이므로 이질적 probe·상관·드리프트에 바로 정리를 적용하지 않는다. |
| **OSCAR-P and aMLLibrary: Profiling and predicting the performance of FaaS-based applications in computing continua**, Sala et al., JSS221,112282(2025). [출판사](https://www.sciencedirect.com/science/article/pii/S0164121224003261), [DOI](https://doi.org/10.1016/j.jss.2024.112282). 이번 조사에서는 출판사 초록만 확인. | cloud–edge 다중 hardware/node 설정의 serverless workflow component 실행시간을 자동 profiling하고 ML 성능 예측에 사용한다. x86/arm64 multi-component 실험을 보고한다. | 이종 edge/cloud workflow 자동 프로파일링과 미측정 설정 예측 자체도 기존이다. 원문 미확보이므로 decision-aware acquisition이나 stopping의 지원·미지원은 단정하지 않는다. |

2026년 동향으로 **Cost-Aware Exploration for Structured Bandits**(Liu, Qin & You)가 [저자 홈페이지](https://cnyouwei.github.io/)에 working paper로 있다. cost-normalized posterior error exponent, 구조화된 exponential-family 관측, 일반 pure-exploration query를 다룬다는 초록을 확인했다. 검색에 보인 PDF는 2026-02-05 표지였으나 직접 접근은404였고 현재 홈페이지에는 Preparing submission to Operations Research라고 되어 있다. **학술지 게재나 전체 정리 검증으로 세지 않는다.** 구조 공유+측정 비용이라는 넓은 조합까지 신규라고 주장하기 어렵게 하는 추가 대조 대상이다.

## 설계 시 구분해야 할 수학적 문제

아래는 위 선행을 현재 문제에 옮긴 **분석 제안**이며 새로운 정리가 아니다. 품질·runtime 검증을 마친 배치 집합 \(\mathcal F\)에서 primitive 비용 벡터를 \(\theta\), 예측 DAG 비용을 \(L(p;\theta)\)로 둔다. probe \(a\)는 task-node 실행 또는 지정 bytes의 transfer를 관측하며 \(Y_a\), 획득 비용 \(c_a\)를 준다.

- 배치를 통째로 arm으로 측정하면 표준 BAI 기준선을 만들기 쉽지만, 공유 primitive 정보의 재사용을 버린다.
- primitive를 측정하고 모든 배치의 값을 갱신하면 structured pure exploration에 가깝다. 배치의 비용은 경로합의 최댓값이므로 globally linear-arm 보장은 자동으로 성립하지 않는다.
- 각 실행의 \(\mathbb E[\max(A,B)]\)와 \(\max(\mathbb E[A],\mathbb E[B])\)는 일반적으로 다르다. 평균 primitive를 넣은 비용표를 실제 평균 종단 latency와 동일시하면 안 된다.
- primitive가 공유된다고 measurement noise가 독립인 것은 아니다. 같은 실행의 stage receipt와 상위 RPC, 같은 서버·시간대의 관측을 독립 표본으로 부풀리지 않는다.

정적 배치 이후 동일 조건에서 \(H\)개 요청을 처리한다고 미리 정하면, 평가할 risk의 한 예는

\[
J=\mathbb E[C_{\mathrm{probe}}+C_{\mathrm{select}}]
+H\,\mathbb E[L(\hat p;\theta)-\min_{p\in\mathcal F}L(p;\theta)].
\]

이 식의 형태는 cost-aware BAI와 겹친다. 실제 latency 총합으로 해석하려면 앞 비용도 같은 단위·서비스 영향으로 환산해야 한다. probe wall-clock, 누적 장치시간, 돈, 사용자 지연은 서로 다르다. 병렬 probe의 시간을 그냥 합산하면 안 된다. 배치 배포 비용이 정책마다 다르면 별도 항으로 포함하되 중복 계산하지 않는다. 정적 \(H\)는 예상 운영 요청 수로 사전 지정하며 측정 결과가 좋아 보이는 \(H\)를 사후 선택하지 않는다.

또 다른 목표는 \(P(L(\hat p)-L^*\le\varepsilon)\ge1-\delta\)를 만족하는 최소 측정 비용이다. 이는 fixed-confidence \(\varepsilon\)-BAI 계약이며 risk 최소화와 별도 문제다. 비용모형이 틀린 경우 confidence는 실제 latency 보장이 아니다.

## 현재 데이터로 가능한 범위

[10월21일 완주 보고서](../experiments/budget-completion-20261021.md)는 배치당6개 평가, 평가36/36, 같은 배치의 관측 공유, 입력 split 재사용, 공유 서버 교란 가능성을 명시한다. 품질 검증585/585는 필수 gate의 근거이며 충분한 성능 학습 데이터 수라는 뜻이 아니다. 현재 알려진 작은 실행 가능 배치 집합(19개)을 유지하면 최적화 자체는 정확 열거 기준을 둘 수 있다. 측정 선택의 이득은 아직 확인하지 않았다.

기존 데이터는 비용모형 구조·probe 후보·수집 오버헤드 항목을 설계하는 데 쓸 수 있다. 모든 primitive의 반복 noise, 모든 배치의 독립 holdout, 선택적 측정 정책의 off-policy 평가를 뒷받침한다고 가정해서는 안 된다. 관측되지 않은 probe 값을 평균표에서 만들어 replay하면 실제 sample-efficiency 실험이 아니라 가정에 따른 simulation이다.

## 필요한 비교와 중단 조건

1. **필수 gate 고정:** source/모델 hash/입력 품질/runtime 후보를 먼저 고정한다. 성능 비용으로 이 검증을 건너뛰지 않는다. 모든 비교 정책에 같은 gate를 적용하고 비용을 공통 항으로 보고한다.
2. **최소 baseline:** 동일 정보의 전체 균등 profiling+정확 열거, 같은 수의 random profiling, 배치별 BAI, 기존 cost-aware sampling을 둔다. 초기 probe와 최종 평가는 동일하게 제공한다.
3. **선택적 구조의 추가 효과:** 측정 횟수뿐 아니라 probe·선택 벽시계, 선택 배치의 독립 평가 regret, 실패/timeout, 총 비용을 기록한다. 공유 정보 사용 여부와 acquisition 규칙을 따로 ablation한다.
4. **이점이 없는 조건:** 충분히 큰 성능 gap·싼 전체 profiling·짧은 \(H\)·작은 후보 집합·심한 모델 bias에서는 복잡한 acquisition이 손해일 수 있다. 이 조건을 함께 검증한다.
5. **진행 판단:** 기존 균등/BAI가 적은 probe로 같은 배치를 선택하거나 선택기 계산 비용이 절약한 측정을 상쇄하면 새 방법 개발을 중단한다. 현재 19개 배치에서부터 이 경계를 보여주는 것이 큰 학습 모델을 도입하는 것보다 먼저다.

본 메모는 새로운 정책을 채택하거나 실험을 실행한 기록이 아니다. OSCAR-P는 초록 접근이며2026 working paper는 초록 확인에 그친다. 나머지 네 주요 논문은 위에 표시한 공식 PDF 본문 범위를 읽었다.
