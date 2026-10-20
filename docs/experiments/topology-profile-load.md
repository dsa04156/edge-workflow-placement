# Raw edge replay with owned shared WAN load

실행 코드는 `experiments/real-topology-live/run_load.py`, 사전 명세는 [edge-load-validation](../../.scratch/edge-load-validation/spec.md), 구현 검토는 [load-review-validation](../../artifacts/topology-profile-live/load-review-validation.md)다. 기존 [native smoke](topology-profile-smoke.md)와 구분한다.

## 질문과 범위

실제 이미지·음성의 압축 해제된 task입력과 공유대역폭 제약, 완료를 기다리지 않는 요청 부하를 주면 서버 집중·엣지 처리·혼합 배치의 지연/실패가 어떻게 달라지는지 진단한다. 동일 배포에서 조건별 반복 요청을 수집하며 요청들을 독립 배포 표본으로 세지 않는다.6정책/온라인재최적화/DRL/node-wide공유servingpool 비교는 이 진단과 구분한다.

E0=`etri-dev0003-raspi5`, E1=`etri-dev0002-raspi5`, C=`etri-ser0002-cgnmsb`. source는E0이미지/E1음성으로 고정한다. Vision 후보E0/C, ASR후보E1/C, fusion=C. 모든 후보Pod가 동일한1CPU/2GiB 요청·한Torchthread·batch1로 상주한다. 각operator FIFO가 독립이며 node전체FIFO가 아니다. ResNet18, Wav2Vec2-base-960h, 원본 strict-loaded MUTAN 모델을 사용한다. live센서는 연결하지 않았으며 실제 공개파일을 edgePod에서 재생한다.

## 데이터와 시간 경계

원 JPEG→PIL RGBuint8, FLAC→16kfloat32mono PCM을 배포 전에 NPY로 파생한다. 임의padding·가짜compute대기는 없다. 원본/파생입력/model/receipt hash를 보존한다. NPY는128Bheader를 포함하며 raw배열 본체와 binarywire envelope를 구분한다. input-ready시각부터 측정하며 현장sensor수집/decode지연은 포함하지 않는다. source오디오60개는readiness전에해시검증/메모리준비하고 E0이미지는trajectory시작전에준비한다. 동일 모델에 압축원본을 전달하는 조건도 별도로 둔다.

CPUreference의60입력(20calibration/40evaluation)에 대해 양쪽visionfeature rtol1e-4/atol1e-5, 양쪽ASR문자열exact를 사전gate한다. calibration20개의isolated실행시간만 별도동결한다. fusion출력은 매요청 Top1/score1e-4,Top5순서별도,모델/입출력hash를검증한다. 원본trainingvocab의독립hash는확보하지못했으며 기존vocab재구성한계를 유지한다.

`e2e_ms`는E0open부터결과수신까지, `source_total_ms`는완료ack까지, `scheduled_to_terminal_ms`는예정release부터후속RPC정리까지다.120초deadline을넘어도정리가끝나야terminal행이남을수있으며 `deadline_exceeded`를기록한다. 성공요청 지연과 실패수를 함께 보고한다.

## 공유 WAN

전용gatewayPod의네트워크namespace에서 rootPRIO아래 cloud/edge방향별 TBF+netem을설정한다. TBFburst4096B, netem64packets, cap100/10/2Mbps, 방향별추가지연0/20ms. source/operator의반대zone동결PodIP에/32blackhole을설정해직접실패와relay성공을확인하고,해제뒤직접통신복구를검증한다. 호스트NIC/CNI/EdgeCore는변경하지않는다.

이경로는HTTPrelay로TCP연결이분리된다. 방향별20ms는왕복추가전파지연40ms설정이지만,매RPC의handshake·여러왕복때문에applicationroundtrip증가가40ms와같지는않다. native와제한없는동일relay대조를둔다. 자세한근거는 [owned gateway kernel contract](../research/owned-gateway-kernel-contract.md)다.

두source가동시전송할때합계cap을검증하고,역방향도검증한다. cap100Mbps는상한조건이며host/NIC협상값이나실효100Mbps달성을뜻하지않는다. 중간결과조작없이실제코퍼스bytes를사용하며probepayload/wirecounts를별도기록한다.

## 수집과 분석

정해진offset에요청을내보내는source-localopenloop,동시workflow최대32,operator대기최대64다. 모든계획release는성공/오류/drivercapacity등terminal1행을갖는다. phase끝에서source/operator/gatewayHTTPactivity,모델FIFO,미완료join,커널backlog가두번연속0이될때까지기다린다. 그렇지않으면조건변경전에중단한다.

