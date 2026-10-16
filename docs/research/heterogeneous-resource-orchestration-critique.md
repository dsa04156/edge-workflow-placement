# 이기종 자원 오케스트레이션 선행연구 비판적 검토

검토일: 2026-10-16. [기존 조사](heterogeneous-resource-orchestration.md)의 핵심 연구 8개를 재검토했다. 관련 원문 절을 읽은 문헌 비평이며 새 실험·독립 재현 결과는 아니다. 아래 **영향과 실패 가능성은 명시한 경우를 제외하면 추론**, **실험은 제안**이다.

현재 주제는 **이종 엣지 AI 환경에서 실행 호환성과 데이터 이동 비용을 고려한 동적 워크플로우 배치 최적화**로 갱신됐다. 현재 범위는 [문제 정의](problem-definition.md), 실행 순서는 [첫 비교 실험 계획](workflow-placement-experiment-plan.md)을 따른다. 아래 기존 8개 연구의 검토는 보존하며, JellyBean·Atlas와의 직접 비교는 §13, AnchorDL 대조는 [추가 검토](ieee-11662529-review.md)에 연결한다. AnchorDL은 데이터+모델 비용과 look-ahead를 이미 다루므로 이 결합이나 greedy 대비 이득을 우리 연구만의 공백으로 삼지 않는다. 본문 미확보 상태에서 NPU/runtime/동적 전환 미지원을 비판하지 않는다.

