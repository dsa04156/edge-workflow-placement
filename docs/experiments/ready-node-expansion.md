# Ready 노드 후보 확장: 기본 스케줄러·프로파일 배치 비교 수집기

2026-10-22. **구현·오프라인 검증만 완료, 실장비 실행 0회.** 성능·정책 비교 결과는 없다.

## 목적과 전제

사용자 요청으로 배치 후보를 기존 3노드에서 현재 Ready인 9노드로 늘린다. 실험 전제는 센서가 엣지 장비에 붙어 있는 엣지 컴퓨팅 환경이다. 영상 source와 결과 목적지는 e0=etri-dev0003-raspi5, 음성 source는 e1=etri-dev0002-raspi5에 고정한다. 서버는 제한 WAN 너머에 있다. 입력은 실제 센서 스트림이 아니라 엣지 Pod에서 재생하는 파일이다. c=etri-ser0002-cgnmsb는 helper·gateway·cache host로 그대로 둔다.

| 별칭 | 노드 | 역할 | arch | 비고 |
|---|---|---|---|---|
| e0 | etri-dev0003-raspi5 | edge | arm64 | 영상 source·결과 목적지 (고정) |
| e1 | etri-dev0002-raspi5 | edge | arm64 | 음성 source (고정) |
| c | etri-ser0002-cgnmsb | cloud | amd64 | helper/gateway/cache (고정) |
| e6 | etri-dev0006-raspi5 | edge | arm64 | 추가 |
| e8 | etri-dev0008-raspi5 | edge | arm64 | 추가 |
| e9 | etri-dev0009-jetorn | edge | arm64 | 추가. CPU 이미지만 사용, GPU 미사용 |
| c1 | etri-ser0001-cg0msb | cloud | amd64 | 추가. control plane. 사용자 요청으로 포함, requests/limits 유지 |
| c3 | etri-ser0003-cg0ms0 | cloud | arm64 | 추가 |
| c4 | etri-ser0004-cgnms0 | cloud | amd64 | 추가 |

제외: etri-dev0001-jetorn, etri-dev0004-tedger, etri-dev0007-jetagx. 2026-10-22T04:00:12Z inventory(`.scratch/ready-node-expansion/inventory.json`)에서 NotReady였고 `node.kubernetes.io/unreachable` NoSchedule/NoExecute taint가 있었다. taint는 제거하지 않았다.

## 구현

- 명시 topology: `experiments/real-topology-live/topologies/ready-nodes-20261022.json`. 모든 노드에 role을 명시해야 하며 기본값은 없다. 추가 서버가 edge로 분류되는 경로가 없다. e0/e1/c의 이름·역할·순서가 바뀌면 거부한다. 제외 노드를 후보에 넣거나 노드 이름이 중복되어도 거부한다.
- `topology.py`: 검증, digest, 별칭·역할, 전송 보정 RPC 수, node projection, preflight.
- 주입 경로: `Session.nodes`와 `Pilot.roles`는 클래스 기본값이 기존 `NODES`·3노드 역할이다. `BaselinePilot(config)`는 인스턴스 속성으로만 바꾼다. 모듈 전역 monkeypatch는 없다. helper 열거(`Pilot.helpers()`), zone 분류, qualification, capacity, 전송 보정 origin/destination, drain, 기본 스케줄러 whitelist가 모두 같은 `self.nodes`를 사용한다. 따라서 default-scheduler와 프로파일 정책의 후보와 자원 검사가 같다.
- 변경 전 preflight: 명시 topology에서만 `topology-preflight` phase가 첫 단계로 실행된다. `kubectl get nodes`를 읽기만 하며 다음 조건이면 helper 배포 전에 중단한다. 설정 노드가 없음, NotReady, cordon(`spec.unschedulable`), NoSchedule/NoExecute taint, 기존 qualification에서 확인되지 않은 arch(amd64/arm64 외). PreferNoSchedule은 차단하지 않는다.
- 증거 동결: `topology-frozen.json`(topology·digest·설정 파일 hash), `topology-preflight.json`(판정·projection), raw `*-topology-nodes.stdout`. design에는 `topology`와 `workload_estimate`가 들어간다.
- capacity는 설정한 후보 노드가 cluster 응답에 없으면 실패한다. 측정값을 채워 넣지 않는다.
- verifier: run의 `topology-frozen.json`에서 후보를 읽는다. 기대 design, qualification 행, 전송 보정 행 수, profile 후보, scheduler whitelist와 고정 source를 이 topology로 재계산한다. digest·preflight 재판정·raw snapshot 재projection이 일치해야 한다. 파일이 없으면 legacy 3노드로 감사한다. 명시 topology run에서 이 파일을 지우면 design 불일치로 실패한다.
- 유지한 항목: 정책 6개, 조건 10/2 Mbps·20 ms, 보정 입력 4개, 평가 입력 3개, warmup 1, 최대 workflow 요청 48.

## 작업량 (dry-run 산출, 상한 요청 수)

| 항목 | 기존 3노드 | 9노드 |
|---|---:|---:|
| qualification 배포 (task×node) | 9 | 27 |
| qualification 요청 (×12) | 108 | 324 |
| 전송 보정 RPC (2조건 합) | 240 | 1,584 |
| helper Pod (source+sink+gateway+cache) | 8 | 20 |
| 평가 workflow 요청 상한 | 48 | 48 |

