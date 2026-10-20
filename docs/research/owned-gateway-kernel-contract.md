# 실험 소유 Pod의 공유 duplex gateway 계약

2026-10-19. 공식 API·소스를 확인한 구현 전 조사다. 이 조사에서는 클러스터·호스트 네트워크를 변경하거나 성능을 측정하지 않았다. 기존 `docs/research/edge-wan-load-emulation.md` 조사(주 작업 폴더)의 좁은 구현 보완이며, 배포 후 아래 preflight 통과가 필요하다.

## 구현 가능한 최소 구조

한 gateway Pod의 `eth0` root PRIO에 방향별 TBF 두 개를 둔다. gateway에서 **cloud 목적지로 나가는 모든 TCP packet**은 `1:1`, **edge 목적지로 나가는 모든 TCP packet**은 `1:2`에 합친다. 각 source별 TBF를 만들지 않는다. 나머지 관리 트래픽은 `1:3`으로 보낸다. PRIO의 band는 자동 생성되며 필터가 class를 선택한다. PRIO 자체는 rate limiter가 아니고 두 방향도 하나의 물리 egress를 공유하므로, 무제약 동시 전송이 Pod/NIC 한계에 닿으면 방향 간 간섭이 남는다. [공식 PRIO 매뉴얼](https://man7.org/linux/man-pages/man8/tc-prio.8.html)

HTTP relay는 inbound body를 목적 operator로 전달하고 응답을 원 source에 반환한다. 요청 body뿐 아니라 TCP ACK·응답·join/control 트래픽도 목적지 분류의 대상이다. source→gateway 구간과 operator→gateway 구간 자체에는 해당 방향 cap이 없다. gateway가 내보내는 다음 구간에서 cap이 적용된다. 이는 **TCP를 두 연결로 나눈 명시적 relay 실험**이라는 설계 선택이며, 투명 router나 end-to-end TCP WAN과 동일하다는 주장이 아니다. 큰 body를 전부 수신한 후 재전송하면 추가 store-and-forward 지연·메모리를 만들므로 unlimited-relay 대조군과 body buffering 방식을 동결한다.

zone별 endpoint Pod IP를 manifest로 고정하고 edge 소유 Pod에는 cloud endpoint `/32`, cloud 소유 Pod에는 edge endpoint `/32` blackhole route를 넣는다. gateway IP는 이 목록에서 제외한다. route는 netns 내부에만 설치한다. 이 방식은 고정한 IP의 직접 접속을 막는 제한된 실험 장치이며 Service IP/DNS/IPv6/새 Pod IP까지 자동 차단하는 전역 방화벽이 아니다. 평가기의 목적지는 allowlist의 IPv4 Pod IP로 제한하고 Pod UID/IP 변경 시 재구성·재검증한다. Linux blackhole은 목적지를 폐기하며 로컬 sender에 EINVAL을 줄 수 있다. [ip-route 매뉴얼](https://man7.org/linux/man-pages/man8/ip-route.8.html), [pyroute2 route API](https://docs.pyroute2.org/iproute_linux.html)

## 정확한 pyroute2 API

PyPI의 `pyroute2==0.9.6` wheel `pyroute2-0.9.6-py3-none-any.whl`을 메모리에서 읽어 확인했다. SHA256은 `3334091326e560a506635449af03b26920d22d4e5a7996aed354363d106fcef8`이다. [공식 PyPI release](https://pypi.org/project/pyroute2/0.9.6/). GitHub MCP로 source commit `1a2e5b52e5de8f381490ebe93f790cb90834c18a`도 확인했다. Context7은 monthly quota exceeded로 문서를 반환하지 않아 공식 소스와 매뉴얼로 대체했다.

다음은 API와 hierarchy 설명용이며 이 파일에서 실행하지 않았다. `burst_bytes`, `tbf_initial_limit_bytes`, `leaf_limit_packets`는 평가 전에 정한 값이다. gateway Pod가 실험 소유임을 namespace/label/UID/netns로 먼저 검증하고 기존 root가 default noqueue 또는 자신의 handle인 경우만 구성한다.

```python
import ipaddress
from pyroute2 import IPRoute

with IPRoute() as ipr:
    idx = ipr.link_lookup(ifname="eth0")[0]
    ipr.tc("add", "prio", index=idx, handle="1:", bands=3,
           priomap=[2] * 16)
    for band, tbf, netem in [(1, "10:", "11:"), (2, "20:", "21:")]:
        ipr.tc("add", "tbf", index=idx, handle=tbf,
               parent=f"1:{band}", rate=f"{mbps}mbit",
               burst=burst_bytes, limit=tbf_initial_limit_bytes)
        ipr.tc("add", "netem", index=idx, handle=netem,
               parent=tbf + "1", delay=20000, jitter=0, loss=0,
               limit=leaf_limit_packets)
    for priority, (destination, band) in enumerate(destination_bands, 1):
        address = int(ipaddress.IPv4Address(destination))
        ipr.tc("add-filter", "u32", index=idx, parent="1:",
               prio=priority, protocol=0x0800, target=f"1:{band}",
               keys=[f"{hex(address)}/0xffffffff+16"])
    qdiscs = ipr.get_qdiscs(index=idx)
    filters = ipr.get_filters(index=idx, parent=0x10000)
```

`parent="10:1"`/`"20:1"`가 TBF의 자식이다. 공식 netem 예제도 PRIO→TBF→netem 조합을 제시한다. 다만 qdisc 조합이 항상 정확하다는 보증은 아니므로 실효 검증이 필요하다. [공식 netem 예제·제약](https://man7.org/linux/man-pages/man8/tc-netem.8.html)

**단위 함정:** pyroute2 netem의 `delay=20000`은 20,000µs다. 해당 release의 `sched_netem.get_parameters()`는 `time2tick(delay)`를 직접 호출하며 `time2tick`은 `int(time)`을 사용한다. 따라서 `delay="20ms"`를 넘기면 안 된다. TBF의 `latency="50ms"`는 다른 parser를 사용하므로 두 API를 혼동하지 않는다. [netem source](https://github.com/svinota/pyroute2/blob/1a2e5b52e5de8f381490ebe93f790cb90834c18a/pyroute2/netlink/rtnl/tcmsg/sched_netem.py), [time parser source](https://github.com/svinota/pyroute2/blob/1a2e5b52e5de8f381490ebe93f790cb90834c18a/pyroute2/netlink/rtnl/tcmsg/common.py)

`netem.limit`는 **packet 수**, TBF `limit`는 **bytes**다. TBF의 기본 bfifo를 netem으로 교체하면 이전 TBF limit/latency가 유효한 queue 상한으로 남지 않는다. netem leaf limit를 별도로 고정하고 실제 `backlog` bytes·drops를 측정한다. GSO skb 때문에 `packets × MTU`를 엄밀한 byte 상한으로 가정하지 않는다. 작은 burst는 높은 rate의 달성률을 낮출 수 있고 큰 burst는 짧은 feature를 거의 제한하지 않을 수 있으므로 long-transfer cap과 짧은 RPC 지연을 따로 검사한다. [공식 TBF 매뉴얼](https://man7.org/linux/man-pages/man8/tc-tbf.8.html), [Linux offload 문서](https://docs.kernel.org/networking/segmentation-offloads.html)

직접 경로 차단과 해제 API는 다음과 같다. 기존 route를 덮어쓰는 `replace` 대신 실험이 새로 소유한 `/32`만 `add`/`del`한다. 충돌은 실패로 보존한다.

```python
ipr.route("add", dst=f"{remote_ip}/32", type="blackhole", table=254)
# 종료 시 자신이 성공적으로 추가한 route만 삭제한다.
ipr.route("del", dst=f"{remote_ip}/32", type="blackhole", table=254)
```

## 호스트 변경 없는 preflight

1. `hostNetwork: false`, host PID/mount 없음, 소유 label과 UID, `/proc/self/ns/net`, `/proc/self/status`의 CapEff, kernel release, `/proc/net/psched`, interface MTU·route·qdisc와 offload 조회를 저장한다. NET_ADMIN은 container `securityContext.capabilities.add: ["NET_ADMIN"]`로만 부여한다. privileged·host mount·SYS_MODULE은 필요하지 않다. 실제 admission 성공과 유효 capability는 별도 확인한다. [Kubernetes security context](https://kubernetes.io/docs/tasks/configure-pod-container/security-context/), [Linux capability 목록](https://man7.org/linux/man-pages/man7/capabilities.7.html), [netns 격리](https://man7.org/linux/man-pages/man7/network_namespaces.7.html)
2. module 파일/`/proc/modules` 조회는 보조 정보다. built-in이면 목록에 없을 수 있다. 소유 gateway netns의 default qdisc만 있는 dummy interface에 PRIO/TBF/netem과 u32 filter를 설치·dump·삭제해 실제 kernel/API 지원을 판정한다. dummy 자체가 지원되지 않으면 default인 소유 eth0에서 가역적인 짧은 검사를 한다. Netlink errno·전체 stdout/stderr를 저장한다. host `modprobe`/host qdisc 변경으로 해결하지 않는다. qdisc 생성은 kernel module 자동 요청을 유발할 수 있으므로 모듈 로딩까지 전혀 없어야 한다면 기존 지원 상태만으로 검사 범위를 제한한다.
3. 무제약 relay에서 단일·두 source 동시 binary upload/download의 payload hash, 총 bytes, wall time을 확인한다. 이어 각 방향 단독과 동시 부하에서 100/10/2Mbps aggregate goodput, qdisc bytes/packets/backlog/drops/overlimits, gateway CPU/throttling을 기록한다. 초기 burst의 영향이 작아질 만큼 충분히 긴 전송을 사용한다. HTTP header/ACK 포함 kernel bytes와 task payload bytes를 분리한다.
4. 각 소유 source/operator netns에서 차단 전 직접 성공, 차단 후 직접 실패, relay 성공, edge-local/cloud-local 성공을 모두 확인한다. 단순 route dump나 HTTP client의 relay log만으로 no-bypass 통과를 선언하지 않는다. cache/model 준비가 끝난 뒤 차단한다.
5. zero-delay와 20ms-per-direction을 따로 측정한다. 두 egress 분류에 20ms를 줘도 relay의 두 TCP 연결 전체가 native RTT+40ms라는 뜻은 아니다. 새 연결 handshake·RPC 왕복 횟수·TCP ACK 영향이 추가된다. 가능하면 각 leg의 idle RTT와 연결 재사용 echo를 저장하고 HTTP workflow wall time과 분리한다. sender 측 netem의 TSQ 왜곡 가능성이 있으므로 이 구조의 TCP-split 의미와 receiver-ingress 권고를 결과 한계에 남긴다. [netem 제약](https://man7.org/linux/man-pages/man8/tc-netem.8.html)
6. arrivals 중지→drain→backlog 0 확인 후 다음 조건으로 이동한다. 종료 시 자신의 root qdisc·추가 route 또는 소유 Pod 전체를 해제하고 원래 protected workload·기존 network 설정을 대조한다. 실패·timeout·부분 출력도 독립 run artifact로 보존한다.

이 preflight가 실패하면 해당 cap/delay 조건의 AI 결과를 WAN 검증 결과로 집계하지 않는다. root PRIO의 방향 간 우선순위나 relay CPU가 병목이면 interface 분리/투명 forwarding 검토가 필요하며, 구현을 바꾼 뒤 새 보정으로 시작한다.
