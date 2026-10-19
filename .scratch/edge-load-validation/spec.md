# 원 Git 데이터 경계·공유 WAN·open-loop 부하 검증

Status: ready-for-agent

사용자가 native smoke의 낮은 전송 비용을 지적하고 Git 실험을 더 분석해 실제 엣지와 같은 병목/부하를 주도록 요청했다. [재분석·수정 계획](../../docs/experiments/topology-profile-load-reanalysis.md)을 기준으로 한다. 이번 분석에서는 새 클러스터 부하를 적용하지 않았다.

필수 순서:

1. JPEG/FLAC에서 decoded uint8/float32PCM으로 task 경계를 복원하되 실제 데이터만 전송한다. CPU reference 동일성·source 비용 경계·binary wire bytes를 동결하고 압축 전송은 별도 대조로 보존한다.
2. completed64/TTL120 admission 제한과 fusion 전역 lock을 보완하고, absolute deadline/미완료 queue/stop/drain/terminal error 행 계약을 구현한다.
3. 소유 gateway의 양방향 aggregate cap과 delay, 두 source 동시 합계 용량, 우회 차단·복구를 검증한다. hostNIC/CNI/EdgeCore는 변경하지 않는다.
4. 낮은0.1rps에서100/10/2Mbps 및 addedRTT40ms를 먼저 진단하고, 동일 gateway의 delay0 및 unlimited/native 대조로 원인을 분리한다.
5. fresh calibration/profile/plan 동결 후6정책·0.5/1rps·burst·cross-traffic·명시 shared serving pool을 단계적으로 확대한다. node-wide FIFO와 독립 Pod를 혼동하지 않는다.

근거: source profiler는 raw image/PCM, 기존 smoke 입력은 압축 파일. 전체 evaluation40의 raw 본체 평균821505+108209.6B, 기존 압축181636.025+33306.2B. main의33.46% 기본효과는 queue보정이며 slowWAN/별도queue 조건0%. 로컬 HTTP64완료 후65번째429 재현. 이들은 새 클러스터 부하 성능 결과가 아니다.


## 실행 범위 동결 (2026-10-19 사용자 재실행 승인)

- 비교 경계 46fd850. 이번 실행은 단계1~4와 단계5의 0.5/1rps 확대에 대한 단일 배포 진단이다.6정책·burst·외부cross-traffic·node-wide shared pool은 별도 확장이다.
- 독립 표본은 요청 수가 아닌 배포/조건 궤적이다. 단일 배포의 기술 통계만 보고한다.
- 공통 후보 V={E0,C}, S={E1,C}, F={C}; 실제 가중치 CPU/batch1/1thread. 후보5개 Pod는 모든 조건에 상주하며 각자 FIFO1lane이다. source E0/E1 고정.
- raw fixture는 배포 전에 PIL RGB uint8 및 soundfile16k float32PCM으로 파생한 NPY. 준비/hash는 요청 clock 전에 수행하며 현장센서/현장decode 지연으로 해석하지 않는다. 압축 원본/기존smoke는 보존한다.
- 사전 HTTP seam은 binary byte/hash, ack후capacity, fusion전역lock과 open-loop terminal ledger다. 계획 승인에 포함된 검증 경계로 사용한다.
- qdisc: 소유 gateway Pod rootPRIO, cloud/edge 방향별 TBF(cap100/10/2Mbps,burst4096B)+netem(0/20ms,limit64packets). HTTP relay의 TCPsplit과 왕복 횟수는 고유 비용이다. 두 source aggregate/up/down 및 application RTT 차이를 별도 검증한다.
- source/operator 소유Pod의 동결된 반대zone PodIP/32를blackhole한다. 직접실패/relay성공/해제복구를 기록하며Service/IPv6 전반을 방화벽으로 차단했다고 하지 않는다.
- 20calibration/40evaluation 전부 raw CPUreference 품질gate. node별vision allclose/ASRexact 후 새calibration만 별도freeze한다. evaluation은비용 fitting에사용하지 않는다.
- native, unlimitedrelay 각3배치; B100/10/2+delay20ms 각3배치 at.1rps/6requests; B2delay0 각3배치. lowfactor9조건 순서는seed20261019.
- B10/2 × .5/1rps ×3배치 각40requests. high순서seed20261020. deadline120s, boundedsource32, stop/drain, terminal실패포함. B2/1rps를stationary성능으로해석하지 않는다.
- 각조건이 끝나면 모든 servingqueue/active0를확인. 기존artifact및초기실패별도보존; 종료시소유Pod/CM0, protected상태대조.

- worker-context audit 후 실제 servingthread에서 intraop1을한번설정하고 intraop/inter op1을확인한뒤readiness를연다. 이근거가없던첫시도wan-20261019t100804는별도보존하고합치지않는다.

- 재실행wan-20261019t102234는187번째quality-call의KubeEdgeexecunexpectedEOF로중단. 모델불일치로세지않고별도보존. 원인미확정. 이후source-localHTTP start/status(동일phase시작idempotent)/로컬JSONL지속저장과직접HTTP profile·health로제어를분리한다. inputpayload는계속edge에서발생하며collector는제어/작은결과만받는다. HTTP재시도는시작동일config/조회와기록된transportfailure에한한다. 보호Pod상태변화는함께기록하고실험인과로단정하지않는다.
