사전 프로파일 기반 워크플로 배치 재실험 — 2026-10-19

범위
이번 작업은 JellyBean과 InferLine의 원문 및 JellyBean 공개 프로파일러를 확인하고, 공개 측정표를 사용하는 배치 시뮬레이션을 새로 작성한 것이다. 새로운 GPU/엣지 실측이나 두 논문의 최적화 알고리즘 재현은 아니다. 현재 실행 환경에서 kubeconfig와 GPU 실행 도구를 확인하지 못했다.

선행연구에서 확인한 방법
1. JellyBean: 모델×worker 종류별 성능을 오프라인 프로파일링하고, 워크플로 정확도 상호작용도 평가한다. 본문 실험은 P75 성능을 사용하고 다른 분위수를 민감도 분석한다. 운영비 최소화가 목적이며 지연 최소화와 다르다.
   공개 이미지 프로파일러: 448×448 임의 영상, 모델별 2회 warm-up 후 200회 측정, mean/std/P10/P50/P75/P90/P95/P99 저장. CPU는 perf_counter, GPU는 CUDA event와 synchronize를 사용한다. GPU와 CPU 타이머의 측정 경계가 동일하다고 단정하지 않는다. 실행 비용은 이미지 변환·장치 전송·특징 추출 경계를 확인해야 한다.
   message-size 코드는 기본 1024개 입력에서 데이터 크기 통계를 계산하며 특징 텐서는 차원과 dtype으로 크기를 계산한다. 파일 크기와 직렬화된 전달 바이트는 구분한다. 네트워크 대역폭·비용은 인프라 입력이며 이 코드가 실제 링크 측정을 수행하는 것은 아니다.
2. InferLine: 샘플 요청을 파이프라인에 통과시켜 단계별 입력과 방문 비율을 얻고, 모델×하드웨어×배치 크기별 프로파일을 생성·재사용한다. 프로파일과 요청 trace를 이산사건 시뮬레이터에 넣어 큐를 포함한 지연을 평가한다. 실험은 계획과 평가 trace를 분리하며 gamma 분포로 요청률과 burstiness를 변화시킨다.

이번 구현
- 공개 P75 표를 그대로 읽어 사전 프로파일 DB로 사용. P50 계획도 민감도 비교.
- 공개 P50/P90을 이용해 lognormal 실행시간을 가정한 평가: 이것은 별도 실제 측정 데이터가 아니다. 분포 가정의 타당성은 추가 검증 필요.
- 원본 입력 약 794 KiB, resnet18 특징 약 2 KiB, resnet50 특징 약 8 KiB. 반환값 1024 bytes는 가정.
- 엣지 E0 입력→이미지 특징 추출→VQA→E0. 질문 입력은 캐시되어 있다고 가정한 축소 serial workload. 기존 전체 음성+영상 fork/join을 재현하지 않는다.
- 두 실행 단계 모두 4개 노드에 배치 가능: 16개 후보. VQA를 클라우드에 강제하지 않으며, 공개 VQA는 약 4ms GPU 실행이므로 무거운 클라우드 모델을 대표하지 않는다.
- 2개 모델×3개 WAN×2개 요청률×2개 CV=24조건, 조건별 12 paired seeds. 계획 trace 60초, 평가 120초. 고정 warm 배치, batch=1, 외부 background 부하 없음. 모델 로딩/배포/변경 비용 제외.
- 계획 trace 생성 seed와 평가 seed를 분리. 평가 요청과 노드별 잠재 실행시간은 모든 방법에서 동일. 동일 배치는 한 번 평가하여 재사용.
- 연산만 고려, 단계별 네트워크 greedy, 전체 chain P75, 전체 chain P50, trace 큐 예측 P75를 비교. 마지막 방법은 InferLine의 원리를 적용한 작은 후보 완전열거이며 InferLine 재현이나 새로운 알고리즘이 아니다.
- 네트워크는 가정된 LAN/WAN을 사용. 실제 유효 대역폭 측정이 아니다. 서비스 부하에 따라 링크 직렬화 대기는 시뮬레이션한다. 링크 propagation은 전송 자원을 점유하지 않는다.
- 프로파일 수집 원가를 알 수 없어 순이익으로 보고하지 않는다. planning wall time은 별도 기록. 모델별 batch/입력 크기 sweep 및 분포 변화는 이번 실험에 포함하지 않았다.

