# Gateway offload 조회

2026-10-19T10:26:49.656331+00:00 UTC에 실험 소유 gateway Pod `wan-20261019t102234-gateway`의 `netctl` container에서 pyroute2 `IPRoute.get_links()`와 `Ethtool.get_features("eth0")`만 조회했다. 설정 변경·host 접속·모델 실행은 하지 않았다. 정확한 argv, stdin Python, 시작/종료 UTC, stdout/stderr와 exit code는 `offload-audit-features.json`에 보존했다.

- network namespace: `net:[4026535620]`
- kernel: `7.0.0-38-generic`
- eth0 MTU: **1330 bytes**
- `tx-generic-segmentation`, `tx-tcp-segmentation`, `tx-tcp6-segmentation`, `tx-scatter-gather`, `tx-gso-list`: enabled
- `rx-gro`, `rx-gro-list`, `rx-gro-hw`, `rx-lro`, `rx-udp-gro-forwarding`: disabled
- `IFLA_GSO_MAX_SIZE`/`IFLA_GRO_MAX_SIZE`: 65,536; `IFLA_GSO_MAX_SEGS`: 65,535
- `IFLA_TSO_MAX_SIZE`: 524,280; `IFLA_TSO_MAX_SEGS`: 65,535

이 값은 interface가 보고한 설정/지원 한계이며 실제 qdisc에 들어온 skb 크기 분포를 측정한 결과는 아니다. 활성 GSO/TSO와 MTU를 고려하면 netem의 `limit=64`를 `64 × 1500 bytes` 또는 `64 × 1330 bytes`의 엄밀한 queue 상한으로 계산할 근거가 없다. 실제 backlog bytes·drops·전송률을 별도로 사용한다. GSO는 하나의 큰 skb를 나중에 작은 segment로 나눌 수 있다. [Linux 공식 offload 문서](https://docs.kernel.org/networking/segmentation-offloads.html)

최초 `offload-audit.json`은 raw link 조회를 보존한다. 그 시도의 `features: {}`는 pyroute2의 namedtuple에 `vars()`를 적용한 직렬화 한계이며, offload feature가 없다는 뜻이 아니다. 수정된 read-only 조회 `offload-audit-features.json`은 `.features` mapping의 각 `enable`/`available` 값을 직접 기록했다. 최초 시도도 덮어쓰지 않았다.