저부하: native/unlimitedrelay/B100·10·2+20ms/방향/B2+delay0/압축B2+20ms,3배치각6요청at.1rps. B조건9개의순서seed20261019. 고부하: B10·2 × .5·1rps ×3배치각40요청,seed20261020. 과부하조건은유한windowstress이며정상상태성능으로해석하지않는다. burst/외부cross-traffic는혼합하지않는다.

주요응답은종단지연분포·실패·release/dispatch지연·operatorqueue다. 단일배포기술통계이며p값·독립표본신뢰구간·새알고리즘우월성주장은하지않는다. 통신잔여를순수네트워크시간으로부르지않는다.

## 재실행

실제사용한도구: Python3, kubectl현재context, hash고정Pythonmultiarchimage, pyroute2 0.9.6. `prepare_load_assets.py`는원본asset경로와기존offlinewheel/modelbundle을필요로하며새load-cache를생성한다. `run_load.py`는서버dryrun/생성/수집/정리를수행한다. `analyze_load.py <run-directory>`는새timestamp분석폴더를만든다. 큰asset은Git에포함하지않고manifest/hash를보존한다.

수집제어는source HTTP `/profile`, idempotent `/start_trace`, `/trace_status`를사용한다. 원격exec연결과관계없이source로컬JSONL/stdout에남기고collector는새event만증분저장한다. 프로파일도동일request ID의원결과를보존해재시도중복추론을막는다. `/32`차단/복구검증은각소유Pod에서직접수행한다. network-control sidecar의CPUlimit은1이고request10m이며측정된operator의1CPU/2GiB와구분한다. 첫시도 `wan-20261019t100804`는 공유WAN검증/240개품질호출/native18workflow를보존하지만, 실제servingthread의OpenMP설정을직접확인한근거가부족해중단했다. 초과스레드실행을확정한것은아니다. 명시적worker초기화/readiness·receipt검증을추가한재실행과성능표를합치지않는다. [thread-context-audit](../../artifacts/topology-profile-live/thread-context-audit.json)에공식소스/별도진단probe근거가있다. CPUreference와의 일치는 원래질문/정답에대한VQA정확도 또는 센서인식정확도를 뜻하지 않는다. 이연구는모델선택/품질향상보다동일품질배치효과를분리한다.


## 중단 기록과 환경 관측

`wan-20261019t102234`는187번째품질호출의E1KubeEdgeexecunexpectedEOF로중단했다. 모델품질실패가아니며수집불완전으로분리한다. 해당노드의ReadyTrue/Memory·Disk·PIDPressureFalse와동시PodReady변화를보존했다. 외부namespace의Pod삭제와기존E1PodReady변화가함께관측됐으나인과관계는확정하지않았다. 소유Pod/CM은0으로정리했다.

최소재현진단 `wan-20261019t103359`의2개소유Pod에서60HTTP조회+10exec조회가통과했다. 이전EOF의원인을재현·확정한것은아니다. 직접HTTP수집으로관측경로의exec의존을줄인뒤전체를새배포에서시작했다. 기존host/CNI/EdgeCore는재설정하지않았다.


## 해석 경계

Fusion이C에고정된작은fork/join에서는고정조건의분기비용을각자줄이는입력/출력비용greedy가DAG최적배치와같아질수있다. 공유WAN·동시도착은분기들을결합하지만혼합배치의이점만으로DAG공동결정의독자적기여나새정책필요성을입증하지않는다. 후속6정책실험은동일정보greedy/재최적화비교와실제로선택이가능한후속Task·중간데이터경계를별도로설계해야한다.


## 최종 재실행 저부하 관측

Run `wan-20261019t103603`, CPU1thread를실제worker에서검증한새Pod배포. 0.1rps,같은first6evaluation입력,매조건6요청,모두성공. 아래는요청평균ms이며독립배포의평균/통계적유의성은아니다.

| 공유대역폭상한·방향별추가지연20ms | 서버집중 V=C/S=C | 엣지처리 V=E0/S=E1 | 혼합 V=E0/S=C |
|---|---:|---:|---:|
|100Mbps|772.0|1420.4|865.0|
|10Mbps|1208.9|1448.3|892.3|
|2Mbps|4031.7|1426.3|960.4|

Native추가지연0:237.3/1248.4/637.9ms,무제한relay추가지연0:244.7/1167.9/638.0ms(서버/엣지/혼합). Native와무제한relay는별도제어조건이다. relay가일부값에서더빠른것을relay의성능개선으로해석하지않는다. 같은입력이어도실행시간변동과순서효과가남는단일배포다.

2Mbps에서혼합은서버집중대비요청평균76.18%낮았다. 반대로100Mbps에서는서버집중이혼합보다빠르다. 전송제약조건과고정센서발생위치가배치선택을바꿀수있다는진단이며,혼합항상우월/새알고리즘기여로일반화하지않는다.