Trident 검토본은 `arXiv:2603.02075v1`이다. 저자들의 공식 목록에는 PVLDB 게재 표기가 있으나 연도 표기가 일치하지 않고 최종 출판본 대조는 미완료다. 미심사를 이유로 평가하지 않으며, 아래 수식 지적은 **v1 p.11에 한정**한다. 코드 미공개도 단정하지 않는다. [Binhang Yuan 연구 목록](https://binhangyuan.github.io/site/), [Zhuangzhuang Zhou 출판 목록](https://www.zzhou612.com/publications/)

## 1. 핵심 판단과 비판의 기준

가장 구체적인 확인 사항은 Trident v1의 공개 수식이다. 그대로 합산하면 인접 stage의 병렬도가 같아지는 제약이 도출된다. **표기·정식화 문제 가능성**을 먼저 점검해야 하며, 구현도 같은지 확인하기 전 실험 결과 전체를 부정해서는 안 된다. 아래 §7.1에서 증명과 확인 실험을 분리했다. [Trident §6.5](https://arxiv.org/html/2603.02075v1#S6.SS5)

가장 생산적인 비판은 **정상 실행에서 유효한 성능·비용 추상화가 변화가 빠른 구간에서도 올바른 결정을 만드는가**이다. Gavel의 throughput 추정, Pollux의 재배정 penalty, INFaaS의 variant profile은 모두 기존 대응을 갖고 있다. 따라서 이를 제거한 약한 구현으로 우위를 보이는 것은 적절하지 않다. [Gavel §6·7.6](https://www.usenix.org/system/files/osdi20-narayanan_deepak.pdf), [Pollux §4.2](https://www.usenix.org/system/files/osdi21-qiao.pdf), [INFaaS §3–4](https://www.usenix.org/system/files/atc21-romero.pdf)

비판은 두 종류로 구분한다.

- **범위 내부의 검증 질문**: 논문이 최적화하는 목적·변화에 대해, 특정 가정이 깨질 때 주장한 이점이 유지되는지 묻는다. 실제 실패를 입증하기 전에는 결함으로 확정하지 않는다.
- **확장 적용의 경계**: 원래 다루지 않은 cross-vendor runtime·새 가중치 버전·다른 QoS에 쓰려면 무엇이 추가로 필요한지 묻는다. 원 논문의 오류나 실서비스 불가를 의미하지 않는다.

요청 다양성, GPU 세대 차이, CPU+가속기, 다중 벤더/runtime은 별개 조건이다. 또한 모델 가중치 갱신, 성능 추정기 재학습, 배치 설정 변경을 모두 '모델 업데이트'로 묶지 않는다. Alpa의 모델 구조 다양성과 Trident의 추정기 갱신을 읽을 때 특히 이 구분이 필요하다. [Alpa §8.1](https://www.usenix.org/system/files/osdi22-zheng-lianmin.pdf), [Trident §4.4·6](https://arxiv.org/html/2603.02075v1)

## 2. Gavel: 성능 추정 오차가 배정 오류로 바뀌는 경계

- **원문 사실**: §3.1은 throughput 행렬의 시간가중 평균으로 정책을 최적화한다. §6은 profiling·matrix completion·reference job 기반 초기 추정, 실행 중 관측과 checkpoint/lease renewal을 제공한다. §7.6은 추정기와 oracle을 비교한다. [Gavel 원문](https://www.usenix.org/system/files/osdi20-narayanan_deepak.pdf)
- **비판적 추론 — 범위 내부**: 평균 예측오차가 작아도 비슷한 후보의 성능 순위가 뒤집히면 배정이 달라진다. 모델·입력·간섭 변화가 reference 유사성을 약화하는 경우, 수집·재추정 비용까지 상각할 시간이 있는지 확인해야 한다.
- **반론·기존 대응**: 성능을 전부 정적으로 안다고 가정하는 시스템은 아니다. online 추정과 oracle 비교, lease renewal이 이미 있으므로 '프로파일 오차/전환 비용을 무시한다'는 비판은 틀리다.
- **반증 실험**: 동일 정책에서 실측 oracle·기존 추정기·최근 관측만 쓰는 단순 갱신을 비교한다. 입력 분포의 변화 주기와 성능 순위 간격을 바꾸고, profiling 자원까지 포함한 JCT를 측정한다. 기존 추정기가 변화에도 적응하면 이 비판은 약해진다.

## 3. Pollux: 과거 재배정 이력으로 미래 비용을 평가해도 되는가

- **원문 사실**: §4.2는 `REALLOC_FACTOR=(T−Rδ)/(T+δ)`를 사용한다. T는 작업 나이, R은 과거 재배정 횟수, δ는 지연 추정치이며, 과거 평균 재배정 빈도가 계속된다는 해석을 명시한다. checkpoint-restart와 실험 overhead도 보고한다. [Pollux 원문](https://www.usenix.org/system/files/osdi21-qiao.pdf)
- **비판적 추론 — 범위 내부**: 과거에 흔들린 작업은 앞으로 안정될 때도 불리할 수 있고, 오래 안정적이던 작업은 새 변동 구간의 비용을 낮게 볼 수 있다. 이력 penalty가 현재 변화의 지속시간·목적지 warm 상태를 얼마나 잘 대변하는지가 핵심이다.
- **반론·기존 대응**: 이는 재배정 폭주를 줄이는 실용적 장치다. goodput도 online 관측으로 갱신하므로 penalty의 가정만으로 전체 적응이 실패한다고 결론낼 수 없다.
- **반증 실험**: 전반부 변동/후반부 안정과 그 역순을 짝지어 실행한다. 원 penalty·최근 window penalty·최소 체류시간·실측 전환비용 정책을 같은 goodput 모델에서 비교한다. 이력 때문에 남는 순손실이 없으면 새 비용모델의 필요성은 약하다.

## 4. INFaaS: 정확도와 프로파일의 유효기간은 계약 밖으로 밀려나 있는가

- **원문 사실**: §3.1은 등록 시 valSet으로 variant 정확도를 구하고 application별 모델 선택에 사용한다. §3.2의 일회 profiling 비용은 장기 서빙에 상각한다고 설명한다. §4는 loaded·interfered·overloaded 상태와 loading 비용을 반영한다. [INFaaS 원문](https://www.usenix.org/system/files/atc21-romero.pdf)
- **비판적 추론 — 확장 경계**: 검증셋 정확도가 바뀐 요청 분포·세부 집단의 품질을 보장하지는 않는다. 잦은 모델 버전 교체나 짧은 사용 기간에는 생성·검증·profiling 상각도 달라진다. '요구 정확도를 만족하는 variant'의 의미를 데이터·기간과 함께 고정해야 한다.
- **반론·기존 대응**: 입력마다 정답을 보장하는 시스템이라고 읽는 것은 부당하다. 사용자는 modelName을 지정할 수 있고, 간섭 상태 감지·확장 정책도 존재한다. 등록 데이터의 적절성은 응용 계약의 일부다.
- **반증 실험**: frozen validation과 시간 분리 validation을 사용하고, 모델별 사용 기간을 바꾼다. 품질·profiling 포함 총비용을 함께 보고, 갱신 주기만 조정한 기존 정책으로 해결되는지도 확인한다.

## 5. ServerlessLLM: 빨리 시작하는 선택이 전체 요청에도 최선인가

- **원문 사실**: §5는 token 이전·KV 재계산을 사용한다. §6.2는 재개시간을 token 수의 선형식으로 추정하며 출력 token 수를 경과시간/평균 token 시간으로 근사한다. §6.3은 예상 startup time이 최소인 서버를 선택한다. §7은 migration pause도 지표에 더한다. [ServerlessLLM 원문](https://www.usenix.org/system/files/osdi24-fu.pdf)
- **비판적 추론 — 내부/확장 구분**: batch·간섭이 바뀌면 평균 token 시간과 재계산 추정의 순위 오류가 커질 수 있다. startup 최소화가 이후 decode 지연·전체 goodput·이동한 기존 요청의 누적 손실까지 최적화한다는 결론은 별도다. 후자는 원 목적함수의 확장이다.
- **반론·기존 대응**: storage queue와 bandwidth를 고려하고 관측 latency로 추정치를 갱신한다. pause를 평가에서 누락했다는 비판도 틀리다.
- **반증 실험**: 동일 loading/migration 구현에서 원 estimator·실측 oracle·최근 batch를 반영한 추정을 비교한다. TTFT, token 간격 tail, 기존 요청 지연, 전체 완료율을 함께 측정한다. startup 개선이 전체 지표에서도 유지되면 목적함수 확장 필요성이 줄어든다.

## 6. Llumnix: 작은 정지시간과 작은 전체 이전 비용은 같은가

- **원문 사실**: §4.2는 append-only KV를 다단계 복사하며 예약·실패 abort handshake를 둔다. §6.2의 microbenchmark는 두 instance 사이 한 요청을 이전하며 downtime과 주변 요청 overhead를 평가한다. §6.6의 64-instance 확장성 검증은 GPU 실행을 측정값 기반 sleep으로 대체한다. [Llumnix 원문](https://www.usenix.org/system/files/osdi24-sun-biao.pdf)
- **비판적 추론 — 범위 내부**: 겹친 복사는 정지시간을 숨겨도 메모리/네트워크 자원을 소비한다. 동시 migration·긴 context·다중 GPU 통신이 같은 경로를 사용할 때 cluster tail이 어떻게 변하는지 확인할 가치가 있다. 실제 병목 발생은 아직 가설이다.
- **반론·기존 대응**: 실제 GPU의 serving 실험도 있어 논문 전체가 모의실험이라는 비판은 틀리다. 간섭과 overhead도 다뤘다. 다만 scheduler 확장성의 검증을 대규모 실제 GPU의 동시 이전·통신 간섭 검증으로 확대할 수는 없다. 다른 weight/runtime 간 KV 변환도 원 적용 범위보다 넓다.
- **반증 실험**: 네트워크·context·동시 이전 수를 바꾸고, 원 정책·이전 rate limit·이전 금지를 비교한다. migrated 요청과 나머지 요청의 P99를 분리한다. 호환성 확장은 model hash·KV layout을 고정한 양성 대조군부터 검증한다.

## 7. Trident: 전환을 안정시키는 규칙이 재적응을 늦추는가

- **원문 사실**: §6.5는 no-rollback·단일 진행 전환을 유지하고 새 추천을 보류한다. §4.4 reset/EMA, §8.3 공통 입력·all-at-once 비교도 있다. [Trident v1](https://arxiv.org/html/2603.02075v1)
- **비판적 추론 — 범위 내부**: regime이 전환보다 빨리 다시 바뀌면 이전 선택에 묶이는 손실이 가능한가?
- **반론·기존 대응**: thrashing·계산량을 줄이는 규칙이며 cold-start discount도 있다. 비교 불공정이나 비용 미고려로 단정할 수 없다.
- **반증 실험**: regime/전환/재학습 시간 비율을 바꾸고 원 규칙·rollback+hysteresis를 취소 비용까지 포함해 비교한다.

### 7.1 공개 수식의 내부 일관성: 별도 확인이 필요한 더 강한 문제

**원문 사실**: v1 §6.5 Eq.14·18·19는 다음과 같다. HTML과 원 PDF p.11의 표기를 대조했다. [원문 수식](https://arxiv.org/html/2603.02075v1#S6.SS5)

\[
\sum_k x_{i,k}=p_i,\qquad
\sum_l w_{i,k,l}=x_{i,k},\qquad
\sum_k w_{i,k,l}=x_{i+1,l}.
\]

**직접 도출**: 각 경계에서 양쪽 node 인덱스를 합하면

\[
p_i=\sum_k x_{i,k}=\sum_{k,l}w_{i,k,l}=\sum_l x_{i+1,l}=p_{i+1}.
\]

따라서 공개 식 그대로는 모든 stage에 같은 병렬도를 강제한다. operator별 독립 병렬도 선택이라는 설명과의 정합성 문제다. **반론**은 오탈자·코드의 다른 정식화 가능성이다. 구현 일치 여부는 미확인이다. **확인 실험**은 CPU stage 4개·가속 stage 1개가 필요한 최소 사례를 식 그대로와 실제 코드에 각각 넣어 feasible 여부를 비교하는 것이다. 이 관찰만으로 실험 조작·전체 성능 무효를 주장할 수 없다.

## 8. Decima: workload 특화 학습이 환경 변화에 얼마나 오래 유효한가

- **원문 사실**: §7.4는 학습·평가 도착분포 불일치 때 악화되는 사례와 mixed workload·도착률 hint의 개선을 함께 보인다. §8은 더 큰 변화와 online 학습의 sample complexity를 후속 과제로 둔다. 실행 중 task 선점도 하지 않는다. [Decima §7.4·8](https://people.csail.mit.edu/malte/pub/papers/2019-sigcomm-decima.pdf)
- **비판적 추론 — 범위 내부**: 분포가 빨리 바뀌면 재학습 전의 누적 손실이 정상 구간 이익을 상쇄할 수 있다. 다만 이 논문은 Spark DAG/executor 정책이므로 GPU runtime 변환을 못 한다는 비판은 적용 범위를 벗어난다.
- **반론·기존 대응**: 일반화를 검증하지 않은 논문이 아니다. mixed training과 hints가 이미 있고 executor 이동 비용에 따라 다른 정책을 배우는 사례도 있다. 'RL이라 전환비용을 무시한다'는 주장도 잘못이다.
- **반증 실험**: mixed-training·hint 포함 정책을 기준선으로 새 DAG 구조·자원 slowdown을 분리해 가한다. 적응 전 regret와 재학습 비용을 측정한다. 저비용 heuristic으로 즉시 복귀하는 정책보다 우수하면 연구적 강점이 유지된다.

## 9. Alpa: 좋은 실행 계획을 구하는 비용을 언제 회수하는가

- **원문 사실**: §4의 mesh는 동등 compute capability와 축별 통신 가정을 둔다. §8.4는 compile/search·profiling 비용을 보고하고 장기간 학습으로 상각한다고 설명한다. 통신·resharding 최적화도 구현한다. [Alpa §4·8.4–8.5](https://www.usenix.org/system/files/osdi22-zheng-lianmin.pdf)
- **비판적 추론 — 확장 경계**: 짧은 작업·빈번한 shape 변경·자원 회수로 재계획이 필요하면 정상 구간 throughput만으로 총이익을 판단할 수 없다. mesh 가정을 깨는 이종 장치 확장도 별도 검증이 필요하다.
- **반론·기존 대응**: 장기 대형 학습이라는 원 목표에는 상각이 합리적일 수 있다. compile 비용과 통신을 아예 숨겼다는 비판은 틀리다. 다른 작업시간을 이유로 원 결론 전체를 부정하면 안 된다.
- **반증 실험**: 동일 모델의 실제 실행 기간·재계획 빈도를 바꾸고 cache 재사용 유무를 분리한다. end-to-end 총시간에서 수동 계획·기존 계획 재사용·매번 재최적화를 비교해 break-even을 구한다.

## 10. 계층을 혼동하면 비판 자체가 약해진다

장치 할당 계층이 모델 의미 호환성까지 해결하지 않는 것은 그 자체로 결함이 아니다. DRA의 claim·driver 계약 위에서 실행 가능성 검사를 보완할 수 있고, UCP는 학습 상태 재구성에, SageMaker guardrails는 traffic rollout에 강점이 있다. 우리 연구는 이들을 적용한 뒤 남는 실패를 보여야 한다. [DRA 공식 흐름](https://kubernetes.io/docs/concepts/resource-management/dynamic-resource-allocation/how-dra-works/), [UCP §5](https://www.usenix.org/system/files/atc25-lian.pdf), [SageMaker guardrails](https://docs.aws.amazon.com/sagemaker/latest/dg/deployment-guardrails.html)

## 11. 이전 조사 우선순위와 우리 제안에도 적용할 기준

아래는 이전의 배포·전환 중심 조사에서 제안한 순서다. 현재는 3~5개 실제 Task의 정적 배치 차이를 확인한 뒤 동적 변화를 추가하는 실험 계획을 우선한다.

다음 순서는 문헌 비평에 기반한 **검증 우선순위 제안**이며 성공 가능성이나 신규성 확정이 아니다.

| 우선순위 | 최소 검증 | 중단·수정할 조건 |
|---|---|---|
| 1 | 전환 도중 regime 재변화: 원 규칙 대 단순 hysteresis/rollback | 작은 운영 수정으로 손실이 사라지면 범용 신규 최적화 필요성을 낮춤 |
| 2 | warm/cache·간섭 변화에 따른 전환시간 추정의 결정 오류 | oracle과 원 estimator 차이가 작으면 비용모델 복잡화 중단 |
| 3 | profile 수명과 재검증 비용을 포함한 짧은 실행 구간 | profiling·cache 정책만으로 해결되면 그 범위로 기여 축소 |
| 4 | 실제 사용할 두 runtime 사이의 실행·상태 계약 | 미지원 조합을 빼거나 새 backend 구현만으로 해결되면 최적화 문제와 구분 |

우리 방법에도 같은 비판을 적용한다. 전환 비용·호환성·업데이트 변수를 추가했다는 주장만으로는 부족하다. 가장 가까운 기존 방법에 같은 관측값·튜닝 예산·캐시·배포 기능을 주고, 각 추가 기능을 제거한 비교를 해야 한다. 우리 프로파일 수집·검증·학습·재컴파일·controller 비용도 전체 관측 구간에 포함한다.

발견할 것은 '모든 것을 고려하는 알고리즘'이 아니라 **어떤 계약이 언제 깨지고, 기존 보완책으로도 남는 손실이 얼마나 되는지**다. 반례가 사라지는 조건, 개선이 없는 workload, 추가 제어가 오히려 악화시키는 구간도 같은 비중으로 기록한다.

이 검토는 Trident v1의 표기된 수식에서 도출되는 제약을 확인했으며, 실행 코드의 오류나 논문 전체의 성능 무효를 입증한 것은 아니다. 통계적 비유의성이나 운영 불가능성도 단정하지 않는다. 코드·후속 버전·실제 환경 검증은 남아 있다. 비판 문장을 논문에 사용할 때는 제안한 실험 결과와 원문의 적용 범위를 함께 명시해야 한다.

## 12. 논문별 해결 범위 밖에 남는 문제

아래의 **명시적 제약**은 해당 검토본에서 확인한 한계다. **미검증 가설**은 실제 손실이 발생하는지 실험해야 하는 질문이다. 두 열을 합쳐 '이 논문은 실패한다'고 읽으면 안 된다. 후속 버전과 다른 연구까지 포함한 전 세계 미해결 문제를 선언하는 표도 아니다.

| 논문 | 이미 해결한 것 | 원문에서 확인한 명시적 제약·범위 밖 | 아직 실패가 입증되지 않은 검증 질문 |
|---|---|---|---|
| **Gavel** | 이종 GPU 성능에 따른 정책·배정, checkpoint 전환·lease renewal | 논문 구현은 PyTorch 지원이고 TensorFlow 지원은 향후 작업. checkpoint 저장·복원 함수는 응용이 제공하므로 임의 runtime 상태 변환을 구현했다는 뜻이 아님. [§6](https://www.usenix.org/system/files/osdi20-narayanan_deepak.pdf#page=10) | 새 workload/간섭에서 성능 추정의 순위 오류와 profiling 비용이 배정 이익을 없애는가? online 추정기를 포함해 검증해야 함. |
| **Pollux** | 학습 goodput·GPU 수·배치 크기 적응과 재배정 비용 penalty | 서로 다른 distributed job의 같은 node 공유를 금지해 간섭을 회피한다. penalty는 과거 평균 재배정 빈도가 유지된다는 가정을 사용한다. [§4.2](https://www.usenix.org/system/files/osdi21-qiao.pdf#page=10) | 부하 regime·목적지 상태가 바뀌면 과거 빈도/지연 기반 penalty가 현재 순이익을 잘못 평가하는가? |
| **INFaaS** | 여러 모델 variant/backend 선택, loading·간섭 상태와 비용을 반영한 확장 | 이미 학습한 모델에서 variant를 만들며 새 모델을 학습하지 않는다. 정확도는 등록 valSet에서, profiling 상각은 장기 서빙을 전제로 설명한다. [§3.1–3.2](https://www.usenix.org/system/files/atc21-romero.pdf#page=5) | 요청 분포·모델 버전이 자주 바뀌면 정확도 metadata와 profile의 유효기간이 짧아지는가? 모델별 검증 데이터와 재프로파일 정책을 함께 비교해야 함. |
| **ServerlessLLM** | 다계층 loading·token 이전/KV 재계산·startup/migration 시간 추정 | checkpoint를 어느 서버/계층에 둘지 최적화하는 문제는 별도이며 논문에서 다루지 않는다고 명시한다. 서버 선택의 직접 목표는 예상 startup 시간이다. [§6.3·7.1](https://www.usenix.org/system/files/osdi24-fu.pdf#page=10) | cache 배치·batch·간섭 변화에서 시작 지연 최소화가 전체 요청의 decode tail·goodput까지 개선하는가? pause와 비용을 무시했다는 뜻은 아님. |
| **Llumnix** | 동일 모델 instance 사이 요청/KV live migration, 부하 균형·우선순위·확장 | 서로 다른 모델 종류/variant를 함께 선택하는 지원은 향후 작업이라고 명시한다. 새 weight 버전으로 진행 중 KV를 변환하는 문제까지 해결한 것은 아님. [§7](https://www.usenix.org/system/files/osdi24-sun-biao.pdf#page=15) | 동시 이전·긴 context·공유 네트워크에서 나머지 요청의 tail 손실이 이전 이익보다 커지는가? 기존 overhead 평가를 넘는 조건을 찾아야 함. |
| **Trident v1** | 병렬도·배치·설정 전환·cold-start 비용을 함께 고려 | 진행 중 전환의 rollback을 허용하지 않고 새 추천은 완료까지 보류한다. 공개 Eq.14·18·19의 동일 병렬도 유도는 §7.1의 별도 확인 사항이며 코드 오류로 확정하지 않음. [§6.5](https://arxiv.org/html/2603.02075v1#S6.SS5) | regime이 전환보다 빨리 재변화하면 전환 완료까지의 구속이 손해인가? 취소 비용을 포함한 단순 hysteresis/rollback으로 해결되는지도 검증해야 함. |
| **Decima** | DAG/executor 병렬도 정책 학습, executor 이동비용에 적응 | 실행 중 task는 선점하지 않으며 stage 완료 후 executor를 회수한다. 더 급격한 변화에 대한 online 학습과 sample complexity는 후속 과제다. [§8](https://people.csail.mit.edu/malte/pub/papers/2019-sigcomm-decima.pdf#page=12) | mixed training·hint 대응에도 새 DAG/자원 변화에서 적응 전 누적 손실이 큰가? GPU runtime 변환은 원래 이 논문의 대상이 아님. |
| **Alpa** | 연산자 안팎 병렬화 계획·resharding 통신 자동화 | 하나의 mesh는 동등 compute capability를 전제한다. compile/profile 비용은 장기 학습으로 상각한다고 설명한다. [§4·8.4](https://www.usenix.org/system/files/osdi22-zheng-lianmin.pdf#page=6) | 짧은 실행·shape/자원 변경으로 재계획이 반복될 때 이익을 회수하는가? mesh 밖의 이종성까지 전부 금지한다고 일반화해서는 안 됨. |

우리 연구와 연결되는 질문은 **호환성·모델 버전·상태·전환 비용을 더 넣을 수 있는가**보다, **기존 대응을 켜도 실행 가능성 또는 전체 구간 순이익의 예측이 깨지는 조건이 있는가**다. 먼저 한 workload와 한 전환 사건에서 재현해야 하며, 이 표만으로 연구 공백이나 새 방법의 필요성이 확정되지는 않는다.

## 13. 현재 주제에서 JellyBean·Atlas를 비판하는 기준

두 연구가 이미 모델 선택과 워크플로 전체 배치를 다룬다는 점을 출발점으로 삼는다. 단순 Task별 배치보다 DAG 방식이 좋다는 결과만으로 이들을 넘어섰다고 주장하지 않는다. [JellyBean 초록](https://arxiv.org/abs/2205.04713), [Atlas §III·V](https://arxiv.org/html/2609.04513v1)

| 대상 | 확인된 범위 | 아직 입증하지 않은 것 | 먼저 할 비교 |
|---|---|---|---|
| JellyBean | 이종 인프라의 워크플로 모델·배치 공동 결정 | 이번 확인은 초록 수준이며 동적 적응·runtime 기능 부재를 확정하지 않음 | 원 구현/본문을 확인하고 최신 프로파일 재계획·실행 경로를 보존한 비교 |
| Atlas v1 | 단일 사이트 offline 계획; §III-A는 단계 간 통신을 명시적으로 모델링하지 않음 | 엣지 네트워크 변화나 잦은 실행환경 변화에서 실제 손실이 얼마나 발생하는지 | 원 가정에 맞는 정적 조건을 먼저 확인하고, 확장 조건에서 통신 인지·재최적화 대조 |
| 우리 가설 | 실행 호환성·준비 상태·데이터 이동의 변화가 배치 판단을 바꿀 수 있다는 검증 제안 | 기존 DAG 재최적화와 단순 안정화 후에도 새 알고리즘이 필요한지 | 같은 정보·캐시·실행기에서 프로파일 갱신, 데이터 인지 greedy, hysteresis와 비교 |

실패를 주장하려면 조건·관측 시점·원인·기존 대응·잔여 손실을 모두 기록한다. 예를 들어 오래된 링크 정보 때문에 느려졌지만 프로파일 갱신으로 회복된다면 이를 DAG 최적화 자체의 실패로 확대하지 않는다. runtime 호환성 필터 하나로 해결되면 실행 계약 개선과 정책의 기여를 구분한다.

현재 우선순위는 [실험 계획](workflow-placement-experiment-plan.md)의 정적 차이 → 네트워크/실행환경 변화 → 기존 재최적화·단순 보완 → 잔여 문제 확인이다. 이 절은 비판의 기준이며 실제 실패 결과가 아니다.