해석
네트워크를 무시한 task-fastest보다 개선되는 조건이 있지만, 단일 요청 지연을 최소화한 배치가 요청 폭주에서는 더 나빠질 수 있다. 동일한 저장 프로파일에 계획용 요청 흐름과 자원 공유를 반영하는 것이 중요하다. 단순히 P50을 P75로 바꾼 것으로는 이번 조건에서 배치가 달라지지 않았다.
10Mbps·8rps에서는 원본 전송만으로 약 52Mbps가 필요해 compute-only가 불안정하다. 이때의 약 99% 개선은 비교 기준 과부하 회피이며 일반 성능 향상으로 홍보하지 않는다. 모든 요청을 마지막까지 drain하여 지연을 계산했으므로 평가 horizon 이후 완료도 포함한다.
CI는 seed-level paired improvement의 5000회 bootstrap, 조건별 비보정 95% 구간이다. 요청 수를 독립 표본으로 세지 않았다. 여러 조건의 유의성을 합쳐 확정적 우월성을 주장하지 않는다.

실장비에서 이어갈 프로파일링 절차
- 같은 모델 버전·입력·정밀도·runtime·thread 수를 고정하고 각 장비에서 실행 가능 여부부터 검증한다.
- warmup 안정성을 확인한 후 최소 200회 측정하고 원시 표본과 분위수를 저장한다. 논문 코드의 warmup=2를 무조건 충분하다고 가정하지 않는다.
- 사용자 환경에선 wall-clock 전체 단계 지연과 GPU kernel 지연을 따로 기록한다. CUDA 양끝 동기화로 비동기 실행 종료를 보장하고 전처리/전송 경계를 명시한다.
- 실제 직렬화 bytes, 여러 payload 크기에서 노드 쌍별 전송시간을 측정한다. 네트워크 RTT를 일방 지연으로 그대로 더하지 않는다.
- 프로파일용 입력과 평가용 입력/요청 trace를 분리하고 DB를 동결한 뒤 정책을 비교한다. 새 모델/장비/runtime 변경 시 재측정한다.

재현
Python + numpy 필요. 패키지 루트에서:
python experiments/profiled-placement-20261019/run.py
python experiments/profiled-placement-20261019/verify.py
원래 저장소의 다른 실험은 수정하지 않았다. 이번 코드와 결과는 첨부 패키지로 제공하며 원격 Git에는 이번 변경을 푸시하지 않았다.

출처 (2026-10-19 확인, focused review; 체계적 문헌고찰 아님)
- Wu et al. Serving and Optimizing Machine Learning Workflows on Heterogeneous Infrastructures. PVLDB 16(3):406–419. DOI 10.14778/3570690.3570692. https://arxiv.org/pdf/2205.04713
- JellyBean 공식 코드: https://github.com/libertyeagle/JellyBean ; profile_image_model.py, execution_profile_p75.csv, message_sizes.csv는 공식 main과 바이트 일치 확인.
- Crankshaw et al. InferLine: ML Prediction Pipeline Provisioning and Management for Tight Latency Objectives. https://arxiv.org/pdf/1812.01776 , §4.1/4.2/6. 읽은 preprint 범위를 근거로 함.
- 기존 코드/자료 기준: dsa04156/edge-workflow-placement, 0e8ebfa001e0f25027ec539333c44647cf25def4.
- K-Dense Scientific Agent Skills v2.72.0, pinned commit a1043ef544e3a100c28e70a736b6cf32554d0677: literature-review/experimental-design의 근거 구분·paired design 절차 적용.
검색어: JellyBean serving machine learning inference workflows profiling execution latency network; InferLine prediction pipeline profiling latency batch size throughput; JellyBean github Yongji.
Semantica 도구가 제공되지 않아 조회·기록하지 못했다.

