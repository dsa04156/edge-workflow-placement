# 품질·네트워크 인지형 다단계 추론: 직접 선행 확인

확인일: 2026-10-23. 방향 전환의 문헌 근거이며 구현·실험 결과가 아니다. LMEdge·OctopInf·Atlas는 아래 버전의 arXiv HTML 본문, JellyBean은 arXiv PDF의 관련 부분을 확인했다. 논문 내용을 재현 실행하거나 성능 수치를 독립 검증하지 않았다. 초록의 일반적 성능 주장과 본문의 목적함수·지표를 구분한다.

## LMEdge — 요청별 모델·실행 위치 선택

**LMEdge: QoS-Aware LLM Inference Orchestration on Edge Clusters**, Reza Farahani 외, arXiv:2607.17175v1, 2026-07-19. [공식 서지·초록](https://arxiv.org/abs/2607.17175v1).

- **대상·선택 단위:** LLM query마다 모델 family·크기·양자화·실행 instance를 선택한다. §3의 최적화 대상은 요청별 배정이며 이 논문의 확인 범위를 영상·음성 fan-in DAG 공동 배치로 확대하지 않는다.
- **품질:** 식 (4)는 선택 후보의 예측 품질이 해당 요청의 최고 예측 품질에 대한 허용 비율 이상이도록 제한한다. 고정된 서비스 데이터셋 품질 하한과 다른 계약이다. §7은 정오답 분류 예측을 사용하며 TruthfulQA의 연속 점수를 0.7 초과 여부로 이진화한다.
- **네트워크·예측:** 입력/응답 크기와 bandwidth, 활성 요청 수에 따른 혼잡 항을 응답시간에 넣는다. 다섯 ML 모델이 추론시간·품질·연산 자원·메모리·응답 크기를 예측한다. 이는 미래 도착 부하 예측을 반드시 요구한다는 뜻은 아니다.
- **지표:** 응답시간·accuracy·serving ratio·자원 사용을 평가한다. §7은 Random의 serving ratio가 가장 높다고 설명하므로, 초록의 개선 표현을 모든 기준선에 대한 처리량 우월성으로 옮기지 않는다. serving ratio를 deadline goodput과 동일시하지 않는다.

근거: [본문 §3–7, 식 (2)–(4), Algorithm 1](https://arxiv.org/html/2607.17175v1). 공식 구현 URL은 이번 검색과 본문에서 확보하지 못했다. 코드가 없다는 증명은 아니다.

## OctopInf — 네트워크·부하 인지형 파이프라인 실행 시스템

**OCTOPINF: Workload-Aware Inference Serving for Edge Video Analytics**, Thanh-Tung Nguyen 외, arXiv:2502.01277v1, 2025-02-03. [공식 서지·초록](https://arxiv.org/abs/2502.01277v1).

- **대상:** traffic·surveillance 영상 DAG. batch size·장치/GPU·instance 수·실행 시점을 정하며, Cwd가 장치 간 부하를 분배하고 Coral이 GPU 동시 실행 간섭을 줄인다.
- **네트워크:** 단계 입출력 크기, bandwidth와 workload/network 관측을 사용한다. 따라서 네트워크 인지 DAG 실행 시스템 자체가 빈 영역이라는 주장은 성립하지 않는다.
- **유효 처리량:** §II는 SLO를 만족한 결과 수/초, §IV-B는 sink에 기한 내 도착한 **objects/s**로 정의한다. 입력 프레임·서비스 요청 수와 단위가 다를 수 있다. 식 (2)의 계획용 근사와 실제 완료 event 집계를 구분해야 한다.
- **품질·예측 범위:** §II의 명시적 제약은 지연·GPU 메모리·연산 용량이고, §IV-B 핵심 지표는 유효 처리량·종단 지연·메모리다. 이 확인 범위에서는 데이터셋 기반 최종 정확도 하한을 명시적 제약으로 확인하지 못했다. §V의 co-location 성능 시계열 예측·RL은 향후 과제로 제시된다. 현재 프로파일·관측 기반 시스템과 구분한다.

근거: [본문 §II–V](https://arxiv.org/html/2502.01277v1).

[논문 연결 공식 저장소](https://github.com/tungngreen/PipelineScheduler)의 README도 확인했다. 현재 README는 NVIDIA GPU/Jetson·TensorRT 중심 실행 경로와 `PPP`(OctopInf), 후속 `FCPO` 선택을 함께 설명한다. **현재 저장소 전체를 2025년 논문 그대로로 간주하지 않는다.** CPU/NPU 실행 후보로 이전하려면 별도 실행 검증과 backend 적응이 필요하다. 코드 내부 감사·빌드·실행은 하지 않았다.

## JellyBean·Atlas — 품질과 전체 워크플로 최적화의 직접 선행

| 연구 | 원문에서 확인한 범위 | 이번 방향에 대한 의미 |
|---|---|---|
| **JellyBean**, *Serving and Optimizing Machine Learning Workflows on Heterogeneous Infrastructures*, arXiv:2205.04713 | 초록은 accuracy·throughput 목표 아래 모델 선택·이종 계층 배치·비용 최소화를 다룬다. Fig. 1과 §1의 VQA는 image feature extraction과 audio speech recognition을 question answering에 연결하고 compute/network 비용을 고려한다. [원문 PDF, p.1](https://arxiv.org/pdf/2205.04713) | 영상+음성+결합, 품질 제약, DAG 공동 배치의 조합만으로 신규성을 주장할 수 없다. 이번에는 해당 관련 부분을 확인했으며 품질 프로파일 및 전체 구현 재현은 별도 과제다. |
| **Atlas**, *Optimizing Deployment of Compound AI Workflows on Heterogeneous Clusters*, arXiv:2609.04513v1 | §III-A는 단일 사이트 offline 계획과 무시할 수 있는 통신 비용을 가정한다. §IV의 MAP은 인접 단계의 조건부 품질 전이를 합성하고, §V는 모델 변형과 배치를 공동 선택한다. §VI는 선택 계획의 실제 종단 품질·p99·비용을 평가한다. [본문 §III–VI](https://arxiv.org/html/2609.04513v1) | 단계별 accuracy의 단순 곱을 피하는 것도 이미 직접 선행이 있다. 동적 엣지 링크 실험은 명시된 원 가정의 확장으로 평가하며, 이를 원 논문의 실패로 단정하지 않는다. |

여기서 Atlas는 위 논문 번호를 뜻하며 동명 MoE cache·에이전트 프레임워크와 구별한다. 기존 [AnchorDL 확인 상태](ieee-11662529-review.md)도 유지한다. 이번 방향 전환이 데이터 이동·모델 준비 비용의 선행을 없애지는 않는다.

## 연구 설계에 반영할 판단

아래는 문헌의 성능 주장이 아니라 현재 프로젝트의 비교 설계 제안이다.

1. 주 지표는 **같은 자원과 서비스 품질 하한 아래 deadline 안에 완료된 서비스 요청 수/관측시간**으로 정의한다. OctopInf의 object 단위를 그대로 가져오지 않고, 분기 결과 여러 개를 한 요청으로 중복 집계하지 않는다.
2. LMEdge의 요청별 예측 품질과 우리 데이터셋·사전 집단별 실측 품질을 구분한다. 품질 하한을 통과한 집단이더라도 개별 요청의 정답을 보장하지 않는다. 선택적 드롭으로 쉬운 요청만 남기는 효과를 숨기지 않도록 품질 평가 모집단·드롭 처리 규칙·실패율을 사전 고정한다.
3. 품질 제거·통신비용 제거 ablation은 동일 실행기·후보·부하·자원에서 한 항만 바꾼다. 품질을 위반한 정책의 높은 goodput은 같은 품질 조건의 승리로 보고하지 않는다. 고정 모델에서 모든 후보가 같은 품질을 통과하면 품질 고려 유무의 차이가 없을 수 있으며 그 결과도 보존한다.
4. 가까운 시스템 비교는 OctopInf의 네트워크·부하·실행 간섭 처리, JellyBean/Atlas의 품질·전체 계획 선택을 우선 검토한다. LMEdge는 요청별 품질 예측·모델/장치 선택 대조에 유용하지만 DAG에 이식하면 기반 변형으로 표시한다. 목적함수·backend·단위를 바꾼 이식을 원 구현 재현으로 부르지 않는다.
5. 첫 시스템은 사전 실측 프로파일과 현재 관측으로 시작할 수 있다. 학습 기반 미래 부하 예측기·새 정확도 합성기·새 GPU 분할 기능은 이번 문헌 확인만으로 필수 구성이나 신규 기여가 되지 않는다. 기존 방법을 동일 정보로 보완한 뒤에도 남는 손실 조건이 있는지 검증해야 한다.

남은 확인: 대표 서비스·정답 데이터셋과 최종 품질 지표, 요청/결과의 단위, 직접 비교할 공식 코드의 논문 대응 revision, 공정한 backend 이식 범위, 품질 검증 비용을 포함한 전체 관측구간. 이 문서는 네 연구를 좁게 대조한 것이며 관련 연구를 전수 조사하거나 연구 공백을 확정한 결과가 아니다.
