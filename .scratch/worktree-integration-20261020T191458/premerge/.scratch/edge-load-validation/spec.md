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
