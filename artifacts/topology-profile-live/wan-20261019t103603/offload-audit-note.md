# Gateway MTU/offload 관측

새 retry `wan-20261019t103603`의 gateway `netctl` container에서 **2026-10-19T10:39:23.752157+00:00 UTC**에 직접 조회했다. `offload-audit.json`에 정확한 kubectl argv·stdin Python·시작/종료 UTC·exit code·stdout/stderr와 전체 feature mapping을 보존했다. 사용 API는 `IPRoute.get_links()`와 `Ethtool.get_features("eth0")`이며 설정 변경·host 접속·모델 실행은 없었다.

- eth0 MTU: **1330 bytes**
- kernel: `7.0.0-38-generic`
- `tx-generic-segmentation`, `tx-tcp-segmentation`, `tx-tcp6-segmentation`, `tx-scatter-gather`, `tx-gso-list`: enabled
- `rx-gro`, `rx-gro-list`, `rx-gro-hw`, `rx-lro`, `rx-udp-gro-forwarding`: disabled
- GSO/GRO maximum size: 65,536 bytes; GSO maximum segments: 65,535
- TSO maximum size: 524,280 bytes; maximum segments: 65,535

이것은 interface의 설정·지원 한계다. 실제 skb 크기 분포를 측정하지 않았으므로 netem `limit=64`를 `64 × 1500 bytes`나 `64 × 1330 bytes`의 엄밀한 queue 상한으로 해석할 수 없다. 활성 GSO는 큰 skb를 이후 여러 segment로 나눌 수 있다. queue backlog bytes·drops·실효 전송률을 별도 관측값으로 사용한다. [Linux 공식 offload 문서](https://docs.kernel.org/networking/segmentation-offloads.html)

과거 실패 run의 값을 가져와 쓰지 않고 이번 Pod에서 새로 조회했다. 동일한 설정값이 관측됐다는 사실이 실제 queue 동작이나 성능까지 같다는 뜻은 아니다.