전체 조건 결과: joint P75 vs compute-only / trace P75 vs joint P75 (양수=개선)
resnet18 WAN=10Mbps rate=1 CV=1 joint_p75 vs compute_p75: 1254.75→61.00ms, seed별 평균 개선=95.09% CI=[94.84,95.35]
resnet18 WAN=10Mbps rate=1 CV=1 trace_p75 vs joint_p75: 61.00→61.00ms, seed별 평균 개선=0.00% CI=[0.00,0.00]
resnet18 WAN=10Mbps rate=1 CV=3 joint_p75 vs compute_p75: 6216.48→180.21ms, seed별 평균 개선=96.97% CI=[96.63,97.35]
resnet18 WAN=10Mbps rate=1 CV=3 trace_p75 vs joint_p75: 180.21→115.37ms, seed별 평균 개선=35.43% CI=[33.61,37.16]
resnet18 WAN=10Mbps rate=8 CV=1 joint_p75 vs compute_p75: 256321.54→81.81ms, seed별 평균 개선=99.97% CI=[99.97,99.97]
resnet18 WAN=10Mbps rate=8 CV=1 trace_p75 vs joint_p75: 81.81→72.03ms, seed별 평균 개선=11.94% CI=[11.19,12.61]
resnet18 WAN=10Mbps rate=8 CV=3 joint_p75 vs compute_p75: 283878.31→435.31ms, seed별 평균 개선=99.85% CI=[99.84,99.86]
resnet18 WAN=10Mbps rate=8 CV=3 trace_p75 vs joint_p75: 435.31→201.83ms, seed별 평균 개선=53.46% CI=[52.33,54.67]
resnet18 WAN=100Mbps rate=1 CV=1 joint_p75 vs compute_p75: 130.96→61.00ms, seed별 평균 개선=53.42% CI=[53.10,53.78]
resnet18 WAN=100Mbps rate=1 CV=1 trace_p75 vs joint_p75: 61.00→61.00ms, seed별 평균 개선=0.00% CI=[0.00,0.00]
resnet18 WAN=100Mbps rate=1 CV=3 joint_p75 vs compute_p75: 244.16→180.21ms, seed별 평균 개선=26.70% CI=[23.63,29.45]
resnet18 WAN=100Mbps rate=1 CV=3 trace_p75 vs joint_p75: 180.21→115.37ms, seed별 평균 개선=35.43% CI=[33.69,37.18]
resnet18 WAN=100Mbps rate=8 CV=1 joint_p75 vs compute_p75: 163.65→81.81ms, seed별 평균 개선=49.99% CI=[49.61,50.39]
resnet18 WAN=100Mbps rate=8 CV=1 trace_p75 vs joint_p75: 81.81→72.03ms, seed별 평균 개선=11.94% CI=[11.19,12.59]
resnet18 WAN=100Mbps rate=8 CV=3 joint_p75 vs compute_p75: 656.51→435.31ms, seed별 평균 개선=33.46% CI=[31.21,35.81]
resnet18 WAN=100Mbps rate=8 CV=3 trace_p75 vs joint_p75: 435.31→201.83ms, seed별 평균 개선=53.46% CI=[52.32,54.65]
resnet18 WAN=1000Mbps rate=1 CV=1 joint_p75 vs compute_p75: 70.56→61.00ms, seed별 평균 개선=13.55% CI=[12.88,14.30]
resnet18 WAN=1000Mbps rate=1 CV=1 trace_p75 vs joint_p75: 61.00→61.00ms, seed별 평균 개선=0.00% CI=[0.00,0.00]
resnet18 WAN=1000Mbps rate=1 CV=3 joint_p75 vs compute_p75: 101.13→180.21ms, seed별 평균 개선=-76.95% CI=[-84.79,-69.49]
resnet18 WAN=1000Mbps rate=1 CV=3 trace_p75 vs joint_p75: 180.21→91.32ms, seed별 평균 개선=48.28% CI=[45.12,51.69]
resnet18 WAN=1000Mbps rate=8 CV=1 joint_p75 vs compute_p75: 72.44→81.81ms, seed별 평균 개선=-12.93% CI=[-14.10,-11.70]
resnet18 WAN=1000Mbps rate=8 CV=1 trace_p75 vs joint_p75: 81.81→72.12ms, seed별 평균 개선=11.82% CI=[11.07,12.59]
resnet18 WAN=1000Mbps rate=8 CV=3 joint_p75 vs compute_p75: 132.44→435.31ms, seed별 평균 개선=-228.19% CI=[-242.39,-214.12]
resnet18 WAN=1000Mbps rate=8 CV=3 trace_p75 vs joint_p75: 435.31→104.48ms, seed별 평균 개선=75.78% CI=[74.59,77.00]
resnet50 WAN=10Mbps rate=1 CV=1 joint_p75 vs compute_p75: 1259.54→107.62ms, seed별 평균 개선=91.38% CI=[90.96,91.82]
resnet50 WAN=10Mbps rate=1 CV=1 trace_p75 vs joint_p75: 107.62→107.41ms, seed별 평균 개선=0.16% CI=[-0.73,1.17]
resnet50 WAN=10Mbps rate=1 CV=3 joint_p75 vs compute_p75: 6221.28→416.98ms, seed별 평균 개선=93.02% CI=[92.27,93.87]
resnet50 WAN=10Mbps rate=1 CV=3 trace_p75 vs joint_p75: 416.98→243.69ms, seed별 평균 개선=41.13% CI=[39.75,42.57]
resnet50 WAN=10Mbps rate=8 CV=1 joint_p75 vs compute_p75: 256326.29→273.07ms, seed별 평균 개선=99.89% CI=[99.89,99.90]
resnet50 WAN=10Mbps rate=8 CV=1 trace_p75 vs joint_p75: 273.07→158.83ms, seed별 평균 개선=41.55% CI=[39.77,43.34]
resnet50 WAN=10Mbps rate=8 CV=3 joint_p75 vs compute_p75: 283883.07→2189.15ms, seed별 평균 개선=99.23% CI=[99.13,99.32]
resnet50 WAN=10Mbps rate=8 CV=3 trace_p75 vs joint_p75: 2189.15→854.46ms, seed별 평균 개선=60.39% CI=[58.45,62.51]
resnet50 WAN=100Mbps rate=1 CV=1 joint_p75 vs compute_p75: 135.74→107.62ms, seed별 평균 개선=20.72% CI=[19.65,21.63]
resnet50 WAN=100Mbps rate=1 CV=1 trace_p75 vs joint_p75: 107.62→107.41ms, seed별 평균 개선=0.16% CI=[-0.73,1.19]
resnet50 WAN=100Mbps rate=1 CV=3 joint_p75 vs compute_p75: 248.96→416.98ms, seed별 평균 개선=-66.36% CI=[-73.09,-60.42]
resnet50 WAN=100Mbps rate=1 CV=3 trace_p75 vs joint_p75: 416.98→246.38ms, seed별 평균 개선=40.32% CI=[38.27,42.41]
resnet50 WAN=100Mbps rate=8 CV=1 joint_p75 vs compute_p75: 168.41→273.07ms, seed별 평균 개선=-61.91% CI=[-68.78,-55.50]
resnet50 WAN=100Mbps rate=8 CV=1 trace_p75 vs joint_p75: 273.07→162.62ms, seed별 평균 개선=40.13% CI=[37.60,42.17]
resnet50 WAN=100Mbps rate=8 CV=3 joint_p75 vs compute_p75: 661.27→2189.15ms, seed별 평균 개선=-229.26% CI=[-257.18,-202.05]
resnet50 WAN=100Mbps rate=8 CV=3 trace_p75 vs joint_p75: 2189.15→661.27ms, seed별 평균 개선=68.99% CI=[66.64,71.48]
resnet50 WAN=1000Mbps rate=1 CV=1 joint_p75 vs compute_p75: 75.59→75.59ms, seed별 평균 개선=0.00% CI=[0.00,0.00]
resnet50 WAN=1000Mbps rate=1 CV=1 trace_p75 vs joint_p75: 75.59→75.59ms, seed별 평균 개선=0.00% CI=[0.00,0.00]
resnet50 WAN=1000Mbps rate=1 CV=3 joint_p75 vs compute_p75: 121.54→121.54ms, seed별 평균 개선=0.00% CI=[0.00,0.00]
resnet50 WAN=1000Mbps rate=1 CV=3 trace_p75 vs joint_p75: 121.54→102.67ms, seed별 평균 개선=15.01% CI=[12.87,17.45]
resnet50 WAN=1000Mbps rate=8 CV=1 joint_p75 vs compute_p75: 79.24→79.24ms, seed별 평균 개선=0.00% CI=[0.00,0.00]
resnet50 WAN=1000Mbps rate=8 CV=1 trace_p75 vs joint_p75: 79.24→79.24ms, seed별 평균 개선=0.00% CI=[0.00,0.00]
resnet50 WAN=1000Mbps rate=8 CV=3 joint_p75 vs compute_p75: 175.71→175.71ms, seed별 평균 개선=0.00% CI=[0.00,0.00]
resnet50 WAN=1000Mbps rate=8 CV=3 trace_p75 vs joint_p75: 175.71→125.06ms, seed별 평균 개선=28.73% CI=[27.75,29.69]