조건당 전송 RPC 수는 (image 9 + audio 9 + vision 81 + speech 81 + return 9 + control 9) × 보정 4 = 792이다. profile이 모든 후보 쌍의 실측 전송값을 요구하므로 vision/speech는 9×9 전체 쌍이다. 이 수에는 health·poll·drain·제어 exec·재시도가 빠져 있다. guard 1회의 kubectl exec도 helper 수에 비례해 약 3배가 된다.

참고 시간: legacy run `wan-20261022t024224`과 `wan-20261021t101203`의 qualification은 8.07분과 8.04분이었다. 전송 보정은 0.19분과 0.25분이었다. 선형 외삽하면 qualification 약 24분, 보정 약 1.3분과 1.7분이다. 신규 노드의 이미지 pull, Jetson·4GB Raspberry Pi 처리 속도, 서버 간 실제 경로는 반영하지 않은 추정치다.

## 검증

- 회귀 테스트: `cd experiments/real-topology-live && rtk proxy python3 -m unittest test_ready_node_expansion`. 20개 통과. 실제 `Pilot.deploy/qualify_all/transfer_profiles/drain/capacity`와 `BaselinePilot.deploy_operators`를 9노드로 실행한다. kubectl·upload·데이터 경로 RPC·netctl만 fake다.
- 전체 live unit: `rtk proxy python3 -m unittest discover -s . -p 'test_*.py'`. 85개 통과. 기존 65개는 수정하지 않았다.
- legacy 호환: 기본 `build_design` 결과가 변경 전 사본과 같다. 동결 run 3개(`wan-20261022t024224`, `wan-20261021t101203`, `wan-20261021t094522`)의 verifier 출력도 `verifier_sha256`을 제외하고 변경 전과 같다.
- 오프라인 dry-run(kubectl 없음):
  `rtk proxy python3 experiments/real-topology-live/run_baseline_comparison.py --dry-run --topology experiments/real-topology-live/topologies/ready-nodes-20261022.json --inventory .scratch/ready-node-expansion/inventory.json`
  → exit 0, 후보 9, 제외 3, 고정 source e0/e1와 목적지 e0, preflight passed, 전송 RPC 1,584. 출력은 `.scratch/ready-node-expansion/dry-run-expanded.json`.

## 한계와 미해결

- **선행 blocker 미해결.** `wan-20261022t024224`에서 발생한 guard 제어 kubectl exec `unexpected EOF`(sink-e1)는 이번 작업에서 고치지 않았다. helper가 늘어 exec 수가 증가하므로 같은 실패에 더 노출될 수 있다. 실행 전에 이 연결 문제를 먼저 최소 재현으로 확인한다.
- 신규 6노드의 이미지·runtime 호환성은 미검증이다. private registry(192.168.0.56:5000) pull, Jetson L4T, ser0003 arm64 서버에서 qualification이 실패하면 그대로 실패로 남긴다. 해당 노드를 대체 측정값으로 채우지 않는다.
- WAN shaping은 기존과 같이 gateway를 거치는 edge↔cloud 구간에만 적용된다. 서버 간(c↔c1/c3/c4)과 엣지 간 직접 경로는 shaping하지 않은 실제 LAN 경로다.
- preflight는 실행 시작 시점의 판정이다. 실행 중 NotReady는 capacity 검사에서 실패로 드러난다. 실행 중 cordon·taint 변화는 Pod 배치 실패로만 나타난다.
- 1 block smoke 규모다. CI·p95·처리량 주장은 없다.

## 다음 실행 명령 (이번에는 미실행)

```bash
rtk proxy python3 -u experiments/real-topology-live/run_baseline_comparison.py --execute \
  --topology experiments/real-topology-live/topologies/ready-nodes-20261022.json
rtk proxy python3 experiments/real-topology-live/verify_baseline_comparison.py \
  artifacts/topology-profile-live/<run-id> --output .scratch/ready-node-expansion/<run-id>-audit.json
```

## 최종 검토 반영

Spec·Standards 독립 검토에서 같은 P2 1건(명시적으로 지정한 3노드 설정의 사전 검사 생략)을 발견해 수정했다. 이제 --topology가 지정되면 노드 수와 관계없이 사전 검사·동결을 수행한다. 기본 no-flag 3노드 실행은 기존 동작을 유지한다. 최종 회귀 테스트23개는 호스트가 직접 재실행해 통과했고, 구현자 전체 테스트는88개 통과했다. 호스트는 수정 전 전체85개도 직접 실행했다. 실장비 배포는 수행하지 않았다.

## 후속 실제 준비 검사

사용자 실행 요청 후9노드 helper preflight를 수행했다. 7노드는 준비됐으나 dev0008/dev0009의 사설 이미지 저장소 timeout으로 모델 실험 전 중단했다. [상세 진단·정리 결과](ready-node-preflight-20261022.md). 위 구현 당시 실장비0회 표시는 이 후속 진단 전 시점을 뜻하며, 성능 비교 실측은 여전히 없다.