저부하.1rps는first6evaluation,고부하.5/1rps는같은evaluation40개전체를쓴다. .1→.5의평균차이는입력분포차이가섞이므로순수도착률효과로부르지않는다. 도착률에따른대기증가는동일40입력을사용하는.5↔1rps에서비교하고,대역폭/배치효과도각입력집합내에서비교한다.

## 커널 큐 관측기 보완

수집 중 기존 큐 요약 코드가 pyroute2의 `nla_slot`을 재귀 순회하지 못하는 문제를 발견했다. 기존 `backlog_bytes=0` 요약만으로 drain을 확정할 수 없으므로, 매 관측에 보존된 원시 `TCA_STATS`/`TCA_STATS2`의 backlog와 qlen을 독립적으로 다시 읽었다. 처리·전송 설정은 유지하고 관측기만 `get_attr('TCA_STATS')`로 보완했다. ConfigMap 갱신은 11:11:59 UTC, 실제 mount hash 일치 확인은 11:15:06 UTC이며, 반영 뒤 capture는 `native-TCA_STATS-v2`로 식별한다. 갱신 전후 소스와 hash, 처음에는 아직 반영되지 않았던 mount 확인도 보존했다.

부모·자식 qdisc 통계는 중복될 수 있어 합계를 큐 bytes 추정치로 사용하지 않는다. 각 실제 필드가 0인지 확인하는 용도다. 모든 phase의 마지막 두 원시 관측을 대상으로 하는 최종 감사 결과는 아래 검증 기록에서 확인한다. 새 모델 보정 없이 바꾼 범위는 이 읽기 전용 통계 추출뿐이다.

## 최종 고부하 관측

33개 조건의 606개 계획 요청을 모두 terminal 행으로 보존했다. 성공 597건, workflow deadline 시간 초과 3건, source 동시 실행 상한(32)에 따른 `driver_capacity` 거절 6건이다. 거절도 분모에 포함했다. 아래 평균과 p95는 **성공 요청만**의 지연이며, 실패 요청의 전체 terminal 시간은 별도 JSON에 남겼다. 요청 수준 p95는 단일 배포의 기술 통계다.

| 상한 Mbps | 도착률 req/s | 배치 | 성공/계획 | 시간 초과 | 상한 거절 | 성공 평균 ms | 성공 p95 ms | ASR 평균 queue ms |
|---|---:|---|---:|---:|---:|---:|---:|---:|
|10|0.5|cloud|40/40|0|0|1290.4|1417.6|0.2|
|10|0.5|edge|40/40|0|0|1567.5|2155.5|9.8|
|10|0.5|mixed|40/40|0|0|898.5|938.8|0.2|
|10|1|cloud|40/40|0|0|1771.1|2531.8|0.2|
|10|1|edge|40/40|0|0|6940.5|13241.3|5278.4|
|10|1|mixed|40/40|0|0|863.1|950.7|0.2|
|2|0.5|cloud|38/40|2|0|77471.7|111869.2|2.2|
|2|0.5|edge|40/40|0|0|1591.0|2097.7|13.9|
|2|0.5|mixed|40/40|0|0|987.6|1211.7|0.2|
|2|1|cloud|33/40|1|6|94737.1|113211.8|8.8|
|2|1|edge|40/40|0|0|7155.3|13446.1|5496.7|
|2|1|mixed|40/40|0|0|1014.3|1221.4|0.2|

`cloud`는 V=C/S=C, `edge`는 V=E0/S=E1, `mixed`는 V=E0/S=C이며 fusion은 모두 C다. 고부하 12조건은 같은 40개 입력과 순서를 사용한다. 최대 release 지연은 전체 고부하 조건에서 1.59ms 이하였지만, B2/1rps/cloud의 마지막 6개 release는 용량 거절되었으므로 실제 수락률이 계획 1rps를 유지했다고 해석하지 않는다.

B2/cloud는 요청당 평가 입력 평균 약 7.44Mbit의 raw image+audio를 공유 경계로 보낸다(NPY header 포함, envelope·TCP 오버헤드 별도). .5rps부터 이 명목 입력 수요가 2Mbps를 넘는다. 이 조건에서 긴 대기, 실제 qdisc drop, deadline 실패를 관측했다. TCP 공유·재전송, split TCP relay, 64 SKB 큐와 offload 설정의 영향이 함께 포함되므로 지연 전체를 순수 전파 지연이나 한 원인으로 환산하지 않는다. qdisc 계층의 drop 합계를 독립 packet 손실 수로 더하지 않으며 overlimits도 drop과 구분한다.

엣지 ASR은 도착률 .5→1rps에서 평균 queue가 10~14ms에서 5.28~5.50초로 커졌다. 혼합은 큰 이미지의 원격 전송과 느린 엣지 ASR을 함께 피한 이 구성의 진단이다. node 전체의 공유 serving queue나 최적화 정책을 검증한 결과는 아니다. 배치별 정상 상태 throughput, 장기 안정성 또는 일반적인 최적 배치를 주장하지 않는다.

