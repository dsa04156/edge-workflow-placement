# WireGuard 운영 잔여물 제거 — 2026-10-20

사용자가 WireGuard 전체 제거를 요청했다. 진행 중인 `wan-20261020t114647`은 SIGINT로 정상 정리 경로를 실행했으며 cleanup의 remaining_owned=0, errors=[]를 확인했다. 품질 보정 시작 단계에서 중단됐고 성능 비교 완료로 보고하지 않는다.

접속 가능한 Ready 노드 9대(서버1–4, Pi dev0002/3/6/8, Orin dev0009)를 점검했다. 패키지와 활성 WireGuard 링크는 이미 없었다. 남은 빈 wg-quick@wg0 drop-in 디렉터리, 서버3/4의 빈 kubelet 20-wireguard.conf, modules-load의 wireguard 행을 제거했다. 로드된 wireguard 모듈은 modprobe -r로 해제했다. OS 커널 패키지에 포함된 미로드 드라이버 바이너리를 지우거나 커널 패키지를 제거하지 않았다.

일반 LAN/CNI에 필요한 설정은 값 변경 없이 이름을 정리했다. 99-edgeai-wireguard-routing.conf→99-edgeai-routing.conf, 99-kubeedge-wireguard.conf→99-kubeedge-forwarding.conf, 서버2의 30-edgeai-wireguard.conf→30-edgeai-lan.conf. server2의 --node-ip=10.254.192.129는 유지했다. systemctl daemon-reload만 수행했고 kubelet 재시작은 하지 않았다.

서버1의 FORWARD→EDGEAI_WG_TRANSIT 점프와 wg0 전용 3규칙을 삭제하고 해당 체인을 제거했다. 다른 CNI/서비스 방화벽은 유지했다. 재검사에서 9노드 모두 WireGuard link/module/unit/config path=0이고 IPv4/IPv6 방화벽 및 확인한 영속 서비스/네트워크 설정에서 WireGuard 규칙이 없었다. 상세 근거는 artifacts/wireguard-removal-20261020/verification.json 및 nodes-after.json이다. 임시 관리 Pod 4개는 삭제했다.

미확인 장비: dev0001(.164.139), dev0004(192.168.0.7), dev0007(.164.140)은 SSH timeout으로 점검/제거하지 못했다. 전체 물리 장비 제거 완료라고 주장하지 않는다.

별도 잔여 참조: edgeai-workflow-api Endpoints/EndpointSlice는 이전 VPN 주소 10.77.0.1:18447을 사용한다. edgeai-buildkit-arm64-config에 10.77.0.1:5000 registry 항목, mlflow allowed-hosts에 10.77.0.1이 있으며 과거 실험 fixture/annotations에도 이전 주소가 남아 있다. 이들은 WireGuard 터널/자동 시작 구성과 구분한다. 다른 애플리케이션의 endpoint 재설계나 과거 실험 자료 변경은 수행하지 않았다.

Semantica 도구가 unsupported call 상태여서 로컬 문서에만 기록했다.