### 데이터 표현 대조

같은 저부하 6개 입력, B2/방향별20ms에서 압축 JPEG/FLAC 전달의 평균은 cloud 1276.9ms, edge 1442.7ms, mixed 901.1ms였다. raw 전달은 각각 4031.7/1426.3/960.4ms였다. B2/delay0 raw는 3741.5/1216.9/672.0ms다. decoded 입력 경계와 지연 조건을 명시해야 원래 Git의 네트워크 부하 가정을 해석할 수 있다. 압축 입력은 operator에서 decode하므로 순수 전송 bytes 효과만 분리한 실험은 아니다.

## 검증·정리와 남은 한계

사전 품질 gate 240개 분기 호출이 통과했다. 평가에서 받은 최종 출력 597개는 Top1/Top5 순서·집합 모두 CPU reference와 일치했고 최대 score 절대 오차는 5.364418029785156e-7이었다. 성공 요청의 1,791개 stage receipt는 모델·입력·출력 hash, 실행 Pod UID/노드, 실제 serving worker의 intraop/inter op 1, 고정 엣지 source를 통과했다. 실패 요청의 받지 못한 출력 품질은 검증한 것으로 세지 않는다. 이 receipt 수는 실패 중 수행된 연산을 포함한 전체 실행 수가 아니다.

[독립 offline 검증](../../artifacts/topology-profile-live/wan-20261019t103603/verification-v1.json)은 오류 0건이다. 각 phase의 마지막 두 원시 backlog 관측과 전체 경로의 idle을 대조했다. [별도 감사 v2](../../artifacts/topology-profile-live/wan-20261019t103603/backlog-native-audit-v2.json)는 33개 phase × 마지막 두 관측, 총 66개 capture의 실제 backlog/qlen이 모두 0임을 확인했다. 이 중 v2 관측기 표시가 있는 10개 capture는 요약값도 원시 필드와 일치했다. phase 내내 큐가 없었다는 뜻은 아니다. 실제 `/32` 차단 시 직접 연결 실패/relay 성공, 해제 뒤 모든 source/operator의 직접 연결 복구를 확인했다. [cleanup](../../artifacts/topology-profile-live/wan-20261019t103603/cleanup.json)의 소유 Pod/ConfigMap 잔여 수는 0, 정리 오류는 0이다.

[보호 리소스 관측](../../artifacts/topology-profile-live/wan-20261019t103603/protected-after.json)은 시작 시 관측한 외부 Pod 197개 중 `ebpf-event-paper-20261019`의 기존 Ready=False Pod 3개가 종료 시 없음을 기록했다. 나머지 UID의 phase/Ready 변화는 기록되지 않았다. 삭제 원인·실험과의 인과관계는 확인하지 않았으며, 클러스터 전체가 변하지 않았다고 주장하지 않는다. CPU throttling·실제 센서·가속기 실행·독립 배포 반복·6정책 비교는 이번 결과로 검증되지 않는다.

### 결과 파일과 실제 명령

- [조건별 JSON 통계](../../artifacts/topology-profile-live/wan-20261019t103603/analysis-20261019t111715/summary.json)
- [조건별 CSV](../../artifacts/topology-profile-live/wan-20261019t103603/analysis-20261019t111715/phases.csv)
- [PNG 그래프](../../artifacts/topology-profile-live/wan-20261019t103603/analysis-20261019t111715/load-diagnostic.png) · [SVG 그래프](../../artifacts/topology-profile-live/wan-20261019t103603/analysis-20261019t111715/load-diagnostic.svg)
- [전체 수집·실패·원시 관측](../../artifacts/topology-profile-live/wan-20261019t103603/)

저장소 루트에서 실제 실행한 명령이다. 첫 명령은 준비한 대용량 asset이 필요하고 클러스터에 실험 리소스를 생성한다. 나머지 두 명령은 저장된 결과를 읽는다. 분석은 새 timestamp 폴더를 생성하고 검증 v1은 기존 파일을 덮어쓰지 않는다.

```bash
rtk proxy python3 -u experiments/real-topology-live/run_load.py
rtk proxy /tmp/tp-reference-env/bin/python experiments/real-topology-live/analyze_load.py artifacts/topology-profile-live/wan-20261019t103603
rtk proxy python3 experiments/real-topology-live/verify_load.py artifacts/topology-profile-live/wan-20261019t103603
```

단위 경계의 로컬 검증은 `load-local-validation-v2.json`에 실제 명령과 9개 통과 결과를 보존했다. 최종 그림은 성공 지연과 실패 분모를 함께 표시하며, 원시 데이터와 이전 중단 시도를 덮어쓰지 않았다.
